"""Initial schema creation

Revision ID: 0001_initial
Revises: 
Create Date: 2025-08-29 23:40:00

This migration creates the core tables for the game server, aligned with
src/models/database.py:
- users
- planets
- buildings
- fleets
- research

It also creates the necessary unique constraints and indexes reflecting the
ORM models and docs/plan.md intentions for early database setup.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # users
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.sql.expression.true()),
        sa.UniqueConstraint("username", name="uq_users_username"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=False)
    op.create_index("ix_users_email", "users", ["email"], unique=False)

    # planets
    op.create_table(
        "planets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("galaxy", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("system", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("temperature", sa.Integer(), nullable=False, server_default=sa.text("25")),
        sa.Column("size", sa.Integer(), nullable=False, server_default=sa.text("163")),
        sa.Column("metal", sa.Integer(), nullable=False, server_default=sa.text("500")),
        sa.Column("crystal", sa.Integer(), nullable=False, server_default=sa.text("300")),
        sa.Column("deuterium", sa.Integer(), nullable=False, server_default=sa.text("100")),
        sa.Column("metal_rate", sa.Float(), nullable=False, server_default=sa.text("30.0")),
        sa.Column("crystal_rate", sa.Float(), nullable=False, server_default=sa.text("20.0")),
        sa.Column("deuterium_rate", sa.Float(), nullable=False, server_default=sa.text("10.0")),
        sa.Column("last_update", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("owner_id", "galaxy", "system", "position", name="uq_owner_coord"),
    )
    op.create_index("ix_planets_owner_id", "planets", ["owner_id"], unique=False)
    op.create_index("ix_planets_coords", "planets", ["galaxy", "system", "position"], unique=False)

    # buildings
    op.create_table(
        "buildings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("planet_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(["planet_id"], ["planets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("planet_id", "type", name="uq_building_unique_per_type"),
    )
    op.create_index("ix_buildings_planet_id", "buildings", ["planet_id"], unique=False)

    # fleets
    op.create_table(
        "fleets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("planet_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("light_fighter", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("heavy_fighter", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("cruiser", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("battleship", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("bomber", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.ForeignKeyConstraint(["planet_id"], ["planets.id"], ondelete="CASCADE"),
    )

    # research
    op.create_table(
        "research",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("energy", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("laser", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("ion", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("hyperspace", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("plasma", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("research")
    op.drop_table("fleets")
    op.drop_index("ix_buildings_planet_id", table_name="buildings")
    op.drop_table("buildings")
    op.drop_index("ix_planets_coords", table_name="planets")
    op.drop_index("ix_planets_owner_id", table_name="planets")
    op.drop_table("planets")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
