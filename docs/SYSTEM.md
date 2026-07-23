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

1. User adds one or more channel usernames in **ControlPanel** (SSE client already connected to `/api/stream-logs`). Activity Console sits in a sticky top-right rail on desktop.
2. `POST /api/discover` → Telethon samples each channel → aggregated texts → Gemma 2 returns unified `CategoryDiscoveryResult`; stages stream live to **ActivityConsole**.
3. User selects categories/titles → `POST /api/extract` starts a FastAPI `BackgroundTasks` job across all channels sequentially; progress events continue over SSE.
4. Scraper iterates each channel’s messages → extracts URLs from text, Telethon entities, and webpage previews → optionally deep-scrapes job-board pages → Gemma 2 extracts `ExtractedJob` → filter by selection → append to `jobs.json` (external apply URL preferred; `telegram_url` as fallback metadata).
5. **JobDashboard** auto-refreshes on `JOB_SAVED` (and via Refresh Feed) using `GET /api/jobs` + `GET /api/categories`, with optional `sort_by`. Supports per-job delete and **Clear All Jobs**.

## API Specification

### `POST /api/discover`

Join/access one or more channels and discover a unified category/title set from aggregated samples.

**Request body**

```json
{
  "channels": ["@tech_jobs", "@frontend_jobs", "@remote_work"]
}
```

Legacy single-channel `channel` is still accepted and merged into `channels`.

**Response `200`**

```json
{
  "channels": ["@tech_jobs", "@frontend_jobs", "@remote_work"],
  "discovered_categories": ["Engineering", "Hospitality"],
  "sample_count": 54,
  "per_channel": { "@tech_jobs": 20, "@frontend_jobs": 18, "@remote_work": 16 },
  "errors": []
}
```

**Errors**: `400` invalid/empty channels; `502` Telegram/Ollama failure.

---

### `POST /api/extract`

Trigger background scrape + AI extraction across all configured channels.

**Request body**

```json
{
  "channels": ["@tech_jobs", "@frontend_jobs"],
  "selected_categories": ["Engineering"]
}
```

**Response `202`**

```json
{
  "status": "started",
  "channels": ["@tech_jobs", "@frontend_jobs"],
  "message": "Extraction pipeline started for 2 channel(s)"
}
```

`selected_titles` is accepted for backward compatibility but ignored by the Control Board (categories-only filtering).

---

### `GET /api/jobs`

List stored jobs with optional category filter and sort order.

**Query parameters**

| Param      | Type   | Required | Description |
|------------|--------|----------|-------------|
| `category` | string | no       | Exact category filter |
| `sort_by`  | string | no       | `date_desc` (default), `date_asc`, `category_asc`, `title_asc` |

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
      "telegram_url": "https://t.me/tech_jobs_channel/12345",
      "translated_summary": "React/TypeScript role, remote-friendly, 3+ years experience."
    }
  ],
  "total": 1
}
```

---

### `DELETE /api/jobs/clear-all`

Atomically wipe `jobs.json` to `[]` (registered before `/{job_id}` so the path is not captured as an id).

**Response `200`**

```json
{
  "success": true,
  "message": "All jobs cleared successfully",
  "removed": 42
}
```

---

### `POST /api/jobs/bulk-delete`

Delete multiple jobs by `id` (or `message_id`) in one locked write.

**Request body**

```json
{ "job_ids": ["channel_123", "channel_456"] }
```

**Response `200`**

```json
{
  "success": true,
  "deleted_count": 2,
  "deleted_ids": ["channel_123", "channel_456"],
  "not_found": [],
  "message": "Deleted 2 job(s)"
}
```

---

### `DELETE /api/jobs/{job_id}`

Delete a single job by unique `id` or `message_id`.

**Response `200`**: `{ "success": true, "deleted_id": "..." }` — **404** if missing.

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

**Persistence rule**: Persist when `is_job_posting` is `true` and the job matches selected category filters (Telegram URL is injected when no external apply link exists).

### `CategoryDiscoveryResult`

| Field                   | Type       | Description |
|-------------------------|------------|-------------|
| `discovered_categories` | `List[str]`| Distinct English job categories in samples |

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
