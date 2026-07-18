import os
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

from data.local_cache import get_cached_json, get_stale_cached_json, set_cached_json, ttl_seconds


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_SERIES_TTL_SECONDS = ttl_seconds(hours=6)
FRED_STALE_FALLBACK_SECONDS = ttl_seconds(days=7)


FRED_SERIES = [
    {
        "id": "DGS10",
        "name": "10Y Treasury Yield",
        "category": "rates",
        "units": "lin",
        "frequency": "daily",
    },
    {
        "id": "DGS2",
        "name": "2Y Treasury Yield",
        "category": "rates",
        "units": "lin",
        "frequency": "daily",
    },
    {
        "id": "T10Y2Y",
        "name": "10Y-2Y Yield Curve",
        "category": "rates",
        "units": "lin",
        "frequency": "daily",
    },
    {
        "id": "FEDFUNDS",
        "name": "Effective Fed Funds Rate",
        "category": "rates",
        "units": "lin",
        "frequency": "monthly",
    },
    {
        "id": "SOFR",
        "name": "SOFR",
        "category": "rates",
        "units": "lin",
        "frequency": "daily",
    },
    {
        "id": "CPIAUCSL",
        "name": "CPI YoY",
        "category": "inflation",
        "units": "pc1",
        "frequency": "monthly",
    },
    {
        "id": "PCEPI",
        "name": "PCE Price Index YoY",
        "category": "inflation",
        "units": "pc1",
        "frequency": "monthly",
    },
    {
        "id": "UNRATE",
        "name": "Unemployment Rate",
        "category": "labor",
        "units": "lin",
        "frequency": "monthly",
    },
    {
        "id": "PAYEMS",
        "name": "Payrolls YoY",
        "category": "labor",
        "units": "pc1",
        "frequency": "monthly",
    },
    {
        "id": "BAMLH0A0HYM2",
        "name": "High Yield Credit Spread",
        "category": "credit",
        "units": "lin",
        "frequency": "daily",
    },
    {
        "id": "BAMLC0A0CM",
        "name": "Investment Grade Credit Spread",
        "category": "credit",
        "units": "lin",
        "frequency": "daily",
    },
    {
        "id": "GDPC1",
        "name": "Real GDP YoY",
        "category": "growth",
        "units": "pc1",
        "frequency": "quarterly",
    },
]


def get_fred_macro_snapshot():
    load_dotenv(ENV_PATH)
    api_key = os.getenv("FRED_API_KEY", "").strip()
    if not api_key:
        return {
            "provider": "FRED",
            "configured": False,
            "status": "not_configured",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "error": "FRED_API_KEY is not configured.",
            "series": [],
            "summary": {},
        }

    series_results = []
    for config in FRED_SERIES:
        series_results.append(fetch_latest_observation(api_key, config))

    return {
        "provider": "FRED",
        "configured": True,
        "status": "ok" if all("error" not in item for item in series_results) else "partial",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "series": series_results,
        "summary": build_fred_summary(series_results),
    }


def fetch_latest_observation(api_key, config):
    params = {
        "series_id": config["id"],
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 12,
        "units": config["units"],
    }
    cache_key = f"series:{config['id']}:{config['units']}"
    cached = get_cached_json("fred", cache_key, FRED_SERIES_TTL_SECONDS)
    if cached:
        return cached

    try:
        response = requests.get(FRED_OBSERVATIONS_URL, params=params, timeout=15)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        stale = get_stale_cached_json("fred", cache_key, FRED_STALE_FALLBACK_SECONDS)
        if stale:
            return stale
        return {
            **base_series(config),
            "error": f"Could not fetch FRED series: {exc}",
        }

    observations = payload.get("observations", [])
    latest = first_numeric_observation(observations)
    if latest is None:
        stale = get_stale_cached_json("fred", cache_key, FRED_STALE_FALLBACK_SECONDS)
        if stale:
            return stale
        return {
            **base_series(config),
            "error": "No numeric observations returned.",
        }

    result = {
        **base_series(config),
        "date": latest["date"],
        "value": latest["value"],
        "realtime_start": latest.get("realtime_start"),
        "realtime_end": latest.get("realtime_end"),
        "cache": {"status": "fresh"},
    }
    set_cached_json("fred", cache_key, result)
    return result


def base_series(config):
    return {
        "id": config["id"],
        "name": config["name"],
        "category": config["category"],
        "units": config["units"],
        "frequency": config["frequency"],
    }


def first_numeric_observation(observations):
    for observation in observations:
        raw_value = observation.get("value")
        if raw_value in {None, "."}:
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        return {
            "date": observation.get("date"),
            "value": value,
            "realtime_start": observation.get("realtime_start"),
            "realtime_end": observation.get("realtime_end"),
        }
    return None


def build_fred_summary(series_results):
    by_id = {
        item["id"]: item
        for item in series_results
        if "error" not in item and item.get("value") is not None
    }

    return {
        "ten_year_yield": value_for(by_id, "DGS10"),
        "two_year_yield": value_for(by_id, "DGS2"),
        "yield_curve_10y_2y": value_for(by_id, "T10Y2Y"),
        "fed_funds": value_for(by_id, "FEDFUNDS"),
        "sofr": value_for(by_id, "SOFR"),
        "cpi_yoy": value_for(by_id, "CPIAUCSL"),
        "pce_yoy": value_for(by_id, "PCEPI"),
        "unemployment_rate": value_for(by_id, "UNRATE"),
        "payrolls_yoy": value_for(by_id, "PAYEMS"),
        "high_yield_spread": value_for(by_id, "BAMLH0A0HYM2"),
        "investment_grade_spread": value_for(by_id, "BAMLC0A0CM"),
        "gdp_yoy": value_for(by_id, "GDPC1"),
    }


def value_for(series_by_id, series_id):
    item = series_by_id.get(series_id)
    if not item:
        return None
    return {
        "value": item["value"],
        "date": item["date"],
    }
