import unittest
from unittest.mock import patch

import pandas as pd

from agents.committee_question import ask_committee, build_position_context
from data.trade_journal import enrich_trade_metrics, summarize_trade_journal


class TradeStatusHandlingTests(unittest.TestCase):
    def test_planned_order_is_not_open_position_or_unrealized_pnl(self):
        journal = pd.DataFrame([{
            "id": "T-PLAN",
            "symbol": "PANW",
            "status": "planned",
            "side": "long",
            "entry": "200",
            "stop": "190",
            "target": "220",
            "shares": "10",
            "current_price": "210",
        }])

        enriched = enrich_trade_metrics(journal)
        summary = summarize_trade_journal(enriched)
        with patch("agents.committee_question.load_trade_journal", return_value=journal), patch(
            "agents.committee_question.enrich_trade_metrics", return_value=enriched
        ):
            context = build_position_context("PANW")

        self.assertEqual(summary["open_trades"], 0)
        self.assertEqual(summary["planned_trades"], 1)
        self.assertEqual(summary["open_unrealized_pnl"], 0)
        self.assertTrue(context["has_planned_order"])
        self.assertFalse(context["has_open_position"])

    def test_explicit_portfolio_scope_is_not_overridden_by_ticker_mention(self):
        portfolio_report = {
            "run_id": "portfolio-run",
            "symbol": "PORTFOLIO",
            "status": "WATCH ONLY",
            "confidence": 80,
            "answer_markdown": "portfolio answer",
        }
        with patch("agents.committee_question.infer_symbol_from_question", return_value="PANW"), patch(
            "agents.committee_question.answer_portfolio_question", return_value=portfolio_report
        ) as portfolio_answer, patch(
            "agents.committee_question.answer_ticker_question"
        ) as ticker_answer, patch(
            "agents.committee_question.save_committee_question", return_value="Q-1"
        ), patch("agents.committee_question.save_committee_question_report"), patch(
            "agents.committee_question.save_agent_report"
        ):
            report = ask_committee("How does PANW affect the portfolio?", scope="portfolio")

        portfolio_answer.assert_called_once()
        ticker_answer.assert_not_called()
        self.assertEqual(report["symbol"], "PORTFOLIO")


if __name__ == "__main__":
    unittest.main()
