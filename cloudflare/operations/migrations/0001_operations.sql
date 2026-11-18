CREATE TABLE members (
    name TEXT PRIMARY KEY NOT NULL,
    token_hash TEXT UNIQUE
);

CREATE TABLE accounts (
    id TEXT PRIMARY KEY NOT NULL,
    platform TEXT NOT NULL CHECK (platform IN ('douyin', 'bilibili', 'xiaohongshu')),
    label TEXT NOT NULL,
    owner TEXT NOT NULL REFERENCES members(name),
    UNIQUE (owner, platform, label)
);

CREATE TABLE publications (
    id TEXT PRIMARY KEY NOT NULL,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned', 'prepared', 'submitted', 'published', 'failed')),
    revision INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE INDEX publications_recent ON publications(updated_at DESC, id);

CREATE TABLE jobs (
    id TEXT PRIMARY KEY NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('copy', 'storyboard')),
    requester TEXT NOT NULL REFERENCES members(name),
    state TEXT NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'running', 'completed', 'failed')),
    summary TEXT,
    summary_hash TEXT NOT NULL,
    runner TEXT REFERENCES members(name),
    lease_hash TEXT,
    lease_expires_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    result TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX jobs_recent ON jobs(requester, created_at DESC, id);
CREATE INDEX jobs_retention ON jobs(created_at);
CREATE INDEX jobs_pending ON jobs(created_at, id) WHERE state IN ('queued', 'running');
