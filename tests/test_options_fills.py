import unittest

from data.options_fills import exit_event, planned_event


class OptionsFillTests(unittest.TestCase):
    def test_planned_option_waits_for_underlying_trigger(self):
        row = {
            "id": "O-1",
            "symbol": "TEST",
            "strategy": "long_call",
            "entry_premium": 2.0,
            "underlying_entry_trigger": 100,
            "underlying_trigger_direction": "at_or_below",
        }
        quote = {
            "contract_symbol": "TESTC",
            "bid": 1.8,
            "ask": 2.0,
            "underlying_price": 101,
            "timestamp": "2026-09-30T10:00:00",
        }

        self.assertIsNone(planned_event(row, quote))
        quote["underlying_price"] = 99.5
        self.assertEqual(planned_event(row, quote)["event_type"], "entry_fill")

    def test_option_exit_uses_bid(self):
        row = {
            "id": "O-1",
            "symbol": "TEST",
            "strategy": "long_call",
            "target_premium": 3.0,
            "stop_premium": 1.0,
            "expiration": "2026-12-18",
        }
        quote = {
            "contract_symbol": "TESTC",
            "bid": 3.1,
            "ask": 3.3,
            "timestamp": "2026-09-30T10:00:00",
        }

        event = exit_event(row, quote)

        self.assertEqual(event["fill_price"], 3.1)
        self.assertEqual(event["reason"], "option premium target reached")


if __name__ == "__main__":
    unittest.main()
