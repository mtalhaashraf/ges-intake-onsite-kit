import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from ges_intake.fields import parse_account, parse_date, parse_ein, parse_money, parse_ssn, parse_year
from ges_intake.manifest import parse_timestamp
from ges_intake.text import name_tokens, normalise, phrase_view, scrub_identifiers


class MoneyTest(unittest.TestCase):
    def test_forms(self):
        cases = {
            "$48,250.00": "48250.00", "1,000.1": "1000.10", "($45.20)": "-45.20", "-45.20": "-45.20",
            "-$45.20": "-45.20", "$-45.20": "-45.20", "$(45.20)": "-45.20", "5000": "5000.00", "-0.00": "0.00",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                value = parse_money(raw)
                self.assertIsInstance(value, Decimal)
                self.assertEqual(format(value, "f"), expected)

    def test_rejects(self):
        for raw in ["", "$", "1,00", "12,3456.00", "(45.20", "-(45.20)", "45.20 USD", "1.005", "abc", "١٢٣"]:
            with self.subTest(raw=raw):
                self.assertIsNone(parse_money(raw))

    def test_decimal_equality_is_exact(self):
        self.assertEqual(parse_money("$1,000.1"), parse_money("1000.10"))


class DateTest(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(parse_date("2025-02-01"), date(2025, 2, 1))
        self.assertEqual(parse_date("02/28/2026"), date(2026, 2, 28))
        self.assertEqual(parse_date("January 31, 2025"), date(2025, 1, 31))
        self.assertEqual(parse_date("may 5, 2021"), date(2021, 5, 5))

    def test_rejects(self):
        for raw in ["2025-02-30", "13/01/2025", "Jan 31, 2025", "31 January 2025", "2025/01/01", "January 31 2025"]:
            with self.subTest(raw=raw):
                self.assertIsNone(parse_date(raw))


class IdentifierTest(unittest.TestCase):
    def test_ssn_is_masked_and_keeps_the_full_value_only_in_memory(self):
        ssn = parse_ssn("900-12-3456")
        self.assertEqual(ssn.masked, "***-**-3456")
        self.assertNotIn("900", repr(ssn))
        self.assertEqual(parse_ssn("XXX-XX-3456").masked, "***-**-3456")
        self.assertEqual(parse_ssn("***-**-3456").masked, "***-**-3456")
        self.assertIsNone(parse_ssn("900123456"))

    def test_ein_account_year(self):
        self.assertEqual(parse_ein("58-1234567"), "**-***4567")
        self.assertIsNone(parse_ein("581234567"))
        self.assertEqual(parse_account("0004 5512 4821"), "4821")
        self.assertEqual(parse_account("77-0019930"), "9930")
        self.assertIsNone(parse_account("123"))
        self.assertIsNone(parse_account("XXXX4821"))
        self.assertEqual(parse_year("2025"), 2025)
        self.assertIsNone(parse_year("٢٠٢٥"))


class TextTest(unittest.TestCase):
    def test_normalise_and_phrase_view(self):
        text = normalise("﻿A\r\nB\rC  D")
        self.assertEqual(text, "A\nB\nC  D")
        self.assertEqual(phrase_view(text), "a b c d")

    def test_name_tokens(self):
        self.assertEqual(name_tokens("RIVERA, JORDAN Q"), name_tokens("Jordan Q. Rivera"))
        self.assertEqual(name_tokens("Jordan Rivera"), ["jordan", "rivera"])
        self.assertNotEqual(name_tokens("Jordan Riviera"), name_tokens("Jordan Q. Rivera"))

    def test_scrub(self):
        text = ("SSN 900-12-3456 EIN 58-1234567 acct 0004 5512 4821 lic 0571 2290 4410 pp 5512 90871 "
                "a@b.com masked XXX-XX-3456 on 2025-01-01 total $48,250.00 year 2025")
        out = scrub_identifiers(text, names=("rivera",))
        for leaked in ["900-12", "1234567", "5512", "2290", "90871", "a@b.com"]:
            self.assertNotIn(leaked, out)
        for kept in ["XXX-XX-3456", "2025-01-01", "$48,250.00", "year 2025"]:
            self.assertIn(kept, out)
        self.assertEqual(scrub_identifiers("Jordan RIVERA", names=("rivera",)), "Jordan [REDACTED-NAME]")


class TimestampTest(unittest.TestCase):
    def test_offsets_become_utc(self):
        self.assertEqual(parse_timestamp("2026-02-17T06:02:00-05:00"), datetime(2026, 2, 17, 11, 2, tzinfo=timezone.utc))
        self.assertEqual(parse_timestamp("2026-02-17T10:01:00Z"), datetime(2026, 2, 17, 10, 1, tzinfo=timezone.utc))
        self.assertIsNotNone(parse_timestamp("2026-02-17T10:01:00.123+0530"))

    def test_invalid(self):
        for raw in ["2026-02-21T09:08:00", "2026-02-21", "2026-02-30T00:00:00Z", "yesterday", None, 17,
                    "2026-02-21T09:08:00+25:00", " 2026-02-21T09:08:00Z"]:
            with self.subTest(raw=raw):
                self.assertIsNone(parse_timestamp(raw))


if __name__ == "__main__":
    unittest.main()
