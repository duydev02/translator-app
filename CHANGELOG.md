# Changelog

Notable user-visible changes. Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- **Multi-input doc tabs** — edit several documents side-by-side. `Ctrl+T` new,
  `Ctrl+W` close, `Ctrl+Tab` / `Ctrl+Shift+Tab` cycle. Double-click a tab title
  to rename it inline; right-click for Rename / Duplicate / Close / Close Others.
  Tabs persist across restarts (saved to `translator_settings.json`).
- **Pytest test suite** under `tests/` covering schema, translate, and designdoc
  pure logic (25 tests). Run with `pytest tests/`.

### Changed
- **Package refactor** — the single-file `translator.py` (~4100 lines) was
  split into a `translator_app/` package with `paths`, `themes`, `config`,
  `schema`, `translate`, `designdoc`, and `ui/` (widgets, dialogs, app).
  `translator.py` is now a thin entry point. No behavior change for users.
- **PyInstaller spec** now lists `translator_app.*` explicitly as
  hidden-imports so the bundled exe is never missing a submodule.

### Fixed
- **Design Doc mode: lowercase identifiers** — `update tr_foo set bar = ?` now
  translates correctly. Lookup keys are uppercased before hitting the schema
  index, so case in the source no longer affects translation.
- **Schema loader crash on `__comment__` key** — `load_index` now skips
  top-level non-dict entries in the schema JSON, so files using a
  `"__comment__": "..."` header (like the shipped sample) no longer break
  startup.

## [1.0.0] — 2026-04-24

Initial tagged release after the project restructure.

### Added
- **Design Doc mode** — parse Java `StringBuffer.append(...)` SQL builders and
  emit Japanese `■処理区分 / ■登録テーブル / ■項目移送 …` design-doc
  templates. Supports `INSERT / UPDATE / DELETE / SELECT / TRUNCATE`,
  `UNION [ALL]`, derived-table subqueries, and `LEFT / RIGHT / FULL / INNER JOIN`.
- **Alias-scoped column resolution** — `RS.SYSTEM_KB` translates to SYSTEM_KB's
  meaning in *RS*'s table, even if the same column name exists elsewhere.
- **Inconsistency detector** — scans the schema JSON for columns with
  conflicting logical names across tables and offers one-click promotion to
  the User Map.
- **User Map** table editor with tabs, search, sort, keyboard shortcuts.
- **Horizontal / vertical split toggle** — `⬌ / ⬍` button in the top bar.
- **Section visibility popup** (`⚙ Sections ▾`) — stays open across multiple
  toggles; every `■`/`【…】` block can be hidden individually.
- **Filter dialog** — multi-select schemas and tables; strict filtering.
- **Inline Replace** underlines translated names, colour-coded by kind
  (blue=table, green=column, yellow=ambiguous) and hoverable for context.
- **Right-click add / remove** for exclusions; whole-word matching for
  identifiers, substring for non-word entries.
- **History** — last 10 inputs retained across sessions.
- **Search in output** (`Ctrl+F`), drag-and-drop file loading, font zoom.
- **Help overlay** (`F1`), toast notifications, window-state persistence.

### Fixed
- Scroll-bar, Combobox and PanedWindow sash now theme correctly in dark mode.
- Action bar no longer disappears when dragging the sash upward.
- `Ctrl+Enter` in the input box no longer inserts a newline alongside translate.
- `"ON"` exclusion no longer matches inside `SYSTEM_CONTROL` (whole-word rule).
- `rs.getString("X")` renders as `「引数：rs」.<translated X>` instead of
  `「引数：rs」の"X"` and the column name is translated.
- Excluded tokens are hidden from both translation and hover tooltips.
