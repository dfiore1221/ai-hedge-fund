import os
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

from data.local_cache import get_cached_json, get_stale_cached_json, set_cached_json, ttl_seconds


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
BENZINGA_BASE_URL = "https://api.benzinga.com/api"
DEFAULT_TIMEOUT = 8
NEWS_TTL_SECONDS = ttl_seconds(minutes=10)
RATINGS_TTL_SECONDS = ttl_seconds(hours=6)
STALE_FALLBACK_SECONDS = ttl_seconds(days=1)


def get_benzinga_api_key():
    load_dotenv(ENV_PATH)
    return os.getenv("BENZINGA_API_KEY", "").strip()


def is_benzinga_configured():
    return bool(get_benzinga_api_key())


def fetch_benzinga_news(symbol, days_back=3, limit=20):
    api_key = get_benzinga_api_key()
    if not api_key:
        return not_configured_response("news", symbol)

    end_date = date.today()
    start_date = end_date - timedelta(days=days_back)
    params = {
        "token": api_key,
        "tickers": symbol.upper(),
        "dateFrom": start_date.isoformat(),
        "dateTo": end_date.isoformat(),
        "pageSize": limit,
        "displayOutput": "abstract",
    }
    cache_key = f"news:v2:{symbol.upper()}:{start_date.isoformat()}:{end_date.isoformat()}:{limit}"
    cached = get_cached_json("benzinga", cache_key, NEWS_TTL_SECONDS)
    if cached:
        return cached

    result = request_benzinga(
        endpoint="/v2/news",
        params=params,
        symbol=symbol,
        response_kind="news",
        cache_key=cache_key,
    )
    if result.get("status") == "empty":
        fallback_params = {
            **params,
            "company_tickers": symbol.upper(),
        }
        fallback_params.pop("tickers", None)
        result = request_benzinga(
            endpoint="/v2/news",
            params=fallback_params,
            symbol=symbol,
            response_kind="news",
            cache_key=f"{cache_key}:tickers",
        )
    return result


def fetch_benzinga_ratings(symbol, days_back=180, limit=20):
    api_key = get_benzinga_api_key()
    if not api_key:
        return not_configured_response("ratings", symbol)

    end_date = date.today()
    start_date = end_date - timedelta(days=days_back)
    params = {
        "token": api_key,
        "parameters[tickers]": symbol.upper(),
        "parameters[date_from]": start_date.isoformat(),
        "parameters[date_to]": end_date.isoformat(),
        "pagesize": limit,
    }
    cache_key = f"ratings:v2:{symbol.upper()}:{start_date.isoformat()}:{end_date.isoformat()}:{limit}"
    cached = get_cached_json("benzinga", cache_key, RATINGS_TTL_SECONDS)
    if cached:
        return cached

    return request_benzinga(
        endpoint="/v2.1/calendar/ratings",
        params=params,
        symbol=symbol,
        response_kind="ratings",
        cache_key=cache_key,
    )


def request_benzinga(endpoint, params, symbol, response_kind, cache_key):
    try:
        response = requests.get(
            f"{BENZINGA_BASE_URL}{endpoint}",
            params=params,
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
    except requests.HTTPError as exc:
        stale = get_stale_cached_json("benzinga", cache_key, STALE_FALLBACK_SECONDS)
        return stale or build_error_response(response_kind, symbol, exc, response=exc.response)
    except requests.RequestException as exc:
        stale = get_stale_cached_json("benzinga", cache_key, STALE_FALLBACK_SECONDS)
        return stale or build_error_response(response_kind, symbol, exc)

    payload = parse_response_payload(response)
    if payload is None:
        return build_error_response(response_kind, symbol, "Unexpected Benzinga response format.")

    items = extract_items(payload)
    if items is None:
        return build_error_response(response_kind, symbol, "Unexpected Benzinga response shape.")

    result = {
        "provider": "Benzinga",
        "configured": True,
        "status": "ok" if items else "empty",
        "symbol": symbol.upper(),
        "items": items,
        "item_count": len(items),
        "response_kind": response_kind,
        "cache": {"status": "fresh"},
    }
    set_cached_json("benzinga", cache_key, result)
    return result


def extract_items(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return None
    for key in ("data", "news", "ratings", "items", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return None


def parse_response_payload(response):
    text = response.text or ""
    content_type = response.headers.get("content-type", "")
    if "json" in content_type.lower():
        try:
            return response.json()
        except ValueError:
            return None

    if text.lstrip().startswith("<?xml") or "xml" in content_type.lower():
        return parse_xml_payload(text)

    try:
        return response.json()
    except ValueError:
        return None


def parse_xml_payload(text):
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None

    items = [xml_item_to_dict(item) for item in root.findall(".//item")]
    return {"data": items}


def xml_item_to_dict(item):
    row = {}
    for child in item:
        if list(child):
            row[child.tag] = [xml_item_to_dict(grandchild) for grandchild in child]
        else:
            row[child.tag] = child.text
    return row


def not_configured_response(response_kind, symbol):
    return {
        "provider": "Benzinga",
        "configured": False,
        "status": "not_configured",
        "symbol": symbol.upper(),
        "items": [],
        "response_kind": response_kind,
        "error": "BENZINGA_API_KEY is not configured.",
    }


def build_error_response(response_kind, symbol, error, response=None):
    status_code = getattr(response, "status_code", None)
    message = sanitize_error_message(error)
    if status_code in {401, 403}:
        message = "Benzinga rejected the API key or this plan is not entitled to the requested endpoint."
    elif status_code == 429:
        message = "Benzinga rate limit reached."
    elif status_code is None and "NameResolutionError" in message:
        message = "Benzinga request failed because the host could not be resolved from this environment."

    return {
        "provider": "Benzinga",
        "configured": True,
        "status": "error",
        "symbol": symbol.upper(),
        "items": [],
        "response_kind": response_kind,
        "error": message,
        "status_code": status_code,
    }


def sanitize_error_message(error):
    message = str(error)
    message = re.sub(r"([?&]token=)[^&\\s')]+", r"\1[REDACTED]", message)
    message = re.sub(r"([?&]apikey=)[^&\\s')]+", r"\1[REDACTED]", message, flags=re.IGNORECASE)
    message = re.sub(r"bz\\.[A-Za-z0-9]+", "[REDACTED]", message)
    return message


def parse_benzinga_datetime(value):
    if value in {None, ""}:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value)
        except (ValueError, OSError):
            return None
    if not isinstance(value, str):
        return None

    clean = value.strip()
    for parser in (
        lambda raw: datetime.fromisoformat(raw.replace("Z", "+00:00")),
        parsedate_to_datetime,
    ):
        try:
            return parser(clean)
        except (TypeError, ValueError, IndexError):
            continue
    return None
