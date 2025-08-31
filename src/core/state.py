from __future__ import annotations

"""Shared application state.

Exports a singleton GameWorld instance that can be imported by API routers.
This avoids circular imports between routers when they need access to the
same world object.
"""

from src.core.game import GameWorld

# Global game world instance
game_world = GameWorld()

__all__ = ["game_world"]
