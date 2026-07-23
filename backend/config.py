"""Application configuration loaded from environment variables."""

from pathlib import Path

from dotenv import load_dotenv
import os

# Load .env from repo root (parent of backend/)
_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")
load_dotenv()  # also allow cwd .env


def _resolve_path(value: str | None, default: Path) -> Path:
    if not value:
        return default
    path = Path(value)
    if not path.is_absolute():
        path = _ROOT / path
    return path


class Settings:
    def __init__(self) -> None:
        self.telegram_api_id: int = int(os.getenv("TELEGRAM_API_ID", "0") or "0")
        self.telegram_api_hash: str = os.getenv("TELEGRAM_API_HASH", "")
        session = os.getenv("TELEGRAM_SESSION", "telegram_job_session")
        session_path = Path(session)
        if not session_path.is_absolute() and not str(session_path).endswith(".session"):
            # Store session under backend/ by default
            self.telegram_session: str = str(_ROOT / "backend" / session)
        else:
            self.telegram_session = str(_resolve_path(session, _ROOT / "backend" / "telegram_job_session"))
        self.ollama_host: str = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
        self.ollama_model: str = os.getenv("OLLAMA_MODEL", "gemma2")
        self.jobs_file: Path = _resolve_path(
            os.getenv("JOBS_FILE"),
            _ROOT / "jobs.json",
        )
        self.scrape_limit: int = int(os.getenv("SCRAPE_LIMIT", "100"))
        self.sample_limit: int = int(os.getenv("SAMPLE_LIMIT", "20"))
        self.frontend_dist: Path = _ROOT / "frontend" / "dist"


settings = Settings()
