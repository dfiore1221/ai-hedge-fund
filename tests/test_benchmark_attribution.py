import unittest
from datetime import date
from unittest.mock import patch

from agents.benchmark_attribution import component_return, symbol_period_return, weighted_return


class BenchmarkAttributionTests(unittest.TestCase):
    def test_weighted_return(self):
        result = weighted_return([
            {"return_pct": 10.0, "weight": 0.8},
            {"return_pct": 20.0, "weight": 0.2},
        ])

        self.assertEqual(result, 12.0)

    @patch("agents.benchmark_attribution.get_ohlcv_history")
    def test_invalid_dates_are_ignored(self, mock_history):
        mock_history.return_value = {
            "provider": "test",
            "rows": [
                {"date": "invalid", "close": 1},
                {"date": "2026-01-02", "close": 100},
                {"date": "2026-01-05", "close": 110},
            ],
        }

        result = symbol_period_return("SPY", date(2026, 1, 1), date(2026, 1, 31))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["return_pct"], 10.0)

    @patch("agents.benchmark_attribution.symbol_period_return")
    def test_fallback_symbol_is_used(self, mock_return):
        mock_return.side_effect = [
            {"symbol": "VXF", "return_pct": None, "status": "unavailable"},
            {"symbol": "IWM", "return_pct": 4.5, "status": "ok"},
        ]

        result = component_return("VXF", date(2026, 1, 1), date(2026, 1, 31), "IWM")

        self.assertTrue(result["fallback_used"])
        self.assertEqual(result["symbol"], "IWM")


if __name__ == "__main__":
    unittest.main()
