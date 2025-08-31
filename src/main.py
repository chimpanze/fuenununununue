from __future__ import annotations

# Minimal entrypoint module for ASGI servers
# Exposes the FastAPI app constructed in src.api.routes
from src.api.routes import app

__all__ = ["app"]
