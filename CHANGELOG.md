# Changelog

## 1.0.0 - 2026-11-25

Initial release.

* Zendesk macros audit over a macros CSV export:
  macros unused in 90 days, macros with no actions,
  macros whose text still quotes an old price or a dead
  link, duplicate and near-duplicate macros (same title
  ignoring case and separators, same reply text),
  macros referencing a deleted group or agent, macros
  missing the field and tag actions that keep reporting
  accurate, and macros with no category.
* Reference files for the checks that need outside
  knowledge: --prices-file (product, current price) for
  stale prices, --dead-domains-file (one domain per
  line) for dead links, and --deleted-file (one group or
  agent name per line) for deleted references. Without
  them those checks are skipped.
* Title comparison ignores case and separators, so
  "Refund request" and "Refund-Request!" match; reply
  text comparison ignores case and whitespace. The macro
  text checked is the comment text column when present,
  otherwise the text of the macro's comment actions.
* Data quality checks: duplicate macro ids, malformed
  rows, empty titles, invalid or future timestamps,
  invalid use counts, and missing last used dates.
* Inactive macros (inactive, disabled, retired, deleted,
  or archived) skip the unused and low-use checks,
  because they are retired on purpose; a zero use count
  is covered by the unused check, never by low use.
* Defensive timestamp parsing (ISO 8601 with or without
  a timezone, plus common day/month/year forms); a bad
  row is reported as a finding and never crashes the
  run. UTF-8 BOM headers and extra columns handled.
* Findings carry a macro id, a rule name, a severity
  (error or warning), and a message; errors and warnings
  are counted in the summary, with a per-category table
  of macro count, total uses, and unused macros.
* Text and --json output, --out file output,
  --prices-file, --dead-domains-file, --deleted-file,
  --unused-days, --min-uses, and --as-of thresholds,
  and a --check CI gate that exits 1 on errors (and on
  warnings with --strict).
* Zero dependencies, Python 3.9 or newer.
