# User Guide

## Modes

### Inline Replace
Same text you pasted, with physical names substituted in-place. Each replaced
name is **underlined** and carries a hover tooltip:

| Colour | Meaning |
|--------|---------|
| Blue underline | Table name |
| Green underline | Column name |
| Yellow underline | Ambiguous (more than one logical name exists) |

### Design Doc
Paste a Java method that builds SQL via `StringBuffer.append(...)`; the output
is the equivalent Japanese design document.

#### What it handles

- `INSERT`, `UPDATE`, `DELETE`, `SELECT`, `TRUNCATE`
- `UNION [ALL]`
- Derived tables: `FROM (SELECT ... UNION ALL ...) alias`
- All JOIN flavours (`INNER / LEFT / RIGHT / FULL [OUTER] / CROSS`)
- Expression recognition inside `append()` arguments:
  - `"'" + var + "'"` → `「引数：var」`
  - `rs.getString("COL")` → `「引数：rs」.<translated COL>`
  - `key[0]` → `「引数：key」[0]`
  - Method calls / constants (e.g. `Foo.bar()`) — passed through verbatim
- Alias-scoped column resolution. `RS.SYSTEM_KB` resolves to the `SYSTEM_KB`
  that actually lives in *RS*'s table, never any other.

#### Layout rule

If a projection list (`SELECT` fields, `UPDATE SET`, `INSERT` mapping) has
**more than 10 items**, the projection section is moved to the **end** of its
block for readability. 10 or fewer keeps the projection at the top.

---

## Direction

The `Phys → Logic` / `Logic → Phys` toggle decides which side you want to see
in the output. For Design Doc specifically:

- **Phys → Logic**: column names appear as Japanese logical names.
- **Logic → Phys**: column names stay in their original physical form.

---

## Filter (⚙)

Multi-select which schemas and tables are considered.

- Strict: if a column has no entry in a selected schema/table, it's simply not
  translated (rather than silently falling back).
- **Schema-scoped tables list**: ticking a schema hides tables that don't
  belong to it (and auto-clears any that were previously checked). Untick all
  schemas to see every table again. Apply additionally drops stale table
  selections that fall outside the selected schemas.
- **Hover tooltip respects the filter** too — the popup over a translated
  word only lists schemas/tables that match your current filter.
- **Empty = all** — when nothing is checked the menu label and the post-Apply
  toast still show counts (e.g. `all 20 S · all 1343 T`). When schemas are
  selected, the table total switches to the count *within* those schemas.
- User-Map entries **always** bypass filters — team overrides still apply.

---

## Exclusions (⊘)

Strings that must be preserved as-is during translation *and* hidden from
hover tooltips.

- **Quick add/remove**: select text in any pane, right-click, pick from menu.
- **Bulk edit**: click `⊘ Exclusions` to open the editor dialog (Ctrl+D deletes
  current line, Ctrl+Z/Y undo/redo).
- **Whole-word rule**: entries that are pure identifiers (A-Z, digits, `_`)
  match only as whole words — so `ON` stays silent in SQL keywords while
  leaving `SYSTEM_CONTROL` untouched.
- **Substring rule**: entries with any non-word character (e.g. `■処理区分`)
  match as a substring — useful for Japanese section headers.

Persisted to `translator_exclusions.txt` (one per line).

---

## User Map (🖉)

Hand-curated `physical ↔ logical` overrides that trump whatever is in
`db_schema_output.json`.

- Two tabs: **Tables** and **Columns**.
- Click a row to edit, `Del` to remove.
- Enter-in-Physical jumps to Logical; Enter-in-Logical applies.
- **Open JSON file** button lets you edit the file directly.
- **Inconsistencies…** opens the detector (see below).

Persisted to `translator_custom_map.json`.

---

## Inconsistency detector (⚠)

Scans the schema JSON for columns whose logical name differs across tables
(e.g. `SYSTEM_KB` = "システム区分" in 5 tables, "システム" in 1 table).

- Lists every conflict with the variant counts and table contexts.
- Select any variant row → **Apply picks to User Map** → saved as an
  override that wins everywhere.
- **📄 Export CSV** for sharing the report with teammates.

---

## Sections popup (⚙ Sections ▾)

Per-section visibility toggles. The popup stays open across multiple clicks
so you can flip several sections without reopening. Closes on click-outside
or `Esc`.

Covers every `■` block and the `【SQL論理名】 / 【SQL定義名】` header lines.

---

## Schema Browser (Ctrl+B)

Searchable two-pane window for looking up tables and columns without leaving
the app. Tables on the left, columns of the selected table on the right
(both physical *and* logical names side-by-side).

- **Per-pane search**: `🔎 tables` filters only the Tables tree;
  `🔎 columns` filters only the Columns tree — they don't fight each other,
  so picking R_SYOHIN and then typing a column name works as expected.
- **Show all** (next to the columns search) clears the table selection so
  the global column list comes back.
- **Column order**: when a table is selected, columns are listed in their
  **JSON declaration order** (i.e. the same order as in `db_schema_output.json`),
  not A–Z, so the view matches the actual DB definition.
