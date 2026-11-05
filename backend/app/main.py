"""FastAPI application entry point.

Run with:  uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import init_db
from .routers import catalog, projects
from .schemas import HealthOut
from .services.image_io import register_openers

VERSION = "0.1.0"

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s"
)
log = logging.getLogger("renovation")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    register_openers()
    log.info("Storage: %s", settings.storage_dir)
    log.info(
        "AI providers: segmentation=%s render=%s (offline renderer is always on)",
        settings.ai_segmentation_available,
        settings.ai_render_available,
    )
    yield


app = FastAPI(
    title=settings.app_name,
    version=VERSION,
    description=(
        "Pre-construction planning assistant: upload a photo of a house "
        "exterior, mark its surfaces, try materials on it, and get measured "
        "quantities and a transparent cost estimate. Runs fully offline."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(catalog.router, prefix=settings.api_prefix)
app.include_router(projects.router, prefix=settings.api_prefix)


@app.get(f"{settings.api_prefix}/health", response_model=HealthOut, tags=["meta"])
def health():
    return HealthOut(
        status="ok",
        version=VERSION,
        offline_mode=not settings.ai_configured,
        ai_segmentation_available=settings.ai_segmentation_available,
        ai_render_available=settings.ai_render_available,
    )
