"""이메일 발송. EMAIL_BACKEND=console(로그만) | smtp | none — 비용 없는 Gmail SMTP(앱 비밀번호)를 기본으로 가정."""
import logging
import smtplib
from email.message import EmailMessage

from app.config import get_settings

log = logging.getLogger(__name__)


class EmailError(Exception):
    pass


def send_email(to: str, subject: str, body: str) -> None:
    settings = get_settings()
    backend = settings.email_backend
    if backend == "none":
        return
    if backend == "console":
        log.info("[email:console] subject=%s\n%s", subject, body)
        return
    if backend != "smtp":
        raise EmailError(f"알 수 없는 EMAIL_BACKEND: {backend}")
    if not settings.smtp_username or not settings.smtp_password:
        raise EmailError("SMTP_USERNAME / SMTP_PASSWORD 가 설정되지 않았습니다.")

    message = EmailMessage()
    message["From"] = settings.email_from or settings.smtp_username
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    try:
        if settings.smtp_port == 465:
            server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20)
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20)
            server.starttls()
        with server:
            server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(message)
    except (smtplib.SMTPException, OSError) as error:
        # 예외 메시지에 계정 정보가 섞일 수 있어 종류만 남긴다
        raise EmailError(f"SMTP 발송 실패: {type(error).__name__}") from None
