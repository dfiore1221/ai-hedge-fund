from datetime import datetime
from pathlib import Path

from data.quiver_data import fetch_quiver_off_exchange, is_quiver_configured


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports" / "alternative_data"


def analyze_alternative_data(symbol):
    symbol = symbol.upper().strip()
    quiver = fetch_quiver_off_exchange(symbol, limit=10) if symbol else {
        "status": "error",
        "error": "No symbol supplied.",
        "items": [],
    }
    interpretation = interpret_quiver_off_exchange(quiver)

    return {
        "agent": "Alternative Data",
        "system_role": "shared_data_layer",
        "layer": "Alternative / Positioning Evidence",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "symbol": symbol,
        "provider": "Quiver Quantitative",
        "configured": is_quiver_configured(),
        "status": quiver.get("status"),
        "stance": interpretation["stance"],
        "confidence": interpretation["confidence"],
        "summary": interpretation["summary"],
        "signals": interpretation["signals"],
        "quiver": quiver,
        "missing_information": build_missing_information(quiver),
        "use_policy": [
            "Alternative data is context, not a standalone trade trigger.",
            "Dark-pool/off-exchange data can show attention or activity, but not intent by itself.",
            "If Quiver entitlement is missing, the Committee must say crowding/ownership data is unavailable.",
        ],
    }


def interpret_quiver_off_exchange(quiver):
    status = quiver.get("status")
    if status == "not_configured":
        return unavailable_interpretation("Quiver is not configured.")
    if status == "not_entitled":
        return unavailable_interpretation(
            "Quiver key is present, but this plan is not entitled to the checked dataset."
        )
    if status in {"error", "rate_limited"}:
        return unavailable_interpretation(quiver.get("error") or "Quiver alternative data is unavailable.")
    if status == "empty":
        return {
            "stance": "no_clear_signal",
            "confidence": 0.3,
            "summary": "Quiver returned no off-exchange records for this symbol.",
            "signals": [],
        }

    items = quiver.get("items") or []
    signals = [normalize_off_exchange_item(item) for item in items]
    latest = next((item for item in signals if item.get("dpi") is not None), None)
    if not latest:
        return {
            "stance": "activity_seen_uninterpreted",
            "confidence": 0.35,
            "summary": f"Quiver returned {len(items)} off-exchange records, but DPI fields were unavailable.",
            "signals": signals,
        }

    dpi = latest["dpi"]
    if dpi >= 0.55:
        stance = "elevated_off_exchange_short_pressure"
        summary = (
            f"Latest Quiver off-exchange DPI is {dpi:.2f}, suggesting elevated short-side activity "
            "that should make bullish setups require stronger confirmation."
        )
    elif dpi <= 0.35:
        stance = "lower_off_exchange_short_pressure"
        summary = (
            f"Latest Quiver off-exchange DPI is {dpi:.2f}, suggesting off-exchange short pressure is not elevated."
        )
    else:
        stance = "mixed_or_normal_off_exchange_activity"
        summary = f"Latest Quiver off-exchange DPI is {dpi:.2f}, which is not an extreme signal."

    return {
        "stance": stance,
        "confidence": 0.45,
        "summary": summary,
        "signals": signals,
    }


def unavailable_interpretation(summary):
    return {
        "stance": "unavailable",
        "confidence": 0,
        "summary": summary,
        "signals": [],
    }


def normalize_off_exchange_item(item):
    dpi = to_float(item.get("DPI") or item.get("dpi"))
    otc_short = to_float(item.get("OTC_Short") or item.get("otc_short"))
    otc_total = to_float(item.get("OTC_Total") or item.get("otc_total"))
    return {
        "date": item.get("Date") or item.get("date"),
        "dpi": dpi,
        "otc_short": otc_short,
        "otc_total": otc_total,
    }


def build_missing_information(quiver):
    status = quiver.get("status")
    if status == "ok":
        return [
            "Quiver free/Hobbyist alternative data does not include Trader-tier insider transactions, hedge-fund activity, top shareholders, or ETF holdings.",
        ]
    if status == "not_configured":
        return ["Quiver alternative-data API key is not configured."]
    if status == "not_entitled":
        return [
            "Quiver key is configured, but the current plan is not entitled to the checked off-exchange API endpoint.",
            "Trader-tier Quiver ownership/crowding datasets are not connected.",
        ]
    return [
        "Quiver alternative-data check is unavailable or incomplete.",
        "Trader-tier Quiver ownership/crowding datasets are not connected.",
    ]


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_alternative_data_report(report):
    lines = [
        "# Alternative Data Report",
        "",
        f"Symbol: {report['symbol']}",
        f"Provider: {report['provider']}",
        f"Status: {report['status']}",
        f"Stance: {report['stance']}",
        f"Confidence: {report['confidence']}",
        "",
        "## Summary",
        f"- {report['summary']}",
        "",
        "## Quiver Signals",
    ]
    lines.extend([
        f"- {item.get('date')}: DPI {format_number(item.get('dpi'))}, "
        f"OTC short {format_number(item.get('otc_short'))}, OTC total {format_number(item.get('otc_total'))}"
        for item in report.get("signals", [])[:5]
    ] or ["- None."])
    lines.extend(["", "## Use Policy"])
    lines.extend([f"- {item}" for item in report.get("use_policy", [])])
    lines.extend(["", "## Missing Information"])
    lines.extend([f"- {item}" for item in report.get("missing_information", [])] or ["- None."])
    return "\n".join(lines) + "\n"


def save_alternative_data_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{report['symbol']}_alternative_data.md"
    path.write_text(format_alternative_data_report(report), encoding="utf-8")
    return path


def format_number(value):
    if value is None:
        return "n/a"
    return f"{value:.2f}"
