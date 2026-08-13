"""Persistent domain module for the question review platform."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Union


class ReviewStoreError(Exception):
    """Base error exposed by the ReviewStore interface."""


class InvalidUploadError(ReviewStoreError):
    pass


class DuplicateUploadError(ReviewStoreError):
    pass


class ReviewConflictError(ReviewStoreError):
    pass


@dataclass(frozen=True)
class ImportErrorDetail:
    row: int
    message: str


@dataclass(frozen=True)
class ImportResult:
    batch_id: int
    imported_count: int
    skipped_count: int
    errors: Sequence[ImportErrorDetail]


@dataclass(frozen=True)
class UploadPreview:
    valid_count: int
    skipped_count: int
    errors: Sequence[ImportErrorDetail]


@dataclass(frozen=True)
class Question:
    id: int
    source_id: str
    instruction: str
    question_parts: Sequence[str]
    output: str
    batch_id: int


@dataclass(frozen=True)
class Review:
    id: int
    question_id: int
    decision: str
    notes: str
    reviewer: str
    reviewed_at: str


@dataclass(frozen=True)
class Analytics:
    total: int
    pending: int
    assigned: int
    reviewed: int
    passed: int
    failed: int
    pass_rate: float
    by_reviewer: Sequence[Dict[str, object]]
    over_time: Sequence[Dict[str, object]]


@dataclass(frozen=True)
class Batch:
    id: int
    filename: str
    uploaded_at: str
    imported_count: int
    skipped_count: int


@dataclass(frozen=True)
class ReviewListItem:
    review_id: int
    question_id: int
    source_id: str
    batch_id: int
    filename: str
    instruction: str
    question: str
    output: str
    decision: str
    notes: str
    reviewer: str
    reviewed_at: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ReviewStore:
    """Deep module owning persistence, validation, and review workflow rules."""

    def __init__(
        self,
        db_path: Union[str, Path],
        lease_minutes: int = 30,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.db_path = str(db_path)
        self.lease_minutes = lease_minutes
        self.clock = clock
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS upload_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL,
                    file_hash TEXT NOT NULL UNIQUE,
                    uploaded_at TEXT NOT NULL,
                    imported_count INTEGER NOT NULL,
                    skipped_count INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS questions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id INTEGER NOT NULL REFERENCES upload_batches(id),
                    source_id TEXT NOT NULL,
                    instruction TEXT NOT NULL,
                    input_json TEXT NOT NULL,
                    output TEXT NOT NULL,
                    imported_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS assignments (
                    question_id INTEGER PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
                    reviewer TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    assigned_at TEXT NOT NULL,
                    last_activity_at TEXT NOT NULL,
                    lease_expires_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question_id INTEGER NOT NULL UNIQUE REFERENCES questions(id),
                    decision TEXT NOT NULL CHECK (decision IN ('Pass', 'Fail')),
                    notes TEXT NOT NULL DEFAULT '',
                    reviewer TEXT NOT NULL,
                    reviewed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    question_id INTEGER REFERENCES questions(id),
                    review_id INTEGER,
                    actor TEXT,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_questions_batch ON questions(batch_id);
                CREATE INDEX IF NOT EXISTS idx_assignments_expiry ON assignments(lease_expires_at);
                CREATE INDEX IF NOT EXISTS idx_reviews_date ON reviews(reviewed_at);
                """
            )

    def _timestamp(self) -> str:
        now = self.clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc).isoformat()

    def _now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc)

    def import_json(self, filename: str, raw: bytes) -> ImportResult:
        file_hash = hashlib.sha256(raw).hexdigest()
        valid_records, errors = self._validated_records(raw)

        timestamp = self._timestamp()
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    INSERT INTO upload_batches
                        (filename, file_hash, uploaded_at, imported_count, skipped_count)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (filename, file_hash, timestamp, len(valid_records), len(errors)),
                )
                batch_id = int(cursor.lastrowid)
                connection.executemany(
                    """
                    INSERT INTO questions
                        (batch_id, source_id, instruction, input_json, output, imported_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            batch_id,
                            str(record["id"]),
                            record["instruction"],
                            json.dumps(record["input"], ensure_ascii=False),
                            record["output"],
                            timestamp,
                        )
                        for record in valid_records
                    ],
                )
                connection.execute(
                    """
                    INSERT INTO audit_events (event_type, actor, details_json, created_at)
                    VALUES ('import', 'admin', ?, ?)
                    """,
                    (
                        json.dumps(
                            {
                                "batch_id": batch_id,
                                "filename": filename,
                                "imported": len(valid_records),
                                "skipped": len(errors),
                            },
                            ensure_ascii=False,
                        ),
                        timestamp,
                    ),
                )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                if "upload_batches.file_hash" in str(exc):
                    raise DuplicateUploadError("This exact file has already been uploaded.") from exc
                raise

        return ImportResult(batch_id, len(valid_records), len(errors), tuple(errors))

    def preview_upload(self, raw: bytes) -> UploadPreview:
        valid_records, errors = self._validated_records(raw)
        return UploadPreview(len(valid_records), len(errors), tuple(errors))

    def _validated_records(self, raw: bytes):
        try:
            document = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidUploadError("The uploaded file is not valid UTF-8 JSON.") from exc

        if not isinstance(document, dict) or not isinstance(document.get("data"), list):
            raise InvalidUploadError("The JSON root must contain a 'data' array.")

        valid_records = []
        errors: List[ImportErrorDetail] = []
        for row_number, record in enumerate(document["data"], start=1):
            message = self._record_error(record)
            if message:
                errors.append(ImportErrorDetail(row_number, message))
            else:
                # A question may arrive as a bare string rather than a list of
                # parts; store both shapes as a list so readers stay uniform.
                if isinstance(record["input"], str):
                    record = {**record, "input": [record["input"]]}
                valid_records.append(record)
        return valid_records, errors

    def claim_question(
        self,
        reviewer: str,
        session_id: str,
        exclude_question_id: Optional[int] = None,
    ) -> Optional[Question]:
        reviewer = reviewer.strip()
        if not reviewer or not session_id:
            raise ValueError("Reviewer and session ID are required.")

        now = self._now()
        now_text = now.isoformat()
        expiry_text = (now + timedelta(minutes=self.lease_minutes)).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            expired = connection.execute(
                "SELECT question_id, reviewer FROM assignments WHERE lease_expires_at <= ?",
                (now_text,),
            ).fetchall()
            for assignment in expired:
                connection.execute(
                    """
                    INSERT INTO audit_events
                        (event_type, question_id, actor, details_json, created_at)
                    VALUES ('lease_expired', ?, ?, '{}', ?)
                    """,
                    (assignment["question_id"], assignment["reviewer"], now_text),
                )
            connection.execute("DELETE FROM assignments WHERE lease_expires_at <= ?", (now_text,))

            existing = connection.execute(
                """
                SELECT q.* FROM questions q
                JOIN assignments a ON a.question_id = q.id
                LEFT JOIN reviews r ON r.question_id = q.id
                WHERE a.reviewer = ? AND a.session_id = ? AND r.id IS NULL
                LIMIT 1
                """,
                (reviewer, session_id),
            ).fetchone()
            if existing is not None:
                connection.execute(
                    """
                    UPDATE assignments
                    SET last_activity_at = ?, lease_expires_at = ?
                    WHERE question_id = ?
                    """,
                    (now_text, expiry_text, existing["id"]),
                )
                connection.commit()
                return self._question_from_row(existing)

            conditions = ["r.id IS NULL", "a.question_id IS NULL"]
            parameters: List[object] = []
            if exclude_question_id is not None:
                conditions.append("q.id != ?")
                parameters.append(exclude_question_id)
            selected = connection.execute(
                """
                SELECT q.* FROM questions q
                LEFT JOIN reviews r ON r.question_id = q.id
                LEFT JOIN assignments a ON a.question_id = q.id
                WHERE """
                + " AND ".join(conditions)
                + " ORDER BY RANDOM() LIMIT 1",
                parameters,
            ).fetchone()
            if selected is None:
                connection.commit()
                return None

            connection.execute(
                """
                INSERT INTO assignments
                    (question_id, reviewer, session_id, assigned_at, last_activity_at, lease_expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (selected["id"], reviewer, session_id, now_text, now_text, expiry_text),
            )
            connection.commit()
            return self._question_from_row(selected)

    def submit_review(
        self,
        question_id: int,
        reviewer: str,
        session_id: str,
        decision: str,
        notes: str = "",
    ) -> Review:
        if decision not in ("Pass", "Fail"):
            raise ValueError("Decision must be 'Pass' or 'Fail'.")
        notes = notes.strip()
        if decision == "Fail" and not notes:
            raise ValueError("Failure notes are required.")
        if decision == "Pass":
            notes = ""

        timestamp = self._timestamp()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM reviews WHERE question_id = ?", (question_id,)
            ).fetchone():
                connection.rollback()
                raise ReviewConflictError("This question has already been reviewed.")
            assignment = connection.execute(
                """
                SELECT 1 FROM assignments
                WHERE question_id = ? AND reviewer = ? AND session_id = ?
                  AND lease_expires_at > ?
                """,
                (question_id, reviewer.strip(), session_id, timestamp),
            ).fetchone()
            if assignment is None:
                connection.rollback()
                raise ReviewConflictError("This assignment is no longer active.")
            cursor = connection.execute(
                """
                INSERT INTO reviews (question_id, decision, notes, reviewer, reviewed_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (question_id, decision, notes, reviewer.strip(), timestamp),
            )
            review_id = int(cursor.lastrowid)
            connection.execute("DELETE FROM assignments WHERE question_id = ?", (question_id,))
            connection.commit()
            return Review(review_id, question_id, decision, notes, reviewer.strip(), timestamp)

    def skip_question(self, question_id: int, reviewer: str, session_id: str) -> None:
        timestamp = self._timestamp()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                DELETE FROM assignments
                WHERE question_id = ? AND reviewer = ? AND session_id = ?
                """,
                (question_id, reviewer.strip(), session_id),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise ReviewConflictError("This assignment is no longer active.")
            connection.execute(
                """
                INSERT INTO audit_events
                    (event_type, question_id, actor, details_json, created_at)
                VALUES ('skip', ?, ?, '{}', ?)
                """,
                (question_id, reviewer.strip(), timestamp),
            )
            connection.commit()

    def renew_assignment(self, question_id: int, reviewer: str, session_id: str) -> bool:
        now = self._now()
        now_text = now.isoformat()
        expiry_text = (now + timedelta(minutes=self.lease_minutes)).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE assignments
                SET last_activity_at = ?, lease_expires_at = ?
                WHERE question_id = ? AND reviewer = ? AND session_id = ?
                  AND lease_expires_at > ?
                """,
                (
                    now_text,
                    expiry_text,
                    question_id,
                    reviewer.strip(),
                    session_id,
                    now_text,
                ),
            )
            return cursor.rowcount == 1

    def get_analytics(self, batch_id: Optional[int] = None) -> Analytics:
        now_text = self._timestamp()
        batch_clause = "" if batch_id is None else " AND q.batch_id = ?"
        parameters: Sequence[object] = () if batch_id is None else (batch_id,)
        with self._connect() as connection:
            counts = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN r.id IS NOT NULL THEN 1 ELSE 0 END) AS reviewed,
                    SUM(CASE WHEN r.decision = 'Pass' THEN 1 ELSE 0 END) AS passed,
                    SUM(CASE WHEN r.decision = 'Fail' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN r.id IS NULL AND a.lease_expires_at > ? THEN 1 ELSE 0 END)
                        AS assigned
                FROM questions q
                LEFT JOIN reviews r ON r.question_id = q.id
                LEFT JOIN assignments a ON a.question_id = q.id
                WHERE 1 = 1
                """
                + batch_clause,
                (now_text,) + tuple(parameters),
            ).fetchone()
            reviewer_rows = connection.execute(
                """
                SELECT r.reviewer, COUNT(*) AS reviews
                FROM reviews r JOIN questions q ON q.id = r.question_id
                WHERE 1 = 1
                """
                + batch_clause
                + " GROUP BY r.reviewer ORDER BY reviews DESC, r.reviewer",
                parameters,
            ).fetchall()
            timeline_rows = connection.execute(
                """
                SELECT substr(r.reviewed_at, 1, 10) AS date, COUNT(*) AS reviews
                FROM reviews r JOIN questions q ON q.id = r.question_id
                WHERE 1 = 1
                """
                + batch_clause
                + " GROUP BY date ORDER BY date",
                parameters,
            ).fetchall()

        total = int(counts["total"] or 0)
        reviewed = int(counts["reviewed"] or 0)
        assigned = int(counts["assigned"] or 0)
        passed = int(counts["passed"] or 0)
        failed = int(counts["failed"] or 0)
        return Analytics(
            total=total,
            pending=max(total - reviewed - assigned, 0),
            assigned=assigned,
            reviewed=reviewed,
            passed=passed,
            failed=failed,
            pass_rate=round((passed / reviewed * 100) if reviewed else 0.0, 1),
            by_reviewer=tuple(dict(row) for row in reviewer_rows),
            over_time=tuple(dict(row) for row in timeline_rows),
        )

    def list_batches(self) -> Sequence[Batch]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, filename, uploaded_at, imported_count, skipped_count
                FROM upload_batches ORDER BY id DESC
                """
            ).fetchall()
        return tuple(Batch(**dict(row)) for row in rows)

    def list_reviews(
        self, search: str = "", batch_id: Optional[int] = None, limit: int = 500
    ) -> Sequence[ReviewListItem]:
        conditions = ["1 = 1"]
        parameters: List[object] = []
        if batch_id is not None:
            conditions.append("q.batch_id = ?")
            parameters.append(batch_id)
        if search.strip():
            token = "%" + search.strip() + "%"
            conditions.append(
                """(
                    q.source_id LIKE ? OR q.instruction LIKE ? OR q.input_json LIKE ?
                    OR q.output LIKE ? OR r.notes LIKE ? OR r.reviewer LIKE ?
                    OR b.filename LIKE ?
                )"""
            )
            parameters.extend([token] * 7)
        parameters.append(max(1, min(limit, 5000)))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    r.id AS review_id, q.id AS question_id, q.source_id, q.batch_id,
                    b.filename, q.instruction, q.input_json, q.output,
                    r.decision, r.notes, r.reviewer, r.reviewed_at
                FROM reviews r
                JOIN questions q ON q.id = r.question_id
                JOIN upload_batches b ON b.id = q.batch_id
                WHERE """
                + " AND ".join(conditions)
                + " ORDER BY r.reviewed_at DESC LIMIT ?",
                parameters,
            ).fetchall()
        return tuple(
            ReviewListItem(
                review_id=int(row["review_id"]),
                question_id=int(row["question_id"]),
                source_id=row["source_id"],
                batch_id=int(row["batch_id"]),
                filename=row["filename"],
                instruction=row["instruction"],
                question="\n".join(json.loads(row["input_json"])),
                output=row["output"],
                decision=row["decision"],
                notes=row["notes"],
                reviewer=row["reviewer"],
                reviewed_at=row["reviewed_at"],
            )
            for row in rows
        )

    def reset_review(self, review_id: int, actor: str = "admin") -> None:
        timestamp = self._timestamp()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            review = connection.execute(
                "SELECT * FROM reviews WHERE id = ?", (review_id,)
            ).fetchone()
            if review is None:
                connection.rollback()
                raise ReviewConflictError("This review no longer exists.")
            details = dict(review)
            connection.execute(
                """
                INSERT INTO audit_events
                    (event_type, question_id, review_id, actor, details_json, created_at)
                VALUES ('review_reset', ?, ?, ?, ?, ?)
                """,
                (
                    review["question_id"],
                    review_id,
                    actor,
                    json.dumps(details, ensure_ascii=False),
                    timestamp,
                ),
            )
            connection.execute("DELETE FROM reviews WHERE id = ?", (review_id,))
            connection.commit()

    def get_export_rows(
        self, batch_id: Optional[int] = None
    ) -> Sequence[Dict[str, str]]:
        batch_clause = "" if batch_id is None else " WHERE q.batch_id = ?"
        parameters: Sequence[object] = () if batch_id is None else (batch_id,)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT q.instruction, q.input_json, q.output, r.decision, r.notes
                FROM reviews r JOIN questions q ON q.id = r.question_id
                """
                + batch_clause
                + " ORDER BY r.reviewed_at, r.id",
                parameters,
            ).fetchall()
        return tuple(
            {
                "instruction": row["instruction"],
                "question": "\n".join(json.loads(row["input_json"])),
                "output": row["output"],
                "pass/fail": row["decision"],
                "notes": row["notes"],
            }
            for row in rows
        )

    @staticmethod
    def _question_from_row(row: sqlite3.Row) -> Question:
        return Question(
            id=int(row["id"]),
            source_id=row["source_id"],
            instruction=row["instruction"],
            question_parts=tuple(json.loads(row["input_json"])),
            output=row["output"],
            batch_id=int(row["batch_id"]),
        )

    @staticmethod
    def _record_error(record: object) -> Optional[str]:
        if not isinstance(record, dict):
            return "Record must be a JSON object."
        missing = [key for key in ("id", "instruction", "input", "output") if key not in record]
        if missing:
            return "Missing required field(s): " + ", ".join(missing) + "."
        if not isinstance(record["id"], int) or isinstance(record["id"], bool):
            return "Field 'id' must be an integer."
        if not isinstance(record["instruction"], str):
            return "Field 'instruction' must be a string."
        if not isinstance(record["input"], (str, list)) or (
            isinstance(record["input"], list)
            and not all(isinstance(value, str) for value in record["input"])
        ):
            return "Field 'input' must be a string or an array of strings."
        if not isinstance(record["output"], str):
            return "Field 'output' must be a string."
        return None
