from datetime import date, datetime

import yfinance as yf


def get_earnings_calendar(ticker):
    ticker = ticker.upper().strip()

    try:
        calendar = yf.Ticker(ticker).calendar
    except Exception as exc:
        return {
            "ticker": ticker,
            "error": f"Could not fetch earnings calendar: {exc}",
        }

    earnings_date = extract_earnings_date(calendar)
    if earnings_date is None:
        return {
            "ticker": ticker,
            "earnings_date": None,
            "days_until_earnings": None,
            "warning": "No earnings date returned by provider.",
        }

    return {
        "ticker": ticker,
        "earnings_date": earnings_date.isoformat(),
        "days_until_earnings": (earnings_date - date.today()).days,
    }


def extract_earnings_date(calendar):
    if calendar is None:
        return None

    candidates = []

    if isinstance(calendar, dict):
        candidates.extend(calendar.get("Earnings Date", []) or [])
        if calendar.get("Earnings Date"):
            candidates.append(calendar["Earnings Date"])

    try:
        if "Earnings Date" in calendar.index:
            value = calendar.loc["Earnings Date"][0]
            candidates.append(value)
    except Exception:
        pass

    for candidate in candidates:
        parsed = parse_date(candidate)
        if parsed:
            return parsed

    return None


def parse_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    try:
        return datetime.fromisoformat(str(value).split(" ")[0]).date()
    except Exception:
        return None


def format_earnings_calendar(data):
    lines = [
        "# Earnings Calendar",
        "",
        f"Ticker: {data['ticker']}",
    ]

    if data.get("error"):
        lines.append(f"Error: {data['error']}")
        return "\n".join(lines) + "\n"

    lines.extend([
        f"Earnings Date: {data.get('earnings_date') or 'n/a'}",
        f"Days Until Earnings: {data.get('days_until_earnings') if data.get('days_until_earnings') is not None else 'n/a'}",
    ])

    if data.get("warning"):
        lines.append(f"Warning: {data['warning']}")

    return "\n".join(lines) + "\n"
