"""Add battle/espionage reports and trade tables

Revision ID: 0005_reports_and_trade
Revises: 0004_build_and_research_queues
Create Date: 2025-09-03 15:25:00

This migration creates the remaining persistence tables aligned with
src/models/database.py for the Postgres-only persistence model:
- battle_reports
- espionage_reports
- trade_offers
- trade_events

Indexes mirror those declared in the ORM models for query performance.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0005_reports_and_trade"
down_revision = "0004_build_and_research_queues"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # battle_reports
    op.create_table(
        "battle_reports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("attacker_user_id", sa.Integer(), nullable=False),
        sa.Column("defender_user_id", sa.Integer(), nullable=True),
        sa.Column("location", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("outcome", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_battle_reports_created_at", "battle_reports", ["created_at"], unique=False)
    op.create_index("ix_battle_reports_attacker", "battle_reports", ["attacker_user_id"], unique=False)
    op.create_index("ix_battle_reports_defender", "battle_reports", ["defender_user_id"], unique=False)

    # espionage_reports
    op.create_table(
        "espionage_reports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("attacker_user_id", sa.Integer(), nullable=False),
        sa.Column("defender_user_id", sa.Integer(), nullable=True),
        sa.Column("location", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_espionage_reports_created_at", "espionage_reports", ["created_at"], unique=False)
    op.create_index("ix_espionage_reports_attacker", "espionage_reports", ["attacker_user_id"], unique=False)
    op.create_index("ix_espionage_reports_defender", "espionage_reports", ["defender_user_id"], unique=False)

    # trade_offers
    op.create_table(
        "trade_offers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("seller_user_id", sa.Integer(), nullable=False),
        sa.Column("offered_resource", sa.String(length=20), nullable=False),
        sa.Column("offered_amount", sa.Integer(), nullable=False),
        sa.Column("requested_resource", sa.String(length=20), nullable=False),
        sa.Column("requested_amount", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'open'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_by", sa.Integer(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["seller_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["accepted_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_trade_offers_status", "trade_offers", ["status"], unique=False)
    op.create_index("ix_trade_offers_created_at", "trade_offers", ["created_at"], unique=False)
    op.create_index("ix_trade_offers_seller_id", "trade_offers", ["seller_user_id"], unique=False)
    op.create_index("ix_trade_offers_accepted_by", "trade_offers", ["accepted_by"], unique=False)

    # trade_events
    op.create_table(
        "trade_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("offer_id", sa.Integer(), nullable=False),
        sa.Column("seller_user_id", sa.Integer(), nullable=False),
        sa.Column("buyer_user_id", sa.Integer(), nullable=True),
        sa.Column("offered_resource", sa.String(length=20), nullable=False),
        sa.Column("offered_amount", sa.Integer(), nullable=False),
        sa.Column("requested_resource", sa.String(length=20), nullable=False),
        sa.Column("requested_amount", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_trade_events_created_at", "trade_events", ["created_at"], unique=False)
    op.create_index("ix_trade_events_seller", "trade_events", ["seller_user_id"], unique=False)
    op.create_index("ix_trade_events_buyer", "trade_events", ["buyer_user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_trade_events_buyer", table_name="trade_events")
    op.drop_index("ix_trade_events_seller", table_name="trade_events")
    op.drop_index("ix_trade_events_created_at", table_name="trade_events")
    op.drop_table("trade_events")

    op.drop_index("ix_trade_offers_accepted_by", table_name="trade_offers")
    op.drop_index("ix_trade_offers_seller_id", table_name="trade_offers")
    op.drop_index("ix_trade_offers_created_at", table_name="trade_offers")
    op.drop_index("ix_trade_offers_status", table_name="trade_offers")
    op.drop_table("trade_offers")

    op.drop_index("ix_espionage_reports_defender", table_name="espionage_reports")
    op.drop_index("ix_espionage_reports_attacker", table_name="espionage_reports")
    op.drop_index("ix_espionage_reports_created_at", table_name="espionage_reports")
    op.drop_table("espionage_reports")

    op.drop_index("ix_battle_reports_defender", table_name="battle_reports")
    op.drop_index("ix_battle_reports_attacker", table_name="battle_reports")
    op.drop_index("ix_battle_reports_created_at", table_name="battle_reports")
    op.drop_table("battle_reports")
