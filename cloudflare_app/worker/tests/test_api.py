from fastapi.testclient import TestClient

from cloudflare_app.worker.src.api import app
from cloudflare_app.worker.src.store import StoreConflict


class Env:
    REVIEWER_PASSWORD = "review-secret"
    ADMIN_PASSWORD = "admin-secret"
    SESSION_SECRET = "a-secure-signing-secret-with-32-characters"
    FRONTEND_ORIGIN = "http://localhost:5173"


class FakeStore:
    async def claim(self, reviewer, session_id, exclude_id):
        return {
            "id": 7,
            "source_id": "42",
            "instruction": "أجب بدقة",
            "input": ["ما الإجابة؟"],
            "output": "الإجابة",
        }

    async def analytics(self):
        return {"total": 3, "reviewed": 1, "assigned": 1, "pending": 1,
                "passed": 1, "failed": 0, "by_reviewer": [], "over_time": []}

    async def list_batches(self):
        return [{"id": 1, "filename": "questions.json", "uploaded_at": "2026-08-17T00:00:00+00:00",
                 "imported_count": 500, "skipped_count": 0, "status": "uploading",
                 "stored": 320, "reviewed": 0}]

    async def bulk_fail(self, items, actor):
        self.bulk_fail_call = (items, actor)
        return {"failed": len(items), "overwritten": 0, "already_failed": 0, "unmatched": []}

    async def delete_batch(self, batch_id, actor):
        if batch_id == 2:
            raise StoreConflict("4 question(s) in this upload have already been reviewed.")
        return {"deleted_questions": 320}


def login(client, password, name=""):
    response = client.post("/api/auth/login", json={"password": password,
                                                     "reviewer_name": name})
    assert response.status_code == 200
    return response.json()["token"]


def test_reviewer_logs_in_and_claims_rtl_question():
    app.state.env = Env()
    app.state.store = FakeStore()
    with TestClient(app) as client:
        token = login(client, "review-secret", "أمينة")
        response = client.post("/api/reviewer/claim",
                               headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["instruction"] == "أجب بدقة"


def test_admin_sees_an_unfinished_upload_and_what_it_holds():
    app.state.env = Env()
    app.state.store = FakeStore()
    with TestClient(app) as client:
        token = login(client, "admin-secret")
        response = client.get("/api/admin/batches",
                              headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    batch = response.json()[0]
    assert batch["status"] == "uploading"
    assert batch["stored"] == 320


def test_admin_deletes_an_upload_to_free_the_file_for_reupload():
    app.state.env = Env()
    app.state.store = FakeStore()
    with TestClient(app) as client:
        token = login(client, "admin-secret")
        response = client.delete("/api/admin/batches/1",
                                 headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["deleted_questions"] == 320


def test_deleting_an_upload_holding_reviews_is_refused():
    app.state.env = Env()
    app.state.store = FakeStore()
    with TestClient(app) as client:
        token = login(client, "admin-secret")
        response = client.delete("/api/admin/batches/2",
                                 headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 409
    assert "already been reviewed" in response.json()["detail"]


def test_reviewer_cannot_delete_an_upload():
    app.state.env = Env()
    app.state.store = FakeStore()
    with TestClient(app) as client:
        token = login(client, "review-secret", "Amina")
        response = client.delete("/api/admin/batches/1",
                                 headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_reviewer_cannot_open_admin_batches():
    app.state.env = Env()
    app.state.store = FakeStore()
    with TestClient(app) as client:
        token = login(client, "review-secret", "Amina")
        response = client.get("/api/admin/batches",
                              headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_reviewer_cannot_open_admin_analytics():
    app.state.env = Env()
    app.state.store = FakeStore()
    with TestClient(app) as client:
        token = login(client, "review-secret", "Amina")
        response = client.get("/api/admin/analytics",
                              headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


HASH = "a" * 64


def test_admin_records_automatic_failures_in_bulk():
    app.state.env = Env()
    app.state.store = FakeStore()
    with TestClient(app) as client:
        token = login(client, "admin-secret")
        response = client.post(
            "/api/admin/reviews/bulk-fail",
            headers={"Authorization": f"Bearer {token}"},
            json={"items": [{"source_id": "42", "row_hash": HASH, "notes": "سؤال مكرر"}]})
    assert response.status_code == 200
    assert response.json()["failed"] == 1
    items, actor = app.state.store.bulk_fail_call
    assert items[0]["notes"] == "سؤال مكرر"
    assert actor == "Admin"


def test_a_reviewer_cannot_record_automatic_failures():
    app.state.env = Env()
    app.state.store = FakeStore()
    with TestClient(app) as client:
        token = login(client, "review-secret", "أمينة")
        response = client.post(
            "/api/admin/reviews/bulk-fail",
            headers={"Authorization": f"Bearer {token}"},
            json={"items": [{"source_id": "42", "row_hash": HASH, "notes": "سؤال مكرر"}]})
    assert response.status_code == 403


def test_an_item_without_a_full_hash_or_notes_is_refused():
    app.state.env = Env()
    app.state.store = FakeStore()
    with TestClient(app) as client:
        token = login(client, "admin-secret")
        headers = {"Authorization": f"Bearer {token}"}
        short = client.post("/api/admin/reviews/bulk-fail", headers=headers,
                            json={"items": [{"source_id": "42", "row_hash": "abc", "notes": "x"}]})
        blank = client.post("/api/admin/reviews/bulk-fail", headers=headers,
                            json={"items": [{"source_id": "42", "row_hash": HASH, "notes": ""}]})
    assert short.status_code == 422 and blank.status_code == 422
