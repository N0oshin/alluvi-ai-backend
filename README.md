# Alluvi — Backend

FastAPI + PostgreSQL backend for the Alluvi AI calorie-tracking app.

The wire contract is dictated by the existing Flutter client — see
[`../frontend/BACKEND.md`](../frontend/BACKEND.md), which documents the client's
hard constraints and the design decisions behind this API. Read it before
changing any response shape.

| | |
|---|---|
| **Stack** | Python 3.14 · FastAPI · SQLAlchemy 2 (async) · Alembic |
| **Database** | PostgreSQL 17 (psycopg 3) · pgAdmin |
| **Food analysis** | Pluggable — `stub` (offline, default) or `claude` (Anthropic vision) |
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

**Start the server with `run.py`, not the `uvicorn` CLI.** On Windows uvicorn
hard-codes `ProactorEventLoop` for single-process mode, and psycopg's async
driver cannot run on it (`InterfaceError: Psycopg cannot use the
'ProactorEventLoop'`). `run.py` selects a compatible loop; the reasoning is in
its module docstring.

- API docs: <http://localhost:8000/docs>
- Health: <http://localhost:8000/health>
- pgAdmin: <http://localhost:5050> — `nooshin.nexus@gmail.com` / `admin`. The
  Alluvi server is pre-registered; the database password is `alluviai123`.

### No Docker?

```bash
DATABASE_URL="sqlite+aiosqlite:///./dev.db" ./.venv/Scripts/python.exe -m alembic upgrade head
DATABASE_URL="sqlite+aiosqlite:///./dev.db" ./.venv/Scripts/python.exe run.py
```

Migrations run on both backends (Alembic uses batch mode on SQLite).

### Tests

```bash
./.venv/Scripts/python.exe -m pytest
```

17 tests covering the error envelope, token rotation and reuse detection, the
plan calculation, the scan → save → dashboard flow, and Health-sync
idempotency. They run against in-memory SQLite with the `stub` analyser, so no
database, network, or API key is needed.

---

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

**Cost:** `claude-opus-5` is $5 / $25 per million input / output tokens. A meal
photo is roughly 1.5k input tokens plus a few hundred output. Use `stub` for
development and tests.

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
tests/            end-to-end API tests
```

---

## Endpoints

### Auth
| Method | Path | |
|---|---|---|
| `POST` | `Auth/SignUp` | Creates the account and emails a 6-digit code. No session yet. |
| `POST` | `Auth/login` | Email + password → token pair |
| `POST` | `Auth/verifyCode` | 6-digit email OTP → token pair |
| `POST` | `Auth/resendCode` | Rate-limited resend |
| `POST` | `Auth/forgotPassword` | Emails a reset **link** |
| `POST` | `Auth/resetPassword` | Completes the reset; revokes all sessions |
| `POST` | `Auth/apple` · `Auth/google` | **Not implemented** — see below |
| `POST` | `Auth/refresh` | Rotates the refresh token |
| `POST` | `Auth/logout` | Revokes one refresh token |
| `DELETE` | `Auth/account` | Hard delete + photo purge |

### Everything else
`userinfo/plan` · `food/analyze` · `meals` · `home/summary` · `home/week` ·
`favorites` · `analytics` · `profile/*` · `legal/*` ·
`UserNotification/add{Anonymous,User}Token`

Full schemas at `/docs`.

---

## Things worth knowing before you change something

**Every error body must be `{"detail": "<string>"}`.** The Flutter client reads
`e.response?.data['detail']` unguarded into a non-nullable `String`. A list, a
bare string, or an HTML error page makes the client crash inside its own error
handler. `app/core/errors.py` overrides FastAPI's validation handler for exactly
this reason — its default 422 body is a *list*. There is a test for it.

**`detail` is shown to users verbatim** and is localised here from the `langCode`
header (`1` = Arabic, `2` = English), because the client does not localise these
messages itself. New user-facing messages go in `app/core/i18n.py`, in both
languages.

**Never return `403` for an ordinary authorization failure.** The client's
interceptor wipes the session on both `401` and `403`. Ownership failures
return `404`; "email not verified" returns `409`. Only genuine
authentication failures use `401`.

**Push-token endpoints return a bare `true`.** `DeviceTokenManager` only records
success when the body is literally `true`, so those two routes deliberately
break the envelope convention.

**The database transaction is committed by middleware, not by a `yield`
dependency.** FastAPI runs dependency teardown *after* the response is sent, so
a commit there races the client's next request — a client that writes then
immediately reads can miss its own write. That race silently defeated
refresh-token reuse detection during testing (the second request read the token
before the first request's "consumed" flag landed). If you move the commit back
into `get_db`, you reintroduce it.

**Macros on the dashboard are what's *left*, not what was consumed.** The design
labels them "Protein left" / "Carbs left" / "Fat left".

**Quantity is applied once, at save.** `food/analyze` returns per-serving
values; the client's stepper multiplies for display and `POST /meals` persists
the totals. Pre-multiplying anywhere would double-count.

**"Fix Results" is a recapture, not a correction.** It discards the analysis and
re-opens the camera. There is deliberately no correction endpoint and no
feedback loop.

**Photos are downscaled and re-encoded on upload**, which strips EXIF — phone
photos carry GPS and meal photos are health data. Deleting a meal deletes its
object; deleting an account purges the user's whole prefix.

**Apple Health ingest is idempotent on the HealthKit sample UUID.** Without
that, every re-sync double-counts. Weight entries also record provenance
(`manual` / `apple_health` / `onboarding`).

**A user-edited plan is flagged `is_override`** and automatic recalculation
leaves it alone, so a weight change never silently discards what the user typed
on Adjust Goals.

---

## Not implemented

- **Apple / Google sign-in** — `Auth/apple` and `Auth/google` return `501`. The
  identity token must be verified against the provider's JWKS (signature,
  `iss`, `aud`, `exp`) before a session is issued; accepting an unverified
  token would let anyone sign in as anyone. The endpoints refuse rather than
  trust.
- **Email delivery** — OTP codes and reset links are written to the application
  log. Wire up a provider in `app/api/v1/auth.py` (marked `TODO`).
- **Object storage** — photos go to local disk under `MEDIA_ROOT`. Implement
  `StorageBackend` for S3/GCS and serve time-limited pre-signed URLs.
- **Achievement unlocking** — the catalogue is seeded and the endpoint reports
  progress, but nothing awards badges yet.
- **Deferred by product decision** — subscriptions/billing, referral rewards,
  PDF export, Family Plan. `isPremium` is returned but never set.

## Client-side work this API expects

The backend is ready for these; the Flutter app is not yet.

1. **Store the refresh token** and add an interceptor that, on `401`, refreshes
   once and replays the original request — queueing concurrent requests so only
   one refresh fires, and logging out only when the refresh itself fails. The
   app currently treats any `401` as permanent logout, so without this the first
   token expiry (15 minutes) signs every user out for good. This is the largest
   client change in the integration.
2. Point `_setupApiLayer()` at this API instead of JSONPlaceholder, and change
   `ApiConstants.baseUrl` off `api.lajolie-eg.com`.
3. Build the Delete Account screen (designed, never implemented).
