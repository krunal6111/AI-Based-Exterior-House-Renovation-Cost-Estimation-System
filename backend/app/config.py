"""Application settings and feature flags.

The prototype is *offline-first*: every stage of the pipeline works with no
network access and no API keys. The Gemini provider is strictly opt-in and is
only ever constructed when ``gemini_api_key`` is present.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> backend/app -> backend -> <project root>
APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR.parent
PROJECT_ROOT = BACKEND_DIR.parent
DATA_DIR = APP_DIR / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AI Exterior Renovation & Cost Estimator"
    api_prefix: str = "/api"

    # --- storage -------------------------------------------------------
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'renovation.db').as_posix()}"
    storage_dir: Path = PROJECT_ROOT / "storage"

    # --- image handling ------------------------------------------------
    max_upload_mb: int = 25
    #: Longest edge kept for the working ("full") image used for rendering.
    max_image_edge: int = 2048
    #: Longest edge of the image streamed to the browser canvas.
    display_edge: int = 1400

    # --- locale / costing ----------------------------------------------
    currency: str = "INR"
    currency_symbol: str = "₹"

    # --- optional AI providers (all default OFF) ------------------------
    # Two ways to authenticate with Gemini, either is enough on its own:
    #   1. AI Studio:  GEMINI_API_KEY
    #   2. Vertex AI:  VERTEX_PROJECT (+ GOOGLE_APPLICATION_CREDENTIALS env
    #      var pointing at a service-account JSON key, set outside this file)
    # Vertex AI's Gemini models currently only answer on the "global",
    # "us" or "eu" Interactions API locations -- a regional location like
    # "us-central1" 400s even though the same model shows up fine in that
    # region's model catalog.
    gemini_api_key: str | None = None
    vertex_project: str | None = None
    vertex_location: str = "global"
    #: Google's own libraries read this *process environment variable*
    #: directly -- pydantic-settings loading backend/.env does not, by
    #: itself, export anything into os.environ for other libraries to see.
    #: Declaring the field lets it come from either a real env var (already
    #: visible to Google's credential discovery, no extra work needed) or
    #: from backend/.env for convenience; get_settings() promotes the latter
    #: case into the real process environment below.
    google_application_credentials: str | None = None

    enable_ai_segmentation: bool = False
    enable_ai_render: bool = False
    ai_segmentation_model: str = "gemini-3.8-flash"
    ai_render_model: str = "gemini-nano-banana-2.1"
    #: "minimal" is documented for this model family but Vertex AI rejects it
    #: ("Thinking level is unsupported: THINKING_LEVEL_MINIMAL") while AI
    #: Studio accepts it; "low" is accepted on both, so it's the safe default.
    ai_thinking_level: str = "low"

    # --- dev -----------------------------------------------------------
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5175",
    ]

    @property
    def ai_backend(self) -> str | None:
        """Which auth path is configured, if any: 'vertex', 'api_key' or None."""
        if self.vertex_project and self.vertex_project.strip():
            return "vertex"
        if self.gemini_api_key and self.gemini_api_key.strip():
            return "api_key"
        return None

    @property
    def ai_configured(self) -> bool:
        return self.ai_backend is not None

    @property
    def ai_segmentation_available(self) -> bool:
        return self.ai_configured and self.enable_ai_segmentation

    @property
    def ai_render_available(self) -> bool:
        return self.ai_configured and self.enable_ai_render

    def project_dir(self, project_id: str) -> Path:
        d = self.storage_dir / project_id
        d.mkdir(parents=True, exist_ok=True)
        return d


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.storage_dir.mkdir(parents=True, exist_ok=True)

    # Promote a credentials path set via backend/.env into the real process
    # environment, since that is where google-auth's ADC discovery actually
    # looks. A real environment variable set outside this process already
    # takes priority and is left untouched.
    if s.google_application_credentials and not os.environ.get(
        "GOOGLE_APPLICATION_CREDENTIALS"
    ):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = s.google_application_credentials

    return s


settings = get_settings()
