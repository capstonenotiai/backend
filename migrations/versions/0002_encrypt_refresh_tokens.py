"""encrypt google refresh tokens — 평문으로 저장돼 있던 토큰을 암호화 (데이터만 변경)

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-30

키는 앱과 같은 규칙(app/crypto.py: TOKEN_ENCRYPTION_KEY, 없으면 SESSION_SECRET)을 사용한다.
"""
from alembic import op
import sqlalchemy as sa

from app.crypto import decrypt, encrypt


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

users = sa.table("users", sa.column("id", sa.Integer), sa.column("google_refresh_token", sa.Text))


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.select(users.c.id, users.c.google_refresh_token).where(users.c.google_refresh_token.is_not(None)))
    for user_id, token in rows.all():
        if decrypt(token) is not None:  # 이미 암호화됨
            continue
        conn.execute(users.update().where(users.c.id == user_id).values(google_refresh_token=encrypt(token)))


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.select(users.c.id, users.c.google_refresh_token).where(users.c.google_refresh_token.is_not(None)))
    for user_id, token in rows.all():
        plain = decrypt(token)
        if plain is not None:
            conn.execute(users.update().where(users.c.id == user_id).values(google_refresh_token=plain))
