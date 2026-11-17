"""Repair stored classifications using saved ContestKorea detail metadata."""
from collections import Counter
from sqlalchemy import select

from app.models import Notice
from app.services.category import contestkorea_metadata_board


def recategorize_contestkorea(db, dry_run=False):
    changes, event_changes = Counter(), Counter()
    skipped, unknown_examples = 0, []
    changed, examples = 0, []
    for notice in db.scalars(select(Notice).where(Notice.site == 'contestkorea').order_by(Notice.id)):
        board = contestkorea_metadata_board(notice.source_metadata)
        if board is None:
            skipped += 1
            if len(unknown_examples) < 5:
                unknown_examples.append(notice.title_raw)
            continue
        if notice.board == board and all(event.category == board for event in notice.events):
            continue
        changed += 1
        if len(examples) < 5:
            examples.append(notice.title_raw)
        if notice.board != board:
            changes[(notice.board, board)] += 1
            if not dry_run:
                notice.board = board
        for event in notice.events:
            if event.category != board:
                event_changes[(event.category, board)] += 1
                if not dry_run:
                    event.category = board
    if not dry_run:
        db.commit()
    return {'changed': changed, 'examples': examples, 'changes': changes,
            'event_changes': event_changes, 'skipped': skipped, 'unknown_examples': unknown_examples}
