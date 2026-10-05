"""
Google OAuth 2.0 + Google Calendar API (REST 직접 호출).

로그인할 때 calendar.events 권한까지 한 번에 받고, refresh token 을 users 테이블에 저장해
캘린더 등록/삭제 때마다 access token 을 새로 발급받아 쓴다.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlencode

import httpx

from app.config import get_settings
from app.models import Event

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
CALENDAR_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
SCOPES = "openid email profile https://www.googleapis.com/auth/calendar.events"


class GoogleError(Exception):
    pass


def is_configured() -> bool:
    settings = get_settings()
    return bool(settings.google_client_id and settings.google_client_secret)


def build_auth_url(state: str) -> str:
    settings = get_settings()
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "access_type": "offline",  # refresh token 발급
        "prompt": "consent",  # 재로그인 때도 refresh token 을 다시 받기 위해
        "include_granted_scopes": "true",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def _post_token(data: dict) -> dict:
    settings = get_settings()
    response = httpx.post(
        TOKEN_URL,
        data={"client_id": settings.google_client_id, "client_secret": settings.google_client_secret, **data},
        timeout=15,
    )
    if response.status_code != 200:
        raise GoogleError(f"token 요청 실패 ({response.status_code})")
    return response.json()


def exchange_code(code: str) -> dict:
    return _post_token(
        {"code": code, "grant_type": "authorization_code", "redirect_uri": get_settings().google_redirect_uri}
    )


def refresh_access_token(refresh_token: str) -> str:
    return _post_token({"refresh_token": refresh_token, "grant_type": "refresh_token"})["access_token"]


def fetch_userinfo(access_token: str) -> dict:
    response = httpx.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
    if response.status_code != 200:
        raise GoogleError(f"userinfo 요청 실패 ({response.status_code})")
    return response.json()


def build_calendar_body(event: Event) -> dict:
    """
    종일 일정으로 등록한다. (Google 종일 일정의 end 는 '다음 날'이어야 함 — exclusive)
      - start_date 가 있으면 start ~ end
      - 없으면 마감일 하루짜리
    """
    end = date.fromisoformat(event.end_date)
    start = date.fromisoformat(event.start_date) if event.start_date else end
    if start > end:
        raise ValueError('Invalid date order')
    description = "\n\n".join(part for part in [event.detail, event.source_url, "NotiAI에서 등록한 일정"] if part)
    body = {
        "summary": event.title,
        "description": description,
        "start": {"date": start.isoformat()},
        "end": {"date": (end + timedelta(days=1)).isoformat()},
    }
    if event.location:
        body["location"] = event.location
    start_time=getattr(event,'start_time','') or ''
    end_time=getattr(event,'end_time','') or ''
    timezone=getattr(event,'timezone','') or 'Asia/Seoul'
    zone=ZoneInfo(timezone)
    if start_time or end_time:
        if start_time and end_time:
            begins=datetime.fromisoformat(f'{start.isoformat()}T{start_time}').replace(tzinfo=zone)
            finishes=datetime.fromisoformat(f'{end.isoformat()}T{end_time}').replace(tzinfo=zone)
            if finishes < begins: raise ValueError('Invalid time order')
            if finishes==begins: finishes=begins+timedelta(minutes=1)
        elif end_time:
            if start!=end: raise ValueError('Missing start time for multiple days')
            begins=datetime.fromisoformat(f'{end.isoformat()}T{end_time}').replace(tzinfo=zone)
            finishes=begins+timedelta(minutes=1)
            body['description']+='\n마감 시각을 표시하는 1분 일정입니다.'
        else:
            if start!=end: raise ValueError('Missing end time for multiple days')
            begins=datetime.fromisoformat(f'{start.isoformat()}T{start_time}').replace(tzinfo=zone)
            finishes=begins+timedelta(minutes=30)
            body['description']+='\n종료 시각은 원문에 없습니다. 캘린더에만 30분 길이로 표시합니다.'
        body['start']={'dateTime':begins.isoformat(),'timeZone':timezone}
        body['end']={'dateTime':finishes.isoformat(),'timeZone':timezone}
    return body


def update_event(refresh_token: str, google_event_id: str, event: Event) -> None:
    token=refresh_access_token(refresh_token)
    body=build_calendar_body(event)
    # PATCH must explicitly clear a former location and the other date representation.
    body['location']=event.location or ''
    for key in ('start','end'):
        if 'dateTime' in body[key]: body[key]['date']=None
        else:
            body[key]['dateTime']=None
            body[key]['timeZone']=None
    response=httpx.patch(f'{CALENDAR_EVENTS_URL}/{google_event_id}',
        headers={'Authorization':f'Bearer {token}'},json=body,timeout=15)
    if response.status_code!=200:
        raise GoogleError(f'캘린더 갱신 실패 ({response.status_code})')


def insert_event(refresh_token: str, event: Event) -> str:
    token = refresh_access_token(refresh_token)
    response = httpx.post(
        CALENDAR_EVENTS_URL,
        headers={"Authorization": f"Bearer {token}"},
        json=build_calendar_body(event),
        timeout=15,
    )
    if response.status_code not in (200, 201):
        raise GoogleError(f"캘린더 등록 실패 ({response.status_code})")
    return response.json()["id"]


def delete_event(refresh_token: str, google_event_id: str) -> None:
    token = refresh_access_token(refresh_token)
    response = httpx.delete(
        f"{CALENDAR_EVENTS_URL}/{google_event_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    # 사용자가 캘린더에서 직접 지운 경우(404/410)는 성공으로 본다
    if response.status_code not in (200, 204, 404, 410):
        raise GoogleError(f"캘린더 삭제 실패 ({response.status_code})")
