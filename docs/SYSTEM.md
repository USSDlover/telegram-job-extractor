# System Architecture — Telegram Job Extractor

## Overview

Single-repository application that joins Telegram channels, discovers job categories via local LLM sampling, scrapes matching posts in the background, extracts structured job data with Ollama (Gemma 2), persists results to `jobs.json`, and serves a React dashboard from FastAPI.

## System Diagram & Data Flow

```
┌─────────────────┐     Telethon (asyncio)      ┌──────────────────┐
│ Telegram Channel│ ───────────────────────────► │  scraper.py      │
│  (@channel)     │   sample / full history      │  TelegramClient  │
└─────────────────┘                              └────────┬─────────┘
                                                          │
                          message texts                   │
                                                          ▼
                                                 ┌──────────────────┐
                                                 │ ai_extractor.py  │
                                                 │ Ollama + gemma2  │
                                                 │ Pydantic schemas │
                                                 └────────┬─────────┘
                                                          │
                              broadcast_log(stage, …)     │
                                          │               │
                                          ▼               ▼
                                 ┌──────────────┐   ExtractedJob
                                 │ activity_log │         │
                                 │ SSE bus      │         ▼
                                 └──────┬───────┘  ┌──────────────┐
                                        │          │  storage.py  │
                                        │          │  jobs.json   │
                                        │          └──────┬───────┘
                                        │                 │
          GET /api/stream-logs          │    GET /api/jobs│/categories
                                        ▼                 ▼
┌─────────────────┐     Static mount /           ┌──────────────────┐
│    main.py      │ ───────────────────────────► │ React UI         │
│    FastAPI      │   frontend/dist              │ ControlPanel +   │
│                 │ ◄─────────────────────────── │ ActivityConsole +│
└─────────────────┘   POST /api/discover|extract │ JobDashboard     │
```

### Flow Summary

1. User enters a channel username in **ControlPanel** (SSE client already connected to `/api/stream-logs`).
2. `POST /api/discover` → Telethon samples ~20 recent text posts → Gemma 2 returns `CategoryDiscoveryResult`; stages stream live to **ActivityConsole**.
3. User selects categories/titles → `POST /api/extract` starts a FastAPI `BackgroundTasks` job; progress events (`EXTRACTION_PROGRESS`, `JOB_SAVED`) continue over SSE.
4. Scraper iterates channel messages → Gemma 2 extracts `ExtractedJob` → filter by selection → append to `jobs.json`.
5. **JobDashboard** auto-refreshes on `JOB_SAVED` (and via Refresh Feed) using `GET /api/jobs` + `GET /api/categories`.

## API Specification

### `POST /api/discover`

Join/access a channel and discover job categories from recent samples.

**Request body**

```json
{
  "channel": "@tech_jobs_channel"
}
```

**Response `200`**

```json
{
  "channel": "@tech_jobs_channel",
  "discovered_categories": ["Engineering", "Hospitality"],
  "suggested_titles": ["Frontend Developer", "Full Stack", "Waiter"],
  "sample_count": 20
}
```

**Errors**: `400` invalid channel; `502` Telegram/Ollama failure.

---

### `POST /api/extract`

Trigger background scrape + AI extraction for selected filters.

**Request body**

```json
{
  "channel": "@tech_jobs_channel",
  "selected_categories": ["Engineering"],
  "selected_titles": ["Frontend Developer", "Full Stack"]
}
```

**Response `202`**

```json
{
  "status": "started",
  "channel": "@tech_jobs_channel",
  "message": "Extraction pipeline started in background"
}
```

---

### `GET /api/jobs`

List stored jobs, newest first.

**Query parameters**

| Param      | Type   | Required | Description                          |
|------------|--------|----------|--------------------------------------|
| `category` | string | no       | Exact category filter (case-sensitive) |

**Response `200`**

```json
{
  "jobs": [
    {
      "id": "channel_msg_12345",
      "channel": "@tech_jobs_channel",
      "message_id": 12345,
      "date": "2026-07-20T14:30:00+00:00",
      "is_job_posting": true,
      "title": "Frontend Developer",
      "category": "Engineering",
      "company": "Acme Corp",
      "apply_links": ["https://boards.greenhouse.io/acme/jobs/1"],
      "translated_summary": "React/TypeScript role, remote-friendly, 3+ years experience."
    }
  ],
  "total": 1
}
```

---

### `GET /api/categories`

Distinct non-empty categories present in `jobs.json`.

**Response `200`**

```json
{
  "categories": ["Engineering", "Hospitality"]
}
```

---

### `GET /api/stream-logs`

Server-Sent Events (SSE) activity stream. Clients connect with `EventSource`.

**Event payload**

```json
{
  "stage": "EXTRACTION_PROGRESS",
  "message": "Processing post 4/100 through Ollama...",
  "data": { "current": 4, "total": 100 },
  "ts": "2026-07-23T17:00:00+00:00"
}
```

Common stages: `JOINING_TELEGRAM`, `FETCHING_POSTS`, `CALLING_OLLAMA`, `DISCOVERED_CATEGORIES`, `EXTRACTION_PROGRESS`, `JOB_SAVED`, `EXTRACTION_DONE`, `ERROR`.

---

### Static UI

In production, FastAPI mounts `frontend/dist` at `/` after API routes. SPA fallback serves `index.html` for non-API paths.

## Local AI Structured Output Schemas

Schemas are Pydantic v2 models; Ollama is instructed to return JSON matching `model_json_schema()`.

### `ExtractedJob`

| Field                | Type           | Description |
|----------------------|----------------|-------------|
| `is_job_posting`     | `bool`         | Whether the message is a job posting |
| `title`              | `str`          | Normalized English title |
| `category`           | `str`          | Job category classification |
| `company`            | `Optional[str]`| Employer name if present |
| `apply_links`        | `List[str]`    | External apply / contact URLs |
| `translated_summary` | `str`          | Concise English summary of requirements |

**Persistence rule**: Only persist when `is_job_posting` is `true` **and** `apply_links` is non-empty (and matches selected category/title filters when provided).

### `CategoryDiscoveryResult`

| Field                   | Type       | Description |
|-------------------------|------------|-------------|
| `discovered_categories` | `List[str]`| Distinct job categories in samples |
| `suggested_titles`      | `List[str]`| Normalized job titles for UI checkboxes |

## Configuration

| Variable            | Purpose                          | Default                |
|---------------------|----------------------------------|------------------------|
| `TELEGRAM_API_ID`   | Telethon API ID                  | (required)             |
| `TELEGRAM_API_HASH` | Telethon API hash                | (required)             |
| `TELEGRAM_SESSION`  | Session file basename            | `telegram_job_session` |
| `OLLAMA_HOST`       | Ollama base URL                  | `http://127.0.0.1:11434` |
| `OLLAMA_MODEL`      | Model name                       | `gemma2`               |
| `JOBS_FILE`         | Path to JSON store               | `../jobs.json`         |
| `SCRAPE_LIMIT`      | Max messages per extract run     | `100`                  |

## Concurrency & Safety

- `storage.py` uses `asyncio.Lock` around read-modify-write of `jobs.json`.
- Background extraction never raises into the request handler; per-message AI/Telegram errors are logged and skipped.
- Deduplication key: `{channel}_{message_id}`.
