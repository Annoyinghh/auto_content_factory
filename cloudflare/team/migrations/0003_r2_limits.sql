CREATE TABLE r2_usage (
    class TEXT NOT NULL CHECK (class IN ('A', 'B')),
    day INTEGER NOT NULL,
    operations INTEGER NOT NULL CHECK (operations > 0),
    PRIMARY KEY (class, day)
);
