const ROOT = "/v1/ops";
const ID = /^[0-9a-f]{32}$/;
const PLATFORMS = new Set(["douyin", "bilibili", "xiaohongshu"]);
const STATUSES = new Set(["planned", "prepared", "submitted", "published", "failed"]);
const KINDS = new Set(["copy", "storyboard"]);
const DAY_MS = 86_400_000;
const RETENTION_MS = 7 * DAY_MS;
const MAX_ATTEMPTS = 3;
const MAX_JSON_BYTES = 300_000;
const MAX_ACCOUNTS = 100;
const MAX_PUBLICATIONS = 100;
const MAX_JOBS = 100;
const MAX_PENDING = 100;
const MAX_MEMBER_PENDING = 8;
const MAX_RETAINED = 1000;
const EXPIRED_LEASE = "The runner did not finish before its final lease expired.";
const SQL_NOW = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')";
const JOB_COLUMNS = "id, kind, state, created_at, updated_at, runner, error, attempts, lease_expires_at";
const PUBLICATION_COLUMNS = "p.id, p.account_id, a.platform, a.label AS account_label, a.owner, p.title, p.status, p.revision, p.updated_at";

class RequestError extends Error {
    constructor(status, message) {
        super(message);
        this.status = status;
    }
}

function json(value, status = 200) {
    return Response.json(value, { status, headers: {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    } });
}

function fieldText(value, field, maximum) {
    if (typeof value !== "string" || value.length > maximum * 2 || [...value].length > maximum
        || !value.trim() || /[\u0000\ud800-\udfff]/u.test(value)) {
        throw new RequestError(400, `Invalid ${field}.`);
    }
    return value;
}

function fieldId(value, field) {
    if (typeof value !== "string" || !ID.test(value)) throw new RequestError(400, `Invalid ${field}.`);
    return value;
}

async function jsonBody(request, required, optional = []) {
    const contentType = request.headers.get("Content-Type")?.split(";", 1)[0].trim().toLowerCase();
    if (contentType !== "application/json") throw new RequestError(415, "Content-Type must be application/json.");
    if (Number(request.headers.get("Content-Length")) > MAX_JSON_BYTES) {
        throw new RequestError(413, "Request exceeds the size limit.");
    }
    if (!request.body) throw new RequestError(400, "Request body is required.");
    let value;
    try {
        const decoder = new TextDecoder("utf-8", { fatal: true });
        let size = 0;
        let text = "";
        for await (const chunk of request.body) {
            size += chunk.byteLength;
            if (size > MAX_JSON_BYTES) throw new RequestError(413, "Request exceeds the size limit.");
            text += decoder.decode(chunk, { stream: true });
        }
        value = JSON.parse(text + decoder.decode());
    } catch (error) {
        if (error instanceof RequestError) throw error;
        throw new RequestError(400, "Invalid JSON body.");
    }
    if (!value || typeof value !== "object" || Array.isArray(value)
        || !required.every((field) => Object.hasOwn(value, field))
        || Object.keys(value).some((field) => !required.includes(field) && !optional.includes(field))) {
        throw new RequestError(400, "Unexpected JSON fields.");
    }
    return value;
}

function newId() {
    return crypto.randomUUID().replaceAll("-", "");
}

