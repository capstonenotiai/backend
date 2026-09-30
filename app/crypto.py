"""
민감한 값(Google refresh token) DB 저장용 암호화 — Fernet(AES + HMAC).

키: TOKEN_ENCRYPTION_KEY (Fernet 키). 없으면 SESSION_SECRET 에서 만든다.
  ⚠️ 키(또는 SESSION_SECRET)를 바꾸면 기존 토큰을 풀 수 없음 → 해당 사용자는 다시 로그인해야 함
  키 만들기: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.config import get_settings

log = logging.getLogger(__name__)


def _fernet() -> Fernet:
    settings = get_settings()
    if settings.token_encryption_key:
        return Fernet(settings.token_encryption_key.encode())
    digest = hashlib.sha256(f"notiai-token-key:{settings.session_secret}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str | None:
    """풀 수 없으면(키 변경, 평문 값 등) None"""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return None


class EncryptedText(TypeDecorator):
    """저장할 때 암호화, 읽을 때 복호화하는 컬럼 타입 (DB 에는 Text 로 저장)"""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else encrypt(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        plain = decrypt(value)
        if plain is None:
            log.warning("암호화된 값을 풀 수 없습니다 (키 변경 등) — 다시 로그인 필요")
        return plain
