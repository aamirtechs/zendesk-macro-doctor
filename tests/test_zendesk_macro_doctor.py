"""Tests for zendesk-macro-doctor."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import zendesk_macro_doctor as zmd

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "examples")

AS_OF = datetime(2026, 11, 18, tzinfo=timezone.utc)

HEADER = ("macro_id,title,category,actions,last_used_at,use_count,"
          "status,updated_at,comment_text\n")

PRICES_HEADER = "product,price\n"

PRICES = PRICES_HEADER + "Pro plan,39.00\n" + "Basic plan,24.00\n"

DEAD_DOMAINS = ("# dead domains\n"
                "old-help.example.com\n"
                "legacy.example.com\n")

DELETED = "Legacy Support\nDana Cole\n"


def write_file(content, suffix=".csv"):
    """Write content to a temp file and return its path."""
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=suffix, delete=False, encoding="utf-8")
    handle.write(content)
    handle.close()
    return handle.name


def row(macro_id="M-1", title="Reset password help",
        category="Account",
        actions="comment: Follow the steps; set field: "
                "issue_type=password_reset; add tag: password_reset",
        last_used="2026-11-10", use_count="42", status="active",
        updated="2026-11-01",
        comment_text="Follow the steps to reset your password"):
    """Build one CSV row with the standard columns."""
    import csv
    import io as _io
    buffer = _io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([macro_id, title, category, actions, last_used,
                     use_count, status, updated, comment_text])
    return buffer.getvalue()


def parse(content, as_of=AS_OF):
    """Parse CSV content and return (macros, findings)."""
    return zmd.parse_csv(write_file(content), as_of)


def audit(content, as_of=AS_OF, **kwargs):
    """Parse CSV content and audit it, returning all findings."""
    macros, findings = parse(content, as_of)
    findings.extend(zmd.audit(macros, as_of, **kwargs))
    return macros, findings


def rules(findings):
    """Return the sorted rule names of a findings list."""
    return sorted(item.rule for item in findings)


def by_rule(findings, rule):
    """Return the findings with one rule name."""
    return [item for item in findings if item.rule == rule]


def load_prices(content):
    """Load prices from CSV content and return (prices, findings)."""
    return zmd.load_prices(write_file(content))


def load_names(content):
    """Load names from text content and return the set."""
    return zmd.load_names(write_file(content, suffix=".txt"))


class TestNormalization(unittest.TestCase):
    """Title and text normalization."""

    def test_normalize_title(self):
        self.assertEqual(zmd.normalize_title("Refund request"),
                         "refund request")
        self.assertEqual(zmd.normalize_title("Refund-Request!"),
                         "refund request")
        self.assertEqual(zmd.normalize_title("REFUND  REQUEST"),
                         "refund request")
        self.assertEqual(zmd.normalize_title("Billing: refund request"),
                         "billing refund request")

    def test_normalize_text(self):
        self.assertEqual(
            zmd.normalize_text("  Hello   World  "), "hello world")
        self.assertEqual(zmd.normalize_text(""), "")


class TestParsingHelpers(unittest.TestCase):
    """Timestamp, use count, and action parsing."""

    def test_parse_timestamp_iso(self):
        parsed = zmd.parse_timestamp("2026-11-10T09:30:00Z")
        self.assertEqual(parsed,
                         datetime(2026, 11, 10, 9, 30,
                                  tzinfo=timezone.utc))

    def test_parse_timestamp_date_only(self):
        parsed = zmd.parse_timestamp("2026-11-10")
        self.assertEqual(parsed,
                         datetime(2026, 11, 10,
                                  tzinfo=timezone.utc))

    def test_parse_timestamp_invalid(self):
        self.assertIsNone(zmd.parse_timestamp("not-a-date"))
        self.assertIsNone(zmd.parse_timestamp(""))
        self.assertIsNone(zmd.parse_timestamp(None))

    def test_parse_use_count(self):
        self.assertEqual(zmd.parse_use_count("42"), (42, True))
        self.assertEqual(zmd.parse_use_count("1,000"), (1000, True))
        self.assertEqual(zmd.parse_use_count(""), (None, True))
        self.assertEqual(zmd.parse_use_count(None), (None, True))
        self.assertEqual(zmd.parse_use_count("many"), (None, False))
        self.assertEqual(zmd.parse_use_count("-3"), (None, False))
        self.assertEqual(zmd.parse_use_count("4.5"), (None, False))

    def test_parse_actions(self):
        actions = zmd.parse_actions(
            "comment: Hi; set field: x=1; add tag: y")
        self.assertEqual(actions, ["comment: Hi", "set field: x=1",
                                  "add tag: y"])
        self.assertEqual(zmd.parse_actions(""), [])
        self.assertEqual(zmd.parse_actions(None), [])

    def test_action_kind(self):
        self.assertEqual(zmd.action_kind("comment: Hi"), "comment")
        self.assertEqual(zmd.action_kind("set field: x=1"), "field")
        self.assertEqual(
            zmd.action_kind("set custom field: x=1"), "field")
        self.assertEqual(zmd.action_kind("add tag: y"), "tag")
        self.assertEqual(
            zmd.action_kind("assign to group: Team"), "assign-group")
        self.assertEqual(
            zmd.action_kind("assign to agent: Dana"), "assign-agent")
        self.assertEqual(zmd.action_kind("set status: solved"),
                         "other")

    def test_action_value(self):
        self.assertEqual(zmd.action_value("assign to group: Team"),
                         "Team")
        self.assertEqual(zmd.action_value("no colon"), "")


class TestCsvParsing(unittest.TestCase):
    """CSV parsing, aliases, and bad data."""

    def test_parse_clean_row(self):
        macros, findings = parse(HEADER + row())
        self.assertEqual(len(macros), 1)
        self.assertEqual(findings, [])
        self.assertEqual(macros[0]["id"], "M-1")
        self.assertEqual(macros[0]["use_count"], 42)
        self.assertEqual(macros[0]["status"], "active")
        self.assertEqual(macros[0]["text"],
                         "Follow the steps to reset your password")

    def test_parse_text_from_comment_action(self):
        content = HEADER + row(comment_text="")
        macros, findings = parse(content)
        self.assertEqual(findings, [])
        self.assertEqual(macros[0]["text"],
                         "Follow the steps")

    def test_parse_header_aliases(self):
        content = ("id,name,group,action,last_used,uses,state,"
                   "updated,reply_text\n"
                   "M-1,Reset password help,Account,"
                   "comment: Follow the steps,2026-11-10,42,active,"
                   "2026-11-01,Follow the steps\n")
        macros, findings = parse(content)
        self.assertEqual(len(macros), 1)
        self.assertEqual(findings, [])

    def test_parse_bom_header(self):
        macros, findings = parse("\ufeff" + HEADER + row())
        self.assertEqual(len(macros), 1)
        self.assertEqual(findings, [])

    def test_parse_duplicate_id(self):
        content = HEADER + row() + row()
        _macros, findings = parse(content)
        self.assertEqual(rules(findings), ["duplicate-macro-id"])

    def test_parse_empty_title(self):
        content = HEADER + row(title="") + row()
        macros, findings = parse(content)
        self.assertEqual(len(macros), 1)
        self.assertEqual(rules(findings), ["empty-title"])

    def test_parse_malformed_row(self):
        content = HEADER + "M-1,Reset\n" + row()
        macros, findings = parse(content)
        self.assertEqual(len(macros), 1)
        self.assertEqual(rules(findings), ["malformed-row"])

    def test_parse_invalid_timestamp(self):
        _macros, findings = parse(HEADER + row(last_used="nope"))
        self.assertEqual(rules(findings), ["invalid-timestamp"])

    def test_parse_future_timestamp(self):
        _macros, findings = parse(HEADER + row(last_used="2026-12-01"))
        self.assertEqual(rules(findings), ["future-timestamp"])

    def test_parse_future_updated(self):
        _macros, findings = parse(HEADER + row(updated="2026-12-01"))
        self.assertEqual(rules(findings), ["future-timestamp"])

    def test_parse_invalid_use_count(self):
        _macros, findings = parse(HEADER + row(use_count="many"))
        self.assertEqual(rules(findings), ["invalid-use-count"])

    def test_parse_missing_file(self):
        with self.assertRaises(ValueError):
            zmd.parse_csv("/nonexistent/macros.csv", AS_OF)

    def test_parse_empty_file(self):
        with self.assertRaises(ValueError):
            zmd.parse_csv(write_file(""), AS_OF)

    def test_parse_no_id_column(self):
        with self.assertRaises(ValueError):
            zmd.parse_csv(write_file("title\nHello\n"), AS_OF)

    def test_parse_blank_lines_skipped(self):
        content = HEADER + "\n" + row() + "\n"
        macros, findings = parse(content)
        self.assertEqual(len(macros), 1)
        self.assertEqual(findings, [])

    def test_parse_inactive_statuses(self):
        for status in ("inactive", "disabled", "retired", "deleted",
                       "archived"):
            macros, _findings = parse(HEADER + row(status=status))
            self.assertIn(macros[0]["status"],
                          zmd.INACTIVE_STATUSES)


class TestReferenceFiles(unittest.TestCase):
    """Prices, dead domains, and deleted names loading."""

    def test_load_prices(self):
        prices, findings = load_prices(PRICES)
        self.assertEqual(findings, [])
        self.assertEqual(prices["pro plan"][1], 39.00)
        self.assertEqual(prices["basic plan"][0], "Basic plan")

    def test_load_prices_invalid_price(self):
        prices, findings = load_prices(
            PRICES_HEADER + "Pro plan,abc\n")
        self.assertEqual(prices, {})
        self.assertEqual(rules(findings), ["invalid-price"])

    def test_load_prices_empty_price(self):
        prices, findings = load_prices(
            PRICES_HEADER + "Pro plan,\n")
        self.assertEqual(prices, {})
        self.assertEqual(rules(findings), ["invalid-price"])

    def test_load_prices_malformed_row(self):
        prices, findings = load_prices(PRICES_HEADER + "Pro plan\n")
        self.assertEqual(prices, {})
        self.assertEqual(rules(findings), ["malformed-row"])

    def test_load_prices_empty_product(self):
        prices, findings = load_prices(
            PRICES_HEADER + ",39.00\n")
        self.assertEqual(prices, {})
        self.assertEqual(rules(findings), ["empty-product"])

    def test_load_prices_missing_file(self):
        with self.assertRaises(ValueError):
            zmd.load_prices("/nonexistent/prices.csv")

    def test_load_names(self):
        names = load_names(DEAD_DOMAINS)
        self.assertEqual(names, {"old-help.example.com",
                                 "legacy.example.com"})
        deleted = load_names(DELETED)
        self.assertEqual(deleted, {"legacy support", "dana cole"})

    def test_load_names_missing_file(self):
        with self.assertRaises(ValueError):
            zmd.load_names("/nonexistent/names.txt")


class TestAuditRules(unittest.TestCase):
    """The audit rules."""

    def test_clean_rows_no_findings(self):
        content = HEADER + row() + row(
            "M-2", "Check order status", "Orders",
            "comment: Your order is on its way; set field: "
            "issue_type=order_status; add tag: order_status",
            "2026-11-12", "30", "active", "2026-11-02",
            "Your order is on its way")
        _macros, findings = audit(content)
        self.assertEqual(findings, [])

    def test_unused_macro(self):
        _macros, findings = audit(HEADER + row(last_used="2026-08-01"))
        self.assertEqual(rules(findings), ["unused-macro"])
        message = by_rule(findings, "unused-macro")[0].message
        self.assertIn("109 days", message)

    def test_unused_macro_custom_days(self):
        content = HEADER + row(last_used="2026-08-01")
        _macros, findings = audit(content, unused_days=200)
        self.assertEqual(findings, [])

    def test_unused_boundary(self):
        content = HEADER + row(last_used="2026-08-20")
        _macros, findings = audit(content)
        self.assertEqual(findings, [])
        content = HEADER + row(last_used="2026-08-19")
        _macros, findings = audit(content)
        self.assertEqual(rules(findings), ["unused-macro"])

    def test_never_used_macro(self):
        content = HEADER + row(last_used="", use_count="0")
        _macros, findings = audit(content)
        self.assertEqual(rules(findings), ["unused-macro"])

    def test_inactive_skips_unused(self):
        content = HEADER + row(last_used="2026-01-15", use_count="0",
                               status="inactive")
        _macros, findings = audit(content)
        self.assertEqual(findings, [])

    def test_low_use_macro(self):
        _macros, findings = audit(HEADER + row(use_count="4"))
        self.assertEqual(rules(findings), ["low-use-macro"])

    def test_low_use_boundary(self):
        _macros, findings = audit(HEADER + row(use_count="5"))
        self.assertEqual(findings, [])
        _macros, findings = audit(HEADER + row(use_count="5"),
                                  min_uses=2)
        self.assertEqual(findings, [])

    def test_low_use_zero_count_is_unused_not_low(self):
        content = HEADER + row(last_used="", use_count="0")
        _macros, findings = audit(content)
        self.assertEqual(rules(findings), ["unused-macro"])

    def test_missing_last_used(self):
        content = HEADER + row(last_used="", use_count="9")
        _macros, findings = audit(content)
        self.assertEqual(rules(findings), ["missing-last-used"])

    def test_no_actions(self):
        content = HEADER + row(actions="", comment_text="")
        _macros, findings = audit(content)
        self.assertEqual(rules(findings), ["no-actions"])

    def test_no_actions_only_error(self):
        content = HEADER + row(actions="", comment_text="",
                               use_count="10")
        _macros, findings = audit(content)
        self.assertEqual(rules(findings), ["no-actions"])

    def test_missing_reporting_actions(self):
        content = HEADER + row(
            actions="comment: Hi; set field: issue_type=x")
        _macros, findings = audit(content)
        self.assertEqual(rules(findings),
                         ["missing-reporting-actions"])
        content = HEADER + row(actions="comment: Hi; add tag: x")
        _macros, findings = audit(content)
        self.assertEqual(rules(findings),
                         ["missing-reporting-actions"])
        content = HEADER + row(actions="comment: Hi")
        _macros, findings = audit(content)
        message = by_rule(
            findings, "missing-reporting-actions")[0].message
        self.assertIn("set field action", message)
        self.assertIn("add tag action", message)

    def test_no_category(self):
        _macros, findings = audit(HEADER + row(category=""))
        self.assertEqual(rules(findings), ["no-category"])

    def test_duplicate_title(self):
        content = HEADER + row() + row(
            "M-2", "Reset Password Help",
            comment_text="A completely different reply")
        _macros, findings = audit(content)
        self.assertEqual(rules(findings), ["duplicate-title"])

    def test_duplicate_title_separators(self):
        content = HEADER + row(title="Refund request") + row(
            "M-2", "Refund-Request!", "Billing",
            "comment: Different text; set field: issue_type=refund; "
            "add tag: refund", "2026-11-10", "11", "active",
            "2026-11-17", "A refund takes 5 business days")
        _macros, findings = audit(content)
        self.assertEqual(rules(findings), ["duplicate-title"])

    def test_duplicate_text(self):
        content = HEADER + row() + row(
            "M-2", "Another title", "Account",
            "comment: Follow the steps; set field: "
            "issue_type=password_reset; add tag: password_reset",
            "2026-11-12", "38", "active", "2026-11-02",
            "Follow the steps to reset your password")
        _macros, findings = audit(content)
        self.assertEqual(rules(findings), ["duplicate-text"])

    def test_stale_price(self):
        prices, _findings = load_prices(PRICES)
        content = HEADER + row(
            comment_text="Our Pro plan is $29 per month")
        _macros, findings = audit(content, prices=prices)
        self.assertEqual(rules(findings), ["stale-price"])
        message = by_rule(findings, "stale-price")[0].message
        self.assertIn("$29.00", message)
        self.assertIn("$39.00", message)

    def test_stale_price_current_ok(self):
        prices, _findings = load_prices(PRICES)
        content = HEADER + row(
            comment_text="Our Pro plan is $39 per month")
        _macros, findings = audit(content, prices=prices)
        self.assertEqual(findings, [])

    def test_stale_price_no_prices_file(self):
        content = HEADER + row(
            comment_text="Our Pro plan is $29 per month")
        _macros, findings = audit(content)
        self.assertEqual(findings, [])

    def test_stale_price_unknown_product(self):
        prices, _findings = load_prices(PRICES)
        content = HEADER + row(
            comment_text="Our Enterprise plan is $99 per month")
        _macros, findings = audit(content, prices=prices)
        self.assertEqual(findings, [])

    def test_dead_link(self):
        dead = load_names(DEAD_DOMAINS)
        content = HEADER + row(
            comment_text="See https://old-help.example.com/guide")
        _macros, findings = audit(content, dead_domains=dead)
        self.assertEqual(rules(findings), ["dead-link"])

    def test_dead_link_subdomain(self):
        dead = load_names(DEAD_DOMAINS)
        content = HEADER + row(
            comment_text="See https://docs.old-help.example.com/x")
        _macros, findings = audit(content, dead_domains=dead)
        self.assertEqual(rules(findings), ["dead-link"])

    def test_dead_link_live_domain_ok(self):
        dead = load_names(DEAD_DOMAINS)
        content = HEADER + row(
            comment_text="See https://help.example.com/guide")
        _macros, findings = audit(content, dead_domains=dead)
        self.assertEqual(findings, [])

    def test_malformed_link(self):
        dead = load_names(DEAD_DOMAINS)
        content = HEADER + row(
            comment_text="See the guide at https://help")
        _macros, findings = audit(content, dead_domains=dead)
        self.assertEqual(rules(findings), ["malformed-link"])

    def test_deleted_reference_group(self):
        deleted = load_names(DELETED)
        content = HEADER + row(
            actions="comment: Escalating; assign to group: "
                    "Legacy Support; set field: issue_type=x; "
                    "add tag: x")
        _macros, findings = audit(content, deleted_names=deleted)
        self.assertEqual(rules(findings), ["deleted-reference"])

    def test_deleted_reference_agent(self):
        deleted = load_names(DELETED)
        content = HEADER + row(
            actions="comment: Passing on; assign to agent: "
                    "Dana Cole; set field: issue_type=x; "
                    "add tag: x")
        _macros, findings = audit(content, deleted_names=deleted)
        self.assertEqual(rules(findings), ["deleted-reference"])

    def test_deleted_reference_live_group_ok(self):
        deleted = load_names(DELETED)
        content = HEADER + row(
            actions="comment: Escalating; assign to group: "
                    "Support Team; set field: issue_type=x; "
                    "add tag: x")
        _macros, findings = audit(content, deleted_names=deleted)
        self.assertEqual(findings, [])


class TestSummaryAndOutput(unittest.TestCase):
    """Summary math, text and JSON output, and CLI behavior."""

    def test_summary_counts(self):
        content = HEADER + row(last_used="2026-08-01")
        macros, findings = audit(content)
        summary = zmd.summarize(macros, findings, AS_OF)
        self.assertEqual(summary["macros"], 1)
        self.assertEqual(summary["active"], 1)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["unused"], 1)
        self.assertEqual(summary["total_uses"], 42)
        self.assertEqual(len(summary["per_category"]), 1)
        self.assertEqual(summary["per_category"][0]["category"],
                         "Account")
        self.assertEqual(summary["per_category"][0]["unused"], 1)

    def test_summary_no_category_group(self):
        content = HEADER + row(category="")
        macros, findings = audit(content)
        summary = zmd.summarize(macros, findings, AS_OF)
        self.assertEqual(summary["per_category"][0]["category"],
                         "(no category)")
        self.assertEqual(summary["no_category"], 1)

    def test_render_text_clean(self):
        macros, findings = audit(HEADER + row())
        summary = zmd.summarize(macros, findings, AS_OF)
        text = zmd.render_text(macros, findings, summary)
        self.assertIn("All macros clean.", text)
        self.assertIn("Macros: 1, active: 1", text)

    def test_render_text_findings(self):
        macros, findings = audit(HEADER + row(last_used="2026-08-01"))
        summary = zmd.summarize(macros, findings, AS_OF)
        text = zmd.render_text(macros, findings, summary)
        self.assertIn("ERROR [unused-macro]", text)
        self.assertIn("Macros: 1, active: 1, inactive: 0, "
                      "findings: 1, errors: 1, warnings: 0", text)

    def test_render_json(self):
        macros, findings = audit(HEADER + row())
        summary = zmd.summarize(macros, findings, AS_OF)
        report = json.loads(zmd.render_json(macros, findings,
                                            summary))
        self.assertEqual(report["errors"], 0)
        self.assertEqual(report["macros"], 1)
        self.assertEqual(report["findings"], [])

    def test_render_json_findings(self):
        macros, findings = audit(HEADER + row(last_used="2026-08-01"))
        summary = zmd.summarize(macros, findings, AS_OF)
        report = json.loads(zmd.render_json(macros, findings,
                                            summary))
        self.assertEqual(len(report["findings"]), 1)
        self.assertEqual(report["findings"][0]["rule"],
                         "unused-macro")

    def test_main_check_exit_codes(self):
        path = write_file(HEADER + row(last_used="2026-08-01"))
        self.assertEqual(zmd.main([path, "--as-of", "2026-11-18",
                                   "--check"]), 1)
        clean = write_file(HEADER + row())
        self.assertEqual(zmd.main([clean, "--as-of", "2026-11-18",
                                   "--check"]), 0)

    def test_main_strict_exit_codes(self):
        path = write_file(HEADER + row(category=""))
        self.assertEqual(zmd.main([path, "--as-of", "2026-11-18",
                                   "--check"]), 0)
        self.assertEqual(zmd.main([path, "--as-of", "2026-11-18",
                                   "--check", "--strict"]), 1)

    def test_main_json_flag(self):
        path = write_file(HEADER + row())
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = zmd.main([path, "--as-of", "2026-11-18", "--json"])
        self.assertEqual(code, 0)
        report = json.loads(buffer.getvalue())
        self.assertEqual(report["macros"], 1)

    def test_main_out_file(self):
        path = write_file(HEADER + row())
        out = tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False).name
        code = zmd.main([path, "--as-of", "2026-11-18", "--out", out])
        self.assertEqual(code, 0)
        with open(out, encoding="utf-8") as handle:
            self.assertIn("All macros clean.", handle.read())

    def test_main_bad_as_of(self):
        path = write_file(HEADER + row())
        self.assertEqual(zmd.main([path, "--as-of", "not-a-date"]), 2)

    def test_main_negative_unused_days(self):
        path = write_file(HEADER + row())
        self.assertEqual(zmd.main([path, "--unused-days", "-1"]), 2)

    def test_main_missing_prices_file(self):
        path = write_file(HEADER + row())
        self.assertEqual(zmd.main([path, "--prices-file",
                                   "/nonexistent/prices.csv"]), 2)

    def test_main_missing_dead_domains_file(self):
        path = write_file(HEADER + row())
        self.assertEqual(zmd.main([path, "--dead-domains-file",
                                   "/nonexistent/dead.txt"]), 2)

    def test_main_missing_deleted_file(self):
        path = write_file(HEADER + row())
        self.assertEqual(zmd.main([path, "--deleted-file",
                                   "/nonexistent/deleted.txt"]), 2)

    def test_sample_file(self):
        path = os.path.join(EXAMPLES, "macros.csv")
        prices = os.path.join(EXAMPLES, "prices.csv")
        dead = os.path.join(EXAMPLES, "dead-domains.txt")
        deleted = os.path.join(EXAMPLES, "deleted.txt")
        macros, findings = zmd.parse_csv(path, AS_OF)
        prices_data, _p = zmd.load_prices(prices)
        findings.extend(zmd.audit(
            macros, AS_OF, prices=prices_data,
            dead_domains=zmd.load_names(dead),
            deleted_names=zmd.load_names(deleted)))
        summary = zmd.summarize(macros, findings, AS_OF)
        self.assertEqual(summary["macros"], 24)
        self.assertEqual(summary["findings"], 30)
        self.assertEqual(summary["errors"], 20)
        self.assertEqual(summary["warnings"], 10)
        self.assertEqual(summary["total_uses"], 421)
        self.assertEqual(summary["unused"], 2)
        self.assertEqual(summary["duplicate_titles"], 3)
        self.assertEqual(summary["duplicate_texts"], 3)
        self.assertEqual(summary["no_category"], 1)

    def test_sample_file_no_reference_files(self):
        path = os.path.join(EXAMPLES, "macros.csv")
        macros, findings = zmd.parse_csv(path, AS_OF)
        findings.extend(zmd.audit(macros, AS_OF))
        summary = zmd.summarize(macros, findings, AS_OF)
        self.assertEqual(summary["findings"], 24)
        self.assertEqual(summary["errors"], 15)
        self.assertEqual(summary["warnings"], 9)

    def test_clean_file(self):
        path = os.path.join(EXAMPLES, "clean.csv")
        prices = os.path.join(EXAMPLES, "prices.csv")
        dead = os.path.join(EXAMPLES, "dead-domains.txt")
        deleted = os.path.join(EXAMPLES, "deleted.txt")
        macros, findings = zmd.parse_csv(path, AS_OF)
        prices_data, _p = zmd.load_prices(prices)
        findings.extend(zmd.audit(
            macros, AS_OF, prices=prices_data,
            dead_domains=zmd.load_names(dead),
            deleted_names=zmd.load_names(deleted)))
        self.assertEqual(findings, [])

    def test_warnings_only_file(self):
        path = os.path.join(EXAMPLES, "warnings-only.csv")
        macros, findings = zmd.parse_csv(path, AS_OF)
        findings.extend(zmd.audit(macros, AS_OF))
        self.assertEqual(len(findings), 3)
        self.assertEqual(
            sorted(item.severity for item in findings),
            ["warning", "warning", "warning"])
        self.assertEqual(zmd.main([path, "--as-of", "2026-11-18",
                                   "--check"]), 0)
        self.assertEqual(zmd.main([path, "--as-of", "2026-11-18",
                                   "--check", "--strict"]), 1)


if __name__ == "__main__":
    unittest.main()
