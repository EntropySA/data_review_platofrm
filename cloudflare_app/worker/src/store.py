"""Asynchronous D1 persistence adapter for the review workflow."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any


class StoreConflict(ValueError):
    pass


# Reviews written by the checking scripts rather than a person. A fixed, plainly
# non-human name keeps them separable in the Reviews tab and the by-reviewer
# analytics, so a rule that turns out wrong can be found and undone in bulk.
AUTOMATIC_REVIEWER = "مراجعة آلية"

# Bound parameters per statement are limited, so questions are looked up in
# groups well inside that ceiling.
LOOKUP_CHUNK = 50


def content_hash(instruction: str, input_parts: list[str], output: str) -> str:
    """Fingerprint of a question's content.

    Must stay identical to autoreview.row_hash in the repository root, which is
    what the checking script writes into its workbook; tests assert the two
    agree. The separators are control characters that cannot appear in the
    imported JSON, so no combination of fields can collide with another.
    """
    payload = "\x1f".join([instruction, "\x1e".join(input_parts), output])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _row(value: Any) -> dict | None:
    if value is None:
        return None
    if hasattr(value, "to_py"):
        value = value.to_py()
    return dict(value)


def _rows(value: Any) -> list[dict]:
    if hasattr(value, "to_py"):
        value = value.to_py()
    return [dict(item) for item in value]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class D1ReviewStore:
    def __init__(self, database: Any, lease_minutes: int = 30):
        self.db = database
        self.lease_minutes = lease_minutes

    async def start_import(self, filename: str, file_hash: str) -> int:
        timestamp = _now().isoformat()
        try:
            row = await self.db.prepare(
                """INSERT INTO upload_batches(filename,file_hash,uploaded_at)
                   VALUES(?1,?2,?3) RETURNING id"""
            ).bind(filename, file_hash, timestamp).first()
        except Exception as exc:
            raise StoreConflict("This exact file has already been uploaded.") from exc
        return int(_row(row)["id"])

    async def add_import_chunk(
        self, batch_id: int, records: list[dict], skipped_count: int
    ) -> dict:
        batch = await self.db.prepare(
            "SELECT status FROM upload_batches WHERE id=?1"
        ).bind(batch_id).first()
        if not batch or _row(batch)["status"] != "uploading":
            raise StoreConflict("Import is not open.")
        timestamp = _now().isoformat()
        statements = [
            self.db.prepare(
                """INSERT INTO questions
                   (batch_id,source_id,instruction,input_json,output,imported_at)
                   VALUES(?1,?2,?3,?4,?5,?6)"""
            ).bind(
                batch_id,
                str(record["id"]),
                record["instruction"],
                json.dumps(record["input"], ensure_ascii=False),
                record["output"],
                timestamp,
            )
            for record in records
        ]
        statements.append(
            self.db.prepare(
                """UPDATE upload_batches SET
                   imported_count=imported_count+?1, skipped_count=skipped_count+?2
                   WHERE id=?3"""
            ).bind(len(records), skipped_count, batch_id)
        )
        await self.db.batch(statements)
        return {"imported": len(records), "skipped": skipped_count}

    async def list_batches(self) -> list[dict]:
        """Metadata for every upload, including how many questions really landed.

        An import that never reached finish_import leaves its batch 'uploading',
        and analytics and claim both ignore those questions, so the stored count
        is reported separately from the count the importer claimed to send.
        """
        result = await self.db.prepare(
            """SELECT b.id,b.filename,b.uploaded_at,b.imported_count,b.skipped_count,
               b.status,COUNT(q.id) stored,
               SUM(CASE WHEN r.id IS NOT NULL THEN 1 ELSE 0 END) reviewed
               FROM upload_batches b LEFT JOIN questions q ON q.batch_id=b.id
               LEFT JOIN reviews r ON r.question_id=q.id
               GROUP BY b.id ORDER BY b.id DESC"""
        ).all()
        return [{**row, "reviewed": int(row["reviewed"] or 0)} for row in _rows(result.results)]

    async def delete_batch(self, batch_id: int, actor: str) -> dict:
        """Remove an upload and its questions so the file can be sent again.

        Refused once any of its questions carry a review, because that would
        discard a reviewer's decision as a side effect of tidying up an import.
        The file hash is released, which is the point: it is what otherwise
        blocks re-uploading a file whose first attempt did not complete.
        """
        batch = _row(await self.db.prepare(
            "SELECT * FROM upload_batches WHERE id=?1"
        ).bind(batch_id).first())
        if not batch:
            raise StoreConflict("This upload no longer exists.")
        counts = _row(await self.db.prepare(
            """SELECT COUNT(q.id) stored,
               SUM(CASE WHEN r.id IS NOT NULL THEN 1 ELSE 0 END) reviewed
               FROM questions q LEFT JOIN reviews r ON r.question_id=q.id
               WHERE q.batch_id=?1"""
        ).bind(batch_id).first()) or {}
        reviewed = int(counts.get("reviewed") or 0)
        if reviewed:
            raise StoreConflict(
                f"{reviewed} question(s) in this upload have already been reviewed. "
                "Reset those reviews first if you really intend to remove it."
            )
        stored = int(counts.get("stored") or 0)
        await self.db.batch([
            self.db.prepare(
                """INSERT INTO audit_events(event_type,actor,details_json,created_at)
                   VALUES('batch_deleted',?1,?2,?3)"""
            ).bind(actor, json.dumps({**batch, "deleted_questions": stored},
                                     ensure_ascii=False), _now().isoformat()),
            self.db.prepare(
                "DELETE FROM assignments WHERE question_id IN "
                "(SELECT id FROM questions WHERE batch_id=?1)"
            ).bind(batch_id),
            self.db.prepare("DELETE FROM questions WHERE batch_id=?1").bind(batch_id),
            self.db.prepare("DELETE FROM upload_batches WHERE id=?1").bind(batch_id),
        ])
        return {"deleted_questions": stored}

    async def finish_import(self, batch_id: int) -> None:
        result = await self.db.prepare(
            "UPDATE upload_batches SET status='ready' WHERE id=?1 AND status='uploading'"
        ).bind(batch_id).run()
        if int(result.meta.changes) != 1:
            raise StoreConflict("Import is not open.")

    async def claim(self, reviewer: str, session_id: str, exclude_id: int | None) -> dict | None:
        now = _now()
        now_text = now.isoformat()
        expiry = (now + timedelta(minutes=self.lease_minutes)).isoformat()
        existing = await self.db.prepare(
            """SELECT q.* FROM assignments a JOIN questions q ON q.id=a.question_id
               WHERE a.reviewer=?1 AND a.session_id=?2 AND a.lease_expires_at>?3
               LIMIT 1"""
        ).bind(reviewer, session_id, now_text).first()
        if existing:
            row = _row(existing)
            await self.renew(int(row["id"]), reviewer, session_id)
            return self._question(row)

        excluded = exclude_id if exclude_id is not None else -1
        for _ in range(2):
            claimed = await self.db.prepare(
                """INSERT INTO assignments
                   (question_id,reviewer,session_id,assigned_at,last_activity_at,lease_expires_at)
                   SELECT q.id,?1,?2,?3,?3,?4 FROM questions q
                   JOIN upload_batches b ON b.id=q.batch_id AND b.status='ready'
                   LEFT JOIN reviews r ON r.question_id=q.id
                   LEFT JOIN assignments a ON a.question_id=q.id
                   WHERE r.id IS NULL AND (a.question_id IS NULL OR a.lease_expires_at<=?3)
                     AND q.id<>?5
                   ORDER BY RANDOM() LIMIT 1
                   ON CONFLICT(question_id) DO UPDATE SET
                     reviewer=excluded.reviewer, session_id=excluded.session_id,
                     assigned_at=excluded.assigned_at,last_activity_at=excluded.last_activity_at,
                     lease_expires_at=excluded.lease_expires_at
                   WHERE assignments.lease_expires_at<=?3
                   RETURNING question_id"""
            ).bind(reviewer, session_id, now_text, expiry, excluded).first()
            if claimed:
                question_id = int(_row(claimed)["question_id"])
                question = await self.db.prepare("SELECT * FROM questions WHERE id=?1").bind(
                    question_id
                ).first()
                return self._question(_row(question))
        return None

    async def renew(self, question_id: int, reviewer: str, session_id: str) -> bool:
        now = _now()
        result = await self.db.prepare(
            """UPDATE assignments SET last_activity_at=?1,lease_expires_at=?2
               WHERE question_id=?3 AND reviewer=?4 AND session_id=?5 AND lease_expires_at>?1"""
        ).bind(
            now.isoformat(),
            (now + timedelta(minutes=self.lease_minutes)).isoformat(),
            question_id,
            reviewer,
            session_id,
        ).run()
        return int(result.meta.changes) == 1

    async def skip(self, question_id: int, reviewer: str, session_id: str) -> None:
        result = await self.db.prepare(
            "DELETE FROM assignments WHERE question_id=?1 AND reviewer=?2 AND session_id=?3"
        ).bind(question_id, reviewer, session_id).run()
        if int(result.meta.changes) != 1:
            raise StoreConflict("Assignment is no longer active.")

    async def submit(
        self, question_id: int, reviewer: str, session_id: str, decision: str, notes: str
    ) -> None:
        notes = notes.strip()
        if decision == "Fail" and not notes:
            raise StoreConflict("Failure notes are required.")
        if decision == "Pass":
            notes = ""
        now = _now().isoformat()
        assignment = await self.db.prepare(
            """SELECT 1 AS ok FROM assignments WHERE question_id=?1 AND reviewer=?2
               AND session_id=?3 AND lease_expires_at>?4"""
        ).bind(question_id, reviewer, session_id, now).first()
        if not assignment:
            raise StoreConflict("Assignment is no longer active.")
        try:
            await self.db.batch([
                self.db.prepare(
                    """INSERT INTO reviews(question_id,decision,notes,reviewer,reviewed_at)
                       VALUES(?1,?2,?3,?4,?5)"""
                ).bind(question_id, decision, notes, reviewer, now),
                self.db.prepare("DELETE FROM assignments WHERE question_id=?1").bind(question_id),
            ])
        except Exception as exc:
            raise StoreConflict("Question has already been reviewed.") from exc

    async def bulk_fail(self, items: list[dict], actor: str) -> dict:
        """Fail every question whose source id and content match a given item.

        Matching on content as well as source id is what makes this safe to run
        unattended: source ids are only unique within an upload, so an id alone
        could name a question from an entirely different file. An item matching
        nothing is reported rather than applied to the closest candidate.

        A question already failed is left exactly as it is. A question a person
        passed is rewritten to a failure, with the whole of their review kept in
        audit_events first, so the decision this overrode stays recoverable.
        """
        wanted = {(item["source_id"], item["row_hash"]): item["notes"] for item in items}
        source_ids = list(dict.fromkeys(item["source_id"] for item in items))

        candidates: list[dict] = []
        for start in range(0, len(source_ids), LOOKUP_CHUNK):
            chunk = source_ids[start:start + LOOKUP_CHUNK]
            placeholders = ",".join(f"?{index}" for index in range(1, len(chunk) + 1))
            result = await self.db.prepare(
                f"""SELECT q.id,q.source_id,q.instruction,q.input_json,q.output,
                    r.id review_id,r.decision,r.notes,r.reviewer,r.reviewed_at
                    FROM questions q LEFT JOIN reviews r ON r.question_id=q.id
                    WHERE q.source_id IN ({placeholders})"""
            ).bind(*chunk).all()
            candidates.extend(_rows(result.results))

        now = _now().isoformat()
        statements: list[Any] = []
        matched: set[tuple[str, str]] = set()
        failed = overwritten = already_failed = 0

        for row in candidates:
            key = (row["source_id"], content_hash(
                row["instruction"], json.loads(row["input_json"]), row["output"]))
            notes = wanted.get(key)
            if notes is None:
                continue
            matched.add(key)
            if row["decision"] == "Fail":
                already_failed += 1
                continue
            # A live lease is released so the question leaves the reviewer's
            # queue, exactly as submitting a review does.
            statements.append(self.db.prepare(
                "DELETE FROM assignments WHERE question_id=?1").bind(row["id"]))
            if row["decision"] == "Pass":
                overwritten += 1
                previous = {key: row[key] for key in
                            ("review_id", "decision", "notes", "reviewer", "reviewed_at")}
                statements.append(self.db.prepare(
                    """INSERT INTO audit_events
                       (event_type,question_id,review_id,actor,details_json,created_at)
                       VALUES('review_overridden',?1,?2,?3,?4,?5)"""
                ).bind(row["id"], row["review_id"], actor,
                       json.dumps({**previous, "replaced_with": notes}, ensure_ascii=False), now))
                statements.append(self.db.prepare(
                    """UPDATE reviews SET decision='Fail',notes=?1,reviewer=?2,reviewed_at=?3
                       WHERE id=?4"""
                ).bind(notes, AUTOMATIC_REVIEWER, now, row["review_id"]))
            else:
                failed += 1
                statements.append(self.db.prepare(
                    """INSERT INTO reviews(question_id,decision,notes,reviewer,reviewed_at)
                       VALUES(?1,'Fail',?2,?3,?4)"""
                ).bind(row["id"], notes, AUTOMATIC_REVIEWER, now))
                statements.append(self.db.prepare(
                    """INSERT INTO audit_events
                       (event_type,question_id,actor,details_json,created_at)
                       VALUES('auto_fail',?1,?2,?3,?4)"""
                ).bind(row["id"], actor,
                       json.dumps({"notes": notes}, ensure_ascii=False), now))

        if statements:
            await self.db.batch(statements)
        unmatched = [source_id for source_id, _hash in wanted if (source_id, _hash) not in matched]
        return {"failed": failed, "overwritten": overwritten,
                "already_failed": already_failed, "unmatched": unmatched}

    async def analytics(self) -> dict:
        now = _now().isoformat()
        counts = _row(await self.db.prepare(
            """SELECT COUNT(*) total,
               SUM(CASE WHEN r.id IS NOT NULL THEN 1 ELSE 0 END) reviewed,
               SUM(CASE WHEN r.decision='Pass' THEN 1 ELSE 0 END) passed,
               SUM(CASE WHEN r.decision='Fail' THEN 1 ELSE 0 END) failed,
               SUM(CASE WHEN r.id IS NULL AND a.lease_expires_at>?1 THEN 1 ELSE 0 END) assigned
               FROM questions q JOIN upload_batches b ON b.id=q.batch_id AND b.status='ready'
               LEFT JOIN reviews r ON r.question_id=q.id
               LEFT JOIN assignments a ON a.question_id=q.id"""
        ).bind(now).first()) or {}
        total, reviewed = int(counts.get("total") or 0), int(counts.get("reviewed") or 0)
        assigned = int(counts.get("assigned") or 0)
        reviewers = await self.db.prepare(
            "SELECT reviewer,COUNT(*) reviews FROM reviews GROUP BY reviewer ORDER BY reviews DESC"
        ).all()
        timeline = await self.db.prepare(
            """SELECT substr(reviewed_at,1,10) date,COUNT(*) reviews FROM reviews
               GROUP BY date ORDER BY date"""
        ).all()
        return {
            "total": total, "reviewed": reviewed, "assigned": assigned,
            "pending": max(total-reviewed-assigned, 0),
            "passed": int(counts.get("passed") or 0), "failed": int(counts.get("failed") or 0),
            "by_reviewer": _rows(reviewers.results), "over_time": _rows(timeline.results),
        }

    async def export_rows(self) -> list[dict]:
        result = await self.db.prepare(
            """SELECT q.instruction,q.input_json,q.output,r.decision,r.notes
               FROM reviews r JOIN questions q ON q.id=r.question_id ORDER BY r.id"""
        ).all()
        return [{
            "instruction": row["instruction"],
            "question": "\n".join(json.loads(row["input_json"])),
            "output": row["output"], "pass/fail": row["decision"], "notes": row["notes"],
        } for row in _rows(result.results)]

    async def list_reviews(self, search: str = "") -> list[dict]:
        token = f"%{search.strip()}%"
        result = await self.db.prepare(
            """SELECT r.id review_id,q.source_id,q.instruction,q.input_json,q.output,
               r.decision,r.notes,r.reviewer,r.reviewed_at
               FROM reviews r JOIN questions q ON q.id=r.question_id
               WHERE ?1='' OR q.source_id LIKE ?2 OR q.instruction LIKE ?2
                 OR q.output LIKE ?2 OR r.notes LIKE ?2 OR r.reviewer LIKE ?2
               ORDER BY r.id DESC LIMIT 500"""
        ).bind(search.strip(), token).all()
        rows = _rows(result.results)
        for row in rows:
            row["question"] = "\n".join(json.loads(row.pop("input_json")))
        return rows

    async def reset_review(self, review_id: int, actor: str) -> None:
        review = _row(await self.db.prepare("SELECT * FROM reviews WHERE id=?1").bind(
            review_id
        ).first())
        if not review:
            raise StoreConflict("Review no longer exists.")
        await self.db.batch([
            self.db.prepare(
                """INSERT INTO audit_events
                   (event_type,question_id,review_id,actor,details_json,created_at)
                   VALUES('review_reset',?1,?2,?3,?4,?5)"""
            ).bind(review["question_id"], review_id, actor,
                   json.dumps(review, ensure_ascii=False), _now().isoformat()),
            self.db.prepare("DELETE FROM reviews WHERE id=?1").bind(review_id),
        ])

    @staticmethod
    def _question(row: dict) -> dict:
        return {"id": int(row["id"]), "source_id": row["source_id"],
                "instruction": row["instruction"], "input": json.loads(row["input_json"]),
                "output": row["output"]}
