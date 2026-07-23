# Telegram Job Extractor

Local full-stack app that samples Telegram job channels, discovers categories with **Ollama + Gemma 2**, extracts structured job data, stores results in `jobs.json`, and serves a React dashboard from FastAPI.

## Prerequisites

- Python 3.11+
- Node.js 18+
- [Ollama](https://ollama.com/) with `gemma2` pulled
- Telegram API credentials from https://my.telegram.org/apps

## Quick start

### 1. Environment

```bash
cp .env.example .env
# Edit .env: set TELEGRAM_API_ID and TELEGRAM_API_HASH
```

### 2. Ollama

```bash
ollama serve
ollama pull gemma2
```

### 3. Backend

```bash
cd backend
python -m venv venv

# Windows
.\venv\Scripts\activate
# macOS / Linux
# source venv/bin/activate

pip install -r requirements.txt
```

**One-time Telegram login** (interactive phone code):

```bash
python -c "from telethon.sync import TelegramClient; import os; from dotenv import load_dotenv; from pathlib import Path; load_dotenv(Path('..')/'.env'); TelegramClient(os.getenv('TELEGRAM_SESSION','telegram_job_session'), int(os.getenv('TELEGRAM_API_ID')), os.getenv('TELEGRAM_API_HASH')).start()"
```

### 4. Frontend build

```bash
cd frontend
npm install
npm run build
```

### 5. Run the app

From `backend/` with the venv active:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Open http://127.0.0.1:8000

### Dev mode (optional)

Terminal A — API:

```bash
cd backend && uvicorn main:app --reload --port 8000
```

Terminal B — Vite (proxies `/api`):

```bash
cd frontend && npm run dev
```

Open the Vite URL (usually http://127.0.0.1:5173).

## API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/discover` | Sample channel + AI category discovery |
| `POST` | `/api/extract` | Start background scrape/extract |
| `GET` | `/api/jobs?category=` | List jobs (newest first) |
| `GET` | `/api/categories` | Distinct stored categories |

See [docs/SYSTEM.md](docs/SYSTEM.md) for schemas and architecture.

## Project layout

```
backend/     FastAPI, Telethon, Ollama extractor, JSON storage
frontend/    React (Vite) ControlPanel + JobDashboard
docs/        SYSTEM.md, PROGRESS.md
jobs.json    Persisted extractions jobs
```
