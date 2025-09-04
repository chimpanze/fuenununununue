# Avoid importing heavy modules on package import to prevent circular imports in tests.
# Import directly from src.api.routes where needed (e.g., `from src.api.routes import app`).

__all__ = []
