import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from agents.intraday_monitor import (
    autonomous_selection_reason,
    check_morning_brief_entry_triggers,
    create_intraday_email_body,
    planned_journal_keys,
)


class IntradayMonitorSelectionTests(unittest.TestCase):
    def test_reports_planner_rejection_without_implying_human_approval(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            reports_dir = Path(temp_dir)
            report = {
                "planning": {
                    "rejected_candidates": [
                        {"symbol": "AMAT", "reason": "Committee score is below the adaptive threshold."}
                    ]
                }
            }
            path = reports_dir / "autonomy.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with patch("agents.intraday_monitor.AUTONOMY_REPORTS_DIR", reports_dir):
                reason = autonomous_selection_reason("AMAT", "2026-10-07-AMAT-committee")

        self.assertEqual(reason, "Committee score is below the adaptive threshold.")

    def test_email_guardrail_describes_autonomous_paper_authority(self):
        report = {
            "created_at": "2026-10-07T10:00:00-04:00",
            "new_alert_count": 0,
            "checked_symbols": [],
            "new_alerts": [],
            "position_manager": {
                "summary": {
                    "open_positions": 0,
                    "planned_positions": 0,
                    "closed_positions": 0,
                    "total_realized_pnl": 0,
                    "total_unrealized_pnl": 0,
                    "net_liquidation": 100000,
                },
                "daily_action_list": [],
                "portfolio_governor": {},
                "market_context": {},
            },
            "paper_fill_check": {"events": [], "applied_events": [], "checked_symbols": []},
            "entry_trigger_check": {"source": "brief", "status": "ok", "checked_symbols": [], "triggered": []},
        }

        with patch(
            "agents.intraday_monitor.format_position_manager_report",
            return_value="Position manager test snapshot",
        ):
            body = create_intraday_email_body(report)

        self.assertIn("without human approval", body)
        self.assertNotIn("require human approval", body)

    def test_active_journal_keys_include_planned_and_open_positions(self):
        journal = pd.DataFrame([
            {"symbol": "AMD", "status": "planned", "agent_run_id": "amd-run"},
            {"symbol": "SCCO", "status": "open", "agent_run_id": "scco-run"},
            {"symbol": "TSM", "status": "closed", "agent_run_id": "tsm-run"},
        ])
        with patch("agents.intraday_monitor.load_trade_journal", return_value=journal):
            keys = planned_journal_keys()

        self.assertEqual(keys["symbols"], {"AMD", "SCCO"})
        self.assertEqual(keys["run_ids"], {"amd-run", "scco-run"})

    def test_unselected_entry_touch_is_informational_not_high_priority(self):
        idea = {
            "symbol": "AMAT",
            "run_id": "amat-run",
            "side": "long",
            "suggested_entry": 100,
            "stop": 95,
            "target_1": 105,
            "score": 70,
        }
        with (
            patch("agents.intraday_monitor.load_morning_brief_entry_ideas", return_value=[idea]),
            patch("agents.intraday_monitor.planned_journal_keys", return_value={"symbols": set(), "run_ids": set()}),
            patch(
                "agents.intraday_monitor.fetch_price_snapshot",
                return_value={"prices": {"AMAT": 99}, "metadata": {}, "provider_status": "ok"},
            ),
            patch(
                "agents.intraday_monitor.autonomous_selection_reason",
                return_value="Committee score is below the adaptive threshold.",
            ),
        ):
            result = check_morning_brief_entry_triggers()

        self.assertEqual(result["alerts"][0]["severity"], "medium")
        self.assertIn("No human approval is required", result["triggered"][0]["action_required"])


if __name__ == "__main__":
    unittest.main()
