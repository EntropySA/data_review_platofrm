import asyncio
import json

import pytest

from autoreview import row_hash
from cloudflare_app.worker.src.store import AUTOMATIC_REVIEWER, D1ReviewStore, content_hash
from fake_d1 import FakeDatabase


@pytest.fixture
def store():
    database = FakeDatabase()
    database.connection.execute(
        "INSERT INTO upload_batches(filename,file_hash,uploaded_at,status)"
        " VALUES('questions.json','hash-a','2026-08-19T00:00:00+00:00','ready')")
    return D1ReviewStore(database)


def add_question(store, source_id, instruction="حول", parts=("مدخل",), output="مخرج", batch=1):
    cursor = store.db.connection.execute(
        "INSERT INTO questions(batch_id,source_id,instruction,input_json,output,imported_at)"
        " VALUES(?,?,?,?,?,'2026-08-19T00:00:00+00:00')",
        (batch, source_id, instruction, json.dumps(list(parts), ensure_ascii=False), output))
    return cursor.lastrowid


def add_review(store, question_id, decision, notes="", reviewer="محمد"):
    store.db.connection.execute(
        "INSERT INTO reviews(question_id,decision,notes,reviewer,reviewed_at)"
        " VALUES(?,?,?,?,'2026-08-18T00:00:00+00:00')", (question_id, decision, notes, reviewer))


def item(source_id, instruction="حول", parts=("مدخل",), output="مخرج", notes="سؤال مكرر"):
    return {"source_id": source_id, "row_hash": row_hash(instruction, list(parts), output),
            "notes": notes}


def run(coroutine):
    """The store is async; these tests are not. asyncio.run keeps it that way
    without adding a plugin the worker does not otherwise need."""
    return asyncio.run(coroutine)


def reviews(store):
    return store.db.rows("SELECT * FROM reviews")


def events(store):
    return store.db.rows("SELECT * FROM audit_events")


def test_the_hash_matches_the_one_the_checker_writes():
    assert content_hash("i", ["a", "b"], "o") == row_hash("i", ["a", "b"], "o")


def test_an_unreviewed_question_is_failed_and_audited(store):
    add_question(store, "42")
    summary = run(store.bulk_fail([item("42", notes="لفظ انجليزي في المخرج")], "أحمد"))
    assert summary == {"failed": 1, "overwritten": 0, "already_failed": 0, "unmatched": []}
    review = reviews(store)[0]
    assert review["decision"] == "Fail"
    assert review["notes"] == "لفظ انجليزي في المخرج"
    assert review["reviewer"] == AUTOMATIC_REVIEWER
    assert [event["event_type"] for event in events(store)] == ["auto_fail"]


def test_a_question_already_failed_is_left_untouched(store):
    question = add_question(store, "42")
    add_review(store, question, "Fail", notes="ملاحظة المراجع")
    summary = run(store.bulk_fail([item("42", notes="سؤال مكرر")], "أحمد"))
    assert summary["already_failed"] == 1 and summary["failed"] == 0
    assert reviews(store)[0]["notes"] == "ملاحظة المراجع"
    assert reviews(store)[0]["reviewer"] == "محمد"
    assert events(store) == []


def test_a_human_pass_is_overwritten_and_kept_in_the_audit(store):
    question = add_question(store, "42")
    add_review(store, question, "Pass", reviewer="محمد")
    summary = run(store.bulk_fail([item("42", notes="سؤال مكرر")], "أحمد"))
    assert summary["overwritten"] == 1 and summary["failed"] == 0

    review = reviews(store)[0]
    assert (review["decision"], review["notes"], review["reviewer"]) == (
        "Fail", "سؤال مكرر", AUTOMATIC_REVIEWER)

    event = events(store)[0]
    assert event["event_type"] == "review_overridden"
    assert event["actor"] == "أحمد"
    details = json.loads(event["details_json"])
    assert details["decision"] == "Pass"
    assert details["reviewer"] == "محمد"
    assert details["replaced_with"] == "سؤال مكرر"


def test_a_question_someone_is_reviewing_is_failed_and_the_lease_released(store):
    question = add_question(store, "42")
    store.db.connection.execute(
        "INSERT INTO assignments(question_id,reviewer,session_id,assigned_at,"
        "last_activity_at,lease_expires_at) VALUES(?,'محمد','s','t','t','2099-01-01T00:00:00')",
        (question,))
    summary = run(store.bulk_fail([item("42")], "أحمد"))
    assert summary["failed"] == 1
    assert store.db.rows("SELECT * FROM assignments") == []


def test_content_that_does_not_match_is_reported_not_applied(store):
    add_question(store, "42", output="مخرج")
    summary = run(store.bulk_fail([item("42", output="مخرج مختلف")], "أحمد"))
    assert summary == {"failed": 0, "overwritten": 0, "already_failed": 0, "unmatched": ["42"]}
    assert reviews(store) == []


def test_a_source_id_that_does_not_exist_is_reported(store):
    summary = run(store.bulk_fail([item("999")], "أحمد"))
    assert summary["unmatched"] == ["999"]
    assert reviews(store) == []


def test_a_reused_source_id_only_fails_the_matching_question(store):
    store.db.connection.execute(
        "INSERT INTO upload_batches(filename,file_hash,uploaded_at,status)"
        " VALUES('other.json','hash-b','2026-08-19T00:00:00+00:00','ready')")
    wanted = add_question(store, "1", output="مخرج", batch=1)
    add_question(store, "1", output="مخرج آخر", batch=2)
    summary = run(store.bulk_fail([item("1", output="مخرج")], "أحمد"))
    assert summary["failed"] == 1
    assert [review["question_id"] for review in reviews(store)] == [wanted]


def test_many_items_are_looked_up_across_several_chunks(store):
    for source_id in range(120):
        add_question(store, str(source_id), output=f"مخرج {source_id}")
    items = [item(str(source_id), output=f"مخرج {source_id}") for source_id in range(120)]
    summary = run(store.bulk_fail(items, "أحمد"))
    assert summary["failed"] == 120 and summary["unmatched"] == []


def test_an_empty_request_writes_nothing(store):
    assert run(store.bulk_fail([], "أحمد")) == {
        "failed": 0, "overwritten": 0, "already_failed": 0, "unmatched": []}
