import json
from pathlib import Path

import pytest

from translator_app.schema import load_index


SAMPLE_JSON = Path(__file__).parent.parent / "data" / "db_schema_output.sample.json"


@pytest.fixture
def sample_index(tmp_path):
    """Return the 5-tuple (table_index, column_index, rev_table_index,
    rev_column_index, schemas) loaded from the sample schema JSON.

    The committed sample file has a top-level '__comment__' string entry for
    human readers; load_index only expects schema→tables mappings, so we strip
    non-dict top-level keys into a temp copy before loading.
    """
    with open(SAMPLE_JSON, "r", encoding="utf-8") as f:
        raw = json.load(f)
    cleaned = {k: v for k, v in raw.items() if isinstance(v, dict)}
    tmp = tmp_path / "schema.json"
    tmp.write_text(json.dumps(cleaned), encoding="utf-8")
    return load_index(tmp)
