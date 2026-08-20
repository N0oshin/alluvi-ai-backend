# AlluviAI — Backend

FastAPI + PostgreSQL backend for the Alluvi AI calorie-tracking app.

The wire contract is dictated by the existing Flutter client 

| | |
|---|---|
| **Stack** | Python 3.14 · FastAPI · SQLAlchemy 2 (async) · Alembic |
| **Database** | PostgreSQL 17 (psycopg 3) · pgAdmin |
| **Food analysis** | Pluggable - `stub` (offline, default) or `claude` (Anthropic vision) |
| **Auth** | 15-min access JWT + 30-day rotating refresh token |
| **Base path** | `/api` |

---

## Quick start

```bash
cd backend
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt  # macOS/Linux

cp .env.example .env

docker compose up -d                  # Postgres :5432 + pgAdmin :5050
./.venv/Scripts/python.exe -m alembic upgrade head
./.venv/Scripts/python.exe -m app.db.seed
./.venv/Scripts/python.exe run.py     # http://127.0.0.1:8000
```



- API docs: <http://localhost:8000/docs>
- Health: <http://localhost:8000/health>
- pgAdmin: <http://localhost:5050>

### No Docker?

```bash
DATABASE_URL="sqlite+aiosqlite:///./dev.db" ./.venv/Scripts/python.exe -m alembic upgrade head
DATABASE_URL="sqlite+aiosqlite:///./dev.db" ./.venv/Scripts/python.exe run.py
```

Migrations run on both backends (Alembic uses batch mode on SQLite).

### Supabase (hosted Postgres)

Point `DATABASE_URL` at the project's **session pooler** and run the same
`alembic upgrade head` / `app.db.seed` pair as above. Three things bite here:

| Trap | Fix |
| --- | --- |
| `db.<ref>.supabase.co` is IPv6-only, so it fails with `getaddrinfo failed` on IPv4-only networks | use `aws-0-<region>.pooler.supabase.com:5432` (Connection string -> Session pooler in the dashboard) |
| the pooler rejects the plain `postgres` user | the user is `postgres.<project-ref>` |
| Supabase's copy button gives a bare `postgresql://` URL | SQLAlchemy needs the driver tag: `postgresql+psycopg://` |

```
DATABASE_URL=postgresql+psycopg://postgres.<ref>:<pw>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
```

Revision `614f47603235` closes the `public` schema to the `anon` and
`authenticated` roles. Supabase otherwise publishes every table in `public`
over HTTP via PostgREST with CRUD granted to `anon` -- and the anon key ships
inside the Flutter client, so without it `users`, `refresh_tokens` and
`otp_codes` are readable by anyone holding it. Authorization for this app
lives in FastAPI, which connects as the table owner `postgres` and therefore
bypasses RLS, so the lockdown costs the backend nothing. The revision no-ops
on local Postgres and SQLite, where those roles do not exist.


## Food analysis providers

Swap with one env var. The API contract is identical either way.

| `VISION_PROVIDER` | Needs a key | Notes |
|---|---|---|
| `stub` *(default)* | No | Deterministic offline analyser. Same photo always yields the same result — this is what makes the food pipeline testable. |
| `claude` | `ANTHROPIC_API_KEY` | Anthropic vision on `claude-opus-5`. |

```env
VISION_PROVIDER=claude
ANTHROPIC_API_KEY=sk-ant-...
VISION_MODEL=claude-opus-5
VISION_EFFORT=medium     # low | medium | high | xhigh | max
```

The Claude provider sends the photo as a base64 image block and constrains the
reply with `output_config.format`, so the model returns schema-valid JSON
rather than prose. `effort` is the cost/latency lever; thinking is left at the
model default. Adding a provider means implementing `VisionProvider` in
`app/services/vision/` and adding one branch to the factory.

---

## Layout

```text
app/
├── core/         config, security (tokens/hashing), errors, i18n, deps
├── db/           SQLAlchemy models, session, seed data
├── schemas/      Pydantic request/response models (camelCase aliases)
├── api/v1/       auth · userinfo (plan) · food · profile · analytics · notifications
└── services/
    ├── vision/   base · stub · claude · factory
    ├── storage/  base · local (image pipeline)
    └── plan.py   BMR / macro / goal-date calculation
alembic/          migrations

```

---

## Endpoints

Refer API_LISTING.md

---

## Things worth knowing before you change something

**Every error body must be `{"detail": "<string>"}`.** The Flutter client reads
`e.response?.data['detail']` unguarded into a non-nullable `String`. A list, a
bare string, or an HTML error page makes the client crash inside its own error
handler. 

**Never return `403` for an ordinary authorization failure.** The client's
interceptor wipes the session on both `401` and `403`. Ownership failures
return `404`; "email not verified" returns `409`. Only genuine
authentication failures use `401`.

**Push-token endpoints return a bare `true`.** `DeviceTokenManager` only records
success when the body is literally `true`, so those two routes deliberately
break the envelope convention.

The database transaction is committed by middleware, not by a `yield`
dependency.

**Quantity is applied once, at save.** `food/analyze` returns per-serving
values; the client's stepper multiplies for display and `POST /meals` persists
the totals. Pre-multiplying anywhere would double-count.

**"Fix Results" is a recapture, not a correction.** 


**Apple Health ingest is idempotent on the HealthKit sample UUID.** Without
that, every re-sync double-counts. Weight entries also record provenance
(`manual` / `apple_health` / `onboarding`).

---

## Not implemented

- **Google sign-in** — verification is implemented, but `Auth/google` answers
  `501` until `GOOGLE_CLIENT_IDS` is set. The token's `aud` is checked against
  that list, so with no configured audience there is nothing to check it
  against; the endpoint refuses rather than trusts. Apple is configured
  (`APPLE_BUNDLE_IDS=com.alluvi.alluvi`) and live.
- **Object storage** — photos go to local disk under `MEDIA_ROOT`. Implement
  `StorageBackend` for S3/GCS and serve time-limited pre-signed URLs.
- **Achievement unlocking** — the catalogue is seeded and the endpoint reports
  progress, but nothing awards badges yet.
- **Deferred by product decision** — subscriptions/billing, referral rewards,
  PDF export, Family Plan. `isPremium` is returned but never set.


