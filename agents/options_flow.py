from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports" / "options"
MAX_EXPIRATIONS = 4
MIN_LIQUID_VOLUME = 50
MIN_LIQUID_OPEN_INTEREST = 100
MAX_LIQUID_SPREAD_PCT = 0.25
ATM_MONEYNESS_BAND = 0.05


def analyze_options_flow(ticker, max_expirations=MAX_EXPIRATIONS):
    ticker = ticker.upper().strip()

    try:
        instrument = yf.Ticker(ticker)
        expirations = list(instrument.options or [])
    except Exception as exc:
        return build_error(ticker, f"Could not fetch options expirations: {exc}")

    if not expirations:
        return build_error(ticker, "No options expirations returned.")

    underlying_price = get_underlying_price(instrument)
    expiration_reports = []

    for expiration in expirations[:max_expirations]:
        try:
            chain = instrument.option_chain(expiration)
        except Exception as exc:
            expiration_reports.append({
                "expiration": expiration,
                "error": f"Could not fetch option chain: {exc}",
            })
            continue

        expiration_reports.append(
            analyze_expiration(expiration, chain.calls, chain.puts, underlying_price)
        )

    usable_reports = [item for item in expiration_reports if not item.get("error")]
    if not usable_reports:
        return build_error(ticker, "No usable options chains returned.")

    aggregate = aggregate_expirations(usable_reports)
    stance = classify_options_stance(
        aggregate["put_call_volume_ratio"],
        aggregate["liquid_put_call_volume_ratio"],
        aggregate["unusual_call_count"],
        aggregate["unusual_put_count"],
    )

    return {
        "agent": "Options & Flow Analyst",
        "symbol": ticker,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "provider": "Yahoo Finance / yfinance",
        "mode": "watch_only_starter",
        "underlying_price": underlying_price,
        "expirations_checked": len(usable_reports),
        "expiration_summaries": usable_reports,
        **aggregate,
        "stance": stance,
        "confidence": calculate_confidence(aggregate, usable_reports),
        "liquidity_quality": classify_liquidity(aggregate),
        "warning": (
            "Starter options snapshot from free/delayed data. Use as context only; "
            "do not treat as execution-grade options flow."
        ),
        "missing_information": [
            "No paid OPRA/ORATS/Tradier options feed connected.",
            "No historical options-chain backtest or intraday flow tape connected.",
            "No execution-quality bid/ask validation beyond starter spread checks.",
        ],
    }


def analyze_expiration(expiration, calls, puts, underlying_price):
    call_rows = normalize_side(calls, "call", expiration, underlying_price)
    put_rows = normalize_side(puts, "put", expiration, underlying_price)
    all_rows = call_rows + put_rows
    liquid_calls = [row for row in call_rows if row["is_liquid"]]
    liquid_puts = [row for row in put_rows if row["is_liquid"]]
    atm_rows = [row for row in all_rows if row["is_atm"] and row.get("implied_volatility") is not None]

    call_volume = sum(row["volume"] for row in call_rows)
    put_volume = sum(row["volume"] for row in put_rows)
    call_oi = sum(row["open_interest"] for row in call_rows)
    put_oi = sum(row["open_interest"] for row in put_rows)
    liquid_call_volume = sum(row["volume"] for row in liquid_calls)
    liquid_put_volume = sum(row["volume"] for row in liquid_puts)
    unusual_calls = find_unusual_contracts(liquid_calls)
    unusual_puts = find_unusual_contracts(liquid_puts)

    return {
        "expiration": expiration,
        "days_to_expiration": days_to_expiration(expiration),
        "call_volume": call_volume,
        "put_volume": put_volume,
        "call_open_interest": call_oi,
        "put_open_interest": put_oi,
        "put_call_volume_ratio": safe_ratio(put_volume, call_volume),
        "put_call_open_interest_ratio": safe_ratio(put_oi, call_oi),
        "liquid_call_count": len(liquid_calls),
        "liquid_put_count": len(liquid_puts),
        "liquid_call_volume": liquid_call_volume,
        "liquid_put_volume": liquid_put_volume,
        "liquid_put_call_volume_ratio": safe_ratio(liquid_put_volume, liquid_call_volume),
        "atm_implied_volatility": average([row["implied_volatility"] for row in atm_rows]),
        "top_call_contracts": top_contracts(call_rows),
        "top_put_contracts": top_contracts(put_rows),
        "unusual_call_contracts": unusual_calls[:5],
        "unusual_put_contracts": unusual_puts[:5],
    }


