# Progress Log — Telegram Job Extractor

## Features & Milestones

| Feature | Status | Notes |
|---------|--------|-------|
| Repo layout (`backend/`, `frontend/`, `docs/`) | Done | Vite React + FastAPI monorepo |
| Config via `.env` | Done | Telethon + Ollama settings |
| Async `jobs.json` storage with lock | Done | Dedup by channel + message_id |
| Ollama Gemma 2 structured extraction | Done | Pydantic JSON schema prompting |
| Category discovery from samples | Done | Multi-channel aggregated discovery |
| Background scrape + extract pipeline | Done | Sequential multi-channel extraction |
| Jobs list + category filter API | Done | `GET /api/jobs`, `GET /api/categories` |
| Job sort controls | Done | `sort_by` API + Sort By dropdown |
| ControlPanel UI | Done | Multi-channel chips + discovery/extract |
| JobDashboard UI | Done | Filter, sort, refresh, apply links |
| FastAPI serves Vite `dist` | Done | Mount + SPA fallback |
| System / progress docs | Done | `docs/SYSTEM.md`, this file |
| SSE live activity stream | Done | `GET /api/stream-logs` + `activity_log.py` |
| Stage logs in Telethon / Ollama pipelines | Done | JOINING → FETCHING → OLLAMA → JOB_SAVED |
| ActivityConsole UI | Done | Sticky top-right rail on desktop |
| Dynamic feed / category sync | Done | Auto-refresh on `JOB_SAVED` |
| Multi-channel support | Done | `channels: List[str]` on discover/extract |

## Architectural Decisions

1. **Telethon + FastAPI background tasks** — Channel sampling and full extraction run as async Telethon operations; extraction is kicked off via FastAPI `BackgroundTasks` so the HTTP response returns immediately (`202`).
2. **Local Gemma 2 via Ollama** — No cloud LLM dependency. Structured outputs enforced by embedding Pydantic `model_json_schema()` in the prompt and validating the parsed JSON response.
3. **JSON file persistence** — `jobs.json` at repo root keeps the MVP simple; `asyncio.Lock` + atomic write prevents corruption under concurrent appends.
4. **Static Vite build mounted in FastAPI** — Single process serves API and UI in production; Vite dev server proxies `/api` during local frontend development.
5. **Filter at extract time** — User-selected categories/titles gate what gets persisted, reducing noise in the dashboard.
6. **SSE activity bus** — `broadcast_log(stage, message, data)` fans out structured events to all `EventSource` clients via asyncio queues + a short ring buffer.
7. **UI state sync via events** — `DISCOVERED_CATEGORIES` updates Control Board checkboxes immediately; `JOB_SAVED` debounced-triggers Job Feed + category dropdown refresh.
8. **Multi-channel aggregation** — Discovery samples each configured channel, concatenates texts, and runs a single Gemma 2 pass for a unified category/title set. Extraction walks channels sequentially on one Telethon session and streams per-channel progress (`Processing channel 2/3: @frontend_jobs...`).
9. **Top-right activity rail** — Desktop layout uses `grid-template-columns: 1fr 380px` with a sticky Activity Console so logs stay visible during long runs without scrolling past the Control Board.
10. **Sort controls** — `GET /api/jobs?sort_by=` supports `date_desc` (default), `date_asc`, `category_asc`, `title_asc`; the Job Feed dropdown persists the choice across refreshes and category filter changes, with client-side re-sort for snappy feedback.

## Future Work

- [ ] Migrate `jobs.json` → SQLite (or Postgres) with proper indexes
- [ ] Concurrent multi-channel extraction with bounded worker pool
- [ ] Pagination / infinite scroll on `GET /api/jobs`
- [ ] Session login helper UI for first-time Telethon auth
- [ ] Retry / backoff policies for Ollama and Telegram rate limits
- [ ] Export jobs as CSV
- [ ] Persist channel list across sessions (localStorage / backend config)
