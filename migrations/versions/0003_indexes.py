"""Add indexes on timestamps for users and planets

Revision ID: 0003_indexes
Revises: 0002_notifications
Create Date: 2025-08-31 00:08:00

This migration adds performance indexes aligned with docs/tasks.md:
- users.created_at
- users.last_login
- planets.last_update

Coordinates and user_id indexes already exist from earlier migrations.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0003_indexes"
down_revision = "0002_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Users timestamp indexes
    op.create_index("ix_users_created_at", "users", ["created_at"], unique=False)
    op.create_index("ix_users_last_login", "users", ["last_login"], unique=False)

    # Planets timestamp index
    op.create_index("ix_planets_last_update", "planets", ["last_update"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_planets_last_update", table_name="planets")
    op.drop_index("ix_users_last_login", table_name="users")
    op.drop_index("ix_users_created_at", table_name="users")
