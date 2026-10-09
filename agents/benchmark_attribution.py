import json
from datetime import date, datetime
from pathlib import Path

from data.market_data import get_ohlcv_history
from data.paper_ledger import build_paper_ledger
from data.trade_journal import load_trade_journal, normalize_status


PROJECT_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = PROJECT_ROOT / "framework" / "benchmark_policy.json"


def generate_benchmark_attribution(ledger=None, journal=None, as_of=None):
    ledger = ledger or build_paper_ledger(journal)
    journal = journal if journal is not None else load_trade_journal()
    policy = load_policy()
    account = ledger.get("account") or {}
    starting_cash = float(account.get("starting_cash") or 0)
    equity = float(account.get("net_liquidation_value") or starting_cash)
    as_of_date = parse_date(as_of) or date.today()
    inception_date = portfolio_inception_date(journal) or date(as_of_date.year, 1, 1)
    ytd_start = date(as_of_date.year, 1, 1)

    portfolio_return = pct_return(equity, starting_cash)
    since_inception = benchmark_period(policy, inception_date, as_of_date)
    year_to_date = benchmark_period(policy, ytd_start, as_of_date)

    return {
        "as_of": as_of_date.isoformat(),
        "portfolio_inception": inception_date.isoformat(),
        "portfolio_return_pct": round_number(portfolio_return),
        "portfolio_value": round_money(equity),
        "starting_value": round_money(starting_cash),
        "since_inception": add_active_returns(since_inception, portfolio_return),
        "year_to_date": add_active_returns(year_to_date, portfolio_return),
        "notes": policy.get("notes", []),
    }


def benchmark_period(policy, start_date, end_date):
    primary = policy.get("primary_benchmark") or {}
    primary_result = component_return(
        primary.get("symbol", "SPY"),
        start_date,
        end_date,
    )
    primary_result["name"] = primary.get("name", primary_result.get("symbol"))

    comparisons = []
    for benchmark in policy.get("comparison_benchmarks", []):
        components = []
        for component in benchmark.get("components", []):
            result = component_return(
                component.get("symbol"),
                start_date,
                end_date,
                fallback_symbol=component.get("fallback_symbol"),
            )
            result["weight"] = float(component.get("weight") or 0)
            result["role"] = component.get("role")
            components.append(result)
        comparisons.append({
            "name": benchmark.get("name", "Blended benchmark"),
            "return_pct": weighted_return(components),
            "components": components,
            "status": "ok" if components and all(item.get("return_pct") is not None for item in components) else "partial",
        })

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "primary": primary_result,
        "comparisons": comparisons,
    }


def component_return(symbol, start_date, end_date, fallback_symbol=None):
    result = symbol_period_return(symbol, start_date, end_date)
    if result.get("return_pct") is None and fallback_symbol:
        fallback = symbol_period_return(fallback_symbol, start_date, end_date)
        fallback["requested_symbol"] = symbol
        fallback["fallback_used"] = fallback.get("return_pct") is not None
        return fallback
    return result


def symbol_period_return(symbol, start_date, end_date):
    if not symbol:
        return {"symbol": "", "return_pct": None, "status": "missing_symbol"}
    period = history_period(start_date, end_date)
    history = get_ohlcv_history(symbol, period=period)
    rows = []
    for row in history.get("rows", []):
        row_date = parse_date(row.get("date"))
        if row_date and start_date <= row_date <= end_date:
            rows.append(row)
    if not rows:
        return {
            "symbol": symbol,
            "return_pct": None,
            "status": "unavailable",
            "error": history.get("error", "No benchmark rows in period."),
        }
    first = float(rows[0]["close"])
    latest = float(rows[-1]["close"])
    return {
        "symbol": symbol,
        "return_pct": round_number(pct_return(latest, first)),
        "start_price": round_money(first),
        "end_price": round_money(latest),
        "start_date": rows[0]["date"],
        "end_date": rows[-1]["date"],
        "provider": history.get("provider"),
        "status": "ok",
    }


def add_active_returns(period, portfolio_return):
    result = dict(period)
    primary = dict(result.get("primary") or {})
    primary_return = primary.get("return_pct")
    primary["active_return_pct"] = (
        round_number(portfolio_return - primary_return)
        if primary_return is not None else None
    )
    result["primary"] = primary

    comparisons = []
    for comparison in result.get("comparisons", []):
        row = dict(comparison)
        benchmark_return = row.get("return_pct")
        row["active_return_pct"] = (
            round_number(portfolio_return - benchmark_return)
            if benchmark_return is not None else None
        )
        comparisons.append(row)
    result["comparisons"] = comparisons
    return result


def portfolio_inception_date(journal):
    dates = []
    if journal is None or journal.empty:
        return None
    for _, row in journal.iterrows():
        if normalize_status(row.get("status")) == "planned":
            continue
        opened = parse_date(row.get("opened_at"))
        if opened:
            dates.append(opened)
    return min(dates) if dates else None


def weighted_return(components):
    if not components or any(item.get("return_pct") is None for item in components):
        return None
    total_weight = sum(float(item.get("weight") or 0) for item in components)
    if total_weight <= 0:
        return None
    value = sum(float(item["return_pct"]) * float(item.get("weight") or 0) for item in components)
    return round_number(value / total_weight)


def history_period(start_date, end_date):
    days = max(1, (end_date - start_date).days)
    if days <= 31:
        return "3mo"
    if days <= 180:
        return "6mo"
    if days <= 365:
        return "1y"
    if days <= 730:
        return "2y"
    return "5y"


def parse_date(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def load_policy():
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def pct_return(latest, first):
    if not first:
        return 0.0
    return ((float(latest) / float(first)) - 1) * 100


def round_money(value):
    return round(float(value or 0), 2)


def round_number(value):
    return round(float(value or 0), 2)
