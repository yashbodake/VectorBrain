"""FastAPI application entrypoint.

Router registration, CORS for the local Vite dev server, and a root health
check. Route handlers live under ``app/api``; this file stays wiring-only.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.chat import router as chat_router
from app.api.documents import router as documents_router
from app.core.config import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title="VectorBrain API",
        description=(
            "Multi-document RAG 'second brain': upload PDFs, ask questions, get "
            "answers with page-level citations."
        ),
        version="0.1.0",
    )

    # CORS — allow the Vite dev server (and only it) by default. Production
    # deployments override FRONTEND_ORIGIN in the environment.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.FRONTEND_ORIGIN],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # All document/chat routes live under /api to match docs/04.
    app.include_router(documents_router, prefix="/api")
    app.include_router(chat_router, prefix="/api")

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        """Cheap liveness probe (DB-agnostic) for uptime checks."""
        return {"status": "ok"}

    return app


app = create_app()
