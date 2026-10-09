import unittest

from agents.strategy_router import route_strategy


class StrategyRouterTests(unittest.TestCase):
    def setUp(self):
        self.data_health = {
            "domain_scores": {
                "options": {"score": 6, "max_score": 10},
            }
        }

    def test_bullish_catalyst_can_route_to_defined_loss_call(self):
        summary = self.base_summary()
        summary["news_catalyst_score"] = 10

        result = route_strategy(summary, data_health=self.data_health, feedback={})

        self.assertEqual(result["selected_family"], "long_call")
        self.assertEqual(result["vehicle"], "option")
        self.assertEqual(result["execution_status"], "eligible")

    def test_bearish_failed_long_can_route_to_put_without_overriding_other_vetoes(self):
        summary = self.base_summary()
        summary.update({
            "technical_stance": "bearish",
            "risk_decision": "veto",
            "risk_vetoes": ["Technical Analyst stance is bearish; long simulated trade is blocked."],
            "failed_long_signal": {"status": "strong_watch"},
            "final_decision": {"status": "NO TRADE"},
        })

        result = route_strategy(summary, data_health=self.data_health, feedback={})

        self.assertEqual(result["selected_family"], "long_put")
        self.assertEqual(result["execution_status"], "eligible")

    def test_scalp_and_day_are_recorded_as_blocked_alternatives(self):
        summary = self.base_summary()
        summary["news_catalyst_score"] = 10

        result = route_strategy(summary, data_health=self.data_health, feedback={})

        names = {item["strategy"] for item in result["blocked_alternatives"]}
        self.assertEqual(names, {"scalp", "day"})

    def test_missing_backtest_sample_does_not_crash_router(self):
        summary = self.base_summary()
        summary["source_reports"]["backtest"] = {
            "expectancy_pct": None,
            "sample_size": None,
        }

        result = route_strategy(summary, data_health=self.data_health, feedback={})

        self.assertEqual(result["selected_family"], "swing")

    @staticmethod
    def base_summary():
        return {
            "technical_stance": "bullish",
            "risk_decision": "conditional_setup",
            "risk_vetoes": [],
            "news_stance": "mixed_or_monitor",
            "market_regime": "Neutral",
            "final_decision": {"status": "CONDITIONAL SETUP"},
            "current_thesis": {"rating": "Deep Research Candidate"},
            "source_reports": {
                "backtest": {"expectancy_pct": 1.0, "sample_size": 20},
                "risk": {"failed_long_signal": {"status": "low"}},
            },
        }


if __name__ == "__main__":
    unittest.main()
