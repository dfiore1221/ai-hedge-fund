import os
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import load_dotenv

from data.local_cache import get_cached_json, get_stale_cached_json, set_cached_json, ttl_seconds


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
TRADING_ECONOMICS_CALENDAR_URL = "https://api.tradingeconomics.com/calendar/country"
FRED_RELEASE_DATES_URL = "https://api.stlouisfed.org/fred/releases/dates"
ECONOMIC_CALENDAR_TTL_SECONDS = ttl_seconds(hours=2)
ECONOMIC_CALENDAR_STALE_SECONDS = ttl_seconds(days=2)
DEFAULT_COUNTRIES = ["united states"]
MARKET_MOVING_KEYWORDS = [
    "adp employment",
    "beige book",
    "capacity utilization",
    "cpi",
    "core cpi",
    "core pce",
    "crude oil inventories",
    "durable goods",
    "employment situation",
    "fed interest rate",
    "federal reserve",
    "fomc",
    "gdp",
    "gross domestic product",
    "initial jobless claims",
    "industrial production",
    "inflation",
    "ism",
    "jobless claims",
    "labor turnover",
    "new residential construction",
    "non farm",
    "non-farm",
    "payroll",
    "personal income",
    "personal income and outlays",
    "pce",
    "pmi",
    "ppi",
    "producer price",
    "retail sales",
    "treasury auction",
    "unemployment insurance",
    "unemployment rate",
]
FRED_EXCLUDED_RELEASE_KEYWORDS = [
    "daily treasury",
    "fomc press release",
    "gdpnow",
    "national accounts",
    "state retail sales",
    "state unemployment insurance",
    "treasury inflation-indexed",
]


def get_economic_calendar(days_ahead=7, days_back=0, countries=None):
    load_dotenv(ENV_PATH)
    trading_economics_key = os.getenv("TRADING_ECONOMICS_API_KEY", "").strip()
    fred_api_key = os.getenv("FRED_API_KEY", "").strip()
    countries = countries or DEFAULT_COUNTRIES
    start_date = date.today() - timedelta(days=days_back)
    end_date = date.today() + timedelta(days=days_ahead)

    if trading_economics_key:
        trading_economics_response = get_trading_economics_calendar(
            trading_economics_key,
            countries,
            start_date,
            end_date,
        )
        if trading_economics_response.get("status") in {"ok", "partial"}:
            return trading_economics_response
        if fred_api_key:
            fred_response = get_fred_release_calendar(fred_api_key, start_date, end_date)
            fred_response["fallback_from"] = "Trading Economics"
            fred_response["fallback_reason"] = trading_economics_response.get("error")
            return fred_response
        return trading_economics_response

    if fred_api_key:
        return get_fred_release_calendar(fred_api_key, start_date, end_date)

    return build_calendar_response(
        provider="Economic Calendar",
        configured=False,
        status="not_configured",
        error="Neither TRADING_ECONOMICS_API_KEY nor FRED_API_KEY is configured.",
        events=[],
        countries=countries,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
    )


def get_trading_economics_calendar(api_key, countries, start_date, end_date):
    try:
        payload = fetch_calendar_events(api_key, countries, start_date, end_date)
    except Exception as exc:
        return build_calendar_response(
            provider="Trading Economics",
            configured=True,
            status="error",
            error=f"Could not fetch Trading Economics calendar: {exc}",
            events=[],
            countries=countries,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        )

    if not isinstance(payload, list):
        return build_calendar_response(
            provider="Trading Economics",
            configured=True,
            status="error",
            error="Trading Economics calendar returned an unexpected payload.",
            events=[],
            countries=countries,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        )

    events = sorted(
        [normalize_trading_economics_event(item) for item in payload],
        key=lambda item: item.get("datetime") or "",
    )

    return build_calendar_response(
        provider="Trading Economics",
        configured=True,
        status="ok",
        error=None,
        events=events,
        countries=countries,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
    )


def fetch_calendar_events(api_key, countries, start_date, end_date):
    country_path = quote(",".join(countries), safe=",")
    url = (
        f"{TRADING_ECONOMICS_CALENDAR_URL}/{country_path}/"
        f"{start_date.isoformat()}/{end_date.isoformat()}"
    )
    cache_key = f"trading-economics:{country_path}:{start_date.isoformat()}:{end_date.isoformat()}"
    cached = get_cached_json("economic_calendar", cache_key, ECONOMIC_CALENDAR_TTL_SECONDS)
    if cached:
        return cached

    response = requests.get(url, params={"c": api_key, "f": "json"}, timeout=20)
    response.raise_for_status()
    payload = response.json()
    set_cached_json("economic_calendar", cache_key, payload)
    return payload


def get_fred_release_calendar(api_key, start_date, end_date):
    try:
        payload = fetch_fred_release_dates(api_key, start_date, end_date)
    except Exception as exc:
        return build_calendar_response(
            provider="FRED Release Calendar",
            configured=True,
            status="error",
            error=f"Could not fetch FRED release calendar: {exc}",
            events=[],
            countries=["united states"],
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        )

    release_dates = payload.get("release_dates", [])
    if not isinstance(release_dates, list):
        return build_calendar_response(
            provider="FRED Release Calendar",
            configured=True,
            status="error",
            error="FRED release calendar returned an unexpected payload.",
            events=[],
            countries=["united states"],
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        )

    events = sorted(
        [normalize_fred_release_event(item) for item in release_dates],
        key=lambda item: item.get("datetime") or "",
    )

    return build_calendar_response(
        provider="FRED Release Calendar",
        configured=True,
        status="ok",
        error=None,
        events=events,
        countries=["united states"],
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
    )


