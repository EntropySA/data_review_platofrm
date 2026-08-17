# Cloudflare deployment

**Live:** https://review-desk-api.entropy-data-review.workers.dev

| Resource | Value |
| --- | --- |
| Worker | `review-desk-api` |
| D1 database | `review-desk` (`88d8d480-f2b6-430b-80fa-30d20968f376`, WEUR) |
| Secrets | `REVIEWER_PASSWORD`, `ADMIN_PASSWORD`, `SESSION_SECRET` |

This is the Cloudflare-native replacement for the Streamlit application. A single
Worker serves both halves from one origin:

- `frontend/`: React/Vite single-page app, uploaded as the Worker's static assets.
- `worker/`: FastAPI Python Worker with D1 persistence, invoked only for `/api/*`.

Requests for the interface are served straight from Cloudflare's asset storage
without starting the Worker, so they cost nothing and consume no CPU time. Because
both halves share an origin, the browser makes same-origin calls and no CORS grant
is issued in production.
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
npm install
npm run dev
```

Open `http://localhost:5173`. The dev server proxies `/api` to the Worker on
port 8787, so development is same-origin exactly like production and no `.env`
file is needed.

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
```

Build the interface, then deploy both halves together. The build must run first,
because `wrangler.jsonc` uploads `frontend/dist` as the Worker's static assets:

```bash
cd ../frontend && npm install && npm run build
cd ../worker && uv run pywrangler deploy
```

The printed `*.workers.dev` URL serves the interface and the API. Do not set
`VITE_API_URL` when building for production; `frontend/.env.production` keeps it
empty so the app calls `/api/*` on whichever origin served it, which also lets a
custom domain work without a rebuild.

## Redeploying

Rebuild the frontend whenever it changes, then deploy:

```bash
cd cloudflare_app/frontend && npm run build
cd ../worker && uv run pywrangler deploy
```

## Verification

```bash
PYTHONPATH=. uv run --project cloudflare_app/worker pytest cloudflare_app/worker/tests -q
cd cloudflare_app/frontend && npm test && npm run build && npm audit
```

Do not commit `.dev.vars`, `.env`, D1 local state, generated Worker packages, `node_modules`, or build output.
