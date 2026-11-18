const PART_BYTES = 8 * 1024 ** 2;
const MAX_VIDEO_BYTES = 2 * 1024 ** 3;
const MAX_JSON_BYTES = 64 * 1024;
const RESUME_WINDOW_MS = 6 * 24 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;
const R2_LIMITS = { R2_STORAGE_BYTES: 8_000_000_000, R2_CLASS_A_LIMIT: 800_000, R2_CLASS_B_LIMIT: 8_000_000 };
const REVIEW_LIMIT = 100;
const PLATFORMS = new Set(["bilibili", "douyin", "xiaohongshu"]);
const DECISIONS = new Set(["comment", "approved", "changes_requested"]);
const PATH = /^\/v1\/reviews(?:\/([0-9a-f]{32})(?:\/(video|feedback|complete|upload|parts\/([1-9][0-9]{0,2})))?)?$/;
const REVIEW_COLUMNS = "id, title, platform, caption, created_at, author, status, revision, bytes";
const PRIVATE_HEADERS = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };

class RequestError extends Error {
    constructor(status, message, code) {
        super(message);
        this.status = status;
        this.code = code;
    }
}

function json(value, status = 200, headers = {}) {
    return Response.json(value, { status, headers: { ...PRIVATE_HEADERS, ...headers } });
}

function fieldText(value, field, maximum, empty = false) {
    if (typeof value !== "string" || [...value].length > maximum || (!empty && !value.trim())
        || /[\u0000\ud800-\udfff]/u.test(value)) {
        throw new RequestError(400, `Invalid ${field}.`);
    }
    return value;
}

async function readBody(request, contentType, maximum, expected) {
    const type = request.headers.get("Content-Type")?.split(";", 1)[0].trim().toLowerCase();
    if (type !== contentType) throw new RequestError(415, `Content-Type must be ${contentType}.`);
    const rawLength = request.headers.get("Content-Length");
    if (!rawLength) throw new RequestError(411, "Content-Length is required.");
    if (!/^[0-9]{1,12}$/.test(rawLength)) throw new RequestError(400, "Invalid Content-Length.");
    const length = Number(rawLength);
    if (length > maximum) throw new RequestError(413, "Request exceeds the size limit.");
    if (expected !== undefined && length !== expected) throw new RequestError(400, "Invalid part length.");
    if (!request.body) throw new RequestError(400, "Request body is required.");

    const reader = request.body.getReader();
    const body = new Uint8Array(length);
    let offset = 0;
    try {
        for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            if (value.byteLength > length - offset) throw new RequestError(400, "Request body exceeds its length.");
            body.set(value, offset);
            offset += value.byteLength;
        }
    } catch (error) {
        await reader.cancel().catch(() => {});
        if (error instanceof RequestError) throw error;
        throw new RequestError(400, "Request body ended early.");
    } finally {
        reader.releaseLock();
    }
    if (offset !== length) throw new RequestError(400, "Request body ended early.");
    return body;
}

async function jsonBody(request, fields, optional = []) {
    const body = await readBody(request, "application/json", MAX_JSON_BYTES);
    let value;
    try {
        value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body));
    } catch {
        throw new RequestError(400, "Invalid JSON body.");
    }
    if (!value || typeof value !== "object" || Array.isArray(value)
        || !fields.every((field) => Object.hasOwn(value, field))
        || Object.keys(value).some((key) => !fields.includes(key) && !optional.includes(key))) {
        throw new RequestError(400, "Unexpected JSON fields.");
    }
    return value;
}

