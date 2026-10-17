"""Repair stored classifications using only ContestKorea activity lists."""
from sqlalchemy import select

from app.models import Notice
from crawler.contestkorea import activity_list_codes, post_id


def recategorize_contestkorea(db, dry_run=False):
    codes = activity_list_codes()
    changed, examples = 0, []
    for notice in db.scalars(select(Notice).where(Notice.site == 'contestkorea').order_by(Notice.id)):
        code = codes.get(post_id(notice.source_url))
        if not code:
            continue
        metadata = dict(notice.source_metadata or {})
        if (notice.board == 'activity' and metadata.get('category_code') == code
                and all(event.category == 'activity' for event in notice.events)):
            continue
        changed += 1
        if len(examples) < 5:
            examples.append(notice.title_raw)
        if not dry_run:
            notice.board = 'activity'
            notice.source_metadata = {**metadata, 'category_code': code}
            for event in notice.events:
                event.category = 'activity'
    if not dry_run:
        db.commit()
    return {'changed': changed, 'examples': examples}
