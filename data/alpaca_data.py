import os
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

from data.local_cache import get_cached_json, get_stale_cached_json, set_cached_json, ttl_seconds


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
ALPACA_DATA_BASE_URL = "https://data.alpaca.markets/v2"
DEFAULT_TIMEOUT = 15
LATEST_BARS_TTL_SECONDS = ttl_seconds(minutes=2)
STALE_FALLBACK_SECONDS = ttl_seconds(minutes=30)


def get_alpaca_credentials():
    load_dotenv(ENV_PATH)
    return {
        "api_key": os.getenv("ALPACA_API_KEY", "").strip(),
        "secret_key": os.getenv("ALPACA_SECRET_KEY", "").strip(),
        "feed": os.getenv("ALPACA_DATA_FEED", "iex").strip() or "iex",
    }


def is_alpaca_configured():
    credentials = get_alpaca_credentials()
    return bool(credentials["api_key"] and credentials["secret_key"])


def fetch_latest_stock_bars(symbols, feed=None):
    symbols = [symbol.upper().strip() for symbol in symbols if symbol and symbol.strip()]
    credentials = get_alpaca_credentials()
    if not symbols:
        return {
            "provider": "Alpaca",
            "configured": is_alpaca_configured(),
            "status": "skipped",
            "bars": {},
            "error": "No symbols supplied.",
        }
    if not credentials["api_key"] or not credentials["secret_key"]:
        return {
            "provider": "Alpaca",
            "configured": False,
            "status": "not_configured",
            "bars": {},
            "error": "ALPACA_API_KEY and ALPACA_SECRET_KEY are not configured.",
        }

    params = {
        "symbols": ",".join(symbols),
        "feed": feed or credentials["feed"],
    }
    cache_key = f"latest-bars:{params['feed']}:{','.join(symbols)}"
    cached = get_cached_json("alpaca", cache_key, LATEST_BARS_TTL_SECONDS)
    if cached:
        return cached

    headers = {
        "APCA-API-KEY-ID": credentials["api_key"],
        "APCA-API-SECRET-KEY": credentials["secret_key"],
        "Accept": "application/json",
    }

    try:
        response = requests.get(
            f"{ALPACA_DATA_BASE_URL}/stocks/bars/latest",
            params=params,
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
    except requests.HTTPError as exc:
        stale = get_stale_cached_json("alpaca", cache_key, STALE_FALLBACK_SECONDS)
        return stale or build_error_response(exc, response=exc.response)
    except requests.RequestException as exc:
        stale = get_stale_cached_json("alpaca", cache_key, STALE_FALLBACK_SECONDS)
        return stale or build_error_response(exc)

    data = response.json()
    raw_bars = data.get("bars") if isinstance(data, dict) else None
    if not isinstance(raw_bars, dict):
        return build_error_response("Unexpected Alpaca latest-bars response.")

    bars = {}
    for symbol, bar in raw_bars.items():
        normalized = normalize_bar(symbol, bar, params["feed"])
        if normalized:
            bars[symbol.upper()] = normalized

    result = {
        "provider": "Alpaca",
        "configured": True,
        "status": "ok" if bars else "empty",
        "feed": params["feed"],
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "bars": bars,
        "symbol_count": len(symbols),
        "bar_count": len(bars),
        "cache": {"status": "fresh"},
    }
    set_cached_json("alpaca", cache_key, result)
    return result


def normalize_bar(symbol, bar, feed):
    if not isinstance(bar, dict):
        return None

    close = bar.get("c")
    try:
        close = float(close)
    except (TypeError, ValueError):
        return None

    return {
        "symbol": symbol.upper(),
        "provider": "Alpaca",
        "feed": feed,
        "timestamp": bar.get("t"),
        "open": safe_float(bar.get("o")),
        "high": safe_float(bar.get("h")),
        "low": safe_float(bar.get("l")),
        "close": close,
        "volume": safe_int(bar.get("v")),
        "trade_count": safe_int(bar.get("n")),
        "vwap": safe_float(bar.get("vw")),
    }


def build_error_response(error, response=None):
    status_code = getattr(response, "status_code", None)
    message = str(error)
    if status_code == 401:
        message = "Alpaca rejected the API credentials."
    elif status_code == 403:
        message = "Alpaca credentials do not have permission for the requested market-data feed."
    elif status_code == 429:
        message = "Alpaca rate limit reached."

    return {
        "provider": "Alpaca",
        "configured": True,
        "status": "error",
        "bars": {},
        "error": message,
        "status_code": status_code,
    }


def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
