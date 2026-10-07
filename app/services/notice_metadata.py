"""Normalize only dates explicitly supplied as publication/application metadata."""
import re
from datetime import date, datetime


def iso_publication(value):
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value).isoformat()
        if re.match(r"^\d{4}-\d{2}-\d{2}T", value):
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
        match = re.search(r"(?<!\d)(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})(?!\d)", value)
        if match:
            return date(*map(int, match.groups())).isoformat()
    except ValueError:
        pass
    return None


def publication_from_metadata(meta):
    for key in ("작성일", "등록일", "게시일", "등록일자", "작성일자"):
        if key in meta:
            value = iso_publication(meta[key])
            if value:
                return value
    return None


def application_deadline(meta):
    for key in ("접수기간", "접수 기간", "신청기간", "신청 기간", "공모기간", "모집기간"):
        value = meta.get(key)
        if not isinstance(value, str):
            continue
        # A labeled period can supply the year for its second endpoint.
        pieces = re.split(r"\s*[~～∼]\s*", value)
        if len(pieces) != 2:
            continue
        start, end = map(iso_publication, pieces)
        if start and not end:
            end = iso_publication(start[:4] + "." + pieces[1])
        if start and end and start <= end:
            return end
    return None
