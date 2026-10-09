import unittest

import pandas as pd

from data.paper_fills import apply_event, execution_price_is_fresh, planned_fill_event


class PaperFillTests(unittest.TestCase):
    def test_long_limit_fill_uses_better_market_price(self):
        event = planned_fill_event(
            {"id": "T-1", "symbol": "AMD"},
            latest=606.33,
            side="long",
            entry=611.00,
            timestamp="2026-09-30T13:11:19-04:00",
        )

        self.assertEqual(event["fill_price"], 606.33)

    def test_short_limit_fill_uses_better_market_price(self):
        event = planned_fill_event(
            {"id": "T-2", "symbol": "TEST"},
            latest=104.00,
            side="short",
            entry=100.00,
            timestamp="2026-09-30T13:11:19-04:00",
        )

        self.assertEqual(event["fill_price"], 104.00)

    def test_entry_application_records_actual_fill_as_cost_basis(self):
        journal = pd.DataFrame([{
            "status": "planned",
            "opened_at": "",
            "entry": "611.00",
            "current_price": "",
            "notes": "",
        }])
        event = {
            "event_type": "entry_fill",
            "timestamp": "2026-09-30T13:11:19-04:00",
            "fill_price": 606.33,
            "latest_price": 606.33,
        }

        apply_event(journal, 0, event)

        self.assertEqual(journal.at[0, "entry"], "606.33")
        self.assertEqual(journal.at[0, "status"], "open")

    def test_daily_fallback_quote_is_not_execution_grade(self):
        metadata = {"freshness": "daily_fallback", "source_field": "daily_close"}

        self.assertFalse(execution_price_is_fresh(metadata))
        self.assertTrue(execution_price_is_fresh(metadata, manual_price_map=True))


if __name__ == "__main__":
    unittest.main()
