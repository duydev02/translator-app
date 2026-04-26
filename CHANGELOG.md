# Changelog

Notable user-visible changes. Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- **🔍 Inspect dialog** (Design Doc mode) — opens a tabbed window with a
  deep-dive view of the parsed SQL: per-column lineage with chosen logical
  name and ambiguity flag, list of unknown identifiers, sequential `?`
  bind positions mapped to their target column, Java placeholder map
  (`${expr}` → original Java expression with usage count), per-buffer
  breakdown for multi-StringBuffer methods (main + helper roles), validation
  warnings (column count vs. VALUES, unbound aliases, etc.), and the
  reconstructed SQL pretty-printed with right-aligned clause keywords and
  one `AND`/`OR` per line. All sections are selectable / copyable.
- **■SQL概要 stats chip** above the output (Design Doc mode) — non-copyable
  read-only Text widget summarising the parsed SQL: target table, column
  counts, source (VALUES vs SELECT), `?` binds, Java embeds, JOIN/WHERE/
  HAVING/GROUP BY/ORDER BY counts. Toggleable via `⚙ Sections ▾` →
  `■SQL概要 (statistics)`. English labels.
- **Section header counts** — every Design Doc section that lists items
  shows `(N)` in its header (`■抽出項目 (14)`, `■結合条件 (2)`,
  `■項目移送 (24)`, etc.). Inline, useful in copied docs.
- **Smart history labels** — the `History` dropdown extracts the Java
  method name and the first javadoc line so similar entries
  (`/** ... */ private String …`) are distinguishable at a glance:
  `getSyohinCdUpdateSQL()  ·  商品コード更新処理PreparedStatement作成`.
  Falls back to `SELECT/UPDATE/...` + table for raw SQL.

### Changed
- **Reconstructed SQL** in the Inspect dialog is pretty-printed with
  right-aligned clause keywords (`UPDATE`, `SET`, `WHERE`, `AND`, `OR`,
  `JOIN`, `ORDER BY`, …) and one `AND`/`OR` per line. String- and
  paren-aware, so subqueries and `'literal'` strings stay intact.

### Fixed
- **`■項目移送 (258)` count bug** — INSERT header now uses the explicit
  `(col_list)` count when present, falling back to fields/values only as
  a backstop.

## [Pre-merge of PR #3]

### Added
- **⚙ Settings menu** — Theme, Layout, Filter, Exclusions, User Map, Open file,
  Reload JSON, Line numbers, Word wrap, and Auto-paste from clipboard are now
  consolidated under a single menubutton in the top bar. The action bar keeps
  only the Translate button (Clear is on `Ctrl+⌫`).
- **Auto-paste from clipboard** — when enabled in Settings, the app watches
  the clipboard and auto-pastes content that looks like SQL or a Java
  SQL-builder method (`StringBuffer`, `.append(`, etc.) into an empty input.
  Heuristic-gated so unrelated copies aren't pasted; uses a low-frequency
  poll to work around macOS focus quirks where `<FocusIn>` doesn't fire.
- **Word wrap toggle** with a real horizontal scrollbar when wrap is off.
  Saved across sessions.
- **Hover tooltips** on mode tabs, direction tabs, Settings, Help, Translate,
  Copy, Save, History, and "+ New" tab.
- **Auto-save of doc-tab input** — every change schedules a debounced save
  (~2.5 s after the last keystroke) so the active tab's content survives
  even if the app exits via Cmd+Q, force-quit, or crash without firing
  `WM_DELETE_WINDOW`. Cmd+Q / Ctrl+Q are also bound to the close handler.
- **Multi-input doc tabs** — edit several documents side-by-side. `Ctrl+T` new,
  `Ctrl+W` close, `Ctrl+Tab` / `Ctrl+Shift+Tab` cycle. Double-click a tab title
  to rename it inline; right-click for Rename / Duplicate / Close / Close Others.
  Tabs persist across restarts (saved to `translator_settings.json`).
- **Pytest test suite** under `tests/` covering schema, translate, and designdoc
  pure logic (25 tests). Run with `pytest tests/`.

### Changed
- **Translate button** shows its shortcut inline (`▶ Translate · Ctrl+Enter`).
  The bottom status bar reports output stats (`42 lines · 1.2k chars`) instead
  of duplicating the translation count.
- **Inline Replace mode** matches lowercase / mixed-case identifiers
  (`tab_col → 名前`) by falling back to uppercase index lookups when the
  exact-case key isn't present.
