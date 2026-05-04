# Changelog

Notable user-visible changes. Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- **Schema-aware autocomplete** in the input box — typing 2+ characters
  of a known table or column pops a small list of matches (tables first,
  then columns, deduped, capped at 10). `↓`/`↑` navigate, `Tab` / `Enter`
  / `→` accept, `Esc` or click-elsewhere dismiss. Reads the live indexes
  so User Map overrides are picked up immediately.
- **84 new tests** (163 total): `tests/test_designdoc_extras.py` covers
  `_pretty_sql`, `_is_paren_group`, multi-buffer detection, `_parse_*`
  edge cases, outer-paren stripping, placeholder resolution, and the
  `compute_design_*` helpers consumed by Inspect; `tests/test_dialog_helpers.py`
  covers the command-palette fuzzy matcher, snippet name extraction, and
  schema-browser data builders; `tests/test_autocomplete.py` exercises
  the new suggestion engine.

### Changed
- **`app.py` split** — extracted the keyboard-shortcut registration table
  (`translator_app/ui/keybindings.py`) and all save/load logic
  (`translator_app/ui/persistence.py`) into focused sibling modules. The
  duplicated 50-line settings-dump that used to live in both `on_close`
  and `_persist_doc_tabs` is now a single helper. `app.py` is down by
  ~100 lines; future dialog/feature work no longer has to scroll past
  unrelated boilerplate.

### Added
- **🛠 Extract SQL from log… UX overhaul** — the dialog now shows a
  row of **chip buttons** at the top, one per recent log path
  (labelled with the last 2 path segments, e.g.
  `…/mdw_lawmasterhansoku-web/log`). Click a chip to switch logs
  instantly; right-click for *Remove* / *Open containing folder*. Up
  to 8 chips, and a **+ Add log** button picks a new one. Replaces
  the old dropdown — designed for hopping between sub-projects
  (`lawmasterhansoku-web` ↔ `lawdailyorder-web` …) without re-typing
  paths. The full active path is shown in monospace below the chip
  strip.
- **Auto-reload (mtime poll)** — a new `☑ Auto` checkbox next to
  Reload watches the active log file's mtime while the dialog is
  open and auto-re-parses on change (~1.5 s lag). Selection is
  preserved across reloads so you don't lose your place when the
  server appends new entries. Toggleable; persisted per project in
  `translator_settings.json`.
- **Extract SQL — Tier-1 polish pass** — four UX upgrades to the
  Result tab and statement list:

  - **Syntax highlighting on Result** — keywords, string literals,
    numbers, and `--` / `/* */` comments now render in distinct
    colors (theme-aware: blue/green/purple/grey on light, softer
    VS-Code-ish palette on dark). A new `tokenize_sql_for_highlight`
    function in `logsql.py` produces the offset list; the dialog
    applies tags to the Tk Text widget.
  - **Bind-substituted values are visually called out** — values
    that came from `?` placeholders (e.g. `'0018      '`, `42`) get
    an accent foreground + subtle background tint so at a glance
    you can tell what came from the original SQL vs what the JDBC
    driver bound. Implemented via sentinel markers (`\x01…\x02`)
    that survive `pretty_sql` and are stripped at the last moment,
    recovering the (start, end) ranges in the final coordinates.
    New helpers: `combine_sql_params_marked` /
    `extract_subst_ranges` / `SUBST_OPEN` / `SUBST_CLOSE`.
  - **Sortable column headers** — click `Time` / `ID` / `DAO` /
    `Type` / `Tables` / `?` / `Score` to sort the statement list;
    click again to flip direction. A `▼` / `▲` glyph next to the
    active column makes the state obvious. Sort happens *within*
    each action group so the grouping isn't lost. Default direction
    is descending for `Score` and `?`-count (most-useful-first),
    ascending for the rest.
  - **Statement-type filter chips** — a second row in the filter
    bar with checkboxes for `SELECT / INSERT / UPDATE / DELETE /
    OTHER`, plus `All` / `None` shortcuts. State persists per
    project in `translator_settings.json`. Most days you only care
    about one type — flipping checkboxes is faster than typing
    `SELECT` into search.