def normalize_side(frame, side, expiration, underlying_price):
    if frame is None or frame.empty:
        return []

    rows = []
    for _, row in frame.iterrows():
        strike = safe_float(row.get("strike"))
        bid = safe_float(row.get("bid"))
        ask = safe_float(row.get("ask"))
        last_price = safe_float(row.get("lastPrice"))
        volume = safe_int(row.get("volume")) or 0
        open_interest = safe_int(row.get("openInterest")) or 0
        implied_volatility = safe_float(row.get("impliedVolatility"))
        spread_pct = bid_ask_spread_pct(bid, ask)
        moneyness = calculate_moneyness(strike, underlying_price)

        rows.append({
            "contract_symbol": row.get("contractSymbol"),
            "side": side,
            "expiration": expiration,
            "strike": strike,
            "last_price": last_price,
            "bid": bid,
            "ask": ask,
            "spread_pct": spread_pct,
            "volume": volume,
            "open_interest": open_interest,
            "volume_open_interest_ratio": safe_ratio(volume, open_interest),
            "implied_volatility": implied_volatility,
            "in_the_money": bool(row.get("inTheMoney")) if row.get("inTheMoney") is not None else None,
            "last_trade_date": format_timestamp(row.get("lastTradeDate")),
            "moneyness": moneyness,
            "is_atm": moneyness is not None and abs(moneyness) <= ATM_MONEYNESS_BAND,
            "is_liquid": is_liquid_contract(volume, open_interest, spread_pct),
        })

    return rows


def aggregate_expirations(expiration_reports):
    call_volume = sum(item["call_volume"] for item in expiration_reports)
    put_volume = sum(item["put_volume"] for item in expiration_reports)
    call_oi = sum(item["call_open_interest"] for item in expiration_reports)
    put_oi = sum(item["put_open_interest"] for item in expiration_reports)
    liquid_call_volume = sum(item["liquid_call_volume"] for item in expiration_reports)
    liquid_put_volume = sum(item["liquid_put_volume"] for item in expiration_reports)
    liquid_call_count = sum(item["liquid_call_count"] for item in expiration_reports)
    liquid_put_count = sum(item["liquid_put_count"] for item in expiration_reports)
    unusual_calls = flatten([item["unusual_call_contracts"] for item in expiration_reports])
    unusual_puts = flatten([item["unusual_put_contracts"] for item in expiration_reports])
    atm_iv_values = [
        item["atm_implied_volatility"]
        for item in expiration_reports
        if item.get("atm_implied_volatility") is not None
    ]

    return {
        "call_volume": call_volume,
        "put_volume": put_volume,
        "call_open_interest": call_oi,
        "put_open_interest": put_oi,
        "put_call_volume_ratio": safe_ratio(put_volume, call_volume),
        "put_call_open_interest_ratio": safe_ratio(put_oi, call_oi),
        "liquid_call_count": liquid_call_count,
        "liquid_put_count": liquid_put_count,
        "liquid_call_volume": liquid_call_volume,
        "liquid_put_volume": liquid_put_volume,
        "liquid_put_call_volume_ratio": safe_ratio(liquid_put_volume, liquid_call_volume),
        "atm_implied_volatility": average(atm_iv_values),
        "unusual_call_count": len(unusual_calls),
        "unusual_put_count": len(unusual_puts),
        "top_unusual_call_contracts": top_contracts(unusual_calls, limit=5),
        "top_unusual_put_contracts": top_contracts(unusual_puts, limit=5),
    }


