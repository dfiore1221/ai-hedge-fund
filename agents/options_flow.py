from datetime import datetime
from pathlib import Path

import yfinance as yf


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports" / "options"


def analyze_options_flow(ticker):
    ticker = ticker.upper().strip()

    try:
        instrument = yf.Ticker(ticker)
        expirations = instrument.options
    except Exception as exc:
        return build_error(ticker, f"Could not fetch options expirations: {exc}")

    if not expirations:
        return build_error(ticker, "No options expirations returned.")

    expiration = expirations[0]
    try:
        chain = instrument.option_chain(expiration)
    except Exception as exc:
        return build_error(ticker, f"Could not fetch options chain: {exc}")

    calls = chain.calls
    puts = chain.puts
    call_volume = int(calls["volume"].fillna(0).sum()) if "volume" in calls else 0
    put_volume = int(puts["volume"].fillna(0).sum()) if "volume" in puts else 0
    call_oi = int(calls["openInterest"].fillna(0).sum()) if "openInterest" in calls else 0
    put_oi = int(puts["openInterest"].fillna(0).sum()) if "openInterest" in puts else 0
    put_call_volume_ratio = safe_ratio(put_volume, call_volume)
    put_call_oi_ratio = safe_ratio(put_oi, call_oi)

    return {
        "agent": "Options & Flow Analyst",
        "symbol": ticker,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "expiration": expiration,
        "call_volume": call_volume,
        "put_volume": put_volume,
        "call_open_interest": call_oi,
        "put_open_interest": put_oi,
        "put_call_volume_ratio": put_call_volume_ratio,
        "put_call_open_interest_ratio": put_call_oi_ratio,
        "stance": classify_options_stance(put_call_volume_ratio),
        "confidence": 0.35,
        "warning": "Starter options snapshot. Treat as a clue, not proof; delayed/free data may be incomplete.",
    }


def build_error(ticker, error):
    return {
        "agent": "Options & Flow Analyst",
        "symbol": ticker,
        "stance": "unknown",
        "confidence": 0.0,
        "error": error,
    }


def classify_options_stance(put_call_ratio):
    if put_call_ratio is None:
        return "unknown"
    if put_call_ratio < 0.7:
        return "bullish_positioning"
    if put_call_ratio > 1.2:
        return "bearish_or_hedging_positioning"
    return "balanced"


def safe_ratio(numerator, denominator):
    if denominator == 0:
        return None
    return numerator / denominator


def format_options_report(report):
    lines = [
        "# Options & Flow Report",
        "",
        f"Symbol: {report['symbol']}",
        f"Stance: {report.get('stance')}",
        f"Confidence: {report.get('confidence')}",
    ]

    if report.get("error"):
        lines.append(f"Error: {report['error']}")
        return "\n".join(lines) + "\n"

    lines.extend([
        f"Timestamp: {report['timestamp']}",
        f"Expiration: {report['expiration']}",
        f"Call Volume: {report['call_volume']}",
        f"Put Volume: {report['put_volume']}",
        f"Put/Call Volume Ratio: {format_number(report['put_call_volume_ratio'])}",
        f"Call Open Interest: {report['call_open_interest']}",
        f"Put Open Interest: {report['put_open_interest']}",
        f"Put/Call OI Ratio: {format_number(report['put_call_open_interest_ratio'])}",
        "",
        f"Warning: {report['warning']}",
    ])
    return "\n".join(lines) + "\n"


def save_options_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{report['symbol']}_options_report.md"
    path.write_text(format_options_report(report), encoding="utf-8")
    return path


def format_number(value):
    if value is None:
        return "n/a"
    return f"{value:.2f}"