async function sha256(bytes) {
    const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
    return Array.from(hash, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function authenticate(db, request) {
    const token = /^Bearer ([A-Za-z0-9_-]{43})$/i.exec(request.headers.get("Authorization") ?? "")?.[1];
    if (token) {
        const hash = await sha256(new TextEncoder().encode(token));
        const member = await db.prepare("SELECT name FROM members WHERE token_hash = ?").bind(hash).first();
        if (member) return member.name;
    }
    throw new RequestError(401, "A valid workspace token is required.");
}

function videoKey(id) {
    return `reviews/${id}.mp4`;
}

function r2Limit(env, key) {
    const raw = env[key] ?? R2_LIMITS[key];
    const limit = Number(raw);
    if (!/^[0-9]+$/.test(String(raw)) || !Number.isSafeInteger(limit) || limit > R2_LIMITS[key]) {
        throw new RequestError(503, `Invalid ${key} configuration.`);
    }
    return limit;
}

async function chargeR2(env, operationClass) {
    const day = Math.floor(Date.now() / DAY_MS);
    const limit = r2Limit(env, `R2_CLASS_${operationClass}_LIMIT`);
    // 32 UTC day buckets cover any 31-day billing period, including partial boundary days.
    const result = await env.DB.prepare(`INSERT INTO r2_usage(class, day, operations)
        SELECT ?, ?, 1 WHERE (SELECT COALESCE(SUM(operations), 0) FROM r2_usage WHERE class = ? AND day >= ?) < ?
        ON CONFLICT(class, day) DO UPDATE SET operations = operations + 1`)
        .bind(operationClass, day, operationClass, day - 31, limit).run();
    if (!result.meta.changes) {
        throw new RequestError(429, "The team reached its R2 operation limit.",
            operationClass === "A" ? "r2_write_limit" : "r2_read_limit");
    }
}

async function uploadPlan(db, id, author, metadata) {
    const slot = await db.prepare(`SELECT title, platform, caption, bytes, author, ready, created_at, previous_id, upload_id
                                   FROM reviews WHERE id = ?`).bind(id).first();
    if (!slot) return null;
    if (slot.author !== author) throw new RequestError(403, "Only the submitter can upload this video.");
    for (const field of ["title", "platform", "caption", "bytes", "previous_id"]) {
        if (slot[field] !== metadata[field]) throw new RequestError(409, "Review metadata changed.");
    }
    if (slot.ready) return { initialized: true, ready: true, uploaded_parts: [] };
    if (Date.now() - Date.parse(slot.created_at) >= RESUME_WINDOW_MS) {
        throw new RequestError(410, "This unfinished upload has expired.");
    }
    if (!slot.upload_id) return { initialized: false, ready: false, uploaded_parts: [] };
    const { results } = await db.prepare(`SELECT part_number, digest FROM review_parts
        WHERE review_id = ? AND etag IS NOT NULL ORDER BY part_number`).bind(id).all();
    return { initialized: true, ready: false, uploaded_parts: results };
}

async function createReview(env, request, author, requestedId) {
    const data = await jsonBody(request, ["title", "platform", "caption", "bytes"], ["previous_id"]);
    const title = fieldText(data.title, "title", 500).trim();
    const platform = fieldText(data.platform, "platform", 30);
    const caption = fieldText(data.caption, "caption", 12000, true);
    if (!PLATFORMS.has(platform)) throw new RequestError(400, "Unknown platform.");
    if (!Number.isInteger(data.bytes) || data.bytes < 16 || data.bytes > MAX_VIDEO_BYTES) {
        throw new RequestError(400, "Invalid MP4 size.");
    }
    const previousId = data.previous_id ?? null;
    if (previousId !== null) {
        if (typeof previousId !== "string" || !/^[0-9a-f]{32}$/.test(previousId)) {
            throw new RequestError(400, "Invalid previous review.");
        }
        const previous = await env.DB.prepare("SELECT platform FROM reviews WHERE id = ? AND ready = 1")
            .bind(previousId).first();
        if (!previous) throw new RequestError(404, "Previous review not found.");
        if (previous.platform !== platform) throw new RequestError(400, "A new version must use the same platform.");
    }
    const id = requestedId ?? crypto.randomUUID().replaceAll("-", "");
    const plan = { id, upload_mode: "multipart", part_bytes: PART_BYTES };
    const metadata = { title, platform, caption, bytes: data.bytes, previous_id: previousId };
    let existing = requestedId ? await uploadPlan(env.DB, id, author, metadata) : null;
    if (!existing) {
        // Reserve the whole file before any R2 call, including space for unfinished uploads.
        const reserve = await env.DB.prepare(`INSERT INTO reviews(id, title, platform, caption, created_at, author, bytes, upload_id, previous_id)
            SELECT ?, ?, ?, ?, ?, ?, ?, '', ? WHERE (SELECT COALESCE(SUM(bytes), 0) FROM reviews) + ? <= ?
            ON CONFLICT(id) DO NOTHING`)
            .bind(id, title, platform, caption, new Date().toISOString(), author, data.bytes, previousId,
                data.bytes, r2Limit(env, "R2_STORAGE_BYTES")).run();
        if (!reserve.meta.changes) {
            existing = await uploadPlan(env.DB, id, author, metadata);
            if (!existing) throw new RequestError(429, "The team reached its R2 storage limit.", "r2_storage_limit");
            if (!requestedId) throw new RequestError(409, "Review identifier already exists.");
        }
    }
    if (existing?.initialized) return json({ ...plan, ...existing });

    await chargeR2(env, "A");
    const upload = await env.VIDEOS.createMultipartUpload(videoKey(id), {
        storageClass: "Standard", httpMetadata: { contentType: "video/mp4" }, customMetadata: { reviewId: id },
    });
    // If the write result is lost, a retry can look up the slot. Unclaimed upload IDs hold no parts.
    const saved = await env.DB.prepare("UPDATE reviews SET upload_id = ? WHERE id = ? AND upload_id = ''")
        .bind(upload.uploadId, id).run();
    if (!saved.meta.changes) {
        await upload.abort().catch(() => {});
        return json({ ...plan, ...await uploadPlan(env.DB, id, author, metadata) });
    }
    return json(requestedId ? { ...plan, ready: false, uploaded_parts: [] } : plan, 201);
}

async function uploadSlot(db, id, author) {
    const slot = await db.prepare("SELECT author, bytes, ready, upload_id FROM reviews WHERE id = ?").bind(id).first();
    if (!slot) throw new RequestError(404, "Review not found.");
    if (slot.author !== author) throw new RequestError(403, "Only the submitter can upload this video.");
    if (!slot.upload_id) throw new RequestError(409, "The upload has not started.");
    return slot;
}

function isMp4(body, totalBytes) {
    if (body.byteLength < 16) return false;
    const header = new DataView(body.buffer, body.byteOffset, body.byteLength);
    const size = header.getUint32(0);
    return header.getUint32(4) === 0x66747970 && size >= 16 && size <= totalBytes; // ftyp
}

async function uploadPart(env, request, author, id, number) {
    const slot = await uploadSlot(env.DB, id, author);
    if (slot.ready) throw new RequestError(409, "This review already has an immutable video.");
    const length = Math.min(PART_BYTES, slot.bytes - (number - 1) * PART_BYTES);
    if (length <= 0) throw new RequestError(400, "Invalid part number.");
    const body = await readBody(request, "video/mp4", PART_BYTES, length);
    if (number === 1 && !isMp4(body, slot.bytes)) throw new RequestError(400, "Invalid MP4 header.");
    const digest = await sha256(body);

    // Concurrent retries can write only identical bytes, which produce the same R2 part ETag.
    const [, reservation] = await env.DB.batch([
        env.DB.prepare(`INSERT INTO review_parts(review_id, part_number, digest)
                        SELECT id, ?, ? FROM reviews WHERE id = ? AND ready = 0
                        ON CONFLICT(review_id, part_number) DO NOTHING`).bind(number, digest, id),
        env.DB.prepare(`SELECT r.ready, p.digest, p.etag FROM reviews r LEFT JOIN review_parts p
                        ON p.review_id = r.id AND p.part_number = ? WHERE r.id = ?`).bind(number, id),
    ]);
    const part = reservation.results[0];
    if (part.ready) throw new RequestError(409, "This review already has an immutable video.");
    if (part.digest !== digest) throw new RequestError(409, "This part was reserved with different bytes.");
    if (!part.etag) {
        await chargeR2(env, "A");
        const upload = env.VIDEOS.resumeMultipartUpload(videoKey(id), slot.upload_id);
        const uploaded = await upload.uploadPart(number, body);
        await env.DB.prepare(`UPDATE review_parts SET etag = ?
                              WHERE review_id = ? AND part_number = ? AND etag IS NULL`)
            .bind(uploaded.etag, id, number).run();
    }
    return json({ id });
}

function matchesReview(object, id, bytes) {
    return object && object.size === bytes && object.customMetadata?.reviewId === id && Boolean(object.etag);
}

async function completeReview(env, request, author, id) {
    await jsonBody(request, []);
    const slot = await uploadSlot(env.DB, id, author);
    if (slot.ready) return json({ id });
    const key = videoKey(id);
    await chargeR2(env, "B");
    let object = await env.VIDEOS.head(key);
    if (!object) {
        const { results: parts } = await env.DB.prepare(`SELECT part_number AS partNumber, etag FROM review_parts
            WHERE review_id = ? AND etag IS NOT NULL ORDER BY part_number`).bind(id).all();
        if (parts.length !== Math.ceil(slot.bytes / PART_BYTES)) {
            throw new RequestError(409, "Upload all parts before completing the review.");
        }
        await chargeR2(env, "A");
        try {
            object = await env.VIDEOS.resumeMultipartUpload(key, slot.upload_id).complete(parts);
        } catch (error) {
            await chargeR2(env, "B");
            object = await env.VIDEOS.head(key);
            if (!object) throw error;
        }
    }
    // The object can already exist after a lost response or a failure updating D1.
    if (!matchesReview(object, id, slot.bytes)) throw new RequestError(409, "The completed video does not match this review.");
    await env.DB.prepare("UPDATE reviews SET ready = 1, video_etag = ? WHERE id = ? AND ready = 0")
        .bind(object.etag, id).run();
    return json({ id });
}

function detailQueries(db, id) {
    return [
        db.prepare(`SELECT ${REVIEW_COLUMNS} FROM reviews WHERE id = ? AND ready = 1`).bind(id),
        db.prepare(`SELECT author, created_at, decision, text FROM feedback
                    WHERE review_id = ? ORDER BY revision`).bind(id),
        db.prepare(`SELECT id, title, author, status, created_at, 'previous' AS relation
            FROM reviews WHERE ready = 1 AND id = (SELECT previous_id FROM reviews WHERE id = ?)
            UNION ALL SELECT id, title, author, status, created_at, 'next' AS relation
            FROM reviews WHERE ready = 1 AND previous_id = ?
            ORDER BY relation DESC, created_at DESC, id DESC`).bind(id, id),
    ];
}

function reviewDetail(reviews, feedback, versions) {
    const review = reviews.results[0];
    if (!review) throw new RequestError(404, "Review not found.");
    return { ...review, feedback: feedback.results, versions: versions.results };
}

async function sendFeedback(db, request, author, id) {
    const data = await jsonBody(request, ["text", "decision", "revision"]);
    const decision = fieldText(data.decision, "decision", 30);
    if (!DECISIONS.has(decision)) throw new RequestError(400, "Unknown decision.");
    const text = fieldText(data.text, "feedback", 3000, decision !== "comment").trim();
    if (!Number.isSafeInteger(data.revision) || data.revision < 0 || data.revision === Number.MAX_SAFE_INTEGER) {
        throw new RequestError(400, "Invalid review revision.");
    }
    // The trigger advances the revision in the same transaction as the conditional ledger insert.
    const [insert, ...detailResults] = await db.batch([
        db.prepare(`INSERT INTO feedback(review_id, revision, author, created_at, decision, text)
                    SELECT id, revision + 1, ?, ?, ?, ? FROM reviews WHERE id = ? AND revision = ? AND ready = 1`)
            .bind(author, new Date().toISOString(), decision, text, id, data.revision),
        ...detailQueries(db, id),
    ]);
    const detail = reviewDetail(...detailResults);
    if (!insert.meta.changes) throw new RequestError(409, "Review changed. Reload it before sending feedback.");
    return json(detail);
}

async function download(env, id) {
    const review = await env.DB.prepare("SELECT bytes, video_etag FROM reviews WHERE id = ? AND ready = 1").bind(id).first();
    if (!review) throw new RequestError(404, "Review not found.");
    await chargeR2(env, "B");
    const object = await env.VIDEOS.get(videoKey(id));
    if (!matchesReview(object, id, review.bytes) || object.etag !== review.video_etag) {
        if (object?.body) await object.body.cancel();
        throw new RequestError(404, "Video is unavailable.");
    }
    return new Response(object.body, { headers: {
        ...PRIVATE_HEADERS, "Content-Type": "video/mp4", "Content-Length": String(review.bytes),
    } });
}

async function listReviews(db, url) {
    const status = url.searchParams.get("status") ?? "";
    const before = url.searchParams.get("before") ?? "";
    const rawLimit = url.searchParams.get("limit") ?? String(REVIEW_LIMIT);
    const search = fieldText(url.searchParams.get("q") ?? "", "review search", 120, true).trim();
    if (status && !["pending", "approved", "changes_requested"].includes(status)) {
        throw new RequestError(400, "Invalid review status.");
    }
    if (before && !/^[0-9a-f]{32}$/.test(before)) throw new RequestError(400, "Invalid review cursor.");
    const limit = Number(rawLimit);
    if (!/^[1-9][0-9]{0,2}$/.test(rawLimit) || limit > REVIEW_LIMIT) {
        throw new RequestError(400, "Invalid review page size.");
    }
    const where = ["ready = 1"];
    const params = [];
    if (status) {
        where.push("status = ?");
        params.push(status);
    }
    if (search) {
        where.push("(instr(lower(title), lower(?)) > 0 OR instr(lower(author), lower(?)) > 0)");
        params.push(search, search);
    }
    if (before) {
        where.push("(created_at, id) < (SELECT created_at, id FROM reviews WHERE id = ? AND ready = 1)");
        params.push(before);
    }
    const { results } = await db.prepare(`SELECT ${REVIEW_COLUMNS} FROM reviews WHERE ${where.join(" AND ")}
        ORDER BY created_at DESC, id DESC LIMIT ?`).bind(...params, limit + 1).all();
    const page = { items: results.slice(0, limit) };
    if (url.search) page.next_cursor = results.length > limit ? results[limit - 1].id : null;
    return json(page);
}

async function route(request, env) {
    const author = await authenticate(env.DB, request);
    const url = new URL(request.url);
    const match = PATH.exec(url.pathname);
    if (!match) throw new RequestError(404, "Endpoint not found.");
    const [, id, resource, partNumber] = match;
    const method = request.method;
    if (!id && method === "GET") return listReviews(env.DB, url);
    if (!id && method === "POST") return createReview(env, request, author);
    if (resource === "upload" && method === "PUT") return createReview(env, request, author, id);
    if (!resource && method === "GET") return json(reviewDetail(...await env.DB.batch(detailQueries(env.DB, id))));
    if (resource === "video" && method === "GET") return download(env, id);
    if (resource === "feedback" && method === "POST") return sendFeedback(env.DB, request, author, id);
    if (partNumber && method === "PUT") return uploadPart(env, request, author, id, Number(partNumber));
    if (resource === "complete" && method === "POST") return completeReview(env, request, author, id);
    throw new RequestError(404, "Endpoint not found.");
}

export default {
    async fetch(request, env) {
        try {
            return await route(request, env);
        } catch (error) {
            if (error instanceof RequestError) {
                const headers = error.status === 401 ? { "WWW-Authenticate": "Bearer" } : {};
                return json({ error: error.message, code: error.code }, error.status, headers);
            }
            console.error("Team workspace storage request failed.");
            return json({ error: "Workspace storage is unavailable. Retry shortly." }, 503);
        }
    },
};
