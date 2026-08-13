# Review Desk

> The Cloudflare-native React + FastAPI + D1 replacement is deployed and serving at
> **https://review-desk-api.entropy-data-review.workers.dev** — see
> [`cloudflare_app/`](cloudflare_app/README.md). The Streamlit implementation below
> remains available during deployment validation. The two do not share a database:
> reviews recorded in `data/reviews.db` are not visible in the Cloudflare app.

A password-protected Streamlit platform for concurrent review of JSON question-and-answer datasets. Reviewers receive exclusive 30-minute assignments, record Pass/Fail decisions, and provide mandatory notes for failures. Administrators can import batches, monitor progress, reset reviews, and export completed work to Excel.

## JSON format

The uploaded file must contain a root `data` array. Extra fields are ignored. Valid records have this shape:

```json
{
  "data": [
    {
      "id": 1,
      "instruction": "Answer accurately.",
      "input": ["Question text"],
      "output": "Proposed answer"
    }
  ]
}
```

`id` must be an integer, `instruction` and `output` must be strings, and `input` must be either a string or an array of strings. A single string is stored as a one-part question, so `"input": "Question text"` and `"input": ["Question text"]` are equivalent. Invalid records are skipped and reported. An exact file cannot be uploaded twice, but IDs may repeat across different files.

## Local setup

Python 3.11 is the supported deployment version. Python 3.14 is currently excluded
because Streamlit 1.49's Altair dependency is incompatible with it.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
streamlit run app.py
```

Edit `.streamlit/secrets.toml` before starting. The reviewer and admin passwords must be present, non-empty, and different. The secrets file and SQLite database are excluded from Git.

Alternatively, set these environment variables:

```bash
export REVIEWER_PASSWORD='strong-reviewer-password'
export ADMIN_PASSWORD='different-strong-admin-password'
export DATABASE_PATH='data/reviews.db'
streamlit run app.py
```

Open `http://localhost:8501`. Run exactly one Streamlit process and keep the `data` directory on persistent storage.

## Private-server deployment with Docker

```bash
export REVIEWER_PASSWORD='strong-reviewer-password'
export ADMIN_PASSWORD='different-strong-admin-password'
docker compose up --build -d
```

The compose configuration runs one app instance and stores SQLite files in the local `data` directory. Put the service behind HTTPS using the private server's reverse proxy or VPN. Do not increase the replica count; a multi-instance deployment should migrate persistence to Postgres first.

## Verification

```bash
pytest -q
```

The suite covers partial imports, duplicate files, concurrent claims, Pass/Fail rules, skips, lease renewal and expiry, analytics, admin reset, export contents, authentication, and a Streamlit login flow.

## Operational notes

- Assignment leases last 30 minutes and renew when the reviewer interacts with the app.
- Completed reviews cannot be changed by reviewers. Admin resets are preserved in the audit log.
- Excel exports include completed reviews only and exactly five columns: `instruction`, `question`, `output`, `pass/fail`, and `notes`.
- Back up the persistent `data/reviews.db` file according to the server's normal backup policy.
