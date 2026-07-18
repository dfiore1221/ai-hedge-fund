import os
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

from data.local_cache import get_cached_json, get_stale_cached_json, set_cached_json, ttl_seconds


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
FINNHUB_BASE_URL = "https://finnhub.io/api/v1"
DEFAULT_TIMEOUT = 15
COMPANY_NEWS_TTL_SECONDS = ttl_seconds(minutes=20)
RECOMMENDATION_TTL_SECONDS = ttl_seconds(hours=12)
STALE_FALLBACK_SECONDS = ttl_seconds(days=1)


def get_finnhub_api_key():
    load_dotenv(ENV_PATH)
    return os.getenv("FINNHUB_API_KEY", "").strip()


def is_finnhub_configured():
    return bool(get_finnhub_api_key())


def fetch_company_news(symbol, days_back=3, limit=20):
    api_key = get_finnhub_api_key()
    if not api_key:
        return {
            "provider": "Finnhub",
            "configured": False,
            "status": "not_configured",
            "symbol": symbol,
            "items": [],
            "error": "FINNHUB_API_KEY is not configured.",
        }

    end_date = date.today()
    start_date = end_date - timedelta(days=days_back)
    params = {
        "symbol": symbol.upper(),
        "from": start_date.isoformat(),
        "to": end_date.isoformat(),
        "token": api_key,
    }
    cache_key = f"company-news:{symbol.upper()}:{start_date.isoformat()}:{end_date.isoformat()}:{limit}"
    cached = get_cached_json("finnhub", cache_key, COMPANY_NEWS_TTL_SECONDS)
    if cached:
        return cached

    try:
        response = requests.get(
            f"{FINNHUB_BASE_URL}/company-news",
            params=params,
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
    except requests.HTTPError as exc:
        stale = get_stale_cached_json("finnhub", cache_key, STALE_FALLBACK_SECONDS)
        return stale or build_error_response(symbol, exc, response=exc.response)
    except requests.RequestException as exc:
        stale = get_stale_cached_json("finnhub", cache_key, STALE_FALLBACK_SECONDS)
        return stale or build_error_response(symbol, exc)

    data = response.json()
    if not isinstance(data, list):
        return build_error_response(symbol, "Unexpected Finnhub company-news response.")

    result = {
        "provider": "Finnhub",
        "configured": True,
        "status": "ok" if data else "empty",
        "symbol": symbol.upper(),
        "from": start_date.isoformat(),
        "to": end_date.isoformat(),
        "items": data[:limit],
        "item_count": len(data),
        "cache": {"status": "fresh"},
    }
    set_cached_json("finnhub", cache_key, result)
    return result


def fetch_recommendation_trends(symbol, limit=4):
    api_key = get_finnhub_api_key()
    if not api_key:
        return {
            "provider": "Finnhub",
            "configured": False,
            "status": "not_configured",
            "symbol": symbol,
            "items": [],
            "error": "FINNHUB_API_KEY is not configured.",
        }

    params = {
        "symbol": symbol.upper(),
        "token": api_key,
    }
    cache_key = f"recommendation-trends:{symbol.upper()}:{limit}"
    cached = get_cached_json("finnhub", cache_key, RECOMMENDATION_TTL_SECONDS)
    if cached:
        return cached

    try:
        response = requests.get(
            f"{FINNHUB_BASE_URL}/stock/recommendation",
            params=params,
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
    except requests.HTTPError as exc:
        stale = get_stale_cached_json("finnhub", cache_key, STALE_FALLBACK_SECONDS)
        return stale or build_error_response(symbol, exc, response=exc.response)
    except requests.RequestException as exc:
        stale = get_stale_cached_json("finnhub", cache_key, STALE_FALLBACK_SECONDS)
        return stale or build_error_response(symbol, exc)

    data = response.json()
    if not isinstance(data, list):
        return build_error_response(symbol, "Unexpected Finnhub recommendation response.")

    result = {
        "provider": "Finnhub",
        "configured": True,
        "status": "ok" if data else "empty",
        "symbol": symbol.upper(),
        "items": data[:limit],
        "item_count": len(data),
        "cache": {"status": "fresh"},
    }
    set_cached_json("finnhub", cache_key, result)
    return result


def build_error_response(symbol, error, response=None):
    status_code = getattr(response, "status_code", None)
    message = str(error)
    if status_code == 401:
        message = "Finnhub rejected the API key."
    elif status_code == 429:
        message = "Finnhub rate limit reached."

    return {
        "provider": "Finnhub",
        "configured": True,
        "status": "error",
        "symbol": symbol.upper(),
        "items": [],
        "error": message,
        "status_code": status_code,
    }


def unix_to_iso(timestamp):
    if timestamp in {None, ""}:
        return None
    try:
        return datetime.fromtimestamp(int(timestamp)).isoformat()
    except (TypeError, ValueError, OSError):
        return None