- **Esc** inside a search box clears it before falling through to closing
  the dialog.
- **Ctrl+Shift+B** opens it scoped to names found in the current input.
  A "Showing only N name(s) found in input" banner appears at the top with
  a *Clear filter* button. The Tables pane shrinks to those tables and the
  global Columns view shrinks to those columns. This replaces the old
  *Translation Table* mode's "list every name in this paste" view —
  with sortable columns, search, and copy actions on top.
- Buttons: **Copy physical**, **Copy logical**, **Override logical name…**
  (opens a small prompt prefilled with the current logical name; saves to
  `translator_custom_map.json` and re-runs translation immediately — the
  override always wins against `db_schema_output.json`), **Close**.

---

## 🛠 Tools

A separate menubutton next to *⚙ Settings* hosts developer utilities that
aren't part of the translation pipeline. Currently:

### Extract SQL from log… (`Ctrl+Shift+L`)

Reads a `stclibApp.log` produced by `commons.dao.PreparedStatementEx`,
parses **every** prepared statement it contains, surfaces the 1–2
*primary* business queries above the dozens of infrastructure calls
(SystemControl reads, audit-log inserts, message lookups…), and
substitutes `?` placeholders with the bound parameters to give you a
runnable SQL.

#### Browsing a log

- **Log dropdown**: pick from your most recent paths, or **Browse…**
  to add a new one. Up to 8 paths are kept so you can hop between e.g.
  `lawmasterhansoku-web` and `lawdailyorder-web` instantly. **Reload**
  re-reads the file (useful after a server run produces new entries).
- The **statement list** is grouped under the user request that
  triggered each batch — you'll see something like
  *`PdaHonbuIdoShijiTorikomiAction#search  (9 queries · 3 ★)`*. The
  grouping uses `commons.struts.RequestProcessor,callMethod` markers
  when present, falling back to 1-second time gaps for orphan
  statements.
- Statements are tagged **★ primary** when their score crosses the
  threshold (default 30). Score combines: DAO package match
  (signal/noise lists), SQL length, `WITH` / `JOIN` / `UNION`
  presence, bound-param count, target-table noise list.
- **🔎 search box**: filters by id, DAO short name, statement type,
  target tables, or substring of the SQL.
- **☑ Hide infrastructure** (default ON): only ★-primary statements
  show. Untick to see everything (every audit insert, every config
  read).
- **Click a statement** → its SQL / Params / Result tabs render below.
  The first ★-primary statement is auto-selected after each load so
  you usually need zero clicks.

#### Defaults (per project, editable in `translator_settings.json`)

- `noise_packages`: `["swc.commons", "mdware.common"]` — DAOs whose
  package contains either of these are demoted (-80).
- `noise_tables`: `["SYSTEM_CONTROL", "DT_TABLE_LOG", "R_MESSAGE",
  "R_DICTIONARY_CONTROL", "R_NAMECTF"]` — statements targeting these
  are demoted (-40).
- `primary_packages`: empty by default. When set (e.g.
  `["mdware.shiire", "mdware.lawmaster"]`), DAOs in those packages
  get a strong **+50** boost — useful when you're working across
  multiple sub-projects and want a single config to recognise them
  all.
- `primary_threshold`: 30. Lower it if you want more queries flagged
  as primary; raise it for stricter filtering.

#### Result actions

- **Copy result**: clipboard, ready to paste into your DB tool.
- **Send to translator input**: replaces the active doc tab's input
  with the runnable SQL and re-runs translation immediately, so
  Inline Replace or Design Doc renders the Japanese names against
  real values.

#### Direct mode

A **Direct mode…** button swaps the body for a 3-pane view (paste SQL
on the left, paste `[STRING:1:…]` blob in the middle, click Process to
fill the result on the right). Useful when you don't have the log file
handy — e.g. someone messaged you a snippet on chat. Same combiner.

#### Param formatter

Recognised types: `STRING` / `CHAR` / `VARCHAR` / `CLOB` (single-quoted
with `''` escaping); `INT` / `BIGINT` / `DECIMAL` / `DOUBLE` / `FLOAT`
(bare); `DATE` / `TIMESTAMP` / `TIME` (quoted); `NULL` (keyword);
`BOOLEAN` (1/0); `BYTES` / `BLOB` (hex). Unknown types fall back to
single-quoted strings. Substitution is quote-aware — `?` inside a
string literal (e.g. `'O''Brien?'`) is left alone.

---

## History (⌄)

Last 10 distinct inputs, saved across sessions. Triggered only by explicit
events (paste, Ctrl+Enter, file open) — not by every keystroke.

---

## Search (Ctrl+F)

Find text in the output pane. Arrow buttons (▲ ▼) jump between matches; the
count is shown next to the search field. `Esc` closes the bar.

---

## Theme & layout

- `☀ Light` / `🌙 Dark` — Catppuccin palette in both variants.
- `⬌ Horizontal` / `⬍ Vertical` — split orientation; preserves all state.
- `Ctrl + = / Ctrl + - / Ctrl + 0` — font size up/down/reset.

All preferences, window size, and layout are persisted between sessions.
