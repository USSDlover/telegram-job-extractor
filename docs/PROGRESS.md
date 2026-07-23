# Progress Log — Telegram Job Extractor

## Features & Milestones

| Feature | Status | Notes |
|---------|--------|-------|
| Repo layout (`backend/`, `frontend/`, `docs/`) | Done | Vite React + FastAPI monorepo |
| Config via `.env` | Done | Telethon + Ollama settings |
| Async `jobs.json` storage with lock | Done | Dedup by channel + message_id |
| Ollama Gemma 2 structured extraction | Done | Pydantic JSON schema prompting |
| Category discovery from samples | Done | `POST /api/discover` |
| Background scrape + extract pipeline | Done | `POST /api/extract` + BackgroundTasks |
| Jobs list + category filter API | Done | `GET /api/jobs`, `GET /api/categories` |
| ControlPanel UI | Done | Channel input, checkboxes, triggers |
| JobDashboard UI | Done | Filter, refresh, apply links |
| FastAPI serves Vite `dist` | Done | Mount + SPA fallback |
| System / progress docs | Done | `docs/SYSTEM.md`, this file |
| SSE live activity stream | Done | `GET /api/stream-logs` + `activity_log.py` |
| Stage logs in Telethon / Ollama pipelines | Done | JOINING → FETCHING → OLLAMA → JOB_SAVED |
| ActivityConsole UI | Done | Collapsible terminal-style event log |
| Dynamic feed / category sync | Done | Auto-refresh on `JOB_SAVED`; Refresh Feed reloads both APIs |

## Architectural Decisions

1. **Telethon + FastAPI background tasks** — Channel sampling and full extraction run as async Telethon operations; extraction is kicked off via FastAPI `BackgroundTasks` so the HTTP response returns immediately (`202`).
2. **Local Gemma 2 via Ollama** — No cloud LLM dependency. Structured outputs enforced by embedding Pydantic `model_json_schema()` in the prompt and validating the parsed JSON response.
3. **JSON file persistence** — `jobs.json` at repo root keeps the MVP simple; `asyncio.Lock` + atomic write prevents corruption under concurrent appends.
4. **Static Vite build mounted in FastAPI** — Single process serves API and UI in production; Vite dev server proxies `/api` during local frontend development.
5. **Filter at extract time** — User-selected categories/titles gate what gets persisted, reducing noise in the dashboard.
6. **SSE activity bus** — `broadcast_log(stage, message, data)` fans out structured events to all `EventSource` clients via asyncio queues + a short ring buffer. Pipelines emit stages (`JOINING_TELEGRAM`, `FETCHING_POSTS`, `CALLING_OLLAMA`, `DISCOVERED_CATEGORIES`, `EXTRACTION_PROGRESS`, `JOB_SAVED`, `ERROR`) so the UI can show spinners and sync state without polling.
7. **UI state sync via events** — `DISCOVERED_CATEGORIES` updates Control Board checkboxes immediately; `JOB_SAVED` debounced-triggers Job Feed + category dropdown refresh. Refresh Feed always reloads `/api/jobs` and `/api/categories` together.

## Future Work

- [ ] Migrate `jobs.json` → SQLite (or Postgres) with proper indexes
- [ ] Multi-channel batch scraping queue with per-job progress panels
- [ ] Pagination / infinite scroll on `GET /api/jobs`
- [ ] Session login helper UI for first-time Telethon auth
- [ ] Retry / backoff policies for Ollama and Telegram rate limits
- [ ] Export jobs as CSV
