import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";
import { after, before, test } from "node:test";
import worker from "./worker.mjs";

const tokens = { Alice: "A".repeat(43), Bao: "B".repeat(43), Chen: "C".repeat(43) };
const sqlite = new DatabaseSync(":memory:");
const db = {
    prepare(sql) {
        const statement = sqlite.prepare(sql);
        function bind(...params) {
            return {
                async first(column) {
                    const row = statement.get(...params);
                    return (column ? row?.[column] : row) ?? null;
                },
                async all() { return { results: statement.all(...params) }; },
                async run() { return { meta: statement.run(...params) }; },
            };
        }
        return { bind, ...bind() };
    },
    async batch(statements) {
        const results = [];
        for (const statement of statements) results.push(await statement.all());
        return results;
    },
};

function fetchOperations(path, init) {
    return worker.fetch(new Request(`http://localhost/v1/ops${path}`, init), { DB: db });
}

before(async () => {
    const schema = await readFile(new URL("./migrations/0001_operations.sql", import.meta.url), "utf8");
    sqlite.exec(schema);
    for (const [name, token] of Object.entries(tokens)) {
        await db.prepare("INSERT INTO members(name, token_hash) VALUES (?, ?)")
            .bind(name, createHash("sha256").update(token).digest("hex")).run();
    }
});

after(() => sqlite.close());

async function request(member, path = "", method = "GET", body, status = 200) {
    const headers = member ? { Authorization: `Bearer ${tokens[member]}` } : {};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetchOperations(path, {
        method, headers, body: JSON.stringify(body),
    });
    const data = await response.json();
    assert.equal(response.status, status, JSON.stringify(data));
    assert.equal(response.headers.get("Cache-Control"), "no-store");
    return data;
}

test("team publication metadata is shared while editing requires ownership and the latest revision", async () => {
    await request(null, "", "GET", undefined, 401);
    const account = await request("Alice", "/accounts", "POST", { platform: "douyin", label: "Channel A" }, 201);
    assert.deepEqual(account, { id: account.id, platform: "douyin", label: "Channel A", owner: "Alice" });
    assert.deepEqual(await request("Alice", "/accounts", "POST", { platform: "douyin", label: "Channel A" }), account);
    const publication = { account_id: account.id, title: "First video" };
    await request("Bao", "/publications", "POST", publication, 403);
    const created = await request("Alice", "/publications", "POST", publication, 201);
    assert.equal(created.status, "planned");
    assert.equal(created.revision, 0);
    assert.equal(created.account_label, account.label);
    const path = `/publications/${created.id}`;
    const edit = { revision: 0, status: "prepared" };
    await request("Bao", path, "PATCH", edit, 403);
    const updates = await Promise.all([edit, { ...edit, status: "submitted" }].map(async (body) => {
        const response = await fetchOperations(path, {
            method: "PATCH",
            headers: { Authorization: `Bearer ${tokens.Alice}`, "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        return response.status;
    }));
    assert.deepEqual(updates.sort(), [200, 409]);
    const snapshot = await request("Bao");
    assert.deepEqual(snapshot.member, { name: "Bao" });
    assert.deepEqual(snapshot.accounts, [account]);
    assert.equal(snapshot.publications[0].revision, 1);
    const secondAccount = await request("Bao", "/accounts", "POST", { platform: "douyin", label: "Channel A" }, 201);
    assert.equal(secondAccount.owner, "Bao");
    assert.notEqual(secondAccount.id, account.id);
});

test("a single eligible runner can claim a job and completion is private and idempotent", async () => {
    const job = { id: "1".repeat(32), kind: "copy", summary: "A confidential brief" };
    const path = `/jobs/${job.id}`;
    assert.deepEqual(await request("Alice", "/jobs", "POST", job, 201), { id: job.id });
    assert.deepEqual(await request("Alice", "/jobs", "POST", job), { id: job.id });
    await request("Alice", "/jobs", "POST", { ...job, summary: "Different brief" }, 409);
    assert.deepEqual(await request("Alice", "/claim", "POST", { kinds: ["copy"] }), { job: null });
    assert.deepEqual(await request("Bao", "/claim", "POST", { kinds: ["storyboard"] }), { job: null });
    const claims = await Promise.all(["Bao", "Chen"].map(async (member) => ({
        member, ...await request(member, "/claim", "POST", { kinds: ["copy"] }),
    })));
    assert.equal(claims.filter(({ job }) => job).length, 1);
    const { member, job: claimed } = claims.find(({ job }) => job);
    assert.deepEqual(claimed, { ...job, lease: claimed.lease, expires_at: claimed.expires_at });
    assert.match(claimed.lease, /^[0-9a-f]{32}$/);
    assert.ok(Date.parse(claimed.expires_at) > Date.now());
    const finish = { lease: claimed.lease, result: "Result" };
    await request("Alice", `${path}/finish`, "POST", finish, 409);
    await request(member, path, "GET", undefined, 404);
    assert.deepEqual((await request(member)).jobs, []);
    assert.deepEqual(await request(member, `${path}/finish`, "POST", finish), { id: job.id });
    assert.deepEqual(await request(member, `${path}/finish`, "POST", finish), { id: job.id });
    await request(member, `${path}/finish`, "POST", { ...finish, result: "Other result" }, 409);
    await request("Alice", "/jobs", "POST", job);
    const completed = await request("Alice", path);
    assert.equal(completed.state, "completed");
    assert.equal(completed.runner, member);
    assert.equal(completed.result, finish.result);
    const snapshotJob = (await request("Alice")).jobs[0];
    assert.ok(!("summary" in snapshotJob) && !("result" in snapshotJob));
    assert.equal(await db.prepare("SELECT summary FROM jobs WHERE id = ?").bind(job.id).first("summary"), null);
});

test("an expired lease can be reclaimed twice, then becomes a bounded failure", async () => {
    const job = { id: "2".repeat(32), kind: "storyboard", summary: "Story idea" };
    const path = `/jobs/${job.id}`;
    await request("Alice", "/jobs", "POST", job, 201);
    const expire = () => db.prepare("UPDATE jobs SET lease_expires_at = ? WHERE id = ?")
        .bind("2020-01-01T00:00:00.000Z", job.id).run();
    const { job: first } = await request("Bao", "/claim", "POST", { kinds: ["storyboard"] });
    await expire();
    const retryable = await request("Alice", path);
    assert.equal(retryable.state, "queued");
    assert.equal(retryable.runner, undefined);
    const { job: second } = await request("Chen", "/claim", "POST", { kinds: ["storyboard"] });
    assert.notEqual(first.lease, second.lease);
    await request("Bao", `${path}/finish`, "POST", { lease: first.lease, result: "Late result" }, 409);
    await expire();
    await request("Bao", "/claim", "POST", { kinds: ["storyboard"] });
    await expire();
    assert.deepEqual(await request("Chen", "/claim", "POST", { kinds: ["storyboard"] }), { job: null });
    assert.equal((await request("Alice", path)).state, "failed");
    await worker.scheduled({ scheduledTime: Date.now() }, { DB: db });
    assert.equal(await db.prepare("SELECT summary FROM jobs WHERE id = ?").bind(job.id).first("summary"), null);
    await worker.scheduled({ scheduledTime: Date.now() + 8 * 86_400_000 }, { DB: db });
    await request("Alice", path, "GET", undefined, 404);
});
