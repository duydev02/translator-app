import xlrd
import json
import re
import os
import oracledb

# --- Configuration ---
INPUT_DIR = r"C:\Users\09204100778\OneDrive - 株式会社ヴィンクス\Dang Sieu's files - MK-ReverseThietKe\04.テーブル一覧"
TARGET_FILE = "20_テーブル一覧（マスタ管理）.xls"
SCHEMA_NAME = "マスタ管理"
OUTPUT_FILE = "db_schema_output.json"

DB_HOST = "192.168.0.109"
DB_PORT = 1521
DB_SERVICE = "MD"
DB_USER = "LAWMASTER"
DB_PASSWORD = "LAWMASTER"


def parse_table_cell(cell_value):
    """Extract logical and physical table names from a combined cell.

    Expected format (newline-separated within one cell):
        商品マスタ
        （R_SYOHIN)
    Returns (logical_name, physical_name) or (None, None) if unparseable.
    """
    if not cell_value:
        return None, None

    lines = str(cell_value).strip().splitlines()
    logical_name = lines[0].strip() if lines else None

    physical_name = None
    for line in lines[1:]:
        # Match content inside full-width （）or half-width ()
        match = re.search(r'[（(]([A-Z0-9_]+)[）)]', line.strip())
        if match:
            physical_name = match.group(1)
            break

    return logical_name, physical_name


def get_db_columns(cursor, table_name):
    """Return ordered list of column names for a table from the DB.

    Queries across all accessible schemas so tables owned by schemas other
    than the login user (e.g. LAWIF) are still found.
    If the same table name exists in multiple schemas, the first owner found
    (alphabetically) is used and a warning is printed.
    """
    cursor.execute(
        """
        SELECT OWNER, COLUMN_NAME
        FROM ALL_TAB_COLUMNS
        WHERE TABLE_NAME = :tname
        ORDER BY OWNER, COLUMN_ID
        """,
        tname=table_name.upper(),
    )
    rows = cursor.fetchall()
    if not rows:
        return []

    # Group by owner; pick first owner alphabetically if multiple exist
    owners = list(dict.fromkeys(r[0] for r in rows))
    if len(owners) > 1:
        print(f"  [WARN] Table {table_name} found in multiple schemas: {owners} — using {owners[0]}")

    chosen = owners[0]
    return [r[1] for r in rows if r[0] == chosen]


def build_schema_json():
    file_path = os.path.join(INPUT_DIR, TARGET_FILE)

    print(f"Opening workbook: {file_path}")
    wb = xlrd.open_workbook(file_path)

    list_sheet_name = f"テーブル一覧（{SCHEMA_NAME}）"
    try:
        list_sheet = wb.sheet_by_name(list_sheet_name)
    except xlrd.biffh.XLRDError:
        raise RuntimeError(f"Sheet not found in workbook: {list_sheet_name}")

    print(f"Connecting to Oracle DB at {DB_HOST}:{DB_PORT}/{DB_SERVICE} ...")
    connection = oracledb.connect(
        user=DB_USER,
        password=DB_PASSWORD,
        dsn=f"{DB_HOST}:{DB_PORT}/{DB_SERVICE}",
    )
    cursor = connection.cursor()

    schema_tables = {}

    # B6 = row index 5, col index 1
    for row_idx in range(5, list_sheet.nrows):
        cell = list_sheet.cell(row_idx, 1)
        cell_value = cell.value if cell.ctype not in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK) else None

        if not cell_value:
            continue

        logical_name, physical_name = parse_table_cell(cell_value)
        if not logical_name or not physical_name:
            print(f"  [SKIP] Row {row_idx + 1}: could not parse '{cell_value!r}'")
            continue

        detail_sheet_name = f"テーブル定義書_（{logical_name}）"
        try:
            detail_sheet = wb.sheet_by_name(detail_sheet_name)
        except xlrd.biffh.XLRDError:
            print(f"  [SKIP] Detail sheet not found: {detail_sheet_name}")
            continue

        # Read logical column names from C9 downward (row 8, col 2), stop at empty
        logical_columns = []
        for detail_row in range(8, detail_sheet.nrows):
            col_cell = detail_sheet.cell(detail_row, 2)
            if col_cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK) or not col_cell.value:
                break
            logical_columns.append(str(col_cell.value).strip())

        # Fetch physical column names from Oracle in declaration order
        db_columns = get_db_columns(cursor, physical_name)

        if not db_columns:
            print(f"  [WARN] No columns found in DB for table: {physical_name}")

        columns_map = {}
        for i, db_col in enumerate(db_columns):
            columns_map[db_col] = logical_columns[i] if i < len(logical_columns) else ""

        schema_tables[physical_name] = {
            "logical_table": logical_name,
            "columns": columns_map,
        }
        print(f"  [OK] {physical_name} ({logical_name}) — {len(db_columns)} columns")

    cursor.close()
    connection.close()

    result = {SCHEMA_NAME: schema_tables}

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=4)

    print(f"\nDone! {len(schema_tables)} tables written to '{OUTPUT_FILE}'.")


if __name__ == "__main__":
    build_schema_json()
