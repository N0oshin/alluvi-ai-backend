# Building the Alluvi Backend — A Complete Walkthrough

This document explains the backend we built, in the order you would build it,
assuming you have never used Docker and have not written a Python API before.

Read it top to bottom once. Every concept is introduced before it is used, and
the last section ties the whole thing together into a single request's journey.

---

## Table of contents

1. [What we are actually building](#1-what-we-are-actually-building)
2. [The vocabulary you need first](#2-the-vocabulary-you-need-first)
3. [Docker, explained properly](#3-docker-explained-properly)
4. [The shape of the project](#4-the-shape-of-the-project)
5. [Phase 1 — Configuration](#phase-1--configuration)
6. [Phase 2 — The database layer](#phase-2--the-database-layer)
7. [Phase 3 — Migrations](#phase-3--migrations)
8. [Phase 4 — Errors and language](#phase-4--errors-and-language)
9. [Phase 5 — Security and tokens](#phase-5--security-and-tokens)
10. [Phase 6 — Schemas](#phase-6--schemas)
11. [Phase 7 — Services](#phase-7--services)
12. [Phase 8 — Dependencies](#phase-8--dependencies)
13. [Phase 9 — The routers](#phase-9--the-routers)
14. [Phase 10 — Wiring it together](#phase-10--wiring-it-together)
15. [Phase 11 — Tests](#phase-11--tests)
16. [Every file, in one table](#every-file-in-one-table)
17. [How it all connects](#how-it-all-connects)
18. [The full picture: one request end to end](#the-full-picture-one-request-end-to-end)
19. [Adding a new endpoint yourself](#adding-a-new-endpoint-yourself)
20. [Things that will bite you](#things-that-will-bite-you)

---

## 1. What we are actually building

You already have a **Flutter mobile app** (the `frontend/` folder). It has
screens, buttons, and beautiful UI — but no data. Every list of meals, every
calorie number, every login form is currently faked with hardcoded values
inside the app.

A **backend** is a program running on a computer somewhere else (a server) that:

- **stores** things permanently (users, meals, weights),
- **computes** things the phone shouldn't (your daily calorie plan, AI food
  analysis),
- **shares** things between devices (log in on a new phone, your meals are
  still there).

The phone and the backend talk over the internet using **HTTP** — the same
protocol your browser uses. The phone sends a request like:

```
POST /api/meals
Authorization: Bearer eyJhbGc...
{ "analysisId": "08099f52-...", "quantity": 2 }
```

and the backend sends back:

```
201 Created
{ "id": "1369db71-...", "title": "Garden Power Bowl", "calories": 922, ... }
```

That is the entire job. Everything below is detail about doing it reliably.

### The one rule that shaped every decision

The app already exists. **We cannot change how it talks** without shipping a new
version to the App Store. So the backend must speak exactly the language the app
already expects. That constraint is documented in
[`../frontend/BACKEND.md`](../frontend/BACKEND.md), and it is why some choices
below look odd (like two endpoints that return a bare `true`).

When you build a backend for an app that doesn't exist yet, you get to choose.
Here, we didn't.

---

## 2. The vocabulary you need first

Skim these. You don't need to memorise them; they'll make sense as you see them
used.

| Term | What it means, plainly |
|---|---|
| **API** | The set of URLs your backend answers, and what each one expects and returns. "The API" ≈ "the menu of things the app can ask for." |
| **Endpoint** | One item on that menu. `POST /api/meals` is an endpoint. |
| **HTTP method** | The verb. `GET` = read, `POST` = create, `PUT`/`PATCH` = update, `DELETE` = delete. |
| **Status code** | A number saying how it went. `200` OK, `201` created, `401` not logged in, `404` not found, `500` we crashed. |
| **JSON** | The text format both sides use: `{"name": "Salad", "calories": 420}`. |
| **FastAPI** | The Python library that turns your functions into endpoints. You write a function, add a decorator, and it becomes a URL. |
| **Database** | A program that stores data on disk in tables (like very strict spreadsheets) and lets you query it fast. We use **PostgreSQL**. |
| **Table / row / column** | A table is `users`. A row is one user. A column is one field, like `email`. |
| **SQL** | The language databases speak: `SELECT * FROM users WHERE email = '...'`. |
| **ORM** | A translator so you write Python instead of SQL. We use **SQLAlchemy**. You write `select(User).where(User.email == x)` and it generates the SQL. |
| **Model** | A Python class that maps to a database table. `class User` ⇄ the `users` table. |
| **Schema** | A Python class describing the *shape of JSON* going in or out. Different from a model — see [Phase 6](#phase-6--schemas). |
| **Migration** | A recorded change to the database's structure ("add a column"). Lets you evolve the DB without wiping it. We use **Alembic**. |
| **Async** | Python code that can pause while waiting (for the database, for the network) and let other requests run meanwhile. That's what `async def` and `await` mean. It's why one server can handle many users at once. |
| **Dependency injection** | FastAPI hands your function things it needs (the database, the logged-in user) automatically. You declare what you want; FastAPI supplies it. |
| **Environment variable** | A setting that lives outside your code — passwords, URLs. Kept in a `.env` file so secrets never get committed to git. |

---

## 3. Docker, explained properly

You said you're new to Docker, so this section is longer. It's worth it.

### The problem Docker solves

Our backend needs PostgreSQL. Without Docker you would:

1. Download a PostgreSQL installer for Windows.
2. Run it, pick a port, set a superuser password, hope nothing else uses port 5432.
3. Find the right menu to create a database and a user.
4. Do all of that again on your colleague's Mac, differently.
5. Discover the production server has PostgreSQL 14 while you installed 17, and
   something behaves differently.

Docker replaces all of that with one file and one command.

### The mental model

Think of Docker as **a very lightweight, disposable computer inside your
computer**.

- An **image** is a *recipe* — a frozen snapshot of a program and everything it
  needs to run. `postgres:17-alpine` is an official image: "PostgreSQL version
  17, on a tiny Linux". You never build it; you download it.
- A **container** is a *running copy* of an image. Like an app window: you can
  start it, stop it, throw it away, start a fresh one. If you delete a
  container, your computer is untouched — nothing was ever "installed".
- A **volume** is *storage that survives* the container. Containers are
  disposable; volumes are not. Your actual database rows live in a volume, so
  you can destroy and recreate the PostgreSQL container without losing data.
- A **port mapping** is a *doorway* from your computer into the container.
  `"5432:5432"` means "traffic to port 5432 on my machine goes to port 5432
  inside the container." Without it, the container is sealed off.
- **Docker Compose** is a way to describe *several* containers and how they
  relate, in one YAML file, started with one command.

### Our `docker-compose.yml`, line by line

```yaml
services:                            # the list of containers we want
  postgres:                          # name #1 — the database
    image: postgres:17-alpine        # recipe to download
    container_name: alluvi-postgres  # a friendly name for docker commands
    restart: unless-stopped          # if it crashes, start it again
    environment:                     # settings the image reads on first boot
      POSTGRES_USER: alluviai        # creates this DB user
      POSTGRES_PASSWORD: alluviai123 # with this password
      POSTGRES_DB: alluviai          # and this empty database
    ports:
      - "5432:5432"                  # doorway: my 5432 -> container 5432
    volumes:
      - postgres_data:/var/lib/postgresql/data   # keep the data on a volume
    healthcheck:                     # how Docker knows it's actually ready
      test: ["CMD-SHELL", "pg_isready -U alluviai -d alluviai"]
      interval: 5s
```

The healthcheck matters more than it looks. A container can be "started" but
PostgreSQL inside it still be booting. `pgadmin` below waits for
`condition: service_healthy`, so it never tries to connect too early.

```yaml
  pgadmin:                           # name #2 — a web UI for the database
    image: dpage/pgadmin4:latest
    environment:
      PGADMIN_DEFAULT_EMAIL: nooshin.nexus@gmail.com
      PGADMIN_DEFAULT_PASSWORD: admin
      PGADMIN_CONFIG_MASTER_PASSWORD_REQUIRED: "False"
    ports:
      - "5050:80"                    # my 5050 -> container's 80 (a web server)
    depends_on:
      postgres:
        condition: service_healthy   # wait for the DB to be ready
    volumes:
      - ./docker/pgadmin-servers.json:/pgadmin4/servers.json:ro

volumes:
  postgres_data:                     # declare the named volumes
  pgadmin_data:
```

**pgAdmin** is just a website that talks to PostgreSQL, so you can click through
your tables instead of typing SQL. `docker/pgadmin-servers.json` pre-registers
the connection so it opens already pointed at our database.

Notice `Host: postgres` in that JSON — not `localhost`. **Inside** the Docker
network, containers reach each other by their service name. From *your* machine
it's `localhost:5432`; from *inside pgAdmin's container* it's `postgres:5432`.
This trips up everybody once.

### The commands you'll actually use

```bash
docker compose up -d      # start everything, -d = in the background
docker compose ps         # what's running?
docker compose logs -f pgadmin   # watch a container's output (-f = follow)
docker compose stop       # stop, keep the data
docker compose down       # stop and remove containers (volumes survive)
docker compose down -v    # ...and delete the volumes too. Wipes the database.
```

If something misbehaves, `docker compose logs <name>` is almost always the
answer. That's exactly how we found that pgAdmin was crash-looping because it
rejects `.local` email addresses as a reserved domain.

### What is *not* in Docker here

Our Python app runs directly on your machine, not in a container. That's a
deliberate choice for development: you get instant reloads and easy debugging.
In production you would usually containerise the app too. The database being in
Docker is the part that saves you the most pain.

---

## 4. The shape of the project

Before the phases, here is the layout, so you know where things land:

```
backend/
├── docker-compose.yml     Database + pgAdmin definition
├── requirements.txt       Python libraries we depend on
├── .env.example           Template for settings (copy to .env)
├── alembic.ini            Migration tool config
├── run.py                 Starts the dev server
├── pytest.ini             Test config
│
├── alembic/               Database migrations
│   ├── env.py             Teaches Alembic about our models
│   └── versions/          One file per schema change
│
├── app/
│   ├── main.py            The application object; wires everything together
│   │
│   ├── core/              Cross-cutting concerns (used by everything)
│   │   ├── config.py      Settings
│   │   ├── errors.py      Error format
│   │   ├── i18n.py        English/Arabic messages
│   │   ├── security.py    Passwords, tokens
│   │   ├── timeutil.py    Timezone helper
│   │   └── deps.py        "give me the DB / the current user"
│   │
│   ├── db/                Everything about the database
│   │   ├── base.py        Shared model foundations
│   │   ├── models.py      The tables
│   │   ├── session.py     Connecting to the DB
│   │   └── seed.py        Initial reference data
│   │
│   ├── schemas/           JSON shapes in and out
│   │   ├── common.py      camelCase base class
│   │   ├── auth.py        Login/signup bodies
│   │   ├── food.py        Meals, analysis, analytics
│   │   └── profile.py     Profile, plan, settings
│   │
│   ├── api/v1/            The endpoints themselves
│   │   ├── router.py      Collects all routers
│   │   ├── auth.py        Signup, login, tokens
│   │   ├── userinfo.py    Onboarding + plan
│   │   ├── food.py        Scan, meals, dashboard
│   │   ├── profile.py     Profile, weight, settings, legal
│   │   ├── analytics.py   Charts screen
│   │   └── notifications.py  Push tokens
│   │
│   └── services/          Business logic, no HTTP knowledge
│       ├── plan.py        Calorie/macro maths
│       ├── vision/        Food photo analysis
│       └── storage/       Photo files
│
└── tests/                 Automated checks
```

**The layering rule:** things point *downward* only.

```
api/  →  services/  →  db/
  ↘         ↘          ↙
      core/  (everyone may use)
```

A router may call a service; a service must never import a router. This is what
keeps the code understandable as it grows: you can read `services/plan.py`
without knowing anything about HTTP.

---

## Phase 1 — Configuration

**Why first:** everything else needs settings, and settings should never be
hardcoded. Your database password must not live in git.

### `app/core/config.py`

```python
class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+psycopg://alluviai:alluviai123@localhost:5432/alluviai"
    JWT_SECRET: str = "change-me-in-production"
    VISION_PROVIDER: Literal["stub", "claude"] = "stub"
    ...

settings = get_settings()
```

`BaseSettings` (from pydantic-settings) does something clever: for each field it
looks for an environment variable of the same name, then in the `.env` file, and
falls back to the default written here. It also **validates types** — if
`ACCESS_TOKEN_TTL_MINUTES` is set to `"abc"`, the app refuses to start instead of
misbehaving later.

Read the `DATABASE_URL` as a sentence:

```
postgresql + psycopg :// alluviai : alluviai123 @ localhost : 5432 / alluviai
└─ database ┘ └driver┘      user      password      host      port   db name
```

The **driver** is the Python library that speaks the database's wire protocol.
We use `psycopg` (version 3) rather than the more common `asyncpg` — see
[Things that will bite you](#things-that-will-bite-you).

`.env.example` is the template committed to git; `.env` is your real one and is
git-ignored. Copy it: `cp .env.example .env`.

---

## Phase 2 — The database layer

### `app/db/base.py` — shared foundations

Every table wants an `id`, a `created_at`, and an `updated_at`. Rather than
repeating them, we define mixins:

```python
class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

class UUIDPrimaryKey:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

class Timestamps:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(onupdate=func.now())
```

`Base` is SQLAlchemy's registry — every model inherits from it, and that's how
Alembic later discovers all your tables.

**Why UUIDs and not `1, 2, 3`?** Sequential IDs leak information (`/meals/5`
tells an attacker there are at least 5 meals, and invites them to try `/meals/6`)
and they collide when merging data. A UUID like
`1369db71-b221-43f9-91d1-a5f99d27f42c` is unguessable.

**Why a naming convention?** Without it, the database invents names for indexes
and constraints, and those names differ between PostgreSQL and SQLite. Migrations
then become unpredictable. Pinning the convention keeps them stable.

### `app/db/models.py` — the tables

This is the heart of the backend. Everything else serves these shapes.

A model reads like a table definition:

```python
class User(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), default=None)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    ...
    meals: Mapped[list[Meal]] = relationship(back_populates="user",
                                             cascade="all, delete-orphan")
```

Things worth understanding here:

- `str | None` means the column allows `NULL`. `password_hash` is nullable
  because a user who signed in with Apple never set a password.
- `unique=True` makes the database itself refuse duplicate emails. Never rely on
  only checking in Python — two simultaneous signups can both pass the check.
- `index=True` makes lookups by that column fast. Index what you search by.
- `relationship(...)` is Python-side convenience: `user.meals` gives you a list.
- `cascade="all, delete-orphan"` means deleting a user deletes their meals. This
  is how "Delete Account" wipes everything without us listing each table.

**Enums** define the allowed values:

```python
class ActivityLevel(str, enum.Enum):
    low = "low"        # 0-2 workouts/week
    moderate = "moderate"  # 3-5
    high = "high"      # 6+
```

The string values (`"low"`) match the Dart enum in the app exactly. That is not
a coincidence — matching them means the app needs no translation layer.

The tables we ended up with, grouped by purpose:

| Group | Tables | Purpose |
|---|---|---|
| Identity | `users`, `refresh_tokens`, `otp_codes`, `password_reset_tokens`, `device_tokens` | Who you are, staying logged in, push notifications |
| Plan | `nutrition_plans`, `weight_entries` | Your targets and your weight history |
| Food | `meal_photos`, `food_analyses`, `detected_items`, `meals` | Scanning and the meal log |
| Preferences | `notification_settings`, `achievements`, `user_achievements`, `legal_documents`, `feedback` | Settings and content |

Two design decisions inside this file are worth calling out, because they're the
kind of thing that's painful to change later:

**Photos store a *key*, not bytes.** `meal_photos` holds
`storage_key = "meals/{userId}/{photoId}.jpg"` — a pointer. The actual image
lives in a file store. Putting megabytes of image data in a database row makes
every backup enormous and every query slower.

**Weight entries record where they came from.** A `source` of `manual` vs
`apple_health` vs `onboarding`, plus an `external_id` (Apple's own sample ID).
The `external_id` has a uniqueness constraint, which is what makes re-syncing
Apple Health safe — the second sync recognises samples it already has instead of
double-counting them.

### `app/db/session.py` — connecting

```python
engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
```

- The **engine** manages a *pool* of connections to PostgreSQL. Opening a
  connection is slow, so we keep a handful open and reuse them.
- `pool_pre_ping=True` tests a connection before handing it out — otherwise a
  connection that died overnight causes a mysterious error on the first morning
  request.
- A **session** is one unit of work: a conversation with the database that ends
  in either `commit()` (save everything) or `rollback()` (discard everything).

This file also contains the single most subtle decision in the codebase, which
gets its own explanation in [Things that will bite you](#things-that-will-bite-you):
the session is created by **middleware**, not by a dependency.

### `app/db/seed.py` — starting data

Some data must exist before anyone uses the app: the 12 achievement badges, the
terms and privacy text. `seed.py` inserts them if they're missing. It's written
to be safe to run repeatedly (it checks before inserting), which matters because
you *will* run it twice.

---

## Phase 3 — Migrations

### The problem

You create the `users` table. A month later you want to add a `birthday` column.
You cannot just edit `models.py` — the real database already exists, with real
rows. Something must apply the change.

Deleting and recreating the database works on your laptop and is unthinkable in
production.

### The solution

**Alembic** compares your models to the actual database and writes a script
describing the difference. Each script is one **migration**, numbered and
ordered. The database remembers which ones it has run (in a table called
`alembic_version`).

```bash
alembic revision --autogenerate -m "add birthday"   # write the script
alembic upgrade head                                # apply it
```

`head` means "the newest migration". Running `upgrade head` on a fresh database
runs every migration in order and builds the whole schema.

### `alembic/env.py`

This is the glue. Two lines do the real work:

```python
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)  # same DB as the app
target_metadata = Base.metadata                                   # know our tables
```

`Base.metadata` is the catalogue of every model — which is why `env.py` imports
`app.db.models` even though it never calls anything from it. The import is what
registers the tables.

One extra detail:

```python
render_as_batch=connection.dialect.name == "sqlite"
```

SQLite can't `ALTER TABLE` the way PostgreSQL can. "Batch mode" makes Alembic
work around it by recreating tables. This is what lets the same migrations run
on PostgreSQL *and* on the SQLite database our tests use.

`alembic/versions/a79fd5dc2965_initial_schema.py` is our one migration so far —
it creates all 16 tables. **Never edit an applied migration.** Write a new one.

---

## Phase 4 — Errors and language

### Why this comes before the endpoints

Because of a genuine landmine. The Flutter app reads errors like this:

```dart
message: e.response?.data['detail'],   // no null check
```

`message` is a non-nullable Dart `String`. So if our error body is anything
other than an object with a string `detail`, **the app crashes inside its own
error handler** — the user sees a crash, not an error message.

FastAPI's default behaviour violates this. A validation failure returns:

```json
{"detail": [{"loc": ["body", "email"], "msg": "field required"}]}
```

`detail` is a **list**. That crashes the app.

### `app/core/errors.py`

So we override every error path:

```python
@app.exception_handler(RequestValidationError)
async def _validation(request, exc):
    first = exc.errors()[0]
    detail = f"{loc}: {first['msg']}"        # flatten the list to one sentence
    return _envelope(detail, "VALIDATION_ERROR", 422)
```

and we add a catch-all so an unexpected crash still returns valid JSON rather
than a bare 500 with no body.

`AppError` is our own exception. Anywhere in the code you can write:

```python
raise AppError("auth.email_taken", status_code=409, code="EMAIL_TAKEN")
```

and it becomes `{"detail": "That email is already registered.", "code": "EMAIL_TAKEN"}`.

The `code` field exists because of another app quirk: the app hardcodes the
status code it stores, so the real HTTP status never reaches its UI. If the app
ever needs to distinguish "email taken" from "wrong password", it must read a
code from the *body*.

### `app/core/i18n.py`

The app supports English and Arabic, and sends a header saying which:
`langCode: 1` for Arabic, `2` for English. It does **not** translate our error
messages — it displays them as-is. So the server must translate.

```python
_MESSAGES = {
    "auth.email_taken": {
        Lang.EN: "That email is already registered.",
        Lang.AR: "هذا البريد الإلكتروني مسجل بالفعل.",
    },
}
```

Every user-facing message lives here in both languages. `AppError` takes the
*key*; the handler looks up the language from the header and resolves it.

---

## Phase 5 — Security and tokens

### `app/core/security.py`

**Passwords are never stored.** We store a hash — a one-way scramble. We use
**Argon2**, which is deliberately slow and memory-hungry, so guessing billions
of passwords is impractical.

```python
hash_password("Passw0rd!")  # '$argon2id$v=19$m=65536,t=3,p=4$...'
verify_password("Passw0rd!", stored_hash)  # True
```

You can never reverse it. Login works by hashing the attempt and comparing.

### The token design

This is the most important part of the auth system, so here's the reasoning.

We need the app to prove who it is on every request. The naive approach — one
token that lasts forever — means a stolen token is stolen forever. The standard
solution is **two tokens**:

| | Access token | Refresh token |
|---|---|---|
| Lifetime | 15 minutes | 30 days |
| Format | JWT (self-describing) | Opaque random string |
| Stored server-side? | No | Yes, **hashed** |
| Used for | Every API request | Only to get a new access token |

An **access token** is a JWT: a string containing `{"sub": "<user id>", "exp": ...}`
plus a signature made with our secret. We can verify it with maths alone — no
database lookup — which is why it's fast enough for every request. But that also
means we *cannot* cancel one; it's valid until it expires. Hence: 15 minutes.

A **refresh token** is just random bytes with a database row. Because we look it
up, we *can* revoke it instantly.

**Rotation:** each time you use a refresh token, it's consumed and you get a new
one. Every token in a login chain shares a `family_id`.

**Reuse detection** is why that matters:

```
You:      token A ──refresh──> token B ──refresh──> token C   ✓
Attacker: token A ──refresh──> ✗ A was already consumed!
                               → the whole family is revoked
```

If a consumed token is presented again, either you or a thief has a copy. We
can't tell which, so we revoke everything and force a fresh login. This is a
standard pattern and it's genuinely valuable — it turns a silent, permanent
compromise into a one-time inconvenience.

**One thing deliberately NOT in the token:** `isPremium`. Anything inside a JWT
is frozen until it expires, so a user who upgrades would keep seeing "not
premium" for 15 minutes. Mutable facts belong in the database.

### `app/core/timeutil.py`

A five-line file that exists for one reason: SQLite has no timezone-aware
timestamp type. A time saved as "10:00 UTC" comes back from PostgreSQL as
"10:00 UTC" but from SQLite as just "10:00" — and comparing the two raises
`TypeError: can't compare offset-naive and offset-aware datetimes`.

`ensure_utc()` normalises on read so the same code works on both.

---

## Phase 6 — Schemas

### Why models aren't enough

You might ask: we already have a `User` model, why define its shape twice?

Because they answer different questions:

- **Model** = what the *database* stores. Includes `password_hash`.
- **Schema** = what the *API* exchanges. Must never include `password_hash`.

If you return models directly, one day you add a sensitive column and silently
start leaking it. Schemas make the public surface explicit.

Schemas also do **validation on the way in**. This:

```python
class PlanRequest(CamelModel):
    height_cm: float = Field(default=170, gt=50, lt=280)
```

means a request with `heightCm: 5000` is rejected with a clear message before
your code ever runs. Your function can trust its inputs.

### `app/schemas/common.py` — the camelCase trick

Python names things `height_cm`. Dart names things `heightCm`. Rather than
converting by hand everywhere:

```python
class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,   # height_cm  <->  heightCm on the wire
        populate_by_name=True,      # accept either form on input
        from_attributes=True,       # can be built straight from a model object
    )
```

Every schema inherits this. We write Python conventions; the app sees Dart
conventions. That's the whole reason `app/schemas/*` mirror the Dart model
field names so closely — the field names are the contract.

---

## Phase 7 — Services

**Services hold business logic and know nothing about HTTP.** No request, no
response, no status codes. This makes them trivial to test and reusable.

### `app/services/plan.py` — the calorie maths

This was moved *from* the Flutter app *to* the server, deliberately. Now the
formula lives in one place, can be fixed without an app release, and its inputs
and outputs are stored.

```python
bmr = (10 * weight_kg) + (6.25 * height_cm) - (5 * age) + (-161 if female else 5)
calories = bmr * activity_multiplier      # 1.2 / 1.375 / 1.55
if goal == lose: calories -= 500
calories = clamp(calories, 1200, 4000)

protein_g = weight_kg * 2                 # 2 g per kg
fats_g    = calories * 0.25 / 9           # 25% of calories, 9 kcal per gram
carbs_g   = (calories - protein*4 - fats*9) / 4   # whatever's left
```

That's Mifflin-St Jeor — a standard BMR estimate. The clamps exist so a strange
input can't produce a dangerous target.

`PLAN_VERSION = 1` is stamped onto every stored plan. When you change the
formula you bump it, and old plans remain interpretable — you know which rules
produced them.

### `app/services/vision/` — pluggable food analysis

This folder is a good example of a pattern worth learning: **program against an
interface, not an implementation.**

```
base.py      "any analyser must have: analyze(bytes) -> FoodAnalysisResult"
stub.py      a fake one (no key, no cost, always same answer)
claude.py    the real one (Anthropic vision)
factory.py   picks one based on a setting
```

The router calls `get_vision_provider().analyze(photo)`. It has no idea which
one it got. Swapping providers is one line in `.env`; adding a third means
writing one class.

The **stub** matters more than it looks. It derives fake nutrition from a hash of
the image bytes, so the same photo always gives the same answer. That makes the
entire food pipeline testable with no API key, no network, and no cost.

The **Claude provider** does three notable things:

1. Sends the photo as base64 inside the message.
2. Uses `output_config.format` with a JSON Schema, so the model is *constrained*
   to return valid JSON in our shape — no parsing prose, no regex rescue.
3. Checks `stop_reason == "refusal"` **before** reading the response. Anthropic's
   safety classifiers can decline a request, and that arrives as a normal
   success response with empty content. Code that assumes `content[0]` exists
   would crash.

We then clamp every number (`calories` to 0–5000, etc.), because a model
returning 50,000 calories would wreck the day's arithmetic.

### `app/services/storage/` — photos

Same interface pattern: `base.py` defines it, `local.py` implements it with disk
files. Production would add an S3 implementation.

`process_photo()` is where the important work happens:

```python
image = ImageOps.exif_transpose(image)   # honour the rotation flag
image.thumbnail((1568, 1568))            # shrink
image.save(buffer, format="JPEG")        # re-encode -> EXIF is dropped
```

Three things at once. The shrink is for cost and speed. The re-encode **strips
EXIF metadata** — phone photos embed GPS coordinates, and a meal photo tagged
with your home address is a privacy problem you don't want to create. And
`exif_transpose` applies the rotation flag *before* it's discarded, so portrait
photos don't come back sideways.

---

## Phase 8 — Dependencies

### `app/core/deps.py`

FastAPI's dependency injection means a route can just *ask* for things:

```python
@router.get("/profile")
async def get_profile(user: CurrentUser, db: Db):
    ...
```

`CurrentUser` and `Db` are shorthand:

```python
CurrentUser = Annotated[User, Depends(get_current_user)]
Db = Annotated[AsyncSession, Depends(get_db)]
```

FastAPI sees them, runs `get_current_user` and `get_db` first, and passes the
results in. `get_current_user` reads the `Authorization` header, verifies the
JWT, loads the user, and raises `401` if any step fails.

**The consequence to internalise: if a route asks for `CurrentUser`, it is
automatically protected.** There is no separate "is this endpoint public?"
setting to forget. Authentication is visible in the function signature.

This file also holds a subtle rule:

```python
def resource_forbidden() -> AppError:
    """Ownership failure — 404, not 403."""
```

Normally "this meal isn't yours" is a `403 Forbidden`. But the Flutter app's
interceptor **logs the user out on both 401 and 403**. Returning 403 for an
ordinary ownership check would sign people out for tapping the wrong thing. So
ownership failures return `404`. (This is also better disclosure practice — you
don't confirm that someone else's meal exists.)

---

## Phase 9 — The routers

Routers are thin. They translate HTTP into service calls and back. If a router
function is getting long and clever, that logic probably belongs in a service.

### `app/api/v1/auth.py`

Signup → email a 6-digit code → verify → get tokens. Plus login, refresh,
logout, password reset, and account deletion.

Two details worth copying into your own work:

```python
if user is None or not verify_password(...):
    raise AppError("auth.invalid_credentials", ...)
```

One error for both "no such user" and "wrong password". Two different messages
would let an attacker discover which emails are registered.

```python
generic = MessageResponse(message="If that email is registered, a code is on its way.")
if user is None:
    return generic
```

Same idea for password reset: the response is identical whether or not the
account exists.

### `app/api/v1/food.py`

The core feature. The flow is deliberately **two calls**:

```
POST /food/analyze   → upload photo, run AI, get a result (nothing saved yet)
POST /meals          → the "Done" button, commit it to the log
```

Why split? Because the user might not press Done. They might press "Fix
Results", which in this design simply throws the analysis away and re-opens the
camera. Saving on analysis would fill the log with rejected scans.

**Quantity is applied exactly once.** `/food/analyze` returns *per-serving*
numbers; the app's stepper multiplies them for display; `/meals` multiplies once
when saving. Multiply in two places and every meal is double-counted.

`/home/summary` computes what's **left**, not what's consumed:

```python
calories_left = max(0, goal - consumed)
```

The design labels them "Protein left", "Carbs left", "Fat left". Getting this
backwards would be very visible and very wrong.

### `app/api/v1/notifications.py`

The odd one out — it returns a bare `true` instead of our envelope:

```python
@router.post("/addAnonymousToken", response_model=bool)
```

Because the already-shipped app checks `response.data == true` literally. This
is the clearest example of the app dictating the API. We don't get to be
consistent here; we get to be correct.

### `app/api/v1/router.py`

Six lines that collect every router into one, so `main.py` mounts a single
object.

---

## Phase 10 — Wiring it together

### `app/main.py`

```python
app = FastAPI(...)
app.add_middleware(CORSMiddleware, ...)

@app.middleware("http")
async def db_session_middleware(request, call_next):
    async with SessionLocal() as session:
        request.state.db = session
        response = await call_next(request)
        if response.status_code < 400:
            await session.commit()
        else:
            await session.rollback()
        return response

register_error_handlers(app)
app.include_router(api_router, prefix="/api")
app.mount("/media", StaticFiles(directory=...))
```

**Middleware** is code that wraps every request — it runs before your route and
after it. Ours opens a database session, puts it on the request, lets the route
run, then commits if the response looks successful and rolls back if not.

This gives you a property that's easy to take for granted: **either everything
in a request is saved, or nothing is.** If saving a meal succeeds but updating
the plan fails, you don't end up with half a change.

Why middleware rather than the more common dependency approach is explained
below — it's the subtlest bug we hit.

### `run.py`

Starts the server. On Windows it must pick a specific event loop; the reason is
in the next section.

---

## Phase 11 — Tests

### `tests/conftest.py`

`conftest.py` is pytest's magic filename: fixtures defined here are available to
every test automatically.

```python
@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
```

Each test gets a **brand-new in-memory SQLite database**, built from the models,
destroyed afterwards. No Docker, no cleanup, no test contaminating another. The
whole suite runs in about two seconds.

`auth_client` goes further: it signs up a user, marks them verified, logs in,
and returns a client with the token already attached — so a test that needs a
logged-in user is one line.

### `tests/test_api.py`

17 tests. They're grouped around risk, not around code coverage:

- The error envelope really is a string (the app-crash landmine).
- Errors really do change with `langCode`.
- Unauthenticated is 401 and ownership failure is 404 — *not* 403.
- Refresh rotation works **and reuse revokes the family**.
- Analyze → save → summary produces consistent numbers.
- Apple Health re-sync imports 0 the second time.
- Push token returns literal `true`.

These tests earned their keep immediately: two of them caught real bugs
described below.

---

## Every file, in one table

| File | What it does |
|---|---|
| `docker-compose.yml` | Defines the PostgreSQL and pgAdmin containers |
| `docker/pgadmin-servers.json` | Pre-registers the DB connection in pgAdmin |
| `requirements.txt` | Python libraries and their pinned versions |
| `.env.example` → `.env` | Settings and secrets |
| `alembic.ini` | Alembic's config; points at the `alembic/` folder |
| `alembic/env.py` | Connects Alembic to our settings and models |
| `alembic/versions/*.py` | One file per schema change |
| `run.py` | Starts the dev server with the right event loop |
| `pytest.ini` | Test configuration (async mode) |
| **`app/__init__.py`** | Sets the Windows event-loop policy on import |
| **`app/main.py`** | Creates the app, adds middleware, mounts routers |
| `app/core/config.py` | All settings, read from `.env` |
| `app/core/errors.py` | `AppError` + handlers guaranteeing `{"detail": "..."}` |
| `app/core/i18n.py` | English/Arabic message catalogue |
| `app/core/security.py` | Password hashing, JWTs, refresh tokens, OTPs |
| `app/core/timeutil.py` | Normalises naive/aware datetimes |
| `app/core/deps.py` | `CurrentUser`, `Db`, and the 401/404 helpers |
| `app/db/base.py` | `Base`, `UUIDPrimaryKey`, `Timestamps` mixins |
| `app/db/models.py` | All 16 tables |
| `app/db/session.py` | Engine, session factory, `get_db` |
| `app/db/seed.py` | Inserts achievements and legal text |
| `app/schemas/common.py` | `CamelModel` — snake_case ⇄ camelCase |
| `app/schemas/auth.py` | Signup/login/token bodies |
| `app/schemas/food.py` | Analysis, meals, dashboard, analytics |
| `app/schemas/profile.py` | Profile, plan, weight, settings |
| `app/api/v1/router.py` | Collects all routers into one |
| `app/api/v1/auth.py` | Signup, login, verify, refresh, logout, delete |
| `app/api/v1/userinfo.py` | Onboarding questionnaire → plan |
| `app/api/v1/food.py` | Analyze, save meal, dashboard, favourites |
| `app/api/v1/profile.py` | Profile, weight, Health sync, settings, legal |
| `app/api/v1/analytics.py` | The charts screen in one call |
| `app/api/v1/notifications.py` | Push token registration |
| `app/services/plan.py` | BMR, macros, goal date, BMI |
| `app/services/vision/base.py` | The analyser interface |
| `app/services/vision/stub.py` | Offline deterministic analyser |
| `app/services/vision/claude.py` | Anthropic vision analyser |
| `app/services/vision/factory.py` | Chooses the analyser from settings |
| `app/services/storage/base.py` | The storage interface |
| `app/services/storage/local.py` | Disk storage + image processing |
| `tests/conftest.py` | Test database and authenticated client fixtures |
| `tests/test_api.py` | The 17 tests |

`__init__.py` files mark a folder as a Python package. Most are empty;
`app/__init__.py` is the exception and does real work.

---

## How it all connects

```mermaid
graph TD
    Phone["📱 Flutter app"] -->|HTTP + JSON| Main["main.py<br/>middleware opens DB session"]
    Main --> Router["api/v1/router.py"]
    Router --> Endpoints["auth · food · profile<br/>analytics · userinfo · notifications"]

    Endpoints -->|asks for| Deps["core/deps.py<br/>CurrentUser · Db"]
    Deps -->|verifies JWT| Security["core/security.py"]

    Endpoints -->|validates with| Schemas["schemas/*<br/>camelCase JSON shapes"]
    Endpoints -->|business logic| Services["services/<br/>plan · vision · storage"]
    Endpoints -->|queries| Models["db/models.py"]
    Models --> DB[("PostgreSQL<br/>in Docker")]

    Endpoints -->|raises| Errors["core/errors.py<br/>always {detail: string}"]
    Errors --> I18n["core/i18n.py<br/>EN / AR"]
    Errors --> Phone
```

Read it as three vertical bands:

- **Top** — the transport. HTTP arrives, middleware and routing decide where it
  goes.
- **Middle** — your code. Endpoints coordinate; schemas guard the edges;
  services do the thinking.
- **Bottom** — persistence. Models and the database.

And `core/` sits beside all of it, usable from anywhere.

---

## The full picture: one request end to end

Let's follow the single most involved request in the system: **the user
photographs a meal**.

### What the phone sends

```http
POST /api/food/analyze HTTP/1.1
Authorization: Bearer eyJhbGciOiJIUzI1NiJ9...
langCode: 2
Content-Type: multipart/form-data

<3 MB JPEG>
```

### Step by step

**1. Uvicorn receives it.** Uvicorn is the web server — it speaks HTTP and hands
a Python object to FastAPI.

**2. CORS middleware** waves it through (irrelevant for a mobile app, needed if
a browser ever calls us).

**3. Our DB middleware** (`main.py`) opens a database session and attaches it to
the request. Nothing has touched PostgreSQL yet — sessions connect lazily.

**4. Routing.** FastAPI matches `/api/food/analyze` to `analyze_food` in
`api/v1/food.py`.

**5. Dependencies run first.** The function signature says
`user: CurrentUser, db: Db`, so:
   - `get_db` pulls the session off the request.
   - `get_current_user` reads the `Authorization` header, verifies the JWT
     signature and expiry with `core/security.py`, extracts the user id, and
     loads the `User` row. **If any of that fails, we return 401 here and the
     function never runs.**

**6. The function body begins.** The upload is read into memory and checked: not
empty, not over 15 MB.

**7. Image processing** (`services/storage/local.py`): rotation applied, shrunk
to 1568px, re-encoded as JPEG — which drops the EXIF/GPS data.

**8. Save the photo.** A `MealPhoto` row is created to get an id, the id becomes
the storage key `meals/{userId}/{photoId}.jpg`, and the bytes are written to
disk.

**9. AI analysis.** `get_vision_provider()` returns whichever provider `.env`
selected. With `claude`, the image goes to Anthropic with a JSON Schema
constraining the reply; the response is checked for a refusal, parsed, and every
number clamped to a sane range.

**10. Store the result.** A `FoodAnalysis` row plus one `DetectedItem` row per
visible ingredient. Nothing is a *meal* yet — the user hasn't pressed Done.

**11. Compute progress.** We load the user's active `NutritionPlan` and express
each macro as a fraction of their daily target, so the app can draw the bars.

**12. Build the response.** A `FoodAnalysisOut` schema instance. Pydantic
serialises it, converting `calories_per_serving` → `caloriesPerServing`.

**13. Middleware commits.** Status is 200, so `session.commit()` — the photo row,
the analysis, and the detected items all become permanent together.

**14. The phone receives:**

```json
{
  "analysisId": "08099f52-b700-420c-9965-bb82f9493acd",
  "name": "Garden Power Bowl",
  "timeLabel": "15:44",
  "mealTypeLabel": "LUNCH",
  "caloriesPerServing": 461,
  "proteinGramsPerServing": 37,
  "proteinProgress": 0.285,
  "healthScore": 9,
  "imageUrl": "/media/meals/d45a.../bc15....jpg",
  "detectedItems": [
    {"label": "Lettuce", "cx": 0.25, "cy": 0.26},
    {"label": "Cherry Tomatoes", "cx": 0.77, "cy": 0.33}
  ]
}
```

The app draws the result screen, floating each ingredient label over the photo
using `cx`/`cy`.

### And if the user presses Done

```http
POST /api/meals
{ "analysisId": "08099f52-...", "quantity": 2 }
```

Steps 1–5 repeat. The endpoint loads that analysis (checking it belongs to this
user), multiplies the per-serving values by 2, writes a `Meal` row, and returns
it. The next `/home/summary` call now shows 922 fewer calories remaining.

### If something goes wrong

Say the image is corrupt. `process_photo()` raises `ValueError`; we catch it and
`raise AppError("food.bad_image")`. That bubbles up to the handler in
`errors.py`, which reads `langCode: 2` and produces:

```json
{"detail": "We couldn't read that image. Try taking the photo again.", "code": "BAD_IMAGE"}
```

with status 400. The middleware sees `400 >= 400` and **rolls back** — so the
half-created photo row disappears. The app displays the message. Nothing is left
inconsistent.

---

## Adding a new endpoint yourself

The best way to internalise the structure is to add something. Say you want
`GET /api/meals/recent` returning the last 10 meals regardless of date.

**1. Do you need a new table?** No — `meals` exists. (If you did: add the model
in `db/models.py`, then `alembic revision --autogenerate -m "..."` and
`alembic upgrade head`.)

**2. Do you need a new schema?** No — `MealOut` exists. (If you did: add it in
`schemas/food.py`, inheriting `CamelModel`.)

**3. Is there business logic?** Not really. (If there were: a function in
`services/`.)

**4. Write the endpoint** in `api/v1/food.py`:

```python
@router.get("/meals/recent", response_model=list[MealOut])
async def recent_meals(user: CurrentUser, db: Db) -> list[MealOut]:
    meals = (await db.scalars(
        select(Meal)
        .where(Meal.user_id == user.id)
        .order_by(Meal.eaten_at.desc())
        .limit(10)
    )).all()
    return [await _meal_response(db, m) for m in meals]
```

Note what you got for free: authentication (`CurrentUser`), a database session
(`Db`), a transaction, camelCase output, and error handling.

**5. Write a test** in `tests/test_api.py`:

```python
async def test_recent_meals(auth_client):
    resp = await auth_client.get("/api/meals/recent")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
```

**6. Run it:** `pytest -q`. Check `/docs` — FastAPI has already documented your
endpoint from the type hints.

One caution: `/meals/recent` must be declared **before** any `/meals/{meal_id}`
route, or FastAPI will match `recent` as a meal id. Specific routes before
wildcard ones.

---

## Things that will bite you

These are the real problems we hit. All four cost time; all four are recorded so
they cost you none.

### 1. `asyncpg` doesn't build on Python 3.14

The usual async PostgreSQL driver has no pre-built package for Python 3.14, and
compiling it from source fails against the new headers. We use **psycopg 3**
instead — hence `postgresql+psycopg://` rather than `postgresql+asyncpg://`.

### 2. Windows event loops

Python on Windows offers two "event loops" (the machinery that runs async code).
psycopg's async mode cannot use `ProactorEventLoop` — and uvicorn hardcodes
exactly that for single-process mode, passing its own loop factory so setting a
policy has no effect:

```python
# inside uvicorn
if sys.platform == "win32" and not use_subprocess:
    return asyncio.ProactorEventLoop
```

Symptom: `InterfaceError: Psycopg cannot use the 'ProactorEventLoop'`.

`run.py` fixes it by driving the server itself under `SelectorEventLoop`. **Use
`python run.py`, not the `uvicorn` command.**

### 3. pgAdmin rejects `.local` email addresses

`admin@alluvi.local` makes pgAdmin exit immediately, over and over, because
`.local` is a reserved domain. The container shows as "Restarting". We use
a real address instead. Any address on a real TLD works — the value is only a
local pgAdmin login, never emailed.

The lesson is the debugging move, not the fix: when a container won't stay up,
`docker compose logs <name>` tells you why in one line.

### 4. The transaction-boundary bug (the important one)

This is the subtlest thing in the project and worth understanding properly.

The common FastAPI pattern is a dependency that commits:

```python
async def get_db():
    async with SessionLocal() as session:
        yield session
        await session.commit()      # runs AFTER the response is sent
```

The problem is in that comment. FastAPI runs the code after `yield` **after the
response has already gone to the client.** So:

```
Time →
 Request 1: ...work... ──► response sent ──► commit
 Request 2:                    └─ arrives, reads DB ─┘   (before the commit!)
```

The app writes something, immediately reads it back, and doesn't see it.

Our tests passed anyway, because in-process tests happened to win the race. Over
real HTTP it lost — and it lost in the worst possible place. Refresh-token reuse
detection stopped working: the second request read the token *before* the first
request's "consumed" flag was committed, so a replayed token looked perfectly
valid. Security theatre with no security.

The fix is to move the commit into middleware, which runs **before** the
response is sent:

```python
@app.middleware("http")
async def db_session_middleware(request, call_next):
    async with SessionLocal() as session:
        request.state.db = session
        response = await call_next(request)
        if response.status_code < 400:
            await session.commit()      # BEFORE the response goes out
        else:
            await session.rollback()
        return response
```

Two takeaways worth more than the fix itself:

- **A test passing does not mean the code is right.** A race can hide behind
  faster, in-process conditions. The live end-to-end check found what the unit
  tests couldn't.
- **Know where your transaction ends.** If you move that commit back into a
  dependency, you silently reintroduce the bug.

---

## Where to go next

Things deliberately left unbuilt, roughly in order of how soon you'll want them:

1. **Email delivery.** OTP codes and reset links are written to the log
   (`logger.info("OTP for %s: %s", ...)`). Wire up an email provider.
2. **Apple/Google sign-in.** Both endpoints return `501` on purpose — the
   identity token must be verified against the provider's public keys first.
   Accepting an unverified token would let anyone sign in as anyone.
3. **Real object storage.** Photos are on local disk. Implement `StorageBackend`
   for S3 and serve time-limited URLs.
4. **Achievement unlocking.** The badges are seeded and reported; nothing awards
   them yet.

And the one piece of **client** work this backend assumes: the app currently
stores only an access token and treats any 401 as a permanent logout. With
15-minute tokens, the first expiry signs every user out for good. It needs to
store the refresh token and retry once via `POST /api/Auth/refresh`.

---

## Cheat sheet

```bash
# Start / stop the database
docker compose up -d
docker compose stop
docker compose logs -f postgres

# Database schema
python -m alembic revision --autogenerate -m "what changed"
python -m alembic upgrade head
python -m app.db.seed

# Run
python run.py                    # http://127.0.0.1:8000/docs
python run.py --no-reload

# Test
python -m pytest -q
python -m pytest tests/test_api.py::test_analyze_then_save_then_summary -v
```

| Where | What |
|---|---|
| <http://localhost:8000/docs> | Interactive API docs — try any endpoint in the browser |
| <http://localhost:5050> | pgAdmin — browse the tables (`nooshin.nexus@gmail.com` / `admin`) |
| `../frontend/BACKEND.md` | The contract this API had to satisfy |
| `README.md` | Setup, endpoints, and the constraints in short form |