def find_unusual_contracts(rows):
    unusual = [
        row for row in rows
        if row["volume"] >= 100 and (row["volume_open_interest_ratio"] or 0) >= 0.75
    ]
    return top_contracts(unusual, limit=10)


def top_contracts(rows, limit=5):
    selected = sorted(rows, key=lambda row: row.get("volume", 0), reverse=True)[:limit]
    return [
        {
            "contract_symbol": row.get("contract_symbol"),
            "side": row.get("side"),
            "expiration": row.get("expiration"),
            "strike": row.get("strike"),
            "last_price": row.get("last_price"),
            "bid": row.get("bid"),
            "ask": row.get("ask"),
            "spread_pct": row.get("spread_pct"),
            "volume": row.get("volume"),
            "open_interest": row.get("open_interest"),
            "volume_open_interest_ratio": row.get("volume_open_interest_ratio"),
            "implied_volatility": row.get("implied_volatility"),
            "moneyness": row.get("moneyness"),
        }
        for row in selected
    ]


def classify_options_stance(put_call_ratio, liquid_put_call_ratio=None, unusual_call_count=0, unusual_put_count=0):
    ratio = liquid_put_call_ratio if liquid_put_call_ratio is not None else put_call_ratio
    if ratio is None:
        return "unknown"

    if ratio < 0.7 and unusual_call_count >= unusual_put_count:
        return "bullish_positioning"
    if ratio > 1.25 and unusual_put_count >= unusual_call_count:
        return "bearish_or_hedging_positioning"
    if ratio < 0.85:
        return "bullish_lean"
    if ratio > 1.1:
        return "protective_or_bearish_lean"
    return "balanced"


def classify_liquidity(aggregate):
    liquid_contracts = aggregate["liquid_call_count"] + aggregate["liquid_put_count"]
    liquid_volume = aggregate["liquid_call_volume"] + aggregate["liquid_put_volume"]
    if liquid_contracts >= 20 and liquid_volume >= 5000:
        return "good"
    if liquid_contracts >= 8 and liquid_volume >= 1000:
        return "usable"
    if liquid_contracts:
        return "thin"
    return "poor"


def calculate_confidence(aggregate, expiration_reports):
    confidence = 0.25
    liquid_contracts = aggregate["liquid_call_count"] + aggregate["liquid_put_count"]
    liquid_volume = aggregate["liquid_call_volume"] + aggregate["liquid_put_volume"]

    if len(expiration_reports) >= 2:
        confidence += 0.1
    if liquid_contracts >= 8:
        confidence += 0.15
    if liquid_contracts >= 20:
        confidence += 0.1
    if liquid_volume >= 1000:
        confidence += 0.1
    if aggregate.get("atm_implied_volatility") is not None:
        confidence += 0.05

    return round(min(0.7, confidence), 2)


def get_underlying_price(instrument):
    try:
        fast_info = instrument.fast_info
        price = safe_float(getattr(fast_info, "last_price", None))
        if price is not None:
            return price
    except Exception:
        pass

    try:
        history = instrument.history(period="5d", auto_adjust=True)
        close = history["Close"].dropna()
        if not close.empty:
            return float(close.iloc[-1])
    except Exception:
        pass

    return None


def build_error(ticker, error):
    return {
        "agent": "Options & Flow Analyst",
        "symbol": ticker,
        "stance": "unknown",
        "confidence": 0.0,
        "error": error,
        "missing_information": [
            "Options chain unavailable from the free starter feed.",
            "No paid OPRA/ORATS/Tradier options feed connected.",
        ],
    }


def is_liquid_contract(volume, open_interest, spread_pct):
    if volume < MIN_LIQUID_VOLUME or open_interest < MIN_LIQUID_OPEN_INTEREST:
        return False
    if spread_pct is None:
        return False
    return spread_pct <= MAX_LIQUID_SPREAD_PCT


