import json

import pytest

from apply_review import ApplyError, Client, apply_failures, chunked, read_failures
from reporting import create_failure_export

HASH = "a" * 64


def workbook(tmp_path, failures, unchecked=()):
    path = tmp_path / "failures.xlsx"
    path.write_bytes(create_failure_export(failures, list(unchecked)))
    return path


def failure(source_id="1", notes="سؤال مكرر", row_hash=HASH):
    return {"source_id": source_id, "instruction": "حول", "input": "مدخل", "output": "مخرج",
            "notes": notes, "check": "duplicate", "row_hash": row_hash}


# --------------------------------------------------------------------------
# Reading the workbook
# --------------------------------------------------------------------------

def test_only_the_three_fields_the_api_needs_are_read(tmp_path):
    items = read_failures(workbook(tmp_path, [failure()]))
    assert items == [{"source_id": "1", "notes": "سؤال مكرر", "row_hash": HASH}]


def test_the_unchecked_sheet_is_never_applied(tmp_path):
    path = workbook(tmp_path, [failure()], [{"source_id": "2", "reason": "no tool schema"}])
    assert [item["source_id"] for item in read_failures(path)] == ["1"]


def test_multi_line_notes_survive_the_round_trip(tmp_path):
    notes = "سؤال مكرر\nلفظ انجليزي في المخرج"
    items = read_failures(workbook(tmp_path, [failure(notes=notes)]))
    assert items[0]["notes"] == notes


def test_a_hand_edited_hash_is_refused(tmp_path):
    with pytest.raises(ApplyError, match="row_hash"):
        read_failures(workbook(tmp_path, [failure(row_hash="abc")]))


def test_a_row_without_notes_is_refused(tmp_path):
    with pytest.raises(ApplyError, match="source_id or notes"):
        read_failures(workbook(tmp_path, [failure(notes="")]))


def test_an_empty_failures_sheet_reads_as_nothing_to_do(tmp_path):
    assert read_failures(workbook(tmp_path, [])) == []


# --------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------

class FakeClient:
    def __init__(self, summaries=None):
        self.sent = []
        self.summaries = summaries or {}

    def bulk_fail(self, items):
        self.sent.append(list(items))
        return self.summaries.get(len(self.sent) - 1,
                                  {"failed": len(items), "overwritten": 0,
                                   "already_failed": 0, "unmatched": []})


def test_rows_are_sent_in_chunks_and_all_of_them_arrive():
    items = [failure(str(number)) for number in range(250)]
    client = FakeClient()
    totals = apply_failures(client, items, size=100)
    assert [len(chunk) for chunk in client.sent] == [100, 100, 50]
    assert totals["failed"] == 250


def test_every_chunk_counts_toward_one_summary():
    client = FakeClient({
        0: {"failed": 2, "overwritten": 1, "already_failed": 0, "unmatched": ["7"]},
        1: {"failed": 1, "overwritten": 0, "already_failed": 3, "unmatched": ["9"]},
    })
    totals = apply_failures(client, [failure(str(n)) for n in range(4)], size=2)
    assert totals == {"failed": 3, "overwritten": 1, "already_failed": 3, "unmatched": ["7", "9"]}


def test_nothing_is_sent_for_an_empty_list():
    client = FakeClient()
    assert apply_failures(client, [])["failed"] == 0
    assert client.sent == []


def test_chunked_never_drops_or_duplicates():
    items = list(range(17))
    assert [item for chunk in chunked(items, 5) for item in chunk] == items


# --------------------------------------------------------------------------
# The HTTP layer
# --------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_reviewer_password_is_refused_before_anything_is_sent():
    client = Client("https://example.test",
                    opener=lambda request, timeout=None: FakeResponse(
                        {"token": "t", "role": "reviewer", "name": "أمينة"}))
    with pytest.raises(ApplyError, match="admin password"):
        client.login("reviewer-secret")


def test_the_token_is_carried_on_the_bulk_request():
    seen = []

    def opener(request, timeout=None):
        seen.append(request)
        if request.full_url.endswith("/api/auth/login"):
            return FakeResponse({"token": "abc", "role": "admin", "name": "Admin"})
        return FakeResponse({"failed": 1, "overwritten": 0, "already_failed": 0, "unmatched": []})

    client = Client("https://example.test/", opener=opener)
    client.login("admin-secret")
    client.bulk_fail([{"source_id": "1", "row_hash": HASH, "notes": "سؤال مكرر"}])

    assert seen[0].headers.get("Authorization") is None
    assert seen[1].headers["Authorization"] == "Bearer abc"
    assert json.loads(seen[1].data.decode("utf-8"))["items"][0]["notes"] == "سؤال مكرر"