def fetch_fred_release_dates(api_key, start_date, end_date):
    params = {
        "api_key": api_key,
        "file_type": "json",
        "realtime_start": start_date.isoformat(),
        "realtime_end": end_date.isoformat(),
        "sort_order": "asc",
        "limit": 1000,
        "include_release_dates_with_no_data": "true",
    }
    cache_key = f"fred-release-dates:{start_date.isoformat()}:{end_date.isoformat()}"
    cached = get_cached_json("economic_calendar", cache_key, ECONOMIC_CALENDAR_TTL_SECONDS)
    if cached:
        return cached

    try:
        response = requests.get(FRED_RELEASE_DATES_URL, params=params, timeout=20)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        stale = get_stale_cached_json(
            "economic_calendar",
            cache_key,
            ECONOMIC_CALENDAR_STALE_SECONDS,
        )
        if stale:
            return stale
        raise

    set_cached_json("economic_calendar", cache_key, payload)
    return payload


def normalize_trading_economics_event(item):
    importance = parse_int(item.get("Importance"))
    event_name = item.get("Event") or ""
    category = item.get("Category") or ""
    raw_datetime = item.get("Date")

    return {
        "calendar_id": item.get("CalendarId") or item.get("CalendarID"),
        "datetime": raw_datetime,
        "date": event_date(raw_datetime),
        "country": item.get("Country"),
        "category": category,
        "event": event_name,
        "reference": item.get("Reference"),
        "source": item.get("Source"),
        "source_url": item.get("SourceURL"),
        "actual": empty_to_none(item.get("Actual")),
        "previous": empty_to_none(item.get("Previous")),
        "forecast": empty_to_none(item.get("Forecast")),
        "te_forecast": empty_to_none(item.get("TEForecast")),
        "importance": importance,
        "is_high_importance": is_market_moving_event(event_name, category, importance),
        "url": item.get("URL"),
        "last_update": item.get("LastUpdate"),
        "ticker": item.get("Ticker") or item.get("Symbol"),
    }


def normalize_fred_release_event(item):
    release_name = item.get("release_name") or ""
    event_name = release_name or f"FRED release {item.get('release_id')}"
    event_is_high_importance = is_fred_market_moving_event(event_name)

    return {
        "calendar_id": item.get("release_id"),
        "datetime": item.get("date"),
        "date": item.get("date"),
        "country": "United States",
        "category": "economic_release",
        "event": event_name,
        "reference": None,
        "source": "FRED",
        "source_url": f"https://fred.stlouisfed.org/release?rid={item.get('release_id')}" if item.get("release_id") else None,
        "actual": None,
        "previous": None,
        "forecast": None,
        "te_forecast": None,
        "importance": 3 if event_is_high_importance else 1,
        "is_high_importance": event_is_high_importance,
        "url": f"https://fred.stlouisfed.org/release?rid={item.get('release_id')}" if item.get("release_id") else None,
        "last_update": item.get("release_last_updated"),
        "ticker": None,
    }


def build_calendar_response(provider, configured, status, error, events, countries, start_date, end_date):
    high_importance_events = [item for item in events if item.get("is_high_importance")]
    future_high_importance_events = [
        item for item in high_importance_events
        if item.get("date") and item["date"] >= date.today().isoformat()
    ]

    return {
        "provider": provider,
        "configured": configured,
        "status": status,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "countries": countries,
        "start_date": start_date,
        "end_date": end_date,
        "error": error,
        "events": events,
        "high_importance_events": high_importance_events,
        "summary": {
            "event_count": len(events),
            "high_importance_count": len(high_importance_events),
            "next_high_importance_event": (
                future_high_importance_events[0] if future_high_importance_events else None
            ),
            "events_today": [
                item for item in events
                if item.get("date") == date.today().isoformat()
            ],
            "high_importance_events_today": [
                item for item in high_importance_events
                if item.get("date") == date.today().isoformat()
            ],
        },
    }


def is_market_moving_event(event_name, category, importance):
    if importance is not None and importance >= 3:
        return True

    text = f"{event_name} {category}".lower()
    return any(keyword in text for keyword in MARKET_MOVING_KEYWORDS)


def is_fred_market_moving_event(event_name):
    text = event_name.lower()
    if any(keyword in text for keyword in FRED_EXCLUDED_RELEASE_KEYWORDS):
        return False
    return any(keyword in text for keyword in MARKET_MOVING_KEYWORDS)


def event_date(raw_datetime):
    parsed = parse_datetime(raw_datetime)
    if not parsed:
        return None
    return parsed.date().isoformat()


def parse_datetime(raw_datetime):
    if not raw_datetime:
        return None
    try:
        return datetime.fromisoformat(str(raw_datetime).replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_int(value):
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def empty_to_none(value):
    if value == "":
        return None
    return value


def format_calendar_event(event):
    if not event:
        return "n/a"

    pieces = [
        event.get("date") or "date n/a",
        event.get("country") or "country n/a",
        event.get("event") or event.get("category") or "event n/a",
    ]
    if event.get("importance") is not None:
        pieces.append(f"importance {event['importance']}")
    if event.get("forecast"):
        pieces.append(f"forecast {event['forecast']}")
    return " - ".join(pieces)