- **Extract SQL is now a one-click topbar button** — the previous
  `🛠 Tools` menu (which had only one entry) is replaced by a direct
  `🛠 Extract SQL` button on the topbar. One click instead of two,
  same icon, same `Ctrl+Shift+L` shortcut, same right-click and
  command-palette entries. If more developer tools land later we'll
  re-introduce the menubutton — for now the menu was pure overhead
  on the most-used tool in the dialog suite.
- **In-tab Copy button + Auto-copy toggle on the Result pane** — the
  Result tab now has its own toolbar with a `📋 Copy` button (so the
  primary action is right next to the content, not hidden in the
  bottom button row) and an `☑ Auto-copy` checkbox. When Auto-copy
  is on, every statement you click in the list is silently pushed to
  the clipboard with a quick toast confirmation — pick a row, paste
  into your DB tool, no extra clicks. Toggling the checkbox while a
  statement is already selected copies it immediately so the toggle
  takes effect for what you're currently looking at. Persisted in
  `translator_settings.json`.
- **Result tab is first + prettified** — the detail notebook order is
  now Result → SQL → Params (Result is the value users came for). A
  new lightweight `pretty_sql()` formatter inserts newlines before
  major clauses (SELECT / FROM / WHERE / ORDER BY / GROUP BY /
  HAVING / UNION / WITH / VALUES / INSERT INTO / SET / MERGE INTO /
  LIMIT / OFFSET / FETCH FIRST), indents each JOIN under FROM, and
  indents AND/OR connectives under their parent clause. Quote-aware
  (skips keywords inside `'…'` literals), works on both browse-mode
  results and Direct-mode results, and is what `Copy result` /
  `Send to translator input` ship out (so what you see is what you
  get). Settings auto-migrate to add `auto_reload: true`.
- **🛠 Tools menu + Extract SQL from log…** (`Ctrl+Shift+L`) — new
  developer utility that parses an entire `stclibApp.log`, surfaces
  the 1–2 *primary* business queries above the dozens of
  infrastructure calls, and combines `?` placeholders with bound
  parameters into a runnable SQL.

  **Browse mode** (default): pick a log from a recent-paths dropdown
  (up to 8 — designed for hopping between sub-projects like
  `lawmasterhansoku-web` and `lawdailyorder-web`); the dialog parses
  the whole file and shows every prepared statement in a treeview,
  grouped under the user request that triggered each batch
  (`commons.struts.RequestProcessor,callMethod` markers, with
  1-second time-gap fallback for orphans). Each statement carries its
  timestamp, hex id, DAO short name, statement type, target tables,
  bind count, and a primary-vs-noise score; statements scoring ≥ 30
  get a ★ tag. A 🔎 search box filters by id / DAO / table / SQL
  substring. **☑ Hide infrastructure** (on by default) shows only ★
  statements. Click a row → SQL / Params / Result render in tabs
  below. The first ★ statement is auto-selected after each load.

  **Scoring** combines DAO package signal (configurable
  `noise_packages` defaults to `swc.commons` + `mdware.common`;
  optional `primary_packages` adds a +50 bonus per match), SQL length,
  `WITH` / `JOIN` / `UNION` presence, bound-param count, and a
  noise-table list (`SYSTEM_CONTROL`, `DT_TABLE_LOG`, `R_MESSAGE`,
  `R_DICTIONARY_CONTROL`, `R_NAMECTF`). All thresholds and lists are
  per-project in `translator_settings.json`. A 2-pass FQCN fill
  ensures statements whose `InvokeDao` line appears *after* their
  init line (e.g. when the log file starts mid-stream) still get the
  right DAO attribution.

  **Direct mode** is preserved as a fallback for users who already
  have the SQL + param blob on the clipboard and don't have the log
  file handy — three side-by-side panes with a Process button, same
  combiner.

  **Result actions**: *Copy result* (clipboard) and *Send to
  translator input* (drops the runnable SQL into the active doc tab
  and re-runs translation immediately, so Inline Replace / Design Doc
  render against real values).

  **Architecture**: a new `🛠 Tools` menubutton sits next to
  `⚙ Settings` in the top bar; future developer utilities slot in
  under there without crowding the translation UI. Param formatter
  recognises `STRING / CHAR / VARCHAR / CLOB / INT / BIGINT / DECIMAL
  / DOUBLE / FLOAT / DATE / TIMESTAMP / TIME / NULL / BOOLEAN / BYTES
  / BLOB`; unknown types fall back to single-quoted strings.
  Quote-aware substitution skips `?` inside string literals.
  Reachable from the Tools menu, the command palette, the input
  pane's right-click context menu, and `Ctrl+Shift+L` /
  `Cmd+Shift+L`.

  Settings auto-migrate from the v1 single-`last_path` shape to the
  v2 `recent_paths` list on first open, so existing users don't lose
  their saved path.
