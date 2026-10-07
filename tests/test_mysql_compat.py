"""SQLite 테스트에서는 드러나지 않는 MySQL 제약을 정적으로 확인한다."""
import re

from sqlalchemy import JSON, LargeBinary, Text

from app.db import BACKEND_ROOT, Base


def test_models_have_no_server_default_on_text_or_json():
    # MySQL 8: BLOB, TEXT, GEOMETRY or JSON column can't have a default value (1101)
    offenders = [f'{table.name}.{column.name}' for table in Base.metadata.tables.values()
                 for column in table.columns
                 if isinstance(column.type, (Text, JSON, LargeBinary)) and column.server_default is not None]
    assert offenders == []


def test_migrations_have_no_server_default_on_text_or_json():
    pattern = re.compile(r'sa\.(Text|JSON|LargeBinary)\(\).*server_default')
    offenders = [f'{path.name}:{number}' for path in sorted((BACKEND_ROOT / 'migrations' / 'versions').glob('*.py'))
                 for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1)
                 if pattern.search(line)]
    assert offenders == []
