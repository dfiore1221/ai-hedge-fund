import os
import re
from pathlib import Path

import requests
from dotenv import load_dotenv

from data.local_cache import get_cached_json, set_cached_json, ttl_seconds


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
QUIVER_BASE_URL = "https://api.quiverquant.com/beta"
DEFAULT_TIMEOUT = 8
OFF_EXCHANGE_TTL_SECONDS = ttl_seconds(hours=6)


def get_quiver_api_key():
    load_dotenv(ENV_PATH)
    return os.getenv("QUIVER_API_KEY", "").strip()


def is_quiver_configured():
    return bool(get_quiver_api_key())


def fetch_quiver_off_exchange(symbol, limit=5):
    symbol = str(symbol or "").upper().strip()
    api_key = get_quiver_api_key()
    if not api_key:
        return not_configured_response(symbol)
    if not symbol:
        return build_error_response(symbol, "No symbol supplied.")

    cache_key = f"off-exchange:{symbol}:{limit}"
    cached = get_cached_json("quiver", cache_key, OFF_EXCHANGE_TTL_SECONDS)
    if cached:
        return cached

    try:
        response = requests.get(
            f"{QUIVER_BASE_URL}/historical/offexchange/{symbol}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        return build_error_response(symbol, exc, response=getattr(exc, "response", None))

    try:
        payload = response.json()
    except ValueError as exc:
        return build_error_response(symbol, f"Unexpected Quiver response format: {exc}")

    if not isinstance(payload, list):
        return build_error_response(symbol, "Unexpected Quiver response shape.")

    items = payload[:limit]
    result = {
        "provider": "Quiver Quantitative",
        "configured": True,
        "status": "ok" if items else "empty",
        "symbol": symbol,
        "dataset": "off_exchange_trading",
        "items": items,
        "item_count": len(items),
    }
    set_cached_json("quiver", cache_key, result)
    return result


def not_configured_response(symbol):
    return {
        "provider": "Quiver Quantitative",
        "configured": False,
        "status": "not_configured",
        "symbol": str(symbol or "").upper(),
        "dataset": "off_exchange_trading",
        "items": [],
        "item_count": 0,
        "error": "QUIVER_API_KEY is not configured.",
    }


def build_error_response(symbol, error, response=None):
    status_code = getattr(response, "status_code", None)
    message = sanitize_error_message(error)
    if status_code in {401, 403}:
        message = "Quiver rejected the API key or this plan is not entitled to the requested endpoint."
        status = "not_entitled"
    elif status_code == 429:
        message = "Quiver rate limit reached."
        status = "rate_limited"
    elif status_code is None and "NameResolutionError" in message:
        message = "Quiver request failed because the host could not be resolved from this environment."
        status = "error"
    else:
        status = "error"

    return {
        "provider": "Quiver Quantitative",
        "configured": True,
        "status": status,
        "symbol": str(symbol or "").upper(),
        "dataset": "off_exchange_trading",
        "items": [],
        "item_count": 0,
        "error": message,
        "status_code": status_code,
    }


def sanitize_error_message(error):
    message = str(error)
    message = re.sub(r"(Authorization['\"]?:\\s*Bearer\\s+)[A-Za-z0-9._\\-]+", r"\1[REDACTED]", message)
    message = re.sub(r"(Bearer\\s+)[A-Za-z0-9._\\-]+", r"\1[REDACTED]", message)
    return message
