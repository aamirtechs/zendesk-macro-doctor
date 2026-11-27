# Good first issues

Seeded issues for new contributors. File each one on GitHub with
the `good first issue` and `help wanted` labels.

## 1. Add a worst-offenders table

Title: Show the macros with the most findings

Description: After the per-category summary, print a short
table of the five macros with the most findings (macro id,
title, findings), so a support lead can see at a glance
which macros need the most work. Findings are counted per
macro id. Ties should break by macro id.

Difficulty: Easy

Acceptance criteria: the table appears in text output with
the correct top five for the sample file (M-101 with 3
findings first, then M-102, M-103, M-106, and M-111 with
2 each, in id order); JSON output is unchanged; a test
checks the table rows.

## 2. Add a category filter

Title: Let teams audit one category at a time

Description: Add a --category flag that audits only the
macros in one category, so a team can clean up Billing
without wading through every other category. The
per-category table then shows only that category, and
duplicate checks only compare macros inside the filter.

Difficulty: Easy

Acceptance criteria: with --category Billing the sample
report covers 5 macros with 100 total uses, 6 findings,
5 errors, and 1 warning, and the per-category table has
only the Billing row; a test covers the filter; README
documents the flag.

## 3. Report the single most unused macro in the summary

Title: Print the macro unused the longest

Description: Add one line to the text summary naming the
macro unused the longest (macro id, title, last used
date, days since last use), so a support lead can act on
the worst case without reading every finding. Macros
never used count as unused since their updated date. When
nothing is unused, the line is omitted.

Difficulty: Easy

Acceptance criteria: the line appears for the sample file
naming M-103, Refund request, last used 2026-08-01, 109
days; it is absent for the clean file; JSON output is
unchanged; a test checks both cases.

## 4. Export the unused macros to a file

Title: Write the unused macro ids to a file

Description: Add a --unused-out flag that writes the ids
of the unused macros, one per line, to a file, so the list
can go straight into a Zendesk bulk delete or a cleanup
ticket. The text report is unchanged.

Difficulty: Easy

Acceptance criteria: with --unused-out unused.txt the file
contains M-103 and M-114 for the sample file, one id per
line in id order; the file is empty for the clean file; a
test checks the file contents.
