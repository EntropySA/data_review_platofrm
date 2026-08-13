# Cloudflare deployment

This is the Cloudflare-native replacement for the Streamlit application:

- `frontend/`: React/Vite static site for Cloudflare Pages.
- `worker/`: FastAPI Python Worker with D1 persistence.
- Excel files are generated in the admin's browser, avoiding Worker CPU usage.
- JSON files are validated in the browser and uploaded in chunks of at most 200 records.

The original Streamlit application remains available at the repository root until this version is deployed and accepted.

## Free-tier limits

As of August 13, 2026, the Workers Free plan provides 100,000 Worker requests/day and 10 ms CPU/request. D1 provides 5 million rows read/day, 100,000 rows written/day, and 5 GB total storage. Static assets are free and unlimited. Free-plan requests fail after a quota is reached rather than creating usage charges.

These limits are appropriate for the expected 20 reviewers, provided indexed queries and chunked imports are retained.

## Local development

Backend:

```bash
cd cloudflare_app/worker
cp .dev.vars.example .dev.vars
uv run pywrangler sync
uv run pywrangler d1 migrations apply review-desk --local
uv run pywrangler dev --port 8787
```

Frontend, in a second terminal:

```bash
cd cloudflare_app/frontend
cp .env.example .env
npm install
npm run dev
```

Open `http://localhost:5173`.

## First Cloudflare deployment

Authenticate Wrangler and create D1:

```bash
npx wrangler login
cd cloudflare_app/worker
uv run pywrangler d1 create review-desk
```

Copy the returned database ID into `worker/wrangler.jsonc`, replacing `REPLACE_WITH_D1_DATABASE_ID`, then apply the schema:

```bash
uv run pywrangler d1 migrations apply review-desk --remote
```

Create production secrets. Use distinct passwords and a random session secret of at least 32 characters:

```bash
uv run pywrangler secret put REVIEWER_PASSWORD
uv run pywrangler secret put ADMIN_PASSWORD
uv run pywrangler secret put SESSION_SECRET
uv run pywrangler deploy
```

Copy the resulting Worker URL. Deploy the frontend:

```bash
cd ../frontend
VITE_API_URL='https://YOUR-WORKER.workers.dev' npm run build
npx wrangler pages deploy dist --project-name review-desk
```

Copy the resulting Pages URL into `FRONTEND_ORIGIN` in `worker/wrangler.jsonc`, then redeploy the Worker. This restricts browser API access to the deployed frontend.

## Verification

```bash
PYTHONPATH=. uv run --project cloudflare_app/worker pytest cloudflare_app/worker/tests -q
cd cloudflare_app/frontend && npm test && npm run build && npm audit
```

Do not commit `.dev.vars`, `.env`, D1 local state, generated Worker packages, `node_modules`, or build output.
