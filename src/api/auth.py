from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.security import (
    create_access_token,
    get_current_user,
    hash_password,
    oauth2_scheme,
    verify_password,
    blacklist_token,
    mem_create_user,
    mem_get_user_by_username,
)
from src.core.database import get_async_session, get_optional_async_session, is_db_enabled
from src.models.database import User as ORMUser, Planet as ORMPlanet, Building as ORMBuilding
from src.core.state import game_world
from src.models import Player, Position, Resources, ResourceProduction, Buildings, BuildQueue, ShipBuildQueue, Fleet, Research, ResearchQueue, Planet as PlanetComp
from src.core.sync import load_player_into_world

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str

    def validate_basic(self) -> None:
        if len(self.username) < 3:
            raise HTTPException(status_code=400, detail="Username too short")
        if "@" not in self.email or "." not in self.email:
            raise HTTPException(status_code=400, detail="Invalid email")
        if len(self.password) < 8:
            raise HTTPException(status_code=400, detail="Password too short (min 8)")


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/register")
async def register(payload: RegisterRequest, session: Optional[AsyncSession] = Depends(get_optional_async_session)):
    payload.validate_basic()

    try:
        pwd_hash = hash_password(payload.password)
    except Exception:
        raise HTTPException(status_code=500, detail="Password hashing unavailable")

    # Config flag to optionally require start choice
    try:
        from src.core.config import REQUIRE_START_CHOICE, STARTER_PLANET_NAME
    except Exception:
        REQUIRE_START_CHOICE = False
        STARTER_PLANET_NAME = "Homeworld"

    if is_db_enabled() and session is not None:
        # Ensure unique username/email
        result = await session.execute(select(ORMUser).where(ORMUser.username == payload.username))
        if result.scalar_one_or_none() is not None:
            raise HTTPException(status_code=400, detail="Username already taken")
        result = await session.execute(select(ORMUser).where(ORMUser.email == payload.email))
        if result.scalar_one_or_none() is not None:
            raise HTTPException(status_code=400, detail="Email already in use")

        user = ORMUser(username=payload.username, email=payload.email, password_hash=pwd_hash)
        session.add(user)
        await session.flush()

        if not REQUIRE_START_CHOICE:
            # Create initial planet for the user (default location)
            planet = ORMPlanet(name=STARTER_PLANET_NAME, owner_id=user.id, galaxy=1, system=1, position=1)
            session.add(planet)

        await session.commit()
        user_id = user.id

        # Ensure ECS Player is created/loaded for the new user (when auto-start is allowed)
        if not REQUIRE_START_CHOICE:
            try:
                load_player_into_world(game_world.world, user_id)
            except Exception:
                pass
    else:
        # In-memory registration fallback
        if mem_get_user_by_username(payload.username) is not None:
            raise HTTPException(status_code=400, detail="Username already taken")
        mem_user = mem_create_user(payload.username, payload.email, pwd_hash)
        mem_user.last_login = None
        user_id = mem_user.id

        # Create ECS entity for the new user so /player/{id} works, unless start choice is required
        if not REQUIRE_START_CHOICE:
            try:
                game_world.world.create_entity(
                    Player(name=payload.username, user_id=user_id),
                    Position(),
                    Resources(metal=100000, crystal=100000, deuterium=100000),
                    ResourceProduction(),
                    Buildings(shipyard=1),
                    BuildQueue(),
                    ShipBuildQueue(),
                    Fleet(),
                    Research(),
                    ResearchQueue(),
                    PlanetComp(name=STARTER_PLANET_NAME, owner_id=user_id),
                )
            except Exception:
                pass

    return {"id": user_id, "username": payload.username, "email": payload.email}


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, session: Optional[AsyncSession] = Depends(get_optional_async_session)):
    if is_db_enabled() and session is not None:
        result = await session.execute(select(ORMUser).where(ORMUser.username == payload.username))
        user = result.scalar_one_or_none()
        if user is None or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
        try:
            if not user.password_hash or not verify_password(payload.password, user.password_hash):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=500, detail="Password verification unavailable")

        user.last_login = datetime.utcnow()
        await session.commit()
        user_id = user.id
    else:
        mem_user = mem_get_user_by_username(payload.username)
        if mem_user is None or mem_user.password_hash is None or not verify_password(payload.password, mem_user.password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
        mem_user.last_login = datetime.now(timezone.utc)
        user_id = mem_user.id

    try:
        load_player_into_world(game_world.world, user_id)
    except Exception:
        pass

    token = create_access_token(subject=str(user_id))
    return TokenResponse(access_token=token)


@router.get("/me")
async def me(current_user: ORMUser = Depends(get_current_user), session: Optional[AsyncSession] = Depends(get_optional_async_session)):
    # Determine whether the user still needs to choose a starting location
    needs_start_choice = False
    try:
        from src.core.config import REQUIRE_START_CHOICE
    except Exception:
        REQUIRE_START_CHOICE = False

    if REQUIRE_START_CHOICE:
        if is_db_enabled() and session is not None:
            try:
                from src.models.database import Planet as ORMPlanet
                result = await session.execute(select(ORMPlanet.id).where(ORMPlanet.owner_id == current_user.id))
                needs_start_choice = result.first() is None
            except Exception:
                needs_start_choice = True
        else:
            # ECS-only check: verify user has an entity with Position component
            try:
                from src.core.state import game_world as _gw
                from src.models import Player as _P, Position as _Pos
                has_pos = False
                for ent, (p, pos) in _gw.world.get_components(_P, _Pos):
                    if p.user_id == current_user.id:
                        has_pos = True
                        break
                needs_start_choice = not has_pos
            except Exception:
                needs_start_choice = True

    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "is_active": current_user.is_active,
        "created_at": current_user.created_at.isoformat() if getattr(current_user, "created_at", None) else None,
        "last_login": current_user.last_login.isoformat() if getattr(current_user, "last_login", None) else None,
        "needs_start_choice": needs_start_choice,
    }


@router.post("/logout")
async def logout(token: str = Depends(oauth2_scheme)):
    try:
        blacklist_token(token)
    except Exception:
        raise HTTPException(status_code=500, detail="Logout unavailable")
    return {"detail": "Logged out"}
