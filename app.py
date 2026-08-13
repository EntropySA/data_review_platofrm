"""Streamlit interface for concurrent question review."""

import os
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

from reporting import create_excel_export
from review_store import (
    DuplicateUploadError,
    InvalidUploadError,
    ReviewConflictError,
    ReviewStore,
)
from security import ConfigurationError, authenticate
from ui_helpers import bar_list_html, directional_text_html


st.set_page_config(page_title="Review Desk", layout="wide")


def apply_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            color-scheme: light;
            --brand: #0f766e;
            --brand-dark: #115e59;
            --surface: #f8fafc;
            --line: #dbe4ea;
            --ink: #102a43;
            --muted: #52667a;
        }
        .stApp, [data-testid="stAppViewContainer"] {
            background: var(--surface);
            color: var(--ink);
        }
        .block-container { max-width: 1180px; padding-top: 2rem; padding-bottom: 4rem; }
        .stApp h1, .stApp h2, .stApp h3, .stApp h4,
        .stApp [data-testid="stMarkdownContainer"],
        .stApp [data-testid="stText"],
        .stApp [data-testid="stMetricLabel"],
        .stApp [data-testid="stMetricValue"] {
            color: var(--ink);
        }
        .stApp h1, .stApp h2, .stApp h3 { letter-spacing: -0.02em; }
        [data-testid="stCaptionContainer"] { color: var(--muted); }
        [data-testid="stSidebar"] {
            background: #eef6f5;
            color: var(--ink);
        }
        div[data-testid="stVerticalBlockBorderWrapper"] {
            background: #ffffff;
            color: var(--ink);
            border-color: var(--line);
            border-radius: 14px;
            box-shadow: 0 1px 2px rgba(16, 42, 67, 0.04);
        }
        .stButton > button, .stDownloadButton > button {
            min-height: 46px;
            border-radius: 9px;
            font-weight: 650;
        }
        .stTextArea textarea, .stTextInput input {
            min-height: 44px;
            background: #ffffff !important;
            color: var(--ink) !important;
            caret-color: var(--ink);
            unicode-bidi: plaintext;
            text-align: start;
        }
        .stTextArea textarea::placeholder, .stTextInput input::placeholder {
            color: #64748b !important;
            opacity: 1;
        }
        [data-testid="stMetric"] {
            background: #ffffff;
            color: var(--ink);
            border: 1px solid var(--line);
            border-radius: 12px;
            padding: 14px 16px;
        }
        .review-text {
            color: var(--ink);
            font-size: 1rem;
            line-height: 1.75;
            white-space: pre-wrap;
            overflow-wrap: anywhere;
            unicode-bidi: plaintext;
            text-align: start;
        }
        .accessible-chart {
            display: grid;
            gap: 12px;
            padding: 16px;
            background: #ffffff;
            border: 1px solid var(--line);
            border-radius: 12px;
        }
        .bar-row {
            display: grid;
            grid-template-columns: minmax(90px, 1.2fr) minmax(120px, 3fr) 42px;
            align-items: center;
            gap: 10px;
            color: var(--ink);
        }
        .bar-label {
            overflow-wrap: anywhere;
            unicode-bidi: plaintext;
            text-align: start;
        }
        .bar-track {
            height: 12px;
            overflow: hidden;
            border-radius: 999px;
            background: #dbe7ea;
        }
        .bar-fill { height: 100%; min-width: 2px; background: var(--brand); }
        .bar-value {
            font-variant-numeric: tabular-nums;
            font-weight: 700;
            text-align: end;
        }
        @media (max-width: 640px) {
            .block-container { padding: 1rem 0.8rem 3rem; }
            .bar-row { grid-template-columns: 1fr 2fr 36px; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def setting(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name, None)
    except FileNotFoundError:
        value = None
    return str(value if value is not None else os.environ.get(name, default))


@st.cache_resource
def get_store(database_path: str) -> ReviewStore:
    return ReviewStore(database_path)


def initialize_session() -> None:
    defaults = {
        "role": None,
        "reviewer_name": "",
        "session_id": str(uuid.uuid4()),
        "fail_mode": False,
        "skipped_question_id": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def render_login(reviewer_password: str, admin_password: str) -> None:
    left, center, right = st.columns([1, 1.3, 1])
    with center:
        st.title("Review Desk")
        st.caption("Secure question and answer quality review")
        with st.container(border=True):
            with st.form("login_form"):
                name = st.text_input(
                    "Reviewer name",
                    help="Required for reviewer access; administrators may leave this blank.",
                )
                password = st.text_input("Password", type="password")
                submitted = st.form_submit_button(
                    "Sign in", type="primary", use_container_width=True
                )
            if submitted:
                try:
                    role = authenticate(password, reviewer_password, admin_password)
                except ConfigurationError as exc:
                    st.error(str(exc))
                    return
                if role is None:
                    st.error("Incorrect password. Check it and try again.")
                elif role == "reviewer" and not name.strip():
                    st.error("Enter your reviewer name before signing in.")
                else:
                    st.session_state.role = role
                    st.session_state.reviewer_name = name.strip() if role == "reviewer" else "Admin"
                    st.rerun()


def render_sidebar() -> None:
    with st.sidebar:
        st.subheader("Review Desk")
        role_label = "Administrator" if st.session_state.role == "admin" else "Reviewer"
        st.caption(role_label)
        st.write(st.session_state.reviewer_name)
        if st.button("Sign out", use_container_width=True):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()


def text_panel(title: str, value: str) -> None:
    with st.container(border=True):
        st.subheader(title)
        st.markdown(directional_text_html(value), unsafe_allow_html=True)


def handle_review_conflict(message: str) -> None:
    st.warning(message)
    st.session_state.fail_mode = False
    st.session_state.skipped_question_id = None


def render_reviewer(store: ReviewStore) -> None:
    reviewer = st.session_state.reviewer_name
    session_id = st.session_state.session_id
    excluded = st.session_state.skipped_question_id
    question = store.claim_question(reviewer, session_id, exclude_question_id=excluded)
    if question is not None:
        st.session_state.skipped_question_id = None

    st.title("Question review")
    st.caption("Read all three sections, then record your decision.")

    if question is None:
        with st.container(border=True):
            st.subheader("No questions are available")
            st.write("All questions are reviewed or currently assigned to another reviewer.")
            if st.button("Check again", type="primary"):
                st.session_state.skipped_question_id = None
                st.rerun()
        return

    st.caption(f"Source item ID: {question.source_id}")
    text_panel("Instruction", question.instruction)
    text_panel("Question", "\n\n".join(question.question_parts))
    text_panel("Output", question.output)

    if st.session_state.fail_mode:
        with st.container(border=True):
            st.subheader("Describe the issue")
            with st.form("failure_form"):
                notes = st.text_area(
                    "Failure notes *",
                    height=140,
                    help="Explain clearly what is wrong and where the issue appears.",
                )
                submit = st.form_submit_button(
                    "Submit Fail & Next", type="primary", use_container_width=True
                )
            cancel = st.button("Cancel", use_container_width=True)
            if submit:
                if not notes.strip():
                    st.error("Failure notes are required.")
                else:
                    try:
                        store.submit_review(
                            question.id, reviewer, session_id, "Fail", notes
                        )
                        st.session_state.fail_mode = False
                        st.toast("Failure recorded. Loading the next question.")
                        st.rerun()
                    except ReviewConflictError as exc:
                        handle_review_conflict(str(exc))
                        st.rerun()
            if cancel:
                st.session_state.fail_mode = False
                st.rerun()
        return

    pass_column, fail_column, skip_column = st.columns(3)
    with pass_column:
        if st.button("Pass & Next", type="primary", use_container_width=True):
            try:
                store.submit_review(question.id, reviewer, session_id, "Pass")
                st.toast("Pass recorded. Loading the next question.")
                st.rerun()
            except ReviewConflictError as exc:
                handle_review_conflict(str(exc))
                st.rerun()
    with fail_column:
        if st.button("Fail", use_container_width=True):
            st.session_state.fail_mode = True
            st.rerun()
    with skip_column:
        if st.button("Skip", use_container_width=True):
            try:
                store.skip_question(question.id, reviewer, session_id)
                st.session_state.skipped_question_id = question.id
                st.toast("Question released to the review pool.")
                st.rerun()
            except ReviewConflictError as exc:
                handle_review_conflict(str(exc))
                st.rerun()


def batch_selector(store: ReviewStore, key: str) -> Optional[int]:
    batches = store.list_batches()
    options = {"All batches": None}
    options.update(
        {
            f"{batch.filename} · {batch.imported_count} imported · Batch {batch.id}": batch.id
            for batch in batches
        }
    )
    selected = st.selectbox("Batch", options=list(options), key=key)
    return options[selected]


def render_upload(store: ReviewStore) -> None:
    st.subheader("Upload questions")
    st.write("Upload a UTF-8 JSON file containing a root `data` array.")
    uploaded = st.file_uploader("JSON file", type=["json"], accept_multiple_files=False)
    if uploaded is None:
        return
    raw = uploaded.getvalue()
    with st.container(border=True):
        st.write(f"**File:** {uploaded.name}")
        st.caption(f"Size: {len(raw):,} bytes")
        try:
            preview = store.preview_upload(raw)
        except InvalidUploadError as exc:
            st.error(str(exc))
            return
        preview_columns = st.columns(2)
        preview_columns[0].metric("Valid records", f"{preview.valid_count:,}")
        preview_columns[1].metric("Records to skip", f"{preview.skipped_count:,}")
        if preview.errors:
            st.dataframe(
                pd.DataFrame([asdict(error) for error in preview.errors]),
                use_container_width=True,
                hide_index=True,
            )
        if st.button("Validate and import", type="primary"):
            try:
                result = store.import_json(uploaded.name, raw)
                st.success(
                    f"Imported {result.imported_count:,} records; "
                    f"skipped {result.skipped_count:,}."
                )
                if result.errors:
                    st.dataframe(
                        pd.DataFrame([asdict(error) for error in result.errors]),
                        use_container_width=True,
                        hide_index=True,
                    )
            except (DuplicateUploadError, InvalidUploadError) as exc:
                st.error(str(exc))


def render_analytics(store: ReviewStore) -> None:
    st.subheader("Progress and activity")
    batch_id = batch_selector(store, "analytics_batch")
    metrics = store.get_analytics(batch_id)
    first_row = st.columns(4)
    for column, label, value in zip(
        first_row,
        ("Total", "Reviewed", "Pending", "Assigned"),
        (metrics.total, metrics.reviewed, metrics.pending, metrics.assigned),
    ):
        column.metric(label, f"{value:,}")
    second_row = st.columns(3)
    second_row[0].metric("Pass", f"{metrics.passed:,}")
    second_row[1].metric("Fail", f"{metrics.failed:,}")
    second_row[2].metric("Pass rate", f"{metrics.pass_rate:.1f}%")
    progress = metrics.reviewed / metrics.total if metrics.total else 0.0
    st.progress(progress, text=f"{progress:.1%} reviewed")

    chart_left, chart_right = st.columns(2)
    with chart_left:
        st.markdown("#### Reviews by reviewer")
        if metrics.by_reviewer:
            st.markdown(
                bar_list_html(
                    metrics.by_reviewer,
                    label_key="reviewer",
                    value_key="reviews",
                    aria_label="Reviews by reviewer",
                ),
                unsafe_allow_html=True,
            )
        else:
            st.info("No completed reviews yet.")
    with chart_right:
        st.markdown("#### Reviews over time")
        if metrics.over_time:
            st.markdown(
                bar_list_html(
                    metrics.over_time,
                    label_key="date",
                    value_key="reviews",
                    aria_label="Reviews over time",
                ),
                unsafe_allow_html=True,
            )
        else:
            st.info("No completed reviews yet.")


def render_review_management(store: ReviewStore) -> None:
    st.subheader("Review management")
    st.warning("Resetting a review returns its question to the pending pool.")
    search = st.text_input("Search reviews", placeholder="Notes, reviewer, source ID, or text")
    batch_id = batch_selector(store, "management_batch")
    reviews = store.list_reviews(search=search, batch_id=batch_id)
    if not reviews:
        st.info("No reviews match the current filters.")
        return

    table = pd.DataFrame(
        [
            {
                "Review ID": review.review_id,
                "Source ID": review.source_id,
                "Decision": review.decision,
                "Reviewer": review.reviewer,
                "Reviewed at (UTC)": review.reviewed_at,
                "Notes": review.notes,
            }
            for review in reviews
        ]
    )
    st.dataframe(table, use_container_width=True, hide_index=True)
    labels = {
        f"Review {review.review_id} · {review.decision} · Source {review.source_id} · {review.reviewer}": review
        for review in reviews
    }
    selected_label = st.selectbox("Review to inspect", list(labels))
    selected = labels[selected_label]
    with st.expander("Review details", expanded=True):
        st.markdown("**Instruction**")
        st.markdown(directional_text_html(selected.instruction), unsafe_allow_html=True)
        st.markdown("**Question**")
        st.markdown(directional_text_html(selected.question), unsafe_allow_html=True)
        st.markdown("**Output**")
        st.markdown(directional_text_html(selected.output), unsafe_allow_html=True)
        st.markdown("**Notes**")
        st.markdown(directional_text_html(selected.notes), unsafe_allow_html=True)
    confirmed = st.checkbox("I understand this will return the question to the pending pool.")
    if st.button("Reset selected review", disabled=not confirmed):
        try:
            store.reset_review(selected.review_id, actor="admin")
            st.success("Review reset. The question is pending again.")
            st.rerun()
        except ReviewConflictError as exc:
            st.error(str(exc))


def render_export(store: ReviewStore) -> None:
    st.subheader("Export reviewed data")
    rows = store.get_export_rows()
    st.metric("Completed reviews", f"{len(rows):,}")
    st.write(
        "The workbook contains exactly: instruction, question, output, pass/fail, and notes."
    )
    if not rows:
        st.info("Complete at least one review before exporting.")
        return
    if st.button("Prepare Excel file", type="primary"):
        st.session_state.export_workbook = create_excel_export(rows)
        st.session_state.export_count = len(rows)
    if st.session_state.get("export_workbook") is not None:
        st.caption(
            f"Prepared workbook contains {st.session_state.export_count:,} completed reviews."
        )
        st.download_button(
            "Download reviewed_data.xlsx",
            data=st.session_state.export_workbook,
            file_name="reviewed_data.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def render_admin(store: ReviewStore) -> None:
    st.title("Administration")
    st.caption("Import data, monitor progress, manage reviews, and export results.")
    upload_tab, analytics_tab, management_tab, export_tab = st.tabs(
        ["Upload", "Analytics", "Review management", "Export"]
    )
    with upload_tab:
        render_upload(store)
    with analytics_tab:
        render_analytics(store)
    with management_tab:
        render_review_management(store)
    with export_tab:
        render_export(store)


def main() -> None:
    apply_styles()
    initialize_session()
    reviewer_password = setting("REVIEWER_PASSWORD")
    admin_password = setting("ADMIN_PASSWORD")
    database_path = setting("DATABASE_PATH", str(Path("data") / "reviews.db"))

    if not reviewer_password or not admin_password:
        st.error(
            "Application passwords are not configured. Set REVIEWER_PASSWORD and "
            "ADMIN_PASSWORD in Streamlit secrets or environment variables."
        )
        st.stop()
    if reviewer_password == admin_password:
        st.error("Reviewer and admin passwords must be different.")
        st.stop()

    if st.session_state.role is None:
        render_login(reviewer_password, admin_password)
        return

    store = get_store(database_path)
    render_sidebar()
    if st.session_state.role == "admin":
        render_admin(store)
    else:
        render_reviewer(store)


if __name__ == "__main__":
    main()
