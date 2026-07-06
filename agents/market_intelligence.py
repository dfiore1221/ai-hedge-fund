from datetime import datetime
from pathlib import Path

from data.market_data import get_macro_market_snapshot, get_sector_rotation_snapshot


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports" / "market_intelligence"


def generate_daily_market_intelligence():
    macro = get_macro_market_snapshot()
    sector_rotation = get_sector_rotation_snapshot()
    assessment = assess_market_regime(macro, sector_rotation)

    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "macro": macro,
        "sector_rotation": sector_rotation,
        "assessment": assessment,
    }


def assess_market_regime(macro, sector_rotation):
    signals = []

    sp500 = macro.get("sp500", {})
    nasdaq = macro.get("nasdaq", {})
    russell = macro.get("russell_2000", {})
    vix = macro.get("vix", {})
    ten_year = macro.get("ten_year_treasury", {})
    dxy = macro.get("dxy", {})
    gold = macro.get("gold", {})
    oil = macro.get("oil", {})
    bitcoin = macro.get("bitcoin", {})

    add_signal(signals, "S&P 500 20-day trend", sp500.get("twenty_day_change_pct"), positive_above=0)
    add_signal(signals, "Nasdaq 20-day trend", nasdaq.get("twenty_day_change_pct"), positive_above=0)
    add_signal(signals, "Russell 2000 20-day trend", russell.get("twenty_day_change_pct"), positive_above=0)
    add_inverse_signal(signals, "VIX 20-day trend", vix.get("twenty_day_change_pct"), positive_below=0)
    add_inverse_signal(signals, "10Y yield 20-day trend", ten_year.get("twenty_day_change_pct"), positive_below=0)
    add_inverse_signal(signals, "Dollar 20-day trend", dxy.get("twenty_day_change_pct"), positive_below=0)
    add_signal(signals, "Gold 20-day trend", gold.get("twenty_day_change_pct"), positive_above=0, weight=0.5)
    add_signal(signals, "Oil 20-day trend", oil.get("twenty_day_change_pct"), positive_above=0, weight=0.5)
    add_signal(signals, "Bitcoin 20-day trend", bitcoin.get("twenty_day_change_pct"), positive_above=0, weight=0.5)

    risk_on_sectors = {"Technology", "Consumer Discretionary", "Communication Services", "Industrials", "Financials"}
    defensive_sectors = {"Utilities", "Consumer Staples", "Healthcare"}
    top_sectors = [item["sector"] for item in sector_rotation.get("sectors", [])[:3]]

    sector_score = 0
    for sector in top_sectors:
        if sector in risk_on_sectors:
            sector_score += 1
        elif sector in defensive_sectors:
            sector_score -= 1

    signals.append({
        "name": "Sector leadership",
        "value": ", ".join(top_sectors),
        "score": sector_score,
        "weight": 1.5,
    })

    weighted_score = sum(signal["score"] * signal["weight"] for signal in signals)
    max_score = sum(abs(signal["weight"]) for signal in signals)
    normalized = 50 if max_score == 0 else 50 + (weighted_score / max_score) * 50
    macro_score = max(0, min(100, round(normalized, 1)))

    if macro_score >= 60:
        regime = "Risk-On"
    elif macro_score <= 40:
        regime = "Risk-Off"
    else:
        regime = "Neutral"

    confidence = round(min(100, 45 + count_available_signals(signals) * 6), 1)

    return {
        "macro_score": macro_score,
        "market_regime": regime,
        "confidence_score": confidence,
        "signals": signals,
    }


def add_signal(signals, name, value, positive_above=0, weight=1.0):
    if value is None:
        score = 0
    elif value > positive_above:
        score = 1
    elif value < positive_above:
        score = -1
    else:
        score = 0

    signals.append({
        "name": name,
        "value": value,
        "score": score,
        "weight": weight,
    })


def add_inverse_signal(signals, name, value, positive_below=0, weight=1.0):
    if value is None:
        score = 0
    elif value < positive_below:
        score = 1
    elif value > positive_below:
        score = -1
    else:
        score = 0

    signals.append({
        "name": name,
        "value": value,
        "score": score,
        "weight": weight,
    })


def count_available_signals(signals):
    return sum(1 for signal in signals if signal.get("value") is not None)


def format_market_intelligence_report(report):
    assessment = report["assessment"]
    lines = [
        "# Daily Market Intelligence",
        "",
        f"Created at: {report['created_at']}",
        f"Macro Score: {assessment['macro_score']}/100",
        f"Market Regime: {assessment['market_regime']}",
        f"Confidence Score: {assessment['confidence_score']}/100",
        "",
        "## Macro Signals",
    ]

    for signal in assessment["signals"]:
        lines.append(
            f"- {signal['name']}: {format_value(signal['value'])} "
            f"(score {signal['score']}, weight {signal['weight']})"
        )

    lines.extend([
        "",
        "## Market Snapshot",
    ])

    for name, data in report["macro"].items():
        if data.get("error"):
            lines.append(f"- {name}: {data['error']}")
            continue

        lines.append(
            f"- {name}: latest {format_value(data.get('latest'))}, "
            f"1D {format_pct(data.get('one_day_change_pct'))}, "
            f"20D {format_pct(data.get('twenty_day_change_pct'))}"
        )

    lines.extend([
        "",
        "## Sector Rotation",
    ])

    for item in report["sector_rotation"]["sectors"]:
        if item.get("error"):
            lines.append(f"- {item['sector']} ({item['ticker']}): {item['error']}")
            continue

        lines.append(
            f"- {item['sector']} ({item['ticker']}): "
            f"20D {format_pct(item.get('twenty_day_change_pct'))}, "
            f"relative to SPY {format_pct(item.get('relative_to_spy_20d'))}"
        )

    lines.extend([
        "",
        "## Required Interpretation",
        f"- Current regime is {assessment['market_regime']}.",
        "- Company research should reference this market backdrop before moving to security-level conclusions.",
    ])

    return "\n".join(lines) + "\n"


def save_market_intelligence_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / "daily_market_intelligence.md"
    path.write_text(format_market_intelligence_report(report), encoding="utf-8")
    return path


def format_value(value):
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def format_pct(value):
    if value is None:
        return "n/a"
    return f"{value:.2f}%"
