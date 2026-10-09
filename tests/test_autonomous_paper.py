import unittest
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from agents.autonomous_paper import (
    autonomous_orders_created_on,
    build_adaptive_parameters,
    build_equity_fallback_idea,
    candidate_rejection_reason,
    category_capacity_reached,
    completed_autonomous_tactical_count,
    expire_stale_autonomous_orders,
    is_regular_market_hours,
    load_policy,
    option_data_unavailable,
)


EASTERN = ZoneInfo("America/New_York")


class AutonomousPaperTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy()

    def test_adaptive_parameters_remain_bounded_with_strong_results(self):
        feedback = {
            "setup_review_learning": {
                "learning_score": 95,
                "target_1_hit_rate": 80,
                "partial_win_rate": 90,
            }
        }

        result = build_adaptive_parameters(feedback, self.policy)

        self.assertLessEqual(result["risk_per_trade_pct"], 0.0025)
        self.assertLessEqual(result["max_new_orders_per_day"], 3)
        self.assertGreaterEqual(result["target_r_multiple"], 0.5)
        self.assertFalse(result["risk_expansion_unlocked"])
        self.assertEqual(result["completed_tactical_trades"], 0)
        self.assertLessEqual(result["risk_per_trade_pct"], 0.0015)

    def test_risk_expansion_unlocks_only_after_thirty_autonomous_closures(self):
        feedback = {
            "setup_review_learning": {
                "learning_score": 95,
                "target_1_hit_rate": 80,
                "partial_win_rate": 90,
            }
        }

        locked = build_adaptive_parameters(feedback, self.policy, completed_tactical_trades=29)
        unlocked = build_adaptive_parameters(feedback, self.policy, completed_tactical_trades=30)

        self.assertFalse(locked["risk_expansion_unlocked"])
        self.assertLessEqual(locked["risk_per_trade_pct"], 0.0015)
        self.assertTrue(unlocked["risk_expansion_unlocked"])
        self.assertGreater(unlocked["risk_per_trade_pct"], locked["risk_per_trade_pct"])

    def test_completed_tactical_count_excludes_core_sleeve(self):
        equities = pd.DataFrame([
            {"status": "closed", "source": "autonomous paper mandate", "setup_type": "autonomous_swing_experiment"},
            {"status": "closed", "source": "autonomous paper mandate", "setup_type": "core_etf_rebalance"},
            {"status": "closed", "source": "manual", "setup_type": "swing"},
        ])
        options = pd.DataFrame([
            {"status": "closed", "source": "autonomous paper mandate"},
        ])

        self.assertEqual(completed_autonomous_tactical_count(equities, options), 2)

    def test_candidate_must_pass_score_and_risk_authorization(self):
        idea = {
            "symbol": "TEST",
            "decision": "CONDITIONAL SETUP",
            "score": 40,
            "technical_stance": "bullish",
            "risk_decision": "conditional_setup",
            "tradability": "tradable",
            "reward_to_risk": 1.2,
            "suggested_entry": 100,
            "stop": 95,
            "side": "long",
        }
        adaptive = {"minimum_candidate_score": 80}

        reason = candidate_rejection_reason(
            idea,
            self.policy,
            adaptive,
            active_symbols=set(),
            active_run_ids=set(),
            used_categories=set(),
        )

        self.assertIn("score", reason.lower())

    def test_duplicate_symbol_is_rejected(self):
        idea = {
            "symbol": "TEST",
            "decision": "CONDITIONAL SETUP",
            "score": 90,
            "technical_stance": "bullish",
            "risk_decision": "conditional_setup",
            "tradability": "tradable",
            "reward_to_risk": 1.2,
            "suggested_entry": 100,
            "stop": 95,
            "side": "long",
        }

        reason = candidate_rejection_reason(
            idea,
            self.policy,
            {"minimum_candidate_score": 80},
            active_symbols={"TEST"},
            active_run_ids=set(),
            used_categories=set(),
        )

        self.assertIn("already", reason.lower())

    def test_market_hours_gate(self):
        self.assertTrue(is_regular_market_hours(datetime(2026, 9, 29, 10, 0, tzinfo=EASTERN)))
        self.assertFalse(is_regular_market_hours(datetime(2026, 9, 29, 8, 0, tzinfo=EASTERN)))
        self.assertFalse(is_regular_market_hours(datetime(2026, 9, 27, 10, 0, tzinfo=EASTERN)))

    def test_daily_order_count_includes_closed_autonomous_orders(self):
        journal = pd.DataFrame([
            {
                "source": "autonomous paper mandate",
                "status": "closed",
                "opened_at": "2026-09-29T10:00:00-04:00",
            },
            {
                "source": "manual",
                "status": "open",
                "opened_at": "2026-09-29T10:01:00-04:00",
            },
        ])

        rows = autonomous_orders_created_on(journal, date(2026, 9, 29))

        self.assertEqual(len(rows), 1)

    def test_stale_autonomous_plan_is_canceled_before_execution(self):
        journal = pd.DataFrame([
            {
                "id": "T-STALE",
                "symbol": "PANW",
                "status": "planned",
                "source": "autonomous paper mandate",
                "opened_at": "2026-10-01T10:00:00-04:00",
            },
            {
                "id": "T-RECENT",
                "symbol": "AMD",
                "status": "planned",
                "source": "autonomous paper mandate",
                "opened_at": "2026-10-06T10:00:00-04:00",
            },
            {
                "id": "T-MANUAL",
                "symbol": "LITE",
                "status": "planned",
                "source": "manual",
                "opened_at": "2026-09-20T10:00:00-04:00",
            },
        ])
        with patch("agents.autonomous_paper.load_trade_journal", return_value=journal), patch(
            "agents.autonomous_paper.cancel_planned_trade"
        ) as cancel:
            result = expire_stale_autonomous_orders(
                {"max_planned_order_age_days": 5},
                datetime(2026, 10, 7, 10, 0, tzinfo=EASTERN),
            )

        self.assertEqual([item["symbol"] for item in result], ["PANW"])
        cancel.assert_called_once()

    def test_category_limit_allows_two_controlled_experiments(self):
        counts = {"Semiconductors": 1}

        self.assertFalse(category_capacity_reached("Semiconductors", counts, self.policy))
        counts["Semiconductors"] = 2
        self.assertTrue(category_capacity_reached("Semiconductors", counts, self.policy))

    def test_option_provider_failure_can_fall_back_to_equity(self):
        error = "Could not fetch options expirations: Could not resolve host"
        idea = {
            "symbol": "AMD",
            "reason": "Qualified bullish setup.",
            "strategy_plan": {"selected_family": "long_call", "vehicle": "option"},
        }

        fallback = build_equity_fallback_idea(idea, "long_call", error)

        self.assertTrue(option_data_unavailable(error))
        self.assertEqual(fallback["strategy_plan"]["selected_family"], "swing")
        self.assertEqual(fallback["strategy_plan"]["vehicle"], "equity")
        self.assertIn("equity fallback", fallback["reason"])


if __name__ == "__main__":
    unittest.main()
