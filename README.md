# Translator — Legacy Schema Helper

A lightweight GUI tool for translating SQL / Java source code between **physical**
database names (e.g. `R_SYOHIN`, `BUNRUI1_CD`) and **logical** Japanese names
(e.g. `商品マスタ`, `分類１コード`), and for generating Japanese design-document
templates directly from Java SQL-builder methods.

Built with Python + Tkinter. Ships as a single-file Windows executable.

---

## Quick start

1. Place the executable and your schema JSON in the same folder:

   ```
   TranslatorApp/
   ├── Translator.exe
   └── db_schema_output.json
   ```

2. Double-click `Translator.exe`.
3. Paste SQL / Java / design-doc text into the top pane. Translation appears live.
4. Press **F1** inside the app for the full keyboard-shortcut cheat sheet.

If `db_schema_output.json` is missing, a dialog will tell you so on startup.
See [`data/db_schema_output.sample.json`](data/db_schema_output.sample.json)
for the expected file format. The file is **gitignored** — each developer
keeps a local copy (it changes frequently and doesn't belong in version
control); copy the sample to bootstrap a fresh clone.

---

## Features

### Two translation modes

| Mode | Purpose |
|------|---------|
| **Inline Replace** | Preserves the input text, substituting names in place. Underlined words carry hover tooltips with full context (schema/table, ambiguity flag, alternate logical names). |
| **Design Doc** | Parses a Java method that builds SQL via `StringBuffer.append(...)` and emits the corresponding `■処理区分 / ■登録テーブル / ■項目移送 …` design-doc template. |

> The old *Translation Table* mode was retired in favour of Inline Replace +
> hover tooltips (which preserves surrounding SQL context) and the Schema
> Browser (`Ctrl+B`, or `Ctrl+Shift+B` to scope it to the names in your
> current input).

### Direction switching

- **Phys → Logic** — use the JSON's logical (Japanese) names for output.
- **Logic → Phys** — keep / restore physical names.

### Built-in helpers

- **🛠 Extract SQL from log…** (topbar button + `Ctrl+Shift+L`) — point at a `stclibApp.log`, browse every prepared statement grouped by user action, with the 1–2 primary business queries surfaced above infrastructure noise. The Result tab shows the SQL with `?` placeholders filled in from bound params, prettified, syntax-highlighted, and with bound values visually called out. Sortable columns; statement-type chips; auto-reload on file change; one click sends the runnable SQL into the translator's input so Inline Replace / Design Doc render against real values.
- **⚙ Filter** — multi-select schemas *and* tables. Tables list scopes to the selected schemas, hover tooltips respect the filter, and the menu label always reflects the active scope (e.g. `all 87 T` when nothing is checked). Custom entries (see User Map) always bypass filters.
- **⊘ Exclusions** — strings that must be preserved as-is. Right-click any selection to add/remove. Whole-word matching for identifiers; substring for Japanese labels.
- **🖉 User Map** — hand-curated `physical ↔ logical` overrides that win against the JSON. Edit in a table UI or in the raw `translator_custom_map.json` file.
- **⚠ Inconsistencies** — scans the JSON for columns with conflicting logical names across tables and lets you promote one variant to a User-Map override with one click.
- **Section toggles** — persistent popup to hide / show any `■` section (processing overview, SQL names, target table, WHERE, JOINs, etc.).
- **Multi-input tabs** — keep several documents open at once. Each tab has its own input, mode, and direction; filter / exclusions / theme are shared. Ctrl+T new, Ctrl+W close, Ctrl+Tab / Ctrl+Shift+Tab cycle. Double-click a tab to rename; right-click for Duplicate / Close Others. Tabs persist across restarts.
- **History** — last 10 pasted / opened inputs, one-click reload.
- **Search in output** (Ctrl+F) with match count.
- **Drag & drop** a `.sql` / `.txt` / `.md` file onto the window (requires `tkinterdnd2`).
- **Dark / Light theme**, **horizontal / vertical split**, **font zoom** (Ctrl + / Ctrl -).
- **Export** (`Ctrl+S`) and **Copy** (`Ctrl+Shift+C`) — the "未定義 (Not in JSON)" section is excluded from both.

### Design-Doc specifics

- Detects `INSERT`, `UPDATE`, `DELETE`, `SELECT`, `TRUNCATE`.
- Handles `UNION [ALL]`, derived tables (`FROM (SELECT …) X`), and `LEFT / RIGHT / FULL [OUTER] JOIN`.
- Column names are translated with **alias-scoped** lookup: `RS.SYSTEM_KB` resolves to the column as it exists in *RS*'s table, not any other table that happens to have `SYSTEM_KB`.
- Expression forms recognised:
  - `"' + var + '"` → `「引数：var」`
  - `rs.getString("COL")` → `「引数：rs」.<translated COL>`
  - `key[0]` → `「引数：key」[0]`
- If a SELECT projection list has more than **10** items, it's moved to the end of the section block (same rule applies to UPDATE's SET and INSERT's mapping).

---

## Directory layout

```
├── translator.py              thin entry point
├── Translator.spec            PyInstaller recipe
├── translator_app/            application package
│   ├── paths.py               file locations + constants
│   ├── themes.py              dark / light palettes
│   ├── config.py              settings / history / exclusions / user-map I/O
│   ├── schema.py              JSON index + filters + table_column_order
│   ├── translate.py           inline translate (both directions)
│   ├── designdoc.py           Java → SQL parser + design-doc emitter
│   ├── logsql.py              stclibApp.log parser + score + pretty_sql
│   └── ui/
│       ├── widgets.py         LineNumberCanvas, Tooltip, Toast
│       ├── app.py             TranslatorApp main window
│       └── dialogs/           filter, exclusions, user_map, inconsistency,
│                              sections, schema_browser, snippets, inspect,
│                              command_palette, log_sql, help
├── tests/                     pytest suite for pure-logic modules
├── assets/
│   ├── image.ico              application icon
│   └── version.txt            Windows VERSION resource
├── data/
│   └── db_schema_output.sample.json   example schema for new users
├── docs/
│   ├── USER_GUIDE.md
│   ├── KEYBOARD_SHORTCUTS.md
│   └── BUILD.md
└── scripts/
    ├── build.ps1              one-click PyInstaller build (PowerShell)
    └── build.bat              same for cmd.exe
```

### Runtime files (auto-created next to the exe, gitignored)

| File | Purpose |
|------|---------|
| `db_schema_output.json`       | Your schema definition — the translator's source of truth. |
| `translator_custom_map.json`  | User-defined physical↔logical overrides. |
| `translator_exclusions.txt`   | Strings preserved as-is during translation. |
| `translator_settings.json`    | Theme, layout, font size, active filters, window geometry. |
| `translator_history.txt`      | Last 10 inputs (click ⌄ History to recall). |

---

## Building from source

See [`docs/BUILD.md`](docs/BUILD.md) for full instructions. Short version:

```powershell
pip install pyinstaller tkinterdnd2
pyinstaller Translator.spec
# → dist/Translator.exe
```

Or run the helper script:

```powershell
.\scripts\build.ps1
```

---

## Development

The project is a single-file app (`translator.py`). No dependencies needed to run
from source except Python 3.10+ with Tkinter (ships with standard CPython).

```powershell
python translator.py
```

For drag-and-drop support when running from source:

```powershell
pip install tkinterdnd2
```

---

## License

MIT — see [`LICENSE`](LICENSE).
