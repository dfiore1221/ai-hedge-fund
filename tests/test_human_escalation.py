import unittest

from agents.human_escalation import evaluate_human_escalations, load_policy


class HumanEscalationTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy()

    def test_weak_learning_requests_source_review(self):
        feedback = {
            "setup_review_learning": {
                "reviewed_setups": 100,
                "learning_score": 55,
            },
            "trade_expectancy": {"count": 4, "avg_r": -0.3, "win_rate": 50, "total_pnl": 1000},
        }
        report = evaluate_human_escalations({}, feedback, self.policy)
        event_ids = {event["event_id"] for event in report["events"]}

        self.assertIn("source_code_review", event_ids)
        self.assertNotIn("real_money_readiness_review", event_ids)

    def test_weak_data_domain_requests_subscription_review(self):
        brief = {
            "data_health": {
                "domain_scores": {
                    "options": {"score": 6, "max_score": 10, "status": "enhanced_starter"},
                }
            },
            "missing_information": ["No paid OPRA options flow provider is connected."],
        }
        feedback = {
            "setup_review_learning": {"reviewed_setups": 0, "learning_score": 0},
            "trade_expectancy": {"count": 0},
        }
        report = evaluate_human_escalations(brief, feedback, self.policy)
        event_ids = {event["event_id"] for event in report["events"]}

        self.assertIn("data_subscription_review", event_ids)

    def test_real_money_review_requires_full_evidence_threshold(self):
        brief = {
            "data_health": {"data_quality_score": 98, "domain_scores": {}},
            "benchmark_attribution": {
                "since_inception": {"primary": {"active_return_pct": 2.0}},
            },
            "missing_information": [],
        }
        feedback = {
            "setup_review_learning": {"reviewed_setups": 100, "learning_score": 80},
            "trade_expectancy": {
                "count": 50,
                "avg_r": 0.4,
                "win_rate": 60,
                "total_pnl": 5000,
            },
        }
        report = evaluate_human_escalations(brief, feedback, self.policy)
        event_ids = {event["event_id"] for event in report["events"]}

        self.assertIn("real_money_readiness_review", event_ids)
        event = next(item for item in report["events"] if item["event_id"] == "real_money_readiness_review")
        self.assertIn("Do not connect a broker", event["prohibited_action"])


if __name__ == "__main__":
    unittest.main()
