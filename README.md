# zendesk-macro-doctor

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/aamirtechs/zendesk-macro-doctor/blob/main/LICENSE) [![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/aamirtechs/zendesk-macro-doctor/issues)

A zero-dependency Python CLI that audits a Zendesk
macros CSV export for the macro sprawl that quietly
costs support teams time: macros unused in 90 days,
macros with no actions, macros whose text still quotes
an old price or a dead link, duplicate and
near-duplicate macros (same title ignoring case and
separators, same reply text), macros referencing a
deleted group or agent, macros missing the field and tag
actions that keep reporting accurate, and macros with
no category, plus bad data such as malformed rows,
invalid dates and use counts, and duplicate macro ids.

Macro underutilization is called the main productivity
killer in Zendesk, and the usual advice is to audit
usage on a schedule and prune anything unused for 90
days. Zendesk caps accounts at 5,000 shared macros, so
there is no natural pressure to clean up, and a stale
macro sits silently until an agent sends a wrong answer
to a customer. Zendesk has no full native usage report
(the workaround is a tracking tag counted in Explore),
and bulk cleanup lives in paid tools, so no free offline
CLI audits an exported macros file.

## Install

Requires Python 3.9 or newer. No dependencies beyond the
standard library.

```bash
pip install zendesk-macro-doctor
```

Or run it straight from the source checkout:

```bash
python3 zendesk_macro_doctor.py examples/macros.csv --prices-file examples/prices.csv --dead-domains-file examples/dead-domains.txt --deleted-file examples/deleted.txt
```

## Input format

A CSV export with a header row, one row per macro.
Columns used: `macro_id` (or `macro`, `id`), `title`,
`category`, `actions`, `last_used_at`, `use_count`,
`status`, `updated_at`, and an optional `comment_text`
column. Extra columns are ignored, so a full macros
export works as-is. A UTF-8 BOM header is handled. Only
`macro_id` is required; the other columns are optional,
and the checks that need a missing column are skipped.

`title` also accepts `name`; `category` also accepts
`group` or `folder`; `actions` also accepts `action`;
`last_used_at` also accepts `last_used` or
`last_used_date`; `use_count` also accepts `uses`,
`usage_count`, or `times_used`; `status` also accepts
`state`; `updated_at` also accepts `updated` or
`updated_date`; `comment_text` also accepts `reply_text`,
`text`, `comment`, or `body`.

Actions are semicolon-separated, in the usual Zendesk
shape:

```csv
macro_id,title,category,actions,last_used_at,use_count,status,updated_at,comment_text
M-101,Reset password help,Account,"comment: Follow the steps below to reset your password; set field: issue_type=password_reset; add tag: password_reset",2026-11-10,42,active,2026-11-01,Follow the steps below to reset your password at https://help.example.com/reset-password
M-103,Refund request,Billing,"comment: We can refund your order within 5 business days; set field: issue_type=refund",2026-08-01,12,active,2026-08-01,We can refund your order within 5 business days
```

The macro text checked for prices and links is the
`comment_text` column when one is present, otherwise
the text of the macro's comment actions.

Three optional reference files power the checks that
need outside knowledge, and without them those checks
are skipped:

* `--prices-file`: a CSV with a `product` column and a
  `price` column (current prices in dollars). When the
  macro text names a product and quotes a different
  price, the macro is stale.

```csv
product,price
Pro plan,39.00
Basic plan,24.00
```

* `--dead-domains-file`: a text file with one dead domain
  per line (blank lines and `#` comments skipped).
  Links to those domains are dead, and links with no
  valid domain are malformed.

```
# Domains that no longer serve help content
old-help.example.com
legacy.example.com
```

* `--deleted-file`: a text file with one deleted group or
  agent name per line. Assign actions naming them are
  errors.

```
Legacy Support
Dana Cole
```

Dates accept ISO 8601 with or without a timezone
(`2026-11-10`, `2026-11-10T09:30:00Z`), plus
day/month/year and month/day/year forms. A date with no
timezone is read as UTC. Use counts accept whole numbers
with optional thousands commas.

## What it checks

Errors, the ones that mean a dead, duplicated, stale, or
badly recorded macro:

* `unused-macro`: the macro was last used more than
  `--unused-days` (default 90) ago, or was never used
  at all. Inactive macros skip this check, because they
  are retired on purpose.
* `no-actions`: the macro has no actions, so running it
  does nothing.
* `stale-price`: the text names a product from the
  prices file and quotes a different price. A difference
  of one cent or less is ignored as rounding.
* `dead-link`: a link in the text points at a domain in
  the dead-domains file.
* `duplicate-title`: the title normalizes to the same
  title as another macro (lowercased, separators and
  case ignored), so the two macros say the same thing.
* `duplicate-text`: the reply text matches another
  macro, so the two macros send the same reply.
* `deleted-reference`: the macro assigns to a group or
  agent in the deleted file, which no longer exists.
* `duplicate-macro-id`: the same macro id twice in the
  export, so the macro was exported twice.
* `malformed-row`: a row with the wrong number of
  fields. The row is skipped and reported, never a
  crash.
* `empty-title`: a row with no title. The row is skipped
  and reported.
* `invalid-timestamp`: a last used or updated date that
  matches no known format. The row is still audited on
  the fields that did parse.
* `future-timestamp`: a last used or updated date after
  `--as-of` (default now).
* `invalid-use-count`: a use count that is not a valid
  non-negative whole number.

Warnings, patterns worth investigating:

* `low-use-macro`: the macro was used fewer than
  `--min-uses` (default 5) times. A zero use count is
  covered by the unused check instead.
* `missing-reporting-actions`: the macro is missing a
  set field action or an add tag action, so tickets it
  touches will not report accurately.
* `no-category`: the macro has no category, so agents
  cannot find it by browsing.
* `missing-last-used`: the macro was used but has no
  last used date.
* `malformed-link`: a link in the text has no valid
  domain, so agents cannot open it.

The summary reports total macros, active and inactive
counts, total uses, unused macros, duplicate titles,
duplicate reply texts, and macros with no category,
with a per-category table of macro count, total uses,
and unused macros.

## Usage

```bash
python3 zendesk_macro_doctor.py examples/macros.csv --prices-file examples/prices.csv --dead-domains-file examples/dead-domains.txt --deleted-file examples/deleted.txt --as-of 2026-11-18
```

Captured output:

```
ERROR [future-timestamp] M-115: last_used_at 2026-12-01 00:00 is after the as-of date 2026-11-18 00:00
ERROR [duplicate-macro-id] M-101: macro id appears more than once in the export, so the macro was exported twice
ERROR [invalid-use-count] M-117: use_count 'many' is not a valid non-negative whole number
ERROR [invalid-timestamp] M-118: last_used_at 'not-a-date' is not a readable date
ERROR [malformed-row] row 20: row has 3 fields but the header has 9, so the row was skipped
ERROR [empty-title] M-126: row has no title, so the row was skipped
ERROR [duplicate-title] M-102: title normalizes to 'reset password help', the same as macro M-101, so the two macros say the same thing
ERROR [duplicate-text] M-102: reply text matches macro M-101, so the two macros send the same reply
WARNING [missing-reporting-actions] M-103: macro is missing an add tag action, so tickets it touches will not report accurately
ERROR [unused-macro] M-103: macro was last used 2026-08-01, 109 days ago, past the 90-day limit
ERROR [stale-price] M-104: text quotes Pro plan at $29.00 but the current price is $39.00
ERROR [dead-link] M-105: link https://old-help.example.com/guide points at old-help.example.com, a dead domain
ERROR [no-actions] M-106: macro has no actions, so running it does nothing
WARNING [low-use-macro] M-106: macro was used only 3 times, below the minimum of 5
ERROR [deleted-reference] M-107: macro assigns to group 'Legacy Support', which no longer exists
WARNING [no-category] M-108: macro has no category, so agents cannot find it by browsing
ERROR [duplicate-text] M-109: reply text matches macro M-103, so the two macros send the same reply
WARNING [missing-reporting-actions] M-111: macro is missing a set field action, so tickets it touches will not report accurately
WARNING [low-use-macro] M-111: macro was used only 4 times, below the minimum of 5
WARNING [malformed-link] M-112: link https://help has no valid domain, so agents cannot open it
ERROR [unused-macro] M-114: macro was never used
ERROR [duplicate-title] M-101: title normalizes to 'reset password help', the same as macro M-101, so the two macros send the same reply
ERROR [duplicate-text] M-101: reply text matches macro M-101, so the two macros send the same reply
WARNING [missing-last-used] M-118: macro was used 7 times but has no last used date
ERROR [deleted-reference] M-121: macro assigns to agent 'Dana Cole', which no longer exists
ERROR [duplicate-title] M-122: title normalizes to 'refund request', the same as macro M-103, so the two macros say the same thing
ERROR [stale-price] M-123: text quotes Basic plan at $19.00 but the current price is $24.00
WARNING [missing-reporting-actions] M-124: macro is missing a set field action, so tickets it touches will not report accurately
WARNING [low-use-macro] M-124: macro was used only 2 times, below the minimum of 5
WARNING [missing-last-used] M-125: macro was used 9 times but has no last used date

Macros: 24, active: 23, inactive: 1, findings: 30, errors: 20, warnings: 10
Total uses: 421, unused macros: 2, duplicate titles: 3, duplicate reply texts: 3, macros with no category: 1

Per-category summary:
category         macros     uses unused
(no category)         1       20      0
Account               4      118      0
Billing               5      100      1
General               7       33      1
Onboarding            1       55      0
Orders                2       62      0
Support               2       24      0
Technical             2        9      0
```

The sample has 26 rows; the malformed row 20 and the
row with no title are skipped, so 24 macros are audited.
The headline numbers are 421 total uses, 2 unused
macros (M-103, last used 109 days ago, and M-114, never
used), 3 duplicate titles, and 3 duplicate reply texts.
The worst cases are M-104 and M-123, still quoting the
old $29 Pro and $19 Basic prices against the current
$39 and $24, M-105 linking to the dead
old-help.example.com domain, and M-107 and M-121
assigning to a deleted group and a departed agent.
M-113 is inactive and retired, so its last use in
January does not count as unused, and M-122 only
differs from M-103 by separators in the title
("Refund-Request!" against "Refund request"), which is
exactly the near-duplicate the check is for.

Use it as a CI gate: `--check` exits 1 when any error is
found, and 0 otherwise. On the sample above it exits 1:

```bash
python3 zendesk_macro_doctor.py examples/macros.csv --prices-file examples/prices.csv --dead-domains-file examples/dead-domains.txt --deleted-file examples/deleted.txt --as-of 2026-11-18 --check
```

On `examples/clean.csv`, four clean macros, it exits 0:

```
Macros: 4, active: 4, inactive: 0, findings: 0, errors: 0, warnings: 0
Total uses: 139, unused macros: 0, duplicate titles: 0, duplicate reply texts: 0, macros with no category: 0

Per-category summary:
category         macros     uses unused
Account               1       42      0
Billing               1       12      0
Onboarding            1       55      0
Orders                1       30      0

All macros clean.
```

With `--strict`, `--check` also fails on warnings.
`examples/warnings-only.csv` has one macro missing a
set field action and one with no category, so it passes
`--check` (exit 0) and fails `--check --strict`
(exit 1). With a longer unused window the older unused
macro drops out of the sample report:

```bash
python3 zendesk_macro_doctor.py examples/macros.csv --prices-file examples/prices.csv --dead-domains-file examples/dead-domains.txt --deleted-file examples/deleted.txt --as-of 2026-11-18 --unused-days 200 --check
```

That run still exits 1 on the sample's errors, but the
`unused-macro` error for M-103 is gone, leaving 29
findings and 19 errors. With a lower minimum use count
the low-use warnings drop out instead:

```bash
python3 zendesk_macro_doctor.py examples/macros.csv --prices-file examples/prices.csv --dead-domains-file examples/dead-domains.txt --deleted-file examples/deleted.txt --as-of 2026-11-18 --min-uses 2 --check
```

That run leaves 27 findings, 20 errors, and 7 warnings,
with all three `low-use-macro` warnings gone. Without
the three reference files the price, link, and deleted
reference checks are skipped, leaving 24 findings, 15
errors, and 9 warnings.

`--json` emits the summary, the per-category table, and
the findings as JSON, and `--out` writes the report to
a file.

## Options

| Flag | Meaning |
| --- | --- |
| `--prices-file` | CSV of current prices (product,price) used to flag macros quoting an old price |
| `--dead-domains-file` | Text file with one dead domain per line, used to flag macros linking to a dead domain |
| `--deleted-file` | Text file with one deleted group or agent name per line, used to flag macros assigning to a deleted group or agent |
| `--unused-days` | A macro last used more than this many days ago is unused (default: 90) |
| `--min-uses` | A macro used fewer times than this is low use (default: 5) |
| `--as-of` | Date to compare last used dates against, for example 2026-11-18 (default: now) |
| `--json` | Emit JSON instead of text |
| `--check` | Exit 1 if any error is found |
| `--strict` | With --check, also exit 1 on warnings |
| `--out` | Write the report to a file |

## Edge cases

* A row with too many or too few fields is a malformed-row
  error and is skipped; the rest of the file is still
  audited.
* A row with no title is an empty-title error and is
  skipped.
* A bad date is an invalid-timestamp error, and the row is
  still audited on the fields that did parse, so one bad
  date never hides the other problems on the macro. A
  macro whose date is unreadable also gets a
  missing-last-used warning when its use count is above
  zero, because the last use cannot be established.
* A macro with no actions gets only the no-actions error,
  not a missing-reporting-actions warning on top of it.
* A zero use count is covered by the unused check, never
  by the low-use warning.
* Inactive macros (inactive, disabled, retired, deleted,
  or archived) skip the unused and low-use checks, because
  they are retired on purpose, but every other check
  still applies to them.
* Title comparison ignores case and separators, so
  "Refund request" and "Refund-Request!" match; reply
  text comparison ignores case and whitespace only, so
  the same reply with different spacing still matches.
* A price difference of one cent or less is ignored as
  rounding, and a link to a subdomain of a dead domain
  counts as dead.
* A blank line in the file is skipped silently. A macro
  with no category is grouped under `(no category)` in
  the per-category table.
* `--as-of` makes runs reproducible: without it, the
  unused and future-timestamp checks compare against the
  current time.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

80 tests, all passing: title and text normalization,
timestamp, use count, and action parsing, header aliases
and BOM handling, duplicate macro ids, malformed rows,
empty titles, invalid and future timestamps, invalid use
counts, unused macros and the 90-day boundary, never
used macros, inactive exemptions, low-use macros and
boundaries, missing last used dates, no actions, missing
reporting actions, no category, duplicate titles
including separator variants, duplicate reply texts,
stale prices, dead and malformed links, deleted group
and agent references, reference file errors, summary
counts and per-category tables, text and JSON output,
and `--check` and `--strict` exit codes. The same 80
tests pass under pytest.

## License

MIT. See [LICENSE](LICENSE).
