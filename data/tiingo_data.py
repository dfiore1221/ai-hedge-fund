import os
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

from data.local_cache import get_cached_json, get_stale_cached_json, set_cached_json, ttl_seconds


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
TIINGO_INTRADAY_BASE_URL = "https://api.tiingo.com/tiingo/equity/intraday"
DEFAULT_TIMEOUT = 15
LATEST_PRICE_TTL_SECONDS = ttl_seconds(minutes=5)
STALE_FALLBACK_SECONDS = ttl_seconds(hours=2)


def get_tiingo_api_key():
    load_dotenv(ENV_PATH)
    return os.getenv("TIINGO_API_KEY", "").strip()


def is_tiingo_configured():
    return bool(get_tiingo_api_key())


def fetch_latest_equity_prices(symbols):
    symbols = [symbol.upper().strip() for symbol in symbols if symbol and symbol.strip()]
    api_key = get_tiingo_api_key()
    if not symbols:
        return {
            "provider": "Tiingo",
            "configured": is_tiingo_configured(),
            "status": "skipped",
            "prices": {},
            "error": "No symbols supplied.",
        }
    if not api_key:
        return {
            "provider": "Tiingo",
            "configured": False,
            "status": "not_configured",
            "prices": {},
            "error": "TIINGO_API_KEY is not configured.",
        }

    cache_key = f"latest-equity-prices:{','.join(symbols)}"
    cached = get_cached_json("tiingo", cache_key, LATEST_PRICE_TTL_SECONDS)
    if cached:
        return cached

    prices = {}
    errors = {}
    for symbol in symbols:
        result = fetch_latest_equity_price(symbol, api_key)
        if result.get("status") == "ok":
            prices[symbol] = result["price"]
        else:
            errors[symbol] = result.get("error") or result.get("status")

    if not prices:
        stale = get_stale_cached_json("tiingo", cache_key, STALE_FALLBACK_SECONDS)
        if stale:
            return stale
        return {
            "provider": "Tiingo",
            "configured": True,
            "status": "error",
            "prices": {},
            "errors": errors,
            "error": "No Tiingo prices returned.",
        }

    result = {
        "provider": "Tiingo",
        "configured": True,
        "status": "ok" if not errors else "partial",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "prices": prices,
        "symbol_count": len(symbols),
        "price_count": len(prices),
        "errors": errors,
        "cache": {"status": "fresh"},
    }
    set_cached_json("tiingo", cache_key, result)
    return result


def fetch_latest_equity_price(symbol, api_key):
    headers = {
        "Authorization": f"Token {api_key}",
        "Accept": "application/json",
    }

    try:
        response = requests.get(
            f"{TIINGO_INTRADAY_BASE_URL}/{symbol.lower()}",
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
    except requests.HTTPError as exc:
        return build_symbol_error(symbol, exc, response=exc.response)
    except requests.RequestException as exc:
        return build_symbol_error(symbol, exc)

    payload = response.json()
    item = normalize_payload_item(payload)
    price = extract_price(item)
    if price is None:
        return build_symbol_error(symbol, "No usable Tiingo price returned.")

    return {
        "provider": "Tiingo",
        "status": "ok",
        "symbol": symbol.upper(),
        "price": {
            "symbol": symbol.upper(),
            "provider": "Tiingo",
            "timestamp": item.get("timestamp") or item.get("date"),
            "close": price,
            "open": safe_float(item.get("open")),
            "high": safe_float(item.get("high")),
            "low": safe_float(item.get("low")),
            "volume": safe_int(item.get("volume")),
            "source_field": price_source_field(item),
        },
    }


def normalize_payload_item(payload):
    if isinstance(payload, list):
        return payload[0] if payload else {}
    if isinstance(payload, dict):
        return payload
    return {}


def extract_price(item):
    for key in ["tngoLast", "last", "close", "prevClose", "mid"]:
        value = safe_float(item.get(key))
        if value is not None:
            return value
    return None


def price_source_field(item):
    for key in ["tngoLast", "last", "close", "prevClose", "mid"]:
        if safe_float(item.get(key)) is not None:
            return key
    return None


def build_symbol_error(symbol, error, response=None):
    status_code = getattr(response, "status_code", None)
    message = str(error)
    if status_code == 401:
        message = "Tiingo rejected the API token."
    elif status_code == 429:
        message = "Tiingo rate limit reached."

    return {
        "provider": "Tiingo",
        "status": "error",
        "symbol": symbol.upper(),
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
