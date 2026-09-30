"""initial schema — Alembic 도입 시점의 테이블 구조

Revision ID: 0001
Revises:
Create Date: 2026-09-30

Alembic 도입 전에 create_all 로 만든 DB 는 app/db.py init_db 가 이 버전으로 stamp 한다.
"""
from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crawl_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("site", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("new_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_crawl_runs_site", "crawl_runs", ["site"])

    op.create_table(
        "notices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("site", sa.String(length=32), nullable=False),
        sa.Column("board", sa.String(length=32), nullable=True),
        sa.Column("source_url", sa.String(length=700), nullable=False),
        sa.Column("title_raw", sa.Text(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("crawled_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_url"),
    )
    op.create_index("ix_notices_crawled_at", "notices", ["crawled_at"])
    op.create_index("ix_notices_site", "notices", ["site"])

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("google_sub", sa.String(length=64), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("google_refresh_token", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("google_sub"),
    )

    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("notice_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_url", sa.String(length=1000), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("start_date", sa.String(length=10), nullable=False),
        sa.Column("end_date", sa.String(length=10), nullable=False),
        sa.Column("location", sa.String(length=255), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=True),
        sa.Column("review_status", sa.String(length=16), nullable=False),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("extractor", sa.String(length=32), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["notice_id"], ["notices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("notice_id"),
    )
    op.create_index("ix_events_end_date", "events", ["end_date"])
    op.create_index("ix_events_source", "events", ["source"])

    op.create_table(
        "preferences",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("ai_mode", sa.String(length=32), nullable=False),
        sa.Column("interests", sa.JSON(), nullable=False),
        sa.Column("enabled_sources", sa.JSON(), nullable=False),
        sa.Column("notifications", sa.JSON(), nullable=False),
        sa.Column("auto_mode_recommend", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "user_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("registered", sa.Boolean(), nullable=False),
        sa.Column("google_event_id", sa.String(length=255), nullable=True),
        sa.Column("bookmarked", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "event_id"),
    )
    op.create_index("ix_user_events_event_id", "user_events", ["event_id"])
    op.create_index("ix_user_events_user_id", "user_events", ["user_id"])


def downgrade() -> None:
    op.drop_table("user_events")
    op.drop_table("preferences")
    op.drop_table("events")
    op.drop_table("users")
    op.drop_table("notices")
    op.drop_table("crawl_runs")