- **Design Doc mode** uses the Inline mode status format
  (`Tables: N · Columns: N · Ambiguous: N`) instead of the old "(N spans)".
  The internal `SELECT_UNION` type is shown as plain `SELECT` to users.
- **UPPERCASE columns** option in Design Doc mode now also uppercases column
  references on the right side of comparisons, table aliases, and SQL keyword
  operators (`IS NULL`, `BETWEEN`, `LIKE`, `IN`, `AS`, `AND`, `OR`).
- **Hover tooltips on output spans** group entries by translated name. A
  column that exists in 50 tables but always maps to the same logical name
  now shows one summary line; ambiguous columns expand into one block per
  distinct translation.
- **Settings persistence**: Line numbers, Word wrap, and Auto-paste are
  written immediately on toggle (not just on close), so choices survive a
  force-quit.
- **Help dialog** refreshed for the new menu structure and recent shortcuts.
- **Package refactor** — the single-file `translator.py` (~4100 lines) was
  split into a `translator_app/` package with `paths`, `themes`, `config`,
  `schema`, `translate`, `designdoc`, and `ui/` (widgets, dialogs, app).
  `translator.py` is now a thin entry point. No behavior change for users.
- **PyInstaller spec** now lists `translator_app.*` explicitly as
  hidden-imports so the bundled exe is never missing a submodule.

### Fixed
- **Design Doc — `UPDATE <table> <alias> SET …`**: previously the table-alias
  shape failed to parse and emitted an empty 更新テーブル / 更新項目. The
  parser now allows an optional alias and surfaces it as `（別名：…）`.
- **Design Doc — `INSERT INTO t (SELECT …)`**: paren-aware parsing so the
  wrapped SELECT shape no longer collapses into garbage column lists when
  the inner SELECT contains nested parens (`NVL(col, 0)` etc.).
- **Design Doc — outer-paren wrapping**: an entire SQL wrapped in `( … )`
  (typical when used as a sub-select later) is now classified correctly
  instead of falling through to "Unknown SQL statement type".
- **Design Doc — `EXISTS (subquery)`**: subqueries are expanded into a
  nested SELECT block instead of being crammed onto a single line.
- **Design Doc — parenthesised condition groups**: `( a OR b )` expands to
  multiple lines (recursively) instead of staying as one raw blob.
- **Design Doc — IN-list rendering**: `IN (var)` no longer leaks Java
  concatenation operators (`( + ... + )`); structural punctuation is now
  treated as part of the SQL.
- **Design Doc — multi-StringBuffer Java**: helper buffers spliced via
  `mainSb.append(other.toString())` are inlined into the main buffer's
  output. The main buffer is detected from `return X.toString()`, the
  splice-consumer count, or any non-`.append(...)` `.toString()` call site.
- **Design Doc — comma-separated FROM**: `FROM t1 a, t2 b` is parsed into
  separate table refs (no more `b,` leaking into the alias) and listed
  under `■抽出テーブル`.
- **Design Doc — duplicate `■処理区分`**: the SELECT-block emitter no
  longer re-prints its own header when the top-level emitter already did.
- **Design Doc — string literals stay verbatim** when **UPPERCASE columns**
  is on; identifiers inside `'Active'` are no longer uppercased to
  `'ACTIVE'` (which would silently change SQL semantics).
- **Sections popup positioning** — flips above the button when the action
  bar is near the bottom of the screen and clamps to the right edge so
  the popup never spills off-screen.
- **Sections popup Esc / Done**: the borderless popup now grabs focus so
  Escape actually closes it, and a visible "✓ Done" button + "Esc to close"
  hint were added.
- **Sections popup flash** — the popup is `withdraw`-ed until positioned and
  then `deiconify`-ed, killing the brief center-of-screen flash on open.
- **Input placeholder is no longer editable** — `Ctrl+⌫` doesn't re-insert
  the placeholder while the input still has focus, and a key/click guard
  wipes the placeholder before any first interaction so the cursor never
  ends up *inside* placeholder text.
- **Design Doc mode: lowercase identifiers** — `update tr_foo set bar = ?` now
  translates correctly. Lookup keys are uppercased before hitting the schema
  index, so case in the source no longer affects translation.
- **Schema loader crash on `__comment__` key** — `load_index` now skips
  top-level non-dict entries in the schema JSON, so files using a
  `"__comment__": "..."` header (like the shipped sample) no longer break
  startup.

### Removed
- **Tracked `translator_settings.json`** — the file is per-user runtime state
  and was already in `.gitignore`; it had been committed earlier and is now
  untracked.

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
