import os
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

from data.local_cache import (
    get_cached_json,
    get_latest_stale_cached_json_by_prefix,
    get_stale_cached_json,
    set_cached_json,
    ttl_seconds,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
TIINGO_INTRADAY_BASE_URL = "https://api.tiingo.com/tiingo/equity/intraday"
TIINGO_DAILY_BASE_URL = "https://api.tiingo.com/tiingo/daily"
DEFAULT_TIMEOUT = 15
LATEST_PRICE_TTL_SECONDS = ttl_seconds(minutes=15)
DAILY_PRICE_TTL_SECONDS = ttl_seconds(hours=6)
STALE_FALLBACK_SECONDS = ttl_seconds(hours=8)
DAILY_STALE_FALLBACK_SECONDS = ttl_seconds(days=3)
_DAILY_RATE_LIMITED_UNTIL = None


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
            fallback = latest_price_from_daily(symbol)
            if fallback:
                prices[symbol] = fallback
                errors[symbol] = f"{errors[symbol]}; using daily fallback"

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
        "cache": {"status": "fresh", "ttl_minutes": 15},
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
            "freshness": "intraday_or_latest",
        },
    }


def latest_price_from_daily(symbol):
    daily = fetch_daily_equity_prices(symbol, period="10d")
    rows = daily.get("rows") or []
    if daily.get("status") not in {"ok", "empty"} or not rows:
        return None

    row = rows[-1]
    close = safe_float(row.get("close"))
    if close is None:
        return None

    return {
        "symbol": symbol.upper(),
        "provider": "Tiingo",
        "timestamp": row.get("date"),
        "close": close,
        "open": safe_float(row.get("open")),
        "high": safe_float(row.get("high")),
        "low": safe_float(row.get("low")),
        "volume": safe_int(row.get("volume")),
        "source_field": "daily_close",
        "freshness": "daily_fallback",
        "fallback_reason": "latest quote unavailable or rate-limited",
        "daily_cache": daily.get("cache"),
    }


def fetch_daily_equity_prices(symbol, period="6mo"):
    global _DAILY_RATE_LIMITED_UNTIL
    symbol = symbol.upper().strip()
    api_key = get_tiingo_api_key()
    if not symbol:
        return {
            "provider": "Tiingo",
            "configured": is_tiingo_configured(),
            "status": "skipped",
            "symbol": symbol,
            "rows": [],
            "error": "No symbol supplied.",
        }
    if not api_key:
        return {
            "provider": "Tiingo",
            "configured": False,
            "status": "not_configured",
            "symbol": symbol,
            "rows": [],
            "error": "TIINGO_API_KEY is not configured.",
        }

    start_date = period_start_date(period)
    cache_key = f"daily-equity-prices:{symbol}:{period}:{start_date}"
    fallback_key_prefix = f"daily-equity-prices:{symbol}:{period}:"
    symbol_fallback_key_prefix = f"daily-equity-prices:{symbol}:"
    cached = get_cached_json("tiingo", cache_key, DAILY_PRICE_TTL_SECONDS)
    if cached:
        return cached

    if _DAILY_RATE_LIMITED_UNTIL and datetime.now() < _DAILY_RATE_LIMITED_UNTIL:
        stale = get_stale_daily_cache(cache_key, fallback_key_prefix, symbol_fallback_key_prefix)
        return stale or build_daily_error(symbol, "Tiingo daily rate limit is active; skipped live request.", status_code=429)

    headers = {
        "Authorization": f"Token {api_key}",
        "Accept": "application/json",
    }
    params = {
        "startDate": start_date,
        "resampleFreq": "daily",
    }

    try:
        response = requests.get(
            f"{TIINGO_DAILY_BASE_URL}/{symbol.lower()}/prices",
            headers=headers,
            params=params,
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
    except requests.HTTPError as exc:
        if getattr(exc.response, "status_code", None) == 429:
            _DAILY_RATE_LIMITED_UNTIL = datetime.now() + timedelta(minutes=10)
        stale = get_stale_daily_cache(cache_key, fallback_key_prefix, symbol_fallback_key_prefix)
        return stale or build_daily_error(symbol, exc, response=exc.response)
    except requests.RequestException as exc:
        stale = get_stale_daily_cache(cache_key, fallback_key_prefix, symbol_fallback_key_prefix)
        return stale or build_daily_error(symbol, exc)

    payload = response.json()
    if not isinstance(payload, list):
        return build_daily_error(symbol, "Unexpected Tiingo daily price response.")

    rows = [normalize_daily_row(item) for item in payload]
    rows = [row for row in rows if row and row.get("close") is not None]
    result = {
        "provider": "Tiingo",
        "configured": True,
        "status": "ok" if rows else "empty",
        "symbol": symbol,
        "period": period,
        "start_date": start_date,
        "rows": rows,
        "row_count": len(rows),
        "cache": {"status": "fresh"},
    }
    set_cached_json("tiingo", cache_key, result)
    return result


def get_stale_daily_cache(cache_key, fallback_key_prefix, symbol_fallback_key_prefix=None):
    return (
        get_stale_cached_json("tiingo", cache_key, DAILY_STALE_FALLBACK_SECONDS)
        or get_latest_stale_cached_json_by_prefix("tiingo", fallback_key_prefix, DAILY_STALE_FALLBACK_SECONDS)
        or (
            get_latest_stale_cached_json_by_prefix("tiingo", symbol_fallback_key_prefix, DAILY_STALE_FALLBACK_SECONDS)
            if symbol_fallback_key_prefix
            else None
        )
    )


def normalize_daily_row(item):
    date_value = item.get("date")
    close = safe_float(item.get("adjClose", item.get("close")))
    if not date_value or close is None:
        return None
    return {
        "date": str(date_value)[:10],
        "open": safe_float(item.get("adjOpen", item.get("open"))) or close,
        "high": safe_float(item.get("adjHigh", item.get("high"))) or close,
        "low": safe_float(item.get("adjLow", item.get("low"))) or close,
        "close": close,
        "volume": safe_int(item.get("adjVolume", item.get("volume"))),
    }


def period_start_date(period):
    today = date.today()
    mapping = {
        "10d": timedelta(days=20),
        "1mo": timedelta(days=45),
        "3mo": timedelta(days=120),
        "6mo": timedelta(days=220),
        "1y": timedelta(days=380),
        "2y": timedelta(days=760),
        "5y": timedelta(days=1900),
    }
    delta = mapping.get(str(period).lower(), timedelta(days=220))
    return (today - delta).isoformat()


def build_daily_error(symbol, error, response=None, status_code=None):
    status_code = status_code if status_code is not None else getattr(response, "status_code", None)
    message = str(error)
    if status_code == 401:
        message = "Tiingo rejected the API token."
    elif status_code == 404:
        message = "Tiingo returned no daily price endpoint for this symbol."
    elif status_code == 429:
        message = "Tiingo rate limit reached."

    return {
        "provider": "Tiingo",
        "configured": True,
        "status": "error",
        "symbol": symbol.upper(),
        "rows": [],
        "error": message,
        "status_code": status_code,
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
