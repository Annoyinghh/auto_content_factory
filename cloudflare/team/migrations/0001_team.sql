CREATE TABLE members (
    name TEXT PRIMARY KEY NOT NULL,
    token_hash TEXT UNIQUE
);

CREATE TABLE reviews (
    id TEXT PRIMARY KEY NOT NULL,
    title TEXT NOT NULL,
    platform TEXT NOT NULL,
    caption TEXT NOT NULL,
    created_at TEXT NOT NULL,
    author TEXT NOT NULL REFERENCES members(name),
    status TEXT NOT NULL DEFAULT 'pending',
    revision INTEGER NOT NULL DEFAULT 0,
    bytes INTEGER NOT NULL,
    upload_id TEXT NOT NULL,
    ready INTEGER NOT NULL DEFAULT 0,
    video_etag TEXT,
    CHECK ((ready = 0 AND video_etag IS NULL) OR (ready = 1 AND video_etag IS NOT NULL))
);

CREATE INDEX ready_reviews ON reviews(ready, created_at DESC);

CREATE TABLE review_parts (
    review_id TEXT NOT NULL REFERENCES reviews(id),
    part_number INTEGER NOT NULL,
    digest TEXT NOT NULL,
    etag TEXT,
    PRIMARY KEY (review_id, part_number)
);

CREATE TABLE feedback (
    review_id TEXT NOT NULL REFERENCES reviews(id),
    revision INTEGER NOT NULL,
    author TEXT NOT NULL REFERENCES members(name),
    created_at TEXT NOT NULL,
    decision TEXT NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (review_id, revision)
);

CREATE TRIGGER apply_feedback AFTER INSERT ON feedback
BEGIN
    UPDATE reviews
    SET revision = NEW.revision,
        status = CASE WHEN NEW.decision = 'comment' THEN status ELSE NEW.decision END
    WHERE id = NEW.review_id;
END;
