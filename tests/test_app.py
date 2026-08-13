import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest

from review_store import ReviewStore


def question_payload():
    return b'{"data":[{"id":1,"instruction":"Answer","input":["Question"],"output":"Output"}]}'


def test_reviewer_can_sign_in_and_reach_empty_review_queue(tmp_path):
    app = AppTest.from_file("app.py")
    app.secrets["REVIEWER_PASSWORD"] = "review-secret"
    app.secrets["ADMIN_PASSWORD"] = "admin-secret"
    app.secrets["DATABASE_PATH"] = str(tmp_path / "reviews.db")
    app.run(timeout=15)

    app.text_input[0].set_value("Amina")
    app.text_input[1].set_value("review-secret")
    app.button[0].click().run(timeout=15)

    assert any(title.value == "Question review" for title in app.title)
    assert any("No questions are available" in item.value for item in app.subheader)


def test_admin_analytics_renders_with_completed_reviews(tmp_path):
    database_path = tmp_path / "reviews.db"
    store = ReviewStore(database_path)
    store.import_json("questions.json", question_payload())
    question = store.claim_question("Amina", "session-a")
    assert question is not None
    store.submit_review(question.id, "Amina", "session-a", "Pass")

    app = AppTest.from_file("app.py")
    app.secrets["REVIEWER_PASSWORD"] = "review-secret"
    app.secrets["ADMIN_PASSWORD"] = "admin-secret"
    app.secrets["DATABASE_PATH"] = str(database_path)
    app.run(timeout=15)
    app.text_input[1].set_value("admin-secret")
    app.button[0].click().run(timeout=15)

    assert not app.exception
    assert any(title.value == "Administration" for title in app.title)
    assert any(metric.label == "Reviewed" and metric.value == "1" for metric in app.metric)
