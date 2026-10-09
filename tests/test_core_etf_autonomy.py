import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from agents.core_etf_sleeve import (
    build_core_rebalance_plan,
    execute_autonomous_core_rebalance,
)


EASTERN = ZoneInfo("America/New_York")


def core_row(symbol, shares, price, trade_id):
    return {
        "id": trade_id,
        "opened_at": "2026-07-01T10:00:00-04:00",
        "symbol": symbol,
        "side": "long",
        "status": "open",
        "setup_type": "core etf sleeve",
        "entry": str(price),
        "current_price": str(price),
        "shares": str(shares),
    }


class CoreEtfAutonomyTests(unittest.TestCase):
    def setUp(self):
        self.brief = {
            "created_at": "2026-09-30T08:00:00-04:00",
            "core_etf_sleeve": {"regime": "Neutral"},
        }

    def test_overweight_sleeve_produces_trim_orders(self):
        journal = pd.DataFrame([
            core_row("VOO", 30, 500, "V1"),
            core_row("QQQ", 20, 500, "Q1"),
            core_row("SMH", 40, 300, "S1"),
            core_row("XLV", 100, 150, "H1"),
            core_row("XLE", 100, 100, "E1"),
        ])
        ledger = {"account": {"net_liquidation_value": 100000, "cash_balance": 38000}}
        prices = {"VOO": 500, "QQQ": 500, "SMH": 300, "XLV": 150, "XLE": 100}

        plan = build_core_rebalance_plan(
            self.brief, journal=journal, ledger=ledger, price_map=prices
        )

        self.assertTrue(plan["requires_rebalance"])
        self.assertGreater(plan["current_sleeve_value"], plan["target_sleeve_value"])
        self.assertTrue(plan["orders"])
        self.assertTrue(all(order["action"] == "sell" for order in plan["orders"]))

    def test_within_band_produces_no_orders(self):
        journal = pd.DataFrame([
            core_row("VOO", 27, 500, "V1"),
            core_row("QQQ", 18, 500, "Q1"),
            core_row("SMH", 22, 300, "S1"),
            core_row("XLV", 60, 150, "H1"),
            core_row("XLE", 67, 100, "E1"),
        ])
        ledger = {"account": {"net_liquidation_value": 100000, "cash_balance": 55200}}
        prices = {"VOO": 500, "QQQ": 500, "SMH": 300, "XLV": 150, "XLE": 100}

        plan = build_core_rebalance_plan(
            self.brief, journal=journal, ledger=ledger, price_map=prices
        )

        self.assertFalse(plan["requires_rebalance"])
        self.assertEqual(plan["orders"], [])

    def test_execution_is_deferred_outside_market_hours(self):
        result = execute_autonomous_core_rebalance(
            self.brief,
            now=datetime(2026, 9, 30, 18, 0, tzinfo=EASTERN),
        )

        self.assertEqual(result["status"], "deferred")
        self.assertEqual(result["actions"], [])

    def test_execution_rejects_stale_brief_before_reading_ledger(self):
        result = execute_autonomous_core_rebalance(
            self.brief,
            now=datetime(2026, 10, 1, 10, 0, tzinfo=EASTERN),
        )

        self.assertEqual(result["status"], "blocked")
        self.assertIn("current-day", result["reason"])


if __name__ == "__main__":
    unittest.main()
