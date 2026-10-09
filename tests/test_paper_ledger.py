import unittest

from data.paper_ledger import add_cash_balances, parse_datetime


class PaperLedgerTests(unittest.TestCase):
    def test_mixed_timezone_timestamps_sort_without_error(self):
        transactions = [
            {"timestamp": "2026-09-30T13:11:19-04:00", "cash_delta": -100.0, "fees": 0.0},
            {"timestamp": "2026-09-30T13:12:00", "cash_delta": 10.0, "fees": 0.0},
        ]

        result = add_cash_balances(transactions, 1000.0)

        self.assertEqual(len(result), 2)
        self.assertIsNone(parse_datetime("invalid"))


if __name__ == "__main__":
    unittest.main()