- **`Ctrl+Shift+B` — Schema Browser scoped to input names** — collects
  every physical identifier in the current input that the translator
  knows about, opens the Schema Browser, and pre-filters both panes
  to just those tables/columns. A "Showing only N name(s) found in
  input" banner appears with a *Clear filter* button. Replaces the
  old Translation Table mode's "list every name in this paste" view
  with a sortable, searchable, copy-friendly one. Also reachable from
  the command palette.
- **Inspect dialog redesign** — header summary cards (`Statement`, `Target`,
  `Columns`, `? binds`, `Warnings ✓`, plus `Ambiguous` / `Unknown` when
  relevant), per-tab search box with row counters, sortable column headers
  (click to sort), double-click any row to copy its primary value, and
  syntax highlighting in the Reconstructed SQL tab (keywords / strings /
  `${placeholders}` / numbers). New `↻ Refresh` button re-parses the
  current input in place. Keyboard shortcuts: `Ctrl/Cmd+1..6` jump to
  tabs, `Esc` closes.
- **First-run welcome toast** pointing new users at `F1` and `Cmd/Ctrl+P`.
  Shown once, tracked via a `welcomed: true` setting.
- **Auto-focus input on launch** so users can paste / type immediately
  without clicking.
- **Drag-drop visual feedback** — translucent accent overlay across the
  input box reading `📂 Drop file here to load` while a file is being
  dragged in. Hides on drop or leave.
- **Right-click context menus** on input and output:
  - Output: `⎘ Copy all output`, `💾 Save…`, `Find in output`, and
    `🔍 Inspect SQL` (Design Doc mode).
  - Input: `✕ Clear input`, `📋 Save as snippet`, `📂 Open file`.
  - Selected text in either: `Copy selection`, `Add column → User Map`
    (with a quick logical-name prompt that re-runs translation
    immediately).
- **New shortcuts**: `Cmd/Ctrl+B` opens Schema Browser, `Cmd/Ctrl+J`
  opens Snippets. Settings menu items now show their accelerators.
- **📚 Schema Browser** — searchable two-pane window listing every physical
  table with its logical name and the columns of the selected table, with
  hints pointing to other tables that share the same column. Actions to copy
  the physical or logical name to the clipboard and to add a column to the
  User Map. Open via Settings → `📚 Schema Browser…` or the command palette.
- **📋 Snippets** — save the current input as a named snippet (with optional
  comma-separated tags) and reload it later. Snippet name auto-suggested
  from the Java method signature when present. Search across name/tags/
  content; double-click or Enter to load. Persisted in
  `translator_settings.json` under a `snippets` array.
- **⌘ Command palette** — `Cmd+P` / `Ctrl+P` opens a fuzzy-search popup over
  all commands (mode/direction switches, every dialog, settings toggles,
  doc-tab navigation, zoom, file ops, help). Substring matches rank above
  fuzzy matches; categories shown next to each entry.
- **Line-number alignment fix** — line numbers in the input/output gutter
  now anchor to each line's exact paint position via Tk's `dlineinfo`
  (IDLE-style). Numbers track text rows correctly through font zoom,
  word-wrap toggles, and blank lines.
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
- **Friendlier parse errors** — `java_to_design_doc()` now returns an
  explanatory message with concrete next-step hints when the Java
  doesn't parse, no SQL is found, or the statement type is unknown
  (instead of one-line cryptic notes).

