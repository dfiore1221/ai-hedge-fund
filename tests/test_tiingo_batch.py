import unittest
from unittest.mock import Mock, patch

from data.tiingo_data import fetch_latest_equity_price_batch


class TiingoBatchTests(unittest.TestCase):
    @patch("data.tiingo_data.requests.get")
    def test_batch_quotes_are_normalized(self, mock_get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [
            {
                "ticker": "AAPL",
                "tngoLast": 250.5,
                "prevClose": 248.0,
                "volume": 12345,
                "timestamp": "2026-09-30T15:00:00Z",
            }
        ]
        mock_get.return_value = response

        result = fetch_latest_equity_price_batch(["AAPL", "MSFT"], "token")

        self.assertEqual(result["request_mode"], "batch")
        self.assertEqual(result["prices"]["AAPL"]["close"], 250.5)
        self.assertEqual(result["prices"]["AAPL"]["prev_close"], 248.0)
        self.assertIn("MSFT", result["errors"])


if __name__ == "__main__":
    unittest.main()
