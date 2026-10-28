# JobTrack — job application tracker

Paste a link to a vacancy → JobTrack downloads the page, pulls out company, position, location,
salary and description, and keeps a timeline for every application. The stats page shows what
actually works: the funnel, response rate **per resume version** and per job board.

Multi-user (send a friend the link, everyone has their own data), mobile-friendly, and it runs on
free hosting. Built as a backend portfolio project.

<!-- Screenshots: add docs/list.png, docs/stats.png and reference them here -->

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2 (sync, typed) · Alembic · PostgreSQL (prod) / SQLite (local) ·
Pydantic v2 + pydantic-settings · Jinja2 + HTMX + Pico.css (CDN, no build step) ·
httpx + extruct + BeautifulSoup/lxml for parsing · bcrypt · signed-cookie sessions ·
pytest · Ruff · Docker · GitHub Actions

## Features

- **Quick add by link.** JSON-LD `JobPosting` → OpenGraph/`<title>` fallback → source detected from
  the domain (Jobs.cz, StartupJobs, LinkedIn, Djinni, Work.ua…). The result is a pre-filled form you
  correct and save (or keep as "saved, not applied yet").
- **Fallback for sites that block bots** (LinkedIn, Indeed): paste the vacancy text; a regex
  heuristic finds the salary (`60 000–80 000 Kč`, `€3000`, `60-80k EUR`, `від 40 000 грн`, `до 3000$`…),
  the rest becomes the description.
- **List** sorted by last activity, status chips with counters, live search, archive toggle,
  one-click status change right in the list (HTMX, no reload).
- **Card**: every field editable, timeline (status changes are logged automatically, plus notes /
  interviews / messages), archive, delete.
- **Stats**: totals, funnel applied → response → interview → offer, response rate by resume version
  and by source, weekly histogram (12 weeks, pure CSS), average days to the first response.
- **CSV export/import** (same format, Excel-friendly), password change, account deletion.
- **Security**: SSRF-safe fetching, CSRF tokens, bcrypt, strict per-user isolation (someone else's
  record is always 404), per-user rate limit on the parser.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                # SQLite by default
alembic upgrade head
uvicorn app.main:app --reload
```

Open http://localhost:8000, register, paste a link. API docs: http://localhost:8000/docs (hidden
when `ENV=prod`).

### With Docker (app + PostgreSQL 16)

```bash
docker compose up --build
```

Migrations run automatically when the container starts. → http://localhost:8000

### Tests and lint

```bash
ruff check . && ruff format --check . && pytest              # SQLite
TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/jobtrack_test pytest   # same suite on Postgres
```

The suite (~120 tests) covers auth and data isolation, CRUD and the timeline, the parser on saved
HTML fixtures (JSON-LD / OpenGraph only / empty page), SSRF blocking (localhost,
169.254.169.254, 10.0.0.1, redirects to internal addresses, DNS rebinding), rate limiting, the
stats funnel on a known data set, and a CSV export → import round trip. The schema is built with
Alembic, so migrations are tested as well. CI runs lint, tests on SQLite **and** PostgreSQL, and a
Docker Compose smoke test.

Try the parser on a real page without starting the app:

```bash
python -m scripts.try_parse "https://www.startupjobs.cz/nabidka/..."
```

## Configuration

Only environment variables (see `.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./jobtrack.db` | `postgres://…` URLs from Neon/Supabase work as-is |
| `SECRET_KEY` | dev value | signs the session cookie; the app refuses to start in prod with the default |
| `ENV` | `dev` | `prod` → `Secure` cookies, `/docs` and `/openapi.json` disabled |
| `ALLOW_REGISTRATION` | `true` | close sign-ups once your friends are in |
| `PARSE_RATE_LIMIT_PER_HOUR` | `20` | parser calls per user per hour |

## Deploy for free (Render + Neon)

Free tiers change — **check the current limits** before relying on them. As of October 2026:

