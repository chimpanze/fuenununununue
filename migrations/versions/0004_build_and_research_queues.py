"""Add building and research queue tables

Revision ID: 0004_build_and_research_queues
Revises: 0003_indexes
Create Date: 2025-09-03 11:40:00

This migration adds two tables used for Postgres-backed persistence of
ECS queues:
- building_queue: per-planet building queue items
- research_queue: per-user research queue items

Indexes are added to support common queries by owner and completion time.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0004_build_and_research_queues"
down_revision = "0003_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:    # building_queue
    op.create_table(
        "building_queue",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("planet_id", sa.Integer(), nullable=False),
        sa.Column("building_type", sa.String(length=50), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("complete_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'pending'")),
        sa.ForeignKeyConstraint(["planet_id"], ["planets.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_build_queue_planet_id", "building_queue", ["planet_id"], unique=False)
    op.create_index("ix_build_queue_complete_at", "building_queue", ["complete_at"], unique=False)
    op.create_index("ix_build_queue_status", "building_queue", ["status"], unique=False)

    # research_queue
    op.create_table(
        "research_queue",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("research_type", sa.String(length=50), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("complete_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'pending'")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_research_queue_user_id", "research_queue", ["user_id"], unique=False)
    op.create_index("ix_research_queue_complete_at", "research_queue", ["complete_at"], unique=False)
    op.create_index("ix_research_queue_status", "research_queue", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_research_queue_status", table_name="research_queue")
    op.drop_index("ix_research_queue_complete_at", table_name="research_queue")
    op.drop_index("ix_research_queue_user_id", table_name="research_queue")
    op.drop_table("research_queue")

    op.drop_index("ix_build_queue_status", table_name="building_queue")
    op.drop_index("ix_build_queue_complete_at", table_name="building_queue")
    op.drop_index("ix_build_queue_planet_id", table_name="building_queue")
    op.drop_table("building_queue")
