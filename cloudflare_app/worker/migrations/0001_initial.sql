PRAGMA foreign_keys = ON;

CREATE TABLE upload_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    file_hash TEXT NOT NULL UNIQUE,
    uploaded_at TEXT NOT NULL,
    imported_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'uploading' CHECK (status IN ('uploading', 'ready'))
);

CREATE TABLE questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES upload_batches(id),
    source_id TEXT NOT NULL,
    instruction TEXT NOT NULL,
    input_json TEXT NOT NULL,
    output TEXT NOT NULL,
    imported_at TEXT NOT NULL
);

CREATE TABLE assignments (
    question_id INTEGER PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
    reviewer TEXT NOT NULL,
    session_id TEXT NOT NULL,
    assigned_at TEXT NOT NULL,
    last_activity_at TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL
);

CREATE TABLE reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL UNIQUE REFERENCES questions(id),
    decision TEXT NOT NULL CHECK (decision IN ('Pass', 'Fail')),
    notes TEXT NOT NULL DEFAULT '',
    reviewer TEXT NOT NULL,
    reviewed_at TEXT NOT NULL
);

CREATE TABLE audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    question_id INTEGER REFERENCES questions(id),
    review_id INTEGER,
    actor TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX idx_batches_status ON upload_batches(status);
CREATE INDEX idx_questions_batch ON questions(batch_id);
CREATE INDEX idx_assignments_session ON assignments(session_id, reviewer);
CREATE INDEX idx_assignments_expiry ON assignments(lease_expires_at);
CREATE INDEX idx_reviews_date ON reviews(reviewed_at);
CREATE INDEX idx_reviews_reviewer ON reviews(reviewer);
