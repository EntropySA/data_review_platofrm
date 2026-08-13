import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from review_store import DuplicateUploadError, ReviewConflictError, ReviewStore


def payload(*records):
    return json.dumps({"data": list(records)}).encode("utf-8")


def valid_record(source_id=1, question="What is 2 + 2?"):
    return {
        "id": source_id,
        "instruction": "Answer accurately.",
        "input": [question],
        "output": "4",
        "ignored": "extra fields do not matter",
    }


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, minutes):
        self.value += timedelta(minutes=minutes)


def test_admin_can_partially_import_a_file_and_duplicate_file_is_rejected(tmp_path):
    store = ReviewStore(tmp_path / "reviews.db")
    raw = payload(
        valid_record(),
        {"id": 2, "instruction": "Missing output", "input": ["Question"]},
    )

    result = store.import_json("batch.json", raw)

    assert result.imported_count == 1
    assert result.skipped_count == 1
    assert result.errors[0].row == 2
    assert "output" in result.errors[0].message

    with pytest.raises(DuplicateUploadError):
        store.import_json("renamed.json", raw)


def test_only_one_reviewer_can_claim_the_same_question(tmp_path):
    store = ReviewStore(tmp_path / "reviews.db")
    store.import_json("one.json", payload(valid_record()))

    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(
            pool.map(
                lambda number: store.claim_question(
                    reviewer=f"Reviewer {number}", session_id=f"session-{number}"
                ),
                range(8),
            )
        )

    assigned = [claim for claim in claims if claim is not None]
    assert len(assigned) == 1
    assert assigned[0].instruction == "Answer accurately."
    assert assigned[0].question_parts == ("What is 2 + 2?",)
    assert assigned[0].output == "4"


def test_fail_requires_notes_and_a_submitted_review_is_final(tmp_path):
    store = ReviewStore(tmp_path / "reviews.db")
    store.import_json("one.json", payload(valid_record()))
    question = store.claim_question("Amina", "session-a")
    assert question is not None

    with pytest.raises(ValueError, match="notes"):
        store.submit_review(question.id, "Amina", "session-a", "Fail", "  ")

    review = store.submit_review(
        question.id, "Amina", "session-a", "Fail", "The output is unsupported."
    )
    assert review.decision == "Fail"
    assert review.notes == "The output is unsupported."
    assert store.claim_question("Bader", "session-b") is None

    with pytest.raises(ReviewConflictError):
        store.submit_review(question.id, "Amina", "session-a", "Pass", "")


def test_skip_releases_question_and_can_exclude_it_from_the_next_claim(tmp_path):
    store = ReviewStore(tmp_path / "reviews.db")
    store.import_json(
        "two.json",
        payload(valid_record(1, "First"), valid_record(2, "Second")),
    )
    first = store.claim_question("Amina", "session-a")
    assert first is not None

    store.skip_question(first.id, "Amina", "session-a")
    next_question = store.claim_question(
        "Amina", "session-a", exclude_question_id=first.id
    )

    assert next_question is not None
    assert next_question.id != first.id


def test_activity_renews_the_lease_and_inactivity_releases_it(tmp_path):
    clock = MutableClock()
    store = ReviewStore(tmp_path / "reviews.db", lease_minutes=30, clock=clock)
    store.import_json("one.json", payload(valid_record()))
    question = store.claim_question("Amina", "session-a")
    assert question is not None

    clock.advance(20)
    assert store.renew_assignment(question.id, "Amina", "session-a") is True
    clock.advance(20)
    assert store.claim_question("Bader", "session-b") is None

    clock.advance(11)
    reassigned = store.claim_question("Bader", "session-b")
    assert reassigned is not None
    assert reassigned.id == question.id


def test_admin_analytics_report_progress_and_reviewer_activity(tmp_path):
    store = ReviewStore(tmp_path / "reviews.db")
    result = store.import_json(
        "three.json",
        payload(
            valid_record(1, "First"),
            valid_record(2, "Second"),
            valid_record(3, "Third"),
        ),
    )
    reviewed = store.claim_question("Amina", "session-a")
    assert reviewed is not None
    store.submit_review(reviewed.id, "Amina", "session-a", "Pass")
    assert store.claim_question("Bader", "session-b") is not None

    metrics = store.get_analytics(batch_id=result.batch_id)

    assert metrics.total == 3
    assert metrics.reviewed == 1
    assert metrics.assigned == 1
    assert metrics.pending == 1
    assert metrics.passed == 1
    assert metrics.failed == 0
    assert metrics.pass_rate == 100.0
    assert metrics.by_reviewer == ({"reviewer": "Amina", "reviews": 1},)


def test_admin_can_find_and_reset_a_review(tmp_path):
    store = ReviewStore(tmp_path / "reviews.db")
    store.import_json("one.json", payload(valid_record()))
    question = store.claim_question("Amina", "session-a")
    assert question is not None
    store.submit_review(
        question.id, "Amina", "session-a", "Fail", "The answer is unsupported."
    )

    matches = store.list_reviews(search="unsupported")
    assert len(matches) == 1
    assert matches[0].source_id == "1"
    assert matches[0].decision == "Fail"

    store.reset_review(matches[0].review_id, actor="admin")
    reassigned = store.claim_question("Bader", "session-b")
    assert reassigned is not None
    assert reassigned.id == question.id


def test_export_contains_only_completed_reviews_and_exact_columns(tmp_path):
    store = ReviewStore(tmp_path / "reviews.db")
    store.import_json(
        "two.json",
        payload(
            {
                **valid_record(1, "Line one"),
                "input": ["Line one", "Line two"],
            },
            valid_record(2, "Pending"),
        ),
    )
    question = store.claim_question("Amina", "session-a")
    assert question is not None
    store.submit_review(question.id, "Amina", "session-a", "Pass")

    rows = store.get_export_rows()

    assert len(rows) == 1
    assert tuple(rows[0]) == ("instruction", "question", "output", "pass/fail", "notes")
    assert rows[0]["pass/fail"] == "Pass"
    assert rows[0]["notes"] == ""
    expected_question = "\n".join(question.question_parts)
    assert rows[0]["question"] == expected_question
