import os
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
TRADING_ECONOMICS_CALENDAR_URL = "https://api.tradingeconomics.com/calendar/country"
DEFAULT_COUNTRIES = ["united states"]
MARKET_MOVING_KEYWORDS = [
    "adp employment",
    "cpi",
    "core cpi",
    "core pce",
    "crude oil inventories",
    "fed interest rate",
    "federal reserve",
    "fomc",
    "gdp",
    "initial jobless claims",
    "ism",
    "jobless claims",
    "non farm",
    "non-farm",
    "payroll",
    "pce",
    "pmi",
    "retail sales",
    "treasury auction",
    "unemployment rate",
]


def get_economic_calendar(days_ahead=7, days_back=0, countries=None):
    load_dotenv(ENV_PATH)
    api_key = os.getenv("TRADING_ECONOMICS_API_KEY", "").strip()
    countries = countries or DEFAULT_COUNTRIES

    if not api_key:
        return build_calendar_response(
            configured=False,
            status="not_configured",
            error="TRADING_ECONOMICS_API_KEY is not configured.",
            events=[],
            countries=countries,
            start_date=(date.today() - timedelta(days=days_back)).isoformat(),
            end_date=(date.today() + timedelta(days=days_ahead)).isoformat(),
        )

    start_date = date.today() - timedelta(days=days_back)
    end_date = date.today() + timedelta(days=days_ahead)

    try:
        payload = fetch_calendar_events(api_key, countries, start_date, end_date)
    except Exception as exc:
        return build_calendar_response(
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
            configured=True,
            status="error",
            error="Trading Economics calendar returned an unexpected payload.",
            events=[],
            countries=countries,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        )

    events = sorted(
        [normalize_event(item) for item in payload],
        key=lambda item: item.get("datetime") or "",
    )

    return build_calendar_response(
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
    response = requests.get(url, params={"c": api_key, "f": "json"}, timeout=20)
    response.raise_for_status()
    return response.json()


def normalize_event(item):
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


def build_calendar_response(configured, status, error, events, countries, start_date, end_date):
    high_importance_events = [item for item in events if item.get("is_high_importance")]
    future_high_importance_events = [
        item for item in high_importance_events
        if item.get("date") and item["date"] >= date.today().isoformat()
    ]

    return {
        "provider": "Trading Economics",
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