def bid_ask_spread_pct(bid, ask):
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2
    if mid == 0:
        return None
    return (ask - bid) / mid


def calculate_moneyness(strike, underlying_price):
    if strike is None or underlying_price in {None, 0}:
        return None
    return (strike - underlying_price) / underlying_price


def days_to_expiration(expiration):
    try:
        expiry_date = datetime.fromisoformat(expiration).date()
    except ValueError:
        return None
    return (expiry_date - datetime.now().date()).days


def safe_ratio(numerator, denominator):
    if denominator in {None, 0}:
        return None
    return numerator / denominator


def average(values):
    clean = [value for value in values if value is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def flatten(groups):
    rows = []
    for group in groups:
        rows.extend(group)
    return rows


def safe_float(value):
    if value is None or value != value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value):
    if value is None or value != value:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def format_timestamp(value):
    if value is None or value != value:
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromtimestamp(int(value), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return str(value)
    return moment.isoformat()


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
        lines.extend(["", "## Missing Information"])
        lines.extend([f"- {item}" for item in report.get("missing_information", [])])
        return "\n".join(lines) + "\n"

    lines.extend([
        f"Timestamp: {report['timestamp']}",
        f"Provider: {report['provider']}",
        f"Mode: {report['mode']}",
        f"Underlying Price: {format_number(report.get('underlying_price'))}",
        f"Expirations Checked: {report['expirations_checked']}",
        f"Liquidity Quality: {report['liquidity_quality']}",
        f"Call Volume: {report['call_volume']}",
        f"Put Volume: {report['put_volume']}",
        f"Put/Call Volume Ratio: {format_number(report['put_call_volume_ratio'])}",
        f"Liquid Put/Call Volume Ratio: {format_number(report['liquid_put_call_volume_ratio'])}",
        f"Call Open Interest: {report['call_open_interest']}",
        f"Put Open Interest: {report['put_open_interest']}",
        f"Put/Call OI Ratio: {format_number(report['put_call_open_interest_ratio'])}",
        f"ATM Implied Volatility: {format_pct(report.get('atm_implied_volatility'))}",
        "",
        "## Expiration Summary",
    ])

    for item in report.get("expiration_summaries", []):
        lines.append(
            f"- {item['expiration']} ({item.get('days_to_expiration')} DTE): "
            f"P/C vol {format_number(item.get('put_call_volume_ratio'))}, "
            f"liquid P/C vol {format_number(item.get('liquid_put_call_volume_ratio'))}, "
            f"ATM IV {format_pct(item.get('atm_implied_volatility'))}"
        )

    lines.extend(["", "## Top Unusual Calls"])
    lines.extend(format_contract_lines(report.get("top_unusual_call_contracts", [])))
    lines.extend(["", "## Top Unusual Puts"])
    lines.extend(format_contract_lines(report.get("top_unusual_put_contracts", [])))

    lines.extend([
        "",
        f"Warning: {report['warning']}",
        "",
        "## Missing Information",
    ])
    lines.extend([f"- {item}" for item in report.get("missing_information", [])])
    return "\n".join(lines) + "\n"


def format_contract_lines(contracts):
    if not contracts:
        return ["- None passed starter unusual-activity filters."]
    return [
        (
            f"- {item.get('contract_symbol')} {item.get('side')} strike {format_number(item.get('strike'))}: "
            f"vol {item.get('volume')}, OI {item.get('open_interest')}, "
            f"vol/OI {format_number(item.get('volume_open_interest_ratio'))}, "
            f"IV {format_pct(item.get('implied_volatility'))}, spread {format_pct(item.get('spread_pct'))}"
        )
        for item in contracts
    ]


def save_options_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{report['symbol']}_options_report.md"
    path.write_text(format_options_report(report), encoding="utf-8")
    return path


def format_number(value):
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def format_pct(value):
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"