1. **Database — [Neon](https://neon.com)** (free, no card): create a project, copy the connection
   string (`postgresql://…?sslmode=require`). Free compute suspends after ~5 min idle; the first
   query after that takes a moment. *Alternative:* [Supabase](https://supabase.com) free Postgres
   (use the pooler connection string).
2. **App — [Render](https://render.com) free web service**: New → Web Service → connect the GitHub
   repo → Runtime **Docker** → Instance type **Free**. Environment variables:
   `DATABASE_URL` (from Neon), `SECRET_KEY` (`python -c "import secrets; print(secrets.token_urlsafe(48))"`),
   `ENV=prod`, `ALLOW_REGISTRATION=true`. Health check path: `/healthz`.
   Render passes `$PORT`; the container applies migrations and starts uvicorn on it.
3. Open `https://<your-app>.onrender.com` and register.

Notes:
- A free Render service **sleeps after 15 minutes without traffic**; the first request after that
  takes ~30–60 s. Fine for a personal tracker. If it bothers you, point a free uptime monitor at
  `/healthz` (keep within the free instance-hours budget).
- Don't use SQLite on free hosts: their disk is ephemeral and the data disappears on restart.
  Render's own free Postgres also expires after 30 days — use Neon/Supabase.
- *Koyeb* (mentioned in the spec) stopped offering free instances to new accounts in 2026.
  Fallbacks: [Hugging Face Spaces](https://huggingface.co/docs/hub/spaces-sdks-docker) with the
  Docker SDK (set `app_port` and `PORT` to 7860; open the `*.hf.space` URL directly), or Google Cloud
  Run's free tier (needs a card on file).

### Giving it to a friend

Send them the link — they register with email + password and get their own empty tracker. When
everyone is in, set `ALLOW_REGISTRATION=false` in the Render dashboard (the service restarts) and the
sign-up page says registration is closed.

## Project structure

```
app/
  main.py          app factory, middleware, error handlers, /healthz
  config.py        settings from env (pydantic-settings)
  db.py            engine, session, naming conventions (SQLite FK + Unicode lower())
  models.py        User, Application, Event, ParseRequest
  schemas.py       one validation path for forms, JSON API and CSV import
  deps.py          current user, login guard, CSRF check
  security.py      bcrypt, CSRF tokens, session rotation
  i18n.py          every UI string (ru), ready for another language
  web.py           template rendering, flash messages, HTMX helpers
  routers/         auth, applications (pages + HTMX fragments), stats, settings (+CSV), api (/api/v1)
  services/        applications, job_parser, salary, ssrf, rate_limit, stats, csv_io
  templates/       Jinja2 pages and partials/ for HTMX
alembic/           migrations
tests/             pytest + fixtures/*.html
scripts/           start.sh (container entrypoint), try_parse.py
```

## Decisions

Things the spec left open, and why they were decided this way:

- **No ORM relationships.** All queries are explicit in `services/`, so there is no lazy loading
  and no N+1; children are removed by `ON DELETE CASCADE` (SQLite gets `PRAGMA foreign_keys=ON`).
  With 1000 applications the list renders in ~100 ms and `/stats` in ~50 ms locally.
- **Events store `from_status` / `to_status`** (in addition to the spec's fields). The funnel is
  rebuilt from history: an application that reached an interview and was then rejected still counts
  as an interview.
- **"Response" = any reaction from the company**: replied, interview, test task, offer **or
  rejection**. Ghosted and withdrawn are not responses. "Applied" = left the `saved` stage. Archived
  applications still count in stats (archiving only hides them from the list).
- **Average days to first response** counts only real status changes after creation, from
  `applied_at` to the first response event.
- **SSRF protection in two layers**: URL validation + DNS check before every hop (redirects are
  followed manually, max 3), and a custom httpcore network backend that re-checks the IP it actually
  connects to (defeats DNS rebinding). Only http/https, ports 80/443/8080/8443, no credentials in
  URLs, 8 s total, 2 MB of *decompressed* body, proxies from the environment ignored.
- **Blocked sites are a normal outcome**: 401/403/429/451/999 → a friendly message with a link to
  the paste-text fallback. The parser never raises into the request.
- **Rate limit is stored in the database** (`parse_requests`), not in memory, so it survives
  restarts and works with several workers.
- **CSRF**: a per-session token, sent as a hidden form field or as the `X-CSRF-Token` header (HTMX
  sets it via `hx-headers`; the JSON API uses the same header). Cookies are also `SameSite=Lax`,
  `HttpOnly`, and `Secure` in prod. The session is rotated on login/logout.
- **Sessions in a signed cookie** (Starlette `SessionMiddleware`), 30 days. The user is reloaded
  from the DB on every request, so a deleted account is logged out immediately.
- **Login doesn't leak which emails exist**: same error and a dummy bcrypt check for unknown emails.
  Passwords are limited to 72 bytes (bcrypt's real limit) instead of being silently truncated.
- **Enums are VARCHAR + CHECK** constraints, identical on SQLite and PostgreSQL and easy to extend.
- **Search is case-insensitive for Czech/Cyrillic on SQLite too** (SQLite's `lower()` is replaced
  with Python's on connect); `%` and `_` in the query are escaped.
- **Salary** is integers + ISO currency code; the period (month/year/hour) is not stored — most
  Czech boards publish monthly amounts. Description salaries are only taken when a currency is
  present (to avoid "10k users").
- **Work mode** comes from `jobLocationType: TELECOMMUTE`, otherwise from words in the title and
  location (`remote`, `hybrid`, `hybridní`, `удалённо`…). The description is not scanned —
  too many false positives.
- **CSV**: UTF-8 with BOM (Excel opens Czech and Cyrillic correctly), delimiter auto-detected
  (`,` `;` tab), dates as ISO or `dd.mm.yyyy`, statuses as codes or Russian labels. Cells that
  start with `= + - @` are prefixed with `'` on export (CSV injection) and restored on import.
  Duplicates (same URL, or same company + position + date) are skipped. The timeline isn't exported.
  Non-UTF-8 files are rejected with a hint to save as "CSV UTF-8" rather than guessing the encoding.
- **Settings endpoints** are split into `POST /settings/password` and `POST /settings/delete`
  (clearer than one `POST /settings` with an action field). Deleting the account requires the password.
- **Quick-add redirects to the list** after saving; edits on the card redirect back to the card.
- **CDN assets are pinned with SRI hashes** (Pico.css 2.1.1, htmx 2.0.11).
- **UI language is Russian**, all strings are in `app/i18n.py`; code, README and API are English.
