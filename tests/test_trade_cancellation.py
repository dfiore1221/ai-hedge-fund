import unittest
from unittest.mock import patch

import pandas as pd

from data.trade_journal import cancel_planned_trade


class TradeCancellationTests(unittest.TestCase):
    def test_cancel_planned_trade_preserves_history_without_pnl(self):
        journal = pd.DataFrame([{
            "id": "T-PLAN",
            "symbol": "PANW",
            "status": "planned",
            "entry": "362.25",
            "stop": "350.68",
            "target": "370.94",
            "shares": "8",
        }])
        with (
            patch("data.trade_journal.load_trade_journal", return_value=journal),
            patch("data.trade_journal.save_trade_journal") as save,
        ):
            result = cancel_planned_trade(
                "T-PLAN",
                reason="Levels are stale.",
                lessons="Expire plans when price moves beyond the intended target.",
                canceled_at="2026-10-07T16:15:00-04:00",
            )

        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["exit_price"], "")
        self.assertEqual(result["realized_pnl"], "")
        self.assertEqual(result["exit_reason"], "Levels are stale.")
        save.assert_called_once()

    def test_open_position_cannot_be_canceled_as_unfilled_plan(self):
        journal = pd.DataFrame([{
            "id": "T-OPEN",
            "symbol": "SCCO",
            "status": "open",
            "entry": "198.80",
            "stop": "191.75",
            "target": "205.70",
            "shares": "15",
        }])
        with patch("data.trade_journal.load_trade_journal", return_value=journal):
            with self.assertRaisesRegex(ValueError, "Only planned"):
                cancel_planned_trade("T-OPEN")


if __name__ == "__main__":
    unittest.main()