### Removed
- **Translation Table mode retired** — Inline Replace + hover tooltips
  strictly dominated it for reading code (it preserved the surrounding
  SQL, the table mode dumped a wall of text), and the Schema Browser
  handles standalone lookups better. New `Ctrl+Shift+B` opens the
  Schema Browser pre-scoped to the physical names found in the current
  input, replacing the table mode's "list every name in this paste"
  use case with a sortable, copy-friendly view. Settings/tabs that had
  `mode: "table"` saved are migrated to `mode: "inline"` on load. The
  `translate_table_mode` and `translate_reverse_table_mode` functions
  and the `_render_table` UI path are gone, along with their tests.
- **`db_schema_output.json` is no longer tracked in git** — the file is
  local data that changes frequently per developer and was already
  listed in `.gitignore` (it had been added before the ignore rule).
  Untracking stops phantom `M db_schema_output.json` from cluttering
  `git status`. Pull this change and keep your existing local copy;
  new clones can copy `data/db_schema_output.sample.json` to bootstrap.

### Changed
- **Schema Browser column order now matches the JSON** — when a table is
  selected, its columns render in the order they're declared in
  `db_schema_output.json` (i.e. the natural DB definition order), not
  forced A–Z. `load_index()` now also returns a `table_column_order`
  map that the dialog uses; the API gained one return value (callers
  destructure six values instead of five).
- **"Add column → User Map" → "Override logical name…"** — the button
  used to silently dump the existing `phys → logical` pair into the
  User Map (where it added nothing), forcing users to open the User
  Map dialog separately to actually edit. Now it opens a small inline
  prompt prefilled with the current logical name (or the existing
  override, if any), saves on Enter, re-runs translation immediately,
  and exposes a "Remove override" button when one already exists. A
  hover tooltip on the button explains what the override is for.

### Fixed
- **Schema Browser search no longer fights itself** — the single search
  box that filtered both the Tables tree *and* the Columns tree was the
  reason that selecting `R_SYOHIN` after searching for it produced an
  empty columns view (the same query was excluding rows like
  `SYOHIN_CD`). The dialog now has **two scoped searches**, one above
  each pane: `🔎 tables` filters tables only, `🔎 columns` filters
  columns only. Each pane shows its own row count. A `Show all` button
  appears in the columns search bar while a table is selected, so one
  click brings the global column list back. `Esc` inside a search
  entry clears it (so it takes one keystroke to escape a stale query)
  before falling through to the dialog-level Esc-to-close.
- **Filter scope leak** — selecting only a schema in the Filter dialog
  no longer lets tables from other schemas slip through. The Tables
  list now scopes to the selected schemas (out-of-scope checkboxes are
  hidden and auto-uncleared), Apply prunes any stale table selections
  whose phys-table doesn't belong to a chosen schema, and the hover
  tooltip in the output now respects the active schema/table filter
  instead of always showing every entry from every schema.
- **Filter button label clarity** — the Settings → Filter… entry and
  the post-Apply toast now spell out totals (`all 87 T`, `1/20 S · all
  87 T`) so an empty selection clearly reads as "everything in scope"
  instead of `0`. When schemas are selected, the table total reflects
  tables in those schemas only.
- **Stats target shows variable name, not `0`** — when the target table
  is a Java variable (`UPDATE <buffer> + tableName + " SET ..."`), the
  Inspect dialog summary card and the stats chip resolve the placeholder
  to `${tableName}` instead of leaking the bare placeholder index.
- **Horizontal layout alignment** — when the `■SQL概要` stats bar is
  shown above the output, the input pane reserves a matching invisible
  spacer so line 1 of each side stays vertically aligned.
- **No more `(N)` count noise in the design doc text** — section
  counts like `■項目移送 (24)` were being copied along with the doc.
  Counts now live only in the out-of-band stats chip and Inspect dialog.
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
