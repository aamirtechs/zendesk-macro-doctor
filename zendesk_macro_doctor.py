"""Zendesk macros audit CLI.

Reads a Zendesk macros CSV export (one row per macro:
macro id, title, category, actions, last used date, use
count, status, updated date, plus an optional comment
text column) and audits it for the macro sprawl that
quietly costs support teams time: macros unused in 90
days, macros with no actions, macros whose text still
quotes an old price or a dead link, duplicate and
near-duplicate macros (same title ignoring case and
separators, same reply text), macros referencing a
deleted group or agent, macros missing the field and tag
actions that keep reporting accurate, and macros with no
category, plus bad data such as malformed rows, invalid
dates and use counts, and duplicate macro ids.

Macro underutilization is called the main productivity
killer in Zendesk, and the usual advice is to audit usage
on a schedule and prune anything unused for 90 days.
Zendesk caps accounts at 5,000 shared macros, so there
is no natural pressure to clean up, and a stale macro
sits silently until an agent sends a wrong answer to a
customer. Zendesk has no full native usage report (the
workaround is a tracking tag counted in Explore), and
bulk cleanup lives in paid tools, so no free offline CLI
audits an exported macros file.

The macro text checked for prices and links is the
comment text column when one is present, otherwise the
text of the macro's comment actions. Prices come from an
optional --prices-file (product, current price): when
the text names a product and quotes a different price,
the macro is stale. Dead links come from an optional
--dead-domains-file (one domain per line): links to
those domains are dead, and links with no valid domain
are malformed. Deleted groups and agents come from an
optional --deleted-file (one name per line): assign
actions naming them are errors. Without those files the
checks that need them are skipped.

Each problem is a finding with a macro id (or title when
the id is missing), a rule name, a severity (error or
warning), and a message. Errors are dead, duplicated,
stale, or badly recorded macros; warnings are patterns
worth investigating. --check exits 1 when any error is
found, and --strict also fails on warnings.

A bad row is reported as a finding and the row is still
audited on the fields that did parse, so one bad row
never crashes the run. Inactive macros (status inactive,
disabled, retired, deleted, or archived) skip the unused
and low-use checks, because they are retired on purpose.

Zero dependencies, Python 3.9 or newer, standard library only.

Usage:
    python3 zendesk_macro_doctor.py examples/macros.csv --prices-file examples/prices.csv --dead-domains-file examples/dead-domains.txt --deleted-file examples/deleted.txt
    python3 zendesk_macro_doctor.py macros.csv --json --check
    python3 zendesk_macro_doctor.py macros.csv --as-of 2026-11-18 --unused-days 90
"""

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

__version__ = "1.0.0"

DEFAULT_UNUSED_DAYS = 90
DEFAULT_MIN_USES = 5
PRICE_TOLERANCE = Decimal("0.01")

TIMESTAMP_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M",
    "%m/%d/%Y %H:%M",
    "%d/%m/%Y",
    "%m/%d/%Y",
)

INACTIVE_STATUSES = ("inactive", "disabled", "retired", "deleted",
                     "archived")
PRICE_PATTERN = re.compile(r"\$(\d[\d,]*(?:\.\d{1,2})?)")
URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")


class Finding:
    """One audit finding for one macro."""

    def __init__(self, macro, rule, severity, message):
        self.macro = macro
        self.rule = rule
        self.severity = severity
        self.message = message

    def as_dict(self):
        """Return the finding as a plain dict."""
        return {"macro": self.macro, "rule": self.rule,
                "severity": self.severity, "message": self.message}

    def __str__(self):
        return "%s [%s] %s: %s" % (self.severity.upper(), self.rule,
                                   self.macro, self.message)


