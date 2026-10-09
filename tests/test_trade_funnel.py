import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from agents import trade_funnel


class TradeFunnelTests(unittest.TestCase):
    def test_funnel_tracks_discovery_to_realized_profit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            morning = root / "morning"
            intraday = root / "intraday"
            morning.mkdir()
            intraday.mkdir()
            (morning / "morning_brief_20261005_070000.json").write_text(json.dumps({
                "created_at": "2026-10-05T07:00:00-04:00",
                "symbols_scanned": ["AMD", "PANW"],
                "committee_summaries": [
                    {"symbol": "AMD", "run_id": "AMD-RUN"},
                    {"symbol": "PANW", "run_id": "PANW-RUN"},
                ],
                "conditional_setups": [{"symbol": "AMD", "run_id": "AMD-RUN", "score": 90}],
            }), encoding="utf-8")
            equities = pd.DataFrame([{
                "id": "T-1",
                "symbol": "AMD",
                "status": "closed",
                "source": "autonomous paper mandate",
                "setup_type": "autonomous_swing_experiment",
                "agent_run_id": "AMD-RUN",
                "opened_at": "2026-10-05T10:00:00-04:00",
                "closed_at": "2026-10-05T15:00:00-04:00",
                "realized_pnl": "125.50",
            }])
            with patch.object(trade_funnel, "MORNING_BRIEF_DIR", morning), patch.object(
                trade_funnel, "INTRADAY_DISCOVERY_DIR", intraday
            ), patch.object(trade_funnel, "load_trade_journal", return_value=equities), patch.object(
                trade_funnel, "load_options_journal", return_value=pd.DataFrame()
            ):
                report = trade_funnel.generate_trade_funnel_report(save=False)

        self.assertEqual(report["stage_counts"]["discovered"], 2)
        self.assertEqual(report["stage_counts"]["qualified"], 1)
        self.assertEqual(report["stage_counts"]["trades_closed"], 1)
        self.assertEqual(report["stage_counts"]["profitable_trades"], 1)
        self.assertEqual(report["realized_pnl"], 125.50)
        self.assertEqual(report["trades_remaining_to_evidence_target"], 29)


if __name__ == "__main__":
    unittest.main()
