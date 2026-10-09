import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from agents import intraday_discovery


EASTERN = ZoneInfo("America/New_York")


class IntradayDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.policy = intraday_discovery.load_policy()
        self.now = datetime(2026, 9, 30, 11, 30, tzinfo=EASTERN)

    def test_breakout_and_volume_acceleration_are_material(self):
        baseline = {
            "category": "Technology",
            "morning_score": 82,
            "morning_decision": "CONDITIONAL SETUP",
            "technical_stance": "bullish",
            "reference_close": 100,
            "atr_14": 2,
            "average_volume_20d": 1_000_000,
            "entry_trigger": 101,
            "pullback_entry": 96,
            "stop": 94,
            "target_1": 105,
        }
        quote = {
            "close": 102,
            "volume": 800_000,
            "timestamp": "2026-09-30T15:30:00Z",
            "freshness": "intraday_or_latest",
        }

        row = intraday_discovery.build_discovery_row(
            "TEST", quote, baseline, self.now, self.policy
        )

        self.assertTrue(row["material"])
        self.assertIn("breakout_trigger_crossed", row["events"])
        self.assertIn("material_price_move", row["events"])
        self.assertIn("material_atr_move", row["events"])
        self.assertIn("volume_acceleration", row["events"])
        self.assertGreater(row["discovery_score"], baseline["morning_score"])

    def test_daily_fallback_quote_cannot_trigger_review(self):
        row = intraday_discovery.build_discovery_row(
            "TEST",
            {"close": 110, "freshness": "daily_fallback"},
            {
                "morning_score": 90,
                "technical_stance": "bullish",
                "reference_close": 100,
                "atr_14": 2,
            },
            self.now,
            self.policy,
        )

        self.assertTrue(row["material"])
        self.assertFalse(row["quote_is_fresh"])

    def test_review_cooldown_dedupes_same_event(self):
        row = {"symbol": "TEST", "events": ["breakout_trigger_crossed"]}
        state = {
            "last_reviews": {
                "TEST": {
                    "reviewed_at": "2026-09-30T11:15:00-04:00",
                    "event_signature": "breakout_trigger_crossed",
                }
            }
        }

        self.assertFalse(
            intraday_discovery.review_is_due(row, state, self.now, self.policy)
        )

    def test_current_day_candidates_are_loaded(self):
        payload = {
            "created_at": "2026-09-30T11:15:00-04:00",
            "status": "ok",
            "qualified_candidates": [{"symbol": "TEST"}],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "intraday_discovery.json"
            report_path.write_text(json.dumps(payload), encoding="utf-8")
            with patch.object(intraday_discovery, "LATEST_REPORT_PATH", report_path):
                candidates = intraday_discovery.load_latest_intraday_candidates(self.now)

        self.assertEqual(candidates, [{"symbol": "TEST"}])

    def test_market_news_discovers_only_new_us_listed_symbols(self):
        news = {
            "status": "ok",
            "items": [
                {
                    "title": "NewCo wins a major contract",
                    "importance_rank": "3",
                    "stocks": [
                        {"name": "NEWC", "exchange": "NASDAQ"},
                        {"name": "AMD", "exchange": "NASDAQ"},
                        {"name": "$BTC", "exchange": None},
                        {"name": "FOREIGN", "exchange": "XLON"},
                    ],
                },
                {
                    "title": "NewCo raises its outlook",
                    "importance_rank": "2",
                    "stocks": [
                        {"name": "NEWC", "exchange": "NASDAQ"},
                        {"name": "SECOND", "exchange": "NYSE"},
                    ],
                },
            ],
        }

        entries = intraday_discovery.discover_market_news_symbols(
            news,
            existing_symbols={"AMD"},
            limit=10,
        )

        self.assertEqual([entry["symbol"] for entry in entries], ["NEWC", "SECOND"])
        self.assertEqual(entries[0]["role"], "dynamic_discovery")
        self.assertEqual(entries[0]["discovery_story_count"], 2)
        self.assertNotIn("AMD", {entry["symbol"] for entry in entries})
        self.assertNotIn("$BTC", {entry["symbol"] for entry in entries})
        self.assertNotIn("FOREIGN", {entry["symbol"] for entry in entries})

    def test_dynamic_news_candidate_is_material_and_rankable(self):
        row = intraday_discovery.build_discovery_row(
            "NEWC",
            {
                "close": 105,
                "prev_close": 100,
                "timestamp": "2026-09-30T15:30:00Z",
                "freshness": "intraday_or_latest",
            },
            {
                "category": "Breaking News Discovery",
                "universe_role": "dynamic_discovery",
                "morning_score": 50,
                "news_discovery_score": 8,
            },
            self.now,
            self.policy,
        )

        self.assertTrue(row["material"])
        self.assertIn("market_news_discovery", row["events"])
        self.assertIn("material_price_move", row["events"])
        self.assertGreater(row["discovery_score"], 70)

    def test_deep_review_requires_critical_event_or_score_threshold(self):
        self.assertFalse(intraday_discovery.deep_review_is_warranted(
            {"events": ["material_price_move"], "discovery_score": 70}, self.policy
        ))
        self.assertTrue(intraday_discovery.deep_review_is_warranted(
            {"events": ["material_price_move"], "discovery_score": 90}, self.policy
        ))
        self.assertTrue(intraday_discovery.deep_review_is_warranted(
            {"events": ["pullback_zone_reached"], "discovery_score": 60}, self.policy
        ))


if __name__ == "__main__":
    unittest.main()