def parse_timestamp(text):
    """Parse a timestamp string into an aware datetime, or None.

    Accepts ISO 8601 with or without a timezone (a trailing Z is
    handled), plus a few common export formats. A naive timestamp
    is read as UTC. Returns None for an empty string or a value
    that matches no known format, so callers can report it
    instead of crashing.
    """
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    candidate = text
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        parsed = None
    if parsed is None:
        for fmt in TIMESTAMP_FORMATS:
            try:
                parsed = datetime.strptime(candidate, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_use_count(text):
    """Parse a use count field, returning (count, is_valid).

    An empty field means no count was recorded. A value that
    is not a whole number, or a negative number, is invalid.
    """
    if text is None:
        return None, True
    text = text.strip().replace(",", "")
    if not text:
        return None, True
    try:
        count = int(text)
    except ValueError:
        return None, False
    if count < 0:
        return None, False
    return count, True


def normalize_title(title):
    """Normalize a macro title for duplicate comparison.

    Lowercases, turns every run of non-alphanumeric
    characters into one space, and collapses whitespace, so
    "Refund request", "Refund-Request!", and "REFUND
    REQUEST" all normalize to the same title.
    """
    lowered = title.lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", lowered)
    return " ".join(cleaned.split())


def normalize_text(text):
    """Normalize macro reply text for duplicate comparison.

    Lowercases and collapses whitespace, so the same reply
    with different spacing or casing still matches.
    """
    return " ".join(text.lower().split())


def find_column(header, names):
    """Return the first of names present in header, or None."""
    for name in names:
        if name in header:
            return name
    return None


def parse_actions(text):
    """Split an actions field into a list of action strings.

    Actions are separated by semicolons. Empty entries are
    dropped.
    """
    if text is None:
        return []
    return [item.strip() for item in text.split(";")
            if item.strip()]


def action_kind(action):
    """Return the kind of one action string, lowercased."""
    lowered = action.lower()
    if lowered.startswith("comment:") \
            or lowered.startswith("comment/description:"):
        return "comment"
    if lowered.startswith("description:"):
        return "comment"
    if lowered.startswith("set field:") \
            or lowered.startswith("set custom field:"):
        return "field"
    if lowered.startswith("add tag:"):
        return "tag"
    if lowered.startswith("assign to group:") \
            or lowered.startswith("assign group:"):
        return "assign-group"
    if lowered.startswith("assign to agent:") \
            or lowered.startswith("assign agent:"):
        return "assign-agent"
    return "other"


def action_value(action):
    """Return the value part of one action string."""
    return action.split(":", 1)[1].strip() \
        if ":" in action else ""


def macro_text(row):
    """Return the macro reply text for a parsed row.

    The comment text column wins when it is present,
    otherwise the text of the macro's comment actions is
    joined.
    """
    if row["comment_text"]:
        return row["comment_text"]
    parts = [action_value(action) for action in row["actions"]
             if action_kind(action) == "comment"]
    return " ".join(parts)


def load_prices(path):
    """Load a current prices CSV file into a dict.

    The file has a product column and a price column
    (dollars). Returns (prices, findings). A price that is
    not a valid non-negative number is reported and skipped,
    never a crash.
    """
    findings = []
    prices = {}
    try:
        handle = open(path, newline="", encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError("cannot read %s: %s" % (path, exc))
    with handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            raise ValueError("%s is empty, no header row found" % path)
        except csv.Error as exc:
            raise ValueError("%s is not a readable CSV file: %s"
                             % (path, exc))
        header = [name.strip() for name in header]
        product_column = find_column(
            header, ("product", "name", "plan"))
        if product_column is None:
            raise ValueError(
                "%s has no product column (header is: %s)"
                % (path, ", ".join(header)))
        price_column = find_column(header, ("price", "current_price",
                                            "amount"))
        if price_column is None:
            raise ValueError(
                "%s has no price column (header is: %s)"
                % (path, ", ".join(header)))
        for line_number, row in enumerate(reader, start=2):
            if not row or all(not cell.strip() for cell in row):
                continue
            if len(row) != len(header):
                findings.append(Finding(
                    "row %d" % line_number, "malformed-row", "error",
                    "prices row has %d fields but the header has "
                    "%d, so the row was skipped"
                    % (len(row), len(header))))
                continue
            cells = dict(zip(header, (cell.strip() for cell in row)))
            product = cells[product_column]
            if not product:
                findings.append(Finding(
                    "row %d" % line_number, "empty-product", "error",
                    "prices row has no product, so the row was "
                    "skipped"))
                continue
            raw = cells[price_column].replace("$", "").replace(",", "")
            if not raw:
                findings.append(Finding(
                    product, "invalid-price", "error",
                    "price is empty, so the product was left out "
                    "of the prices"))
                continue
            try:
                price = Decimal(raw)
            except InvalidOperation:
                findings.append(Finding(
                    product, "invalid-price", "error",
                    "price %r is not a valid non-negative number, "
                    "so the product was left out of the prices"
                    % cells[price_column]))
                continue
            if price < 0:
                findings.append(Finding(
                    product, "invalid-price", "error",
                    "price %r is not a valid non-negative number, "
                    "so the product was left out of the prices"
                    % cells[price_column]))
                continue
            prices[product.lower()] = (product, price)
    return prices, findings


def load_names(path):
    """Load a plain list file into a set of lowercased names.

    One name per line, blank lines and lines starting with
    # are skipped.
    """
    try:
        with open(path, encoding="utf-8-sig") as handle:
            lines = handle.read().splitlines()
    except OSError as exc:
        raise ValueError("cannot read %s: %s" % (path, exc))
    names = set()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        names.add(stripped.lower())
    return names


def parse_csv(path, as_of):
    """Parse a Zendesk macros CSV export.

    Returns (macros, findings). Each macro is a dict with the
    id, title, category, actions, last used date, use count,
    status, updated date, and reply text. Rows with the wrong
    number of fields, or with no title, are reported and
    skipped, never a crash. A UTF-8 BOM header is handled.
    Extra columns are ignored.
    """
    findings = []
    macros = []
    try:
        handle = open(path, newline="", encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError("cannot read %s: %s" % (path, exc))
    with handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            raise ValueError("%s is empty, no header row found" % path)
        except csv.Error as exc:
            raise ValueError("%s is not a readable CSV file: %s"
                             % (path, exc))
        header = [name.strip() for name in header]
        id_column = find_column(header, ("macro_id", "macro", "id"))
        if id_column is None:
            raise ValueError(
                "%s has no macro_id column (header is: %s)"
                % (path, ", ".join(header)))
        title_column = find_column(header, ("title", "name"))
        category_column = find_column(
            header, ("category", "group", "folder"))
        actions_column = find_column(header, ("actions", "action"))
        last_used_column = find_column(
            header, ("last_used_at", "last_used", "last_used_date"))
        use_count_column = find_column(
            header, ("use_count", "uses", "usage_count", "times_used"))
        status_column = find_column(header, ("status", "state"))
        updated_column = find_column(
            header, ("updated_at", "updated", "updated_date"))
        comment_column = find_column(
            header, ("comment_text", "reply_text", "text", "comment",
                     "body"))
        seen_ids = set()
        for line_number, row in enumerate(reader, start=2):
            if not row or all(not cell.strip() for cell in row):
                continue
            if len(row) != len(header):
                findings.append(Finding(
                    "row %d" % line_number, "malformed-row", "error",
                    "row has %d fields but the header has %d, "
                    "so the row was skipped" % (len(row), len(header))))
                continue
            cells = dict(zip(header, (cell.strip() for cell in row)))
            macro_id = cells[id_column] or "row %d" % line_number
            title = cells[title_column] if title_column else ""
            if not title:
                findings.append(Finding(
                    macro_id, "empty-title", "error",
                    "row has no title, so the row was skipped"))
                continue
            if cells[id_column] and cells[id_column] in seen_ids:
                findings.append(Finding(
                    macro_id, "duplicate-macro-id", "error",
                    "macro id appears more than once in the "
                    "export, so the macro was exported twice"))
            elif cells[id_column]:
                seen_ids.add(cells[id_column])
            record = {"id": macro_id,
                      "title": title,
                      "category": (cells[category_column]
                                   if category_column else ""),
                      "actions": parse_actions(
                          cells[actions_column]
                          if actions_column else ""),
                      "last_used_at": None,
                      "last_used_empty": True,
                      "use_count": None,
                      "status": (cells[status_column].lower()
                                 if status_column else "active"),
                      "updated_at": None,
                      "comment_text": (cells[comment_column]
                                       if comment_column else ""),
                      "text": "",
                      "line": line_number}
            for column, key, label in (
                    (last_used_column, "last_used_at",
                     "last_used_at"),
                    (updated_column, "updated_at", "updated_at")):
                if column is None:
                    continue
                raw = cells[column]
                if not raw:
                    continue
                if key == "last_used_at":
                    record["last_used_empty"] = False
                parsed = parse_timestamp(raw)
                if parsed is None:
                    findings.append(Finding(
                        macro_id, "invalid-timestamp", "error",
                        "%s %r is not a readable date"
                        % (label, raw)))
                else:
                    record[key] = parsed
                    if parsed > as_of:
                        findings.append(Finding(
                            macro_id, "future-timestamp", "error",
                            "%s %s is after the as-of date %s"
                            % (label,
                               parsed.strftime("%Y-%m-%d %H:%M"),
                               as_of.strftime("%Y-%m-%d %H:%M"))))
            if use_count_column is not None:
                count, valid = parse_use_count(cells[use_count_column])
                if not valid:
                    findings.append(Finding(
                        macro_id, "invalid-use-count", "error",
                        "use_count %r is not a valid non-negative "
                        "whole number" % cells[use_count_column]))
                else:
                    record["use_count"] = count
            record["text"] = macro_text(record)
            macros.append(record)
    return macros, findings


def audit(macros, as_of, unused_days=DEFAULT_UNUSED_DAYS,
          min_uses=DEFAULT_MIN_USES, prices=None,
          dead_domains=None, deleted_names=None):
    """Audit parsed macros and return a list of findings.

    The prices argument is a dict of lowercased product name
    to (product, price); dead_domains is a set of
    lowercased domains; deleted_names is a set of lowercased
    group and agent names. When any of them is None, the
    checks that need it are skipped.
    """
    findings = []
    seen_titles = {}
    seen_texts = {}
    for macro in macros:
        macro_id = macro["id"]
        title_key = normalize_title(macro["title"])
        if title_key in seen_titles:
            findings.append(Finding(
                macro_id, "duplicate-title", "error",
                "title normalizes to %r, the same as macro %s, "
                "so the two macros say the same thing"
                % (title_key, seen_titles[title_key])))
        else:
            seen_titles[title_key] = macro_id
        text_key = normalize_text(macro["text"])
        if text_key:
            if text_key in seen_texts:
                findings.append(Finding(
                    macro_id, "duplicate-text", "error",
                    "reply text matches macro %s, so the two "
                    "macros send the same reply"
                    % seen_texts[text_key]))
            else:
                seen_texts[text_key] = macro_id
        if not macro["actions"]:
            findings.append(Finding(
                macro_id, "no-actions", "error",
                "macro has no actions, so running it does "
                "nothing"))
        else:
            kinds = [action_kind(action)
                     for action in macro["actions"]]
            missing = []
            if "field" not in kinds:
                missing.append("a set field action")
            if "tag" not in kinds:
                missing.append("an add tag action")
            if missing:
                findings.append(Finding(
                    macro_id, "missing-reporting-actions", "warning",
                    "macro is missing %s, so tickets it touches "
                    "will not report accurately"
                    % " and ".join(missing)))
        if not macro["category"]:
            findings.append(Finding(
                macro_id, "no-category", "warning",
                "macro has no category, so agents cannot find "
                "it by browsing"))
        inactive = macro["status"] in INACTIVE_STATUSES
        last_used = macro["last_used_at"]
        use_count = macro["use_count"]
        if not inactive:
            if last_used is None and use_count == 0:
                findings.append(Finding(
                    macro_id, "unused-macro", "error",
                    "macro was never used"))
            elif last_used is not None:
                days = (as_of - last_used).days
                if days > unused_days:
                    findings.append(Finding(
                        macro_id, "unused-macro", "error",
                        "macro was last used %s, %d days ago, "
                        "past the %d-day limit"
                        % (last_used.strftime("%Y-%m-%d"), days,
                           unused_days)))
            if use_count is not None and 0 < use_count < min_uses:
                findings.append(Finding(
                    macro_id, "low-use-macro", "warning",
                    "macro was used only %d times, below the "
                    "minimum of %d" % (use_count, min_uses)))
        if use_count is not None and use_count > 0 \
                and last_used is None:
            findings.append(Finding(
                macro_id, "missing-last-used", "warning",
                "macro was used %d times but has no last used "
                "date" % use_count))
        text = macro["text"]
        if prices is not None and text:
            lowered = text.lower()
            for product_lower, (product, price) in sorted(
                    prices.items()):
                if product_lower not in lowered:
                    continue
                for match in PRICE_PATTERN.finditer(text):
                    quoted = Decimal(match.group(1).replace(",", ""))
                    if abs(quoted - price) > PRICE_TOLERANCE:
                        findings.append(Finding(
                            macro_id, "stale-price", "error",
                            "text quotes %s at $%s but the current "
                            "price is $%s"
                            % (product, format(quoted, ".2f"),
                               format(price, ".2f"))))
                        break
        if dead_domains is not None and text:
            for match in URL_PATTERN.finditer(text):
                url = match.group(0)
                host = urlsplit(url).netloc.lower()
                host = host.split(":")[0]
                if "." not in host:
                    findings.append(Finding(
                        macro_id, "malformed-link", "warning",
                        "link %s has no valid domain, so agents "
                        "cannot open it" % url))
                    continue
                if host in dead_domains or any(
                        host.endswith("." + domain)
                        for domain in dead_domains):
                    findings.append(Finding(
                        macro_id, "dead-link", "error",
                        "link %s points at %s, a dead domain"
                        % (url, host)))
        if deleted_names is not None:
            for action in macro["actions"]:
                kind = action_kind(action)
                if kind not in ("assign-group", "assign-agent"):
                    continue
                name = action_value(action)
                if name.lower() in deleted_names:
                    findings.append(Finding(
                        macro_id, "deleted-reference", "error",
                        "macro assigns to %s %r, which no longer "
                        "exists" % ("group" if kind == "assign-group"
                                    else "agent", name)))
    return findings


def is_unused(macro, as_of, unused_days):
    """Return True when a macro counts as unused.

    An active macro counts as unused when it was never used
    (no last used date and a zero use count), or when its
    last use is more than unused_days ago. Inactive macros
    never count as unused.
    """
    if macro["status"] in INACTIVE_STATUSES:
        return False
    last_used = macro["last_used_at"]
    if last_used is None and macro["use_count"] == 0:
        return True
    if last_used is not None:
        return (as_of - last_used).days > unused_days
    return False


def summarize(macros, findings, as_of,
              unused_days=DEFAULT_UNUSED_DAYS):
    """Return the summary counts for macros and findings."""
    errors = sum(1 for item in findings if item.severity == "error")
    warnings = sum(1 for item in findings
                   if item.severity == "warning")
    active = sum(1 for macro in macros
                 if macro["status"] not in INACTIVE_STATUSES)
    total_uses = sum((macro["use_count"] for macro in macros
                      if macro["use_count"] is not None), 0)
    per_category = {}
    for macro in macros:
        category = macro["category"] or "(no category)"
        entry = per_category.setdefault(category, {
            "category": category, "macros": 0, "uses": 0,
            "unused": 0})
        entry["macros"] += 1
        if macro["use_count"] is not None:
            entry["uses"] += macro["use_count"]
        if is_unused(macro, as_of, unused_days):
            entry["unused"] += 1
    rows = sorted(per_category.values(),
                  key=lambda entry: entry["category"])
    return {"macros": len(macros),
            "active": active,
            "inactive": len(macros) - active,
            "findings": len(findings),
            "errors": errors,
            "warnings": warnings,
            "total_uses": total_uses,
            "unused": sum(entry["unused"] for entry in rows),
            "duplicate_titles": sum(
                1 for item in findings
                if item.rule == "duplicate-title"),
            "duplicate_texts": sum(
                1 for item in findings
                if item.rule == "duplicate-text"),
            "no_category": sum(1 for macro in macros
                               if not macro["category"]),
            "per_category": rows}


def render_text(macros, findings, summary):
    """Render the text report."""
    lines = [str(item) for item in findings]
    lines.append("")
    lines.append("Macros: %(macros)d, active: %(active)d, "
                 "inactive: %(inactive)d, findings: %(findings)d, "
                 "errors: %(errors)d, warnings: %(warnings)d"
                 % summary)
    lines.append("Total uses: %(total_uses)d, unused macros: "
                 "%(unused)d, duplicate titles: "
                 "%(duplicate_titles)d, duplicate reply texts: "
                 "%(duplicate_texts)d, macros with no category: "
                 "%(no_category)d" % summary)
    lines.append("")
    lines.append("Per-category summary:")
    lines.append("%-16s %6s %8s %6s"
                 % ("category", "macros", "uses", "unused"))
    for entry in summary["per_category"]:
        lines.append("%-16s %6d %8d %6d"
                     % (entry["category"], entry["macros"],
                        entry["uses"], entry["unused"]))
    if summary["findings"] == 0:
        lines.append("")
        lines.append("All macros clean.")
    return "\n".join(lines) + "\n"


def render_json(macros, findings, summary):
    """Render the JSON report."""
    report = dict(summary)
    report["findings"] = [item.as_dict() for item in findings]
    return json.dumps(report, indent=2) + "\n"


def parse_as_of(text):
    """Parse an --as-of date into an aware datetime, or None."""
    if text is None:
        return datetime.now(timezone.utc)
    parsed = parse_timestamp(text)
    return parsed


def build_parser():
    """Build the command line parser."""
    parser = argparse.ArgumentParser(
        prog="zendesk-macro-doctor",
        description="Audit a Zendesk macros CSV export for "
                    "unused, duplicated, and stale macros.")
    parser.add_argument("csv_file",
                        help="Zendesk macros CSV to audit")
    parser.add_argument("--prices-file", default=None,
                        help="CSV of current prices (product,price) "
                        "used to flag macros quoting an old price")
    parser.add_argument("--dead-domains-file", default=None,
                        help="text file with one dead domain per "
                        "line, used to flag macros linking to a "
                        "dead domain")
    parser.add_argument("--deleted-file", default=None,
                        help="text file with one deleted group or "
                        "agent name per line, used to flag macros "
                        "assigning to a deleted group or agent")
    parser.add_argument("--unused-days", type=int,
                        default=DEFAULT_UNUSED_DAYS,
                        help="a macro last used more than this many "
                        "days ago is unused (default: 90)")
    parser.add_argument("--min-uses", type=int,
                        default=DEFAULT_MIN_USES,
                        help="a macro used fewer times than this is "
                        "low use (default: 5)")
    parser.add_argument("--as-of", default=None,
                        help="date to compare last used dates "
                        "against, for example 2026-11-18 "
                        "(default: now)")
    parser.add_argument("--json", action="store_true",
                        help="emit JSON instead of text")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if any error is found")
    parser.add_argument("--strict", action="store_true",
                        help="with --check, also exit 1 on warnings")
    parser.add_argument("--out", default=None,
                        help="write the report to a file")
    return parser


def main(argv=None):
    """Run the CLI and return the exit code."""
    args = build_parser().parse_args(argv)
    if args.unused_days < 0:
        print("error: --unused-days must be at least 0",
              file=sys.stderr)
        return 2
    if args.min_uses < 0:
        print("error: --min-uses must be at least 0",
              file=sys.stderr)
        return 2
    as_of = parse_as_of(args.as_of)
    if as_of is None:
        print("error: --as-of %r is not a readable date"
              % args.as_of, file=sys.stderr)
        return 2
    prices = None
    price_findings = []
    if args.prices_file is not None:
        try:
            prices, price_findings = load_prices(args.prices_file)
        except ValueError as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 2
    dead_domains = None
    if args.dead_domains_file is not None:
        try:
            dead_domains = load_names(args.dead_domains_file)
        except ValueError as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 2
    deleted_names = None
    if args.deleted_file is not None:
        try:
            deleted_names = load_names(args.deleted_file)
        except ValueError as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 2
    try:
        macros, findings = parse_csv(args.csv_file, as_of)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    findings.extend(price_findings)
    findings.extend(audit(macros, as_of,
                          unused_days=args.unused_days,
                          min_uses=args.min_uses,
                          prices=prices,
                          dead_domains=dead_domains,
                          deleted_names=deleted_names))
    summary = summarize(macros, findings, as_of,
                        unused_days=args.unused_days)
    if args.json:
        report = render_json(macros, findings, summary)
    else:
        report = render_text(macros, findings, summary)
    if args.out is not None:
        try:
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write(report)
        except OSError as exc:
            print("error: cannot write %s: %s" % (args.out, exc),
                  file=sys.stderr)
            return 2
    else:
        sys.stdout.write(report)
    if args.check:
        if summary["errors"] > 0:
            return 1
        if args.strict and summary["warnings"] > 0:
            return 1
    return 0


def main_entry():
    """Console script entry point."""
    sys.exit(main())


if __name__ == "__main__":
    main_entry()