async function digest(text) {
    const bytes = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function authenticate(request, db) {
    const token = /^Bearer ([A-Za-z0-9_-]{43})$/i.exec(request.headers.get("Authorization") ?? "")?.[1];
    if (token) {
        const member = await db.prepare("SELECT name FROM members WHERE token_hash = ?").bind(await digest(token)).first();
        if (member) return member.name;
    }
    throw new RequestError(401, "A valid workspace token is required.");
}

function retentionCutoff(time) {
    return new Date(time - RETENTION_MS).toISOString();
}

function jobView(row, now) {
    const { attempts, lease_expires_at, runner, error, result, ...view } = row;
    if (runner) view.runner = runner;
    if (error) view.error = error;
    if (result) view.result = result;
    if (view.state === "running" && lease_expires_at <= now) {
        view.updated_at = lease_expires_at;
        if (attempts >= MAX_ATTEMPTS) {
            view.state = "failed";
            view.error = EXPIRED_LEASE;
        } else {
            view.state = "queued";
            delete view.runner;
        }
    }
    return view;
}

async function snapshot(db, name) {
    const now = Date.now();
    const [accounts, publications, jobs] = await db.batch([
        db.prepare("SELECT id, platform, label, owner FROM accounts ORDER BY platform, label, id LIMIT ?")
            .bind(MAX_ACCOUNTS),
        db.prepare(`SELECT ${PUBLICATION_COLUMNS}
            FROM publications p JOIN accounts a ON a.id = p.account_id ORDER BY p.updated_at DESC, p.id LIMIT ?`)
            .bind(MAX_PUBLICATIONS),
        db.prepare(`SELECT ${JOB_COLUMNS} FROM jobs WHERE requester = ? AND created_at > ?
            ORDER BY created_at DESC, id LIMIT ?`).bind(name, retentionCutoff(now), MAX_JOBS),
    ]);
    const timestamp = new Date(now).toISOString();
    return json({
        member: { name },
        accounts: accounts.results,
        publications: publications.results,
        jobs: jobs.results.map((row) => jobView(row, timestamp)),
    });
}

async function createAccount(db, request, owner) {
    const data = await jsonBody(request, ["platform", "label"]);
    const { platform } = data;
    if (!PLATFORMS.has(platform)) throw new RequestError(400, "Unknown platform.");
    const label = fieldText(data.label, "label", 120).trim();
    const row = await db.prepare(`INSERT INTO accounts(id, platform, label, owner)
        SELECT ?, ?, ?, ? WHERE (SELECT COUNT(*) FROM accounts) < ?
        ON CONFLICT(owner, platform, label) DO NOTHING RETURNING id, platform, label, owner`)
        .bind(newId(), platform, label, owner, MAX_ACCOUNTS).first();
    if (row) return json(row, 201);
    const existing = await db.prepare("SELECT id, platform, label, owner FROM accounts WHERE owner = ? AND platform = ? AND label = ?")
        .bind(owner, platform, label).first();
    if (existing) return json(existing);
    throw new RequestError(429, "The workspace reached its account limit.");
}

function publicationView(row, account) {
    return { ...row, platform: account.platform, account_label: account.label, owner: account.owner };
}

async function createPublication(db, request, name) {
    const data = await jsonBody(request, ["account_id", "title"]);
    const accountId = fieldId(data.account_id, "account_id");
    const title = fieldText(data.title, "title", 300).trim();
    const account = await db.prepare("SELECT platform, label, owner FROM accounts WHERE id = ?").bind(accountId).first();
    if (!account) throw new RequestError(404, "Account not found.");
    if (account.owner !== name) throw new RequestError(403, "Only the account owner can add a publication.");
    const row = await db.prepare(`INSERT INTO publications(id, account_id, title, updated_at)
        VALUES (?, ?, ?, ${SQL_NOW}) RETURNING id, account_id, title, status, revision, updated_at`)
        .bind(newId(), accountId, title).first();
    return json(publicationView(row, account), 201);
}

async function updatePublication(db, request, name, id) {
    const { revision, status } = await jsonBody(request, ["revision", "status"]);
    if (!Number.isSafeInteger(revision) || revision < 0) throw new RequestError(400, "Invalid revision.");
    if (!STATUSES.has(status)) throw new RequestError(400, "Unknown publication status.");
    const account = await db.prepare(`SELECT a.platform, a.label, a.owner FROM publications p
        JOIN accounts a ON a.id = p.account_id WHERE p.id = ?`).bind(id).first();
    if (!account) throw new RequestError(404, "Publication not found.");
    if (account.owner !== name) throw new RequestError(403, "Only the account owner can update this publication.");
    const row = await db.prepare(`UPDATE publications SET status = ?, revision = revision + 1, updated_at = ${SQL_NOW}
        WHERE id = ? AND revision = ? RETURNING id, account_id, title, status, revision, updated_at`)
        .bind(status, id, revision).first();
    if (!row) throw new RequestError(409, "The publication changed. Refresh and try again.");
    return json(publicationView(row, account));
}

async function submitJob(db, request, requester) {
    const data = await jsonBody(request, ["id", "kind", "summary"]);
    const id = fieldId(data.id, "id");
    const { kind } = data;
    if (!KINDS.has(kind)) throw new RequestError(400, "Unknown job kind.");
    const summary = fieldText(data.summary, "summary", 6000);
    const hash = await digest(summary);
    const now = Date.now();
    const cutoff = retentionCutoff(now);
    // Keep queue caps and inserts in the same statement so concurrent submissions cannot overfill it.
    const created = await db.prepare(`WITH pending AS (
        SELECT requester FROM jobs WHERE state IN ('queued', 'running') AND created_at > ?
        AND (state = 'queued' OR attempts < ? OR lease_expires_at > ${SQL_NOW})
    )
    INSERT INTO jobs(id, kind, requester, summary, summary_hash, created_at, updated_at)
    SELECT ?, ?, ?, ?, ?, ${SQL_NOW}, ${SQL_NOW}
    WHERE (SELECT COUNT(*) FROM pending) < ?
      AND (SELECT COUNT(*) FROM pending WHERE requester = ?) < ?
      AND (SELECT COUNT(*) FROM jobs) < ?
    ON CONFLICT(id) DO NOTHING RETURNING id`)
        .bind(cutoff, MAX_ATTEMPTS, id, kind, requester, summary, hash,
            MAX_PENDING, requester, MAX_MEMBER_PENDING, MAX_RETAINED).first();
    if (created) return json(created, 201);
    const existing = await db.prepare("SELECT requester, kind, summary_hash, created_at FROM jobs WHERE id = ?")
        .bind(id).first();
    if (existing?.created_at > cutoff && existing.requester === requester
        && existing.kind === kind && existing.summary_hash === hash) {
        return json({ id });
    }
    if (existing) throw new RequestError(409, "Job identifier is already in use.");
    throw new RequestError(429, "The workspace or member reached its job limit.");
}

async function jobDetail(db, name, id) {
    const now = Date.now();
    const row = await db.prepare(`SELECT ${JOB_COLUMNS}, result FROM jobs
        WHERE id = ? AND requester = ? AND created_at > ?`).bind(id, name, retentionCutoff(now)).first();
    if (!row) throw new RequestError(404, "Job not found.");
    return json(jobView(row, new Date(now).toISOString()));
}

async function claimJob(db, request, runner) {
    const { kinds } = await jsonBody(request, ["kinds"]);
    if (!Array.isArray(kinds) || kinds.length < 1 || kinds.length > KINDS.size || !kinds.every((kind) => KINDS.has(kind))) {
        throw new RequestError(400, "Invalid job kinds.");
    }
    const lease = newId();
    // SQLite evaluates and updates the next eligible row atomically.
    const job = await db.prepare(`UPDATE jobs SET state = 'running', runner = ?, lease_hash = ?,
        lease_expires_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '+15 minutes'),
        attempts = attempts + 1, updated_at = ${SQL_NOW}
        WHERE id = (
            SELECT id FROM jobs WHERE requester <> ? AND kind IN (?, ?) AND created_at > ?
            AND state IN ('queued', 'running')
            AND (state = 'queued' OR (lease_expires_at <= ${SQL_NOW} AND attempts < ?))
            ORDER BY created_at, id LIMIT 1
        )
        RETURNING id, kind, summary, lease_expires_at AS expires_at`)
        .bind(runner, await digest(lease), runner, kinds[0], kinds[1] ?? null,
            retentionCutoff(Date.now()), MAX_ATTEMPTS).first();
    return json({ job: job ? { ...job, lease } : null });
}

async function finishJob(db, request, runner, id) {
    const data = await jsonBody(request, ["lease"], ["result", "error"]);
    const lease = fieldId(data.lease, "lease");
    const success = Object.hasOwn(data, "result");
    if (success === Object.hasOwn(data, "error")) throw new RequestError(400, "Provide one result or error.");
    const result = success ? fieldText(data.result, "result", 24000) : null;
    const error = success ? null : fieldText(data.error, "error", 1000);
    const state = success ? "completed" : "failed";
    const hash = await digest(lease);
    const cutoff = retentionCutoff(Date.now());
    const done = await db.prepare(`UPDATE jobs SET state = ?, result = ?, error = ?,
        summary = NULL, lease_expires_at = NULL, updated_at = ${SQL_NOW}
        WHERE id = ? AND runner = ? AND lease_hash = ? AND state = 'running'
        AND created_at > ? AND lease_expires_at > ${SQL_NOW} RETURNING id`)
        .bind(state, result, error, id, runner, hash, cutoff).first();
    if (done) return json(done);
    // A lost response is safe to retry with the same lease and exact outcome.
    const duplicate = await db.prepare(`SELECT id FROM jobs WHERE id = ? AND runner = ? AND lease_hash = ?
        AND created_at > ? AND state = ? AND result IS ? AND error IS ?`)
        .bind(id, runner, hash, cutoff, state, result, error).first();
    if (duplicate) return json(duplicate);
    throw new RequestError(409, "The job lease is invalid or no longer current.");
}

export default {
    async fetch(request, { DB: db }) {
        try {
            const path = new URL(request.url).pathname;
            if (path !== ROOT && !path.startsWith(ROOT + "/")) throw new RequestError(404, "Route not found.");
            const name = await authenticate(request, db);
            const route = path.slice(ROOT.length);
            const { method } = request;
            if (method === "GET" && route === "") return await snapshot(db, name);
            if (method === "POST" && route === "/accounts") return await createAccount(db, request, name);
            if (method === "POST" && route === "/publications") return await createPublication(db, request, name);
            if (method === "POST" && route === "/jobs") return await submitJob(db, request, name);
            if (method === "POST" && route === "/claim") return await claimJob(db, request, name);
            const publication = /^\/publications\/([0-9a-f]{32})$/.exec(route);
            if (method === "PATCH" && publication) return await updatePublication(db, request, name, publication[1]);
            const job = /^\/jobs\/([0-9a-f]{32})(\/finish)?$/.exec(route);
            if (method === "GET" && job && !job[2]) return await jobDetail(db, name, job[1]);
            if (method === "POST" && job?.[2]) return await finishJob(db, request, name, job[1]);
            throw new RequestError(404, "Route not found.");
        } catch (error) {
            if (error instanceof RequestError) return json({ error: error.message }, error.status);
            return json({ error: "The workspace service encountered an error." }, 500);
        }
    },

    async scheduled({ scheduledTime }, { DB: db }) {
        await db.batch([
            db.prepare("DELETE FROM jobs WHERE created_at <= ?").bind(retentionCutoff(scheduledTime)),
            db.prepare(`UPDATE jobs SET state = 'failed', error = ?, summary = NULL, lease_hash = NULL,
                updated_at = lease_expires_at, lease_expires_at = NULL
                WHERE state = 'running' AND attempts >= ? AND lease_expires_at <= ?`)
                .bind(EXPIRED_LEASE, MAX_ATTEMPTS, new Date(scheduledTime).toISOString()),
        ]);
    },
};
