ALTER TABLE reviews ADD COLUMN previous_id TEXT REFERENCES reviews(id);
CREATE INDEX review_followups ON reviews(previous_id, ready, created_at DESC);
