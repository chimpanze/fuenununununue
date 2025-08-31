"""SQLAlchemy ORM models for persistent game data.

This module defines the initial database schema aligned with the ECS components
in src/models/components.py and the roadmap in docs/tasks.md.

The schema is intentionally minimal and amenable to future migrations.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Index,
    Float,
    JSON,
)
from sqlalchemy.orm import declarative_base, relationship, Mapped, mapped_column

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255), unique=True, index=True, nullable=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False, index=True)
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    planets: Mapped[List["Planet"]] = relationship("Planet", back_populates="owner", cascade="all, delete-orphan")
    research: Mapped[Optional["Research"]] = relationship("Research", back_populates="user", uselist=False, cascade="all, delete-orphan")


class Planet(Base):
    __tablename__ = "planets"
    __table_args__ = (
        UniqueConstraint("owner_id", "galaxy", "system", "position", name="uq_owner_coord"),
        Index("ix_planets_owner_id", "owner_id"),
        Index("ix_planets_coords", "galaxy", "system", "position"),
        Index("ix_planets_last_update", "last_update"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    # Coordinates
    galaxy: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    system: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Environment
    temperature: Mapped[int] = mapped_column(Integer, default=25, nullable=False)
    size: Mapped[int] = mapped_column(Integer, default=163, nullable=False)

    # Resources (planet-local)
    metal: Mapped[int] = mapped_column(Integer, default=500, nullable=False)
    crystal: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    deuterium: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    # Production (per hour)
    metal_rate: Mapped[float] = mapped_column(Float, default=30.0, nullable=False)
    crystal_rate: Mapped[float] = mapped_column(Float, default=20.0, nullable=False)
    deuterium_rate: Mapped[float] = mapped_column(Float, default=10.0, nullable=False)

    last_update: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    owner: Mapped["User"] = relationship("User", back_populates="planets")
    buildings: Mapped[List["Building"]] = relationship("Building", back_populates="planet", cascade="all, delete-orphan")
    fleet: Mapped[Optional["Fleet"]] = relationship("Fleet", back_populates="planet", uselist=False, cascade="all, delete-orphan")


class Building(Base):
    __tablename__ = "buildings"
    __table_args__ = (
        UniqueConstraint("planet_id", "type", name="uq_building_unique_per_type"),
        Index("ix_buildings_planet_id", "planet_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    planet_id: Mapped[int] = mapped_column(ForeignKey("planets.id", ondelete="CASCADE"), nullable=False)
    # Type string matching ECS keys: 'metal_mine', 'crystal_mine', ...
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    planet: Mapped["Planet"] = relationship("Planet", back_populates="buildings")


class Fleet(Base):
    __tablename__ = "fleets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    planet_id: Mapped[int] = mapped_column(ForeignKey("planets.id", ondelete="CASCADE"), unique=True, nullable=False)

    # Counts for ship types (aligned with ECS Fleet dataclass)
    light_fighter: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    heavy_fighter: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cruiser: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    battleship: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bomber: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    colony_ship: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    planet: Mapped["Planet"] = relationship("Planet", back_populates="fleet")


class Research(Base):
    __tablename__ = "research"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)

    # Research levels (aligned with ECS Research dataclass)
    energy: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    laser: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ion: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    hyperspace: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    plasma: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="research")


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_id", "user_id"),
        Index("ix_notifications_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class TradeOffer(Base):
    __tablename__ = "trade_offers"
    __table_args__ = (
        Index("ix_trade_offers_status", "status"),
        Index("ix_trade_offers_created_at", "created_at"),
        Index("ix_trade_offers_seller_id", "seller_user_id"),
        Index("ix_trade_offers_accepted_by", "accepted_by"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    seller_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    offered_resource: Mapped[str] = mapped_column(String(20), nullable=False)
    offered_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_resource: Mapped[str] = mapped_column(String(20), nullable=False)
    requested_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    accepted_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class TradeEvent(Base):
    __tablename__ = "trade_events"
    __table_args__ = (
        Index("ix_trade_events_created_at", "created_at"),
        Index("ix_trade_events_seller", "seller_user_id"),
        Index("ix_trade_events_buyer", "buyer_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # offer_created | trade_completed
    offer_id: Mapped[int] = mapped_column(Integer, nullable=False)
    seller_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    buyer_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    offered_resource: Mapped[str] = mapped_column(String(20), nullable=False)
    offered_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_resource: Mapped[str] = mapped_column(String(20), nullable=False)
    requested_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
