import json
import os
import contextlib
import io
from datetime import date, datetime
from pathlib import Path

from data.alpaca_data import fetch_latest_stock_bars
from data.economic_calendar import get_economic_calendar
from data.finnhub_data import (
    fetch_company_news,
    fetch_recommendation_trends,
    is_finnhub_configured,
)
from data.fred_data import get_fred_macro_snapshot
from data.local_cache import cache_summary


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WATCHLIST_PATH = PROJECT_ROOT / "framework" / "watchlist.json"
ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_LIVE_CHECK_LIMIT = 12


PROVIDER_CONFIGS = [
    {
        "name": "Yahoo Finance / yfinance",
        "domain": "prices, charts, starter fundamentals, starter news/options",
        "env_key": None,
        "status_when_missing": "available",
        "note": "Prototype provider only; should remain a fallback once paid/official feeds are added.",
    },
    {
        "name": "SEC EDGAR",
        "domain": "filings, company facts, as-filed fundamentals",
        "env_key": "SEC_USER_AGENT",
        "status_when_missing": "needs_config",
        "note": "Primary source for filings and financial-statement truth.",
    },
    {
        "name": "FRED",
        "domain": "rates, inflation, credit, macro time series",
        "env_key": "FRED_API_KEY",
        "status_when_missing": "not_configured",
        "note": "Recommended official macro time-series upgrade.",
    },
    {
        "name": "Alpaca",
        "domain": "equity/options/crypto market data and future paper-trading bridge",
        "env_key": "ALPACA_API_KEY",
        "required_env_keys": ["ALPACA_API_KEY", "ALPACA_SECRET_KEY"],
        "status_when_missing": "not_configured",
        "note": "Candidate primary market-data provider if paper trading matters.",
    },
    {
        "name": "Polygon",
        "domain": "equity/options/reference market data",
        "env_key": "POLYGON_API_KEY",
        "status_when_missing": "not_configured",
        "note": "Candidate primary market-data provider if clean developer data matters most.",
    },
    {
        "name": "Trading Economics",
        "domain": "economic calendar, global macro, earnings calendar",
        "env_key": "TRADING_ECONOMICS_API_KEY",
        "status_when_missing": "not_configured",
        "note": "Best candidate for event-risk and economic-calendar layer.",
    },
    {
        "name": "Benzinga",
        "domain": "market-moving news, analyst actions, calendars, unusual options",
        "env_key": "BENZINGA_API_KEY",
        "status_when_missing": "not_configured",
        "note": "Best candidate for trader-grade overnight news.",
    },
    {
        "name": "Finnhub",
        "domain": "company news, earnings, estimates, fundamentals",
        "env_key": "FINNHUB_API_KEY",
        "status_when_missing": "not_configured",
        "note": "Broad practical API for news/earnings/estimates.",
    },
    {
        "name": "Tradier",
        "domain": "options chains, greeks, brokerage/paper trading workflow",
        "env_key": "TRADIER_ACCESS_TOKEN",
        "status_when_missing": "not_configured",
        "note": "Options and broker workflow candidate.",
    },
    {
        "name": "ORATS",
        "domain": "options analytics, implied volatility, greeks",
        "env_key": "ORATS_TOKEN",
        "status_when_missing": "not_configured",
        "note": "Specialized options analytics candidate.",
    },
    {
        "name": "Databento",
        "domain": "institutional historical/live market data, futures, OPRA",
        "env_key": "DATABENTO_API_KEY",
        "status_when_missing": "not_configured",
        "note": "Later-stage institutional backtesting provider.",
    },
]


def generate_data_health_report(symbols=None, live_checks=True, live_check_limit=DEFAULT_LIVE_CHECK_LIMIT):
    load_environment()
    watchlist_entries = load_watchlist_entries(symbols)
    symbols = [entry["symbol"] for entry in watchlist_entries]
    providers = build_provider_statuses()
    fred_snapshot = get_fred_macro_snapshot()
    economic_calendar = get_economic_calendar()
    news_check = check_news_provider(symbols[0]) if symbols else {"status": "skipped"}
    live_price_checks = []
    alpaca_price_check = {"status": "skipped", "bars": {}, "comparisons": []}

    if live_checks:
        for symbol in symbols[:live_check_limit]:
            live_price_checks.append(check_price_history(symbol))
        alpaca_price_check = check_alpaca_price_provider(symbols[:live_check_limit])

    domain_scores = score_domains(
        providers,
        live_price_checks,
        live_checks,
        fred_snapshot,
        economic_calendar,
        news_check,
        alpaca_price_check,
    )
    quality_score = sum(item["score"] for item in domain_scores.values())
    gate = classify_gate(quality_score, domain_scores, live_price_checks, live_checks)

    return {
        "agent": "Data Quality",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "watchlist_count": len(symbols),
        "live_checks_enabled": live_checks,
        "live_check_limit": live_check_limit if live_checks else 0,
        "symbols_checked": [item["symbol"] for item in live_price_checks],
        "live_price_checks": live_price_checks,
        "alpaca_price_check": alpaca_price_check,
        "providers": providers,
        "official_macro": fred_snapshot,
        "economic_calendar": economic_calendar,
        "starter_news_check": news_check,
        "cache": cache_summary(),
        "domain_scores": domain_scores,
        "data_quality_score": quality_score,
        "gate": gate,
        "coverage": build_coverage(live_price_checks, len(symbols), live_checks),
        "blockers": build_blockers(providers, domain_scores, live_price_checks, live_checks),
        "recommendations": build_recommendations(providers, domain_scores),
    }


def load_watchlist_entries(symbols=None):
    if symbols:
        return [{"symbol": symbol.upper().strip(), "category": "Ad Hoc"} for symbol in symbols if symbol.strip()]

    if not WATCHLIST_PATH.exists():
        return [{"symbol": symbol} for symbol in ["SPY", "QQQ", "MSFT", "NVDA"]]

    data = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
    entries = []

    for item in data.get("symbols", []):
        if isinstance(item, str):
            symbol = item.upper().strip()
            category = "Uncategorized"
        else:
            symbol = item.get("symbol", "").upper().strip()
            category = item.get("category", "Uncategorized")

        if symbol:
            entries.append({"symbol": symbol, "category": category})

    return entries


def build_provider_statuses():
    statuses = []

    for config in PROVIDER_CONFIGS:
        env_key = config["env_key"]
        required_env_keys = config.get("required_env_keys")
        if env_key is None:
            configured = True
        elif required_env_keys:
            configured = all(bool(os.getenv(key, "").strip()) for key in required_env_keys)
        else:
            configured = bool(os.getenv(env_key, "").strip())

        if configured:
            status = "available" if env_key is None else "configured"
        else:
            status = config["status_when_missing"]

        statuses.append({
            "name": config["name"],
            "domain": config["domain"],
            "env_key": "+".join(required_env_keys) if required_env_keys else env_key,
            "configured": configured,
            "status": status,
            "note": config["note"],
        })

    return statuses


def check_price_history(symbol):
    try:
        import yfinance as yf
    except ModuleNotFoundError:
        return {
            "symbol": symbol,
            "status": "error",
            "message": "yfinance is not installed in the active Python environment.",
        }

    try:
        with contextlib.redirect_stderr(io.StringIO()):
            history = yf.Ticker(symbol).history(period="10d", auto_adjust=True)
    except Exception as exc:
        return {
            "symbol": symbol,
            "status": "error",
            "message": str(exc),
        }

    if history is None or history.empty or "Close" not in history:
        return {
            "symbol": symbol,
            "status": "missing",
            "message": "No price history returned.",
        }

    close = history["Close"].dropna()
    if close.empty:
        return {
            "symbol": symbol,
            "status": "missing",
            "message": "No close prices returned.",
        }

    latest_date = close.index[-1].date()
    calendar_age = (date.today() - latest_date).days
    status = "ok" if calendar_age <= 5 else "stale"

    return {
        "symbol": symbol,
        "status": status,
        "latest_date": latest_date.isoformat(),
        "calendar_age_days": calendar_age,
        "latest_close": float(close.iloc[-1]),
        "rows": len(close),
    }


def check_yahoo_news(symbol):
    try:
        import yfinance as yf
    except ModuleNotFoundError:
        return {
            "symbol": symbol,
            "status": "error",
            "message": "yfinance is not installed in the active Python environment.",
        }

    try:
        with contextlib.redirect_stderr(io.StringIO()):
            news_items = yf.Ticker(symbol).news or []
    except Exception as exc:
        return {
            "symbol": symbol,
            "status": "error",
            "message": str(exc),
        }

    return {
        "symbol": symbol,
        "provider": "Yahoo Finance",
        "status": "ok" if news_items else "missing",
        "headline_count": len(news_items),
        "message": "Starter Yahoo news feed returned headlines." if news_items else "No starter headlines returned.",
    }


def check_news_provider(symbol):
    if is_finnhub_configured():
        news = fetch_company_news(symbol, days_back=3, limit=10)
        recommendations = fetch_recommendation_trends(symbol, limit=2)
        news_ok = news.get("status") in {"ok", "empty"}
        recommendations_ok = recommendations.get("status") in {"ok", "empty"}

        return {
            "symbol": symbol,
            "provider": "Finnhub",
            "status": "ok" if news_ok and recommendations_ok else "error",
            "headline_count": len(news.get("items", [])),
            "recommendation_count": len(recommendations.get("items", [])),
            "message": build_finnhub_news_check_message(news, recommendations),
            "news_status": news.get("status"),
            "recommendation_status": recommendations.get("status"),
        }

    return check_yahoo_news(symbol)


def check_alpaca_price_provider(symbols):
    response = fetch_latest_stock_bars(symbols)
    if response.get("status") != "ok":
        return {
            **response,
            "comparisons": [],
            "agreement_status": response.get("status"),
        }

    comparisons = []
    for symbol, alpaca_bar in response.get("bars", {}).items():
        yahoo_check = check_price_history(symbol)
        comparison = compare_price_sources(symbol, yahoo_check, alpaca_bar)
        comparisons.append(comparison)

    agreement_statuses = {item["status"] for item in comparisons}
    if "conflict" in agreement_statuses:
        agreement_status = "conflict"
    elif "missing" in agreement_statuses or "error" in agreement_statuses:
        agreement_status = "partial"
    else:
        agreement_status = "ok"

    return {
        **response,
        "comparisons": comparisons,
        "agreement_status": agreement_status,
    }


def compare_price_sources(symbol, yahoo_check, alpaca_bar):
    if yahoo_check.get("status") not in {"ok", "stale"}:
        return {
            "symbol": symbol,
            "status": "missing",
            "message": f"Yahoo comparison unavailable: {yahoo_check.get('message', yahoo_check.get('status'))}",
        }

    yahoo_close = yahoo_check.get("latest_close")
    alpaca_close = alpaca_bar.get("close")
    if yahoo_close in {None, 0} or alpaca_close in {None, 0}:
        return {
            "symbol": symbol,
            "status": "missing",
            "message": "One provider returned no usable close price.",
        }

    diff_pct = abs(alpaca_close - yahoo_close) / yahoo_close * 100
    status = "ok" if diff_pct <= 5 else "conflict"
    return {
        "symbol": symbol,
        "status": status,
        "yahoo_close": yahoo_close,
        "alpaca_close": alpaca_close,
        "diff_pct": round(diff_pct, 2),
        "alpaca_timestamp": alpaca_bar.get("timestamp"),
        "message": "Provider prices are within tolerance." if status == "ok" else "Provider prices differ by more than 5%.",
    }


def score_domains(
    providers,
    live_price_checks,
    live_checks,
    fred_snapshot=None,
    economic_calendar=None,
    news_check=None,
    alpaca_price_check=None,
):
    provider_names = {provider["name"]: provider for provider in providers}
    configured_names = {
        provider["name"]
        for provider in providers
        if provider["configured"]
    }
    market_provider_configured = bool({"Alpaca", "Polygon", "Databento"} & configured_names)
    market_provider_working = bool(
        alpaca_price_check and alpaca_price_check.get("status") == "ok"
    )
    provider_agreement_ok = bool(
        market_provider_working and alpaca_price_check.get("agreement_status") == "ok"
    )
    premium_news_provider_configured = bool({"Benzinga", "Finnhub"} & configured_names)
    starter_news_available = bool(news_check and news_check.get("status") == "ok")
    news_provider_configured = premium_news_provider_configured or starter_news_available
    economic_calendar_ok = bool(
        economic_calendar and economic_calendar.get("status") in {"ok", "partial"}
    )
    event_provider_configured = (
        economic_calendar_ok
        or bool({"Finnhub", "Benzinga"} & configured_names)
    )
    options_provider_configured = bool({"Tradier", "ORATS"} & configured_names)
    fred_ok = bool(fred_snapshot and fred_snapshot.get("status") in {"ok", "partial"})
    macro_provider_configured = fred_ok or economic_calendar_ok
    sec_configured = provider_names["SEC EDGAR"]["configured"]

    if live_checks:
        ok_checks = [item for item in live_price_checks if item["status"] == "ok"]
        price_score = round(25 * safe_ratio(len(ok_checks), len(live_price_checks))) if live_price_checks else 0
    else:
        price_score = 12

    return {
        "price_bars": {
            "score": min(25, price_score + (3 if market_provider_working else 0)),
            "max_score": 25,
            "status": "strong" if market_provider_working else "configured_unverified" if market_provider_configured else "prototype",
            "detail": build_price_bars_detail(alpaca_price_check, market_provider_configured, market_provider_working),
        },
        "corporate_actions_reference": {
            "score": 10 if market_provider_working and sec_configured else 6 if sec_configured else 3,
            "max_score": 10,
            "status": "partial" if not market_provider_working else "strong",
            "detail": "SEC configured; second market-data provider not live yet." if sec_configured and not market_provider_working else "Reference-data coverage needs configuration.",
        },
        "earnings_events": {
            "score": 15 if event_provider_configured else 5,
            "max_score": 15,
            "status": "strong" if event_provider_configured else "starter",
            "detail": build_event_context_detail(economic_calendar, event_provider_configured),
        },
        "news_analyst": {
            "score": 15 if premium_news_provider_configured else 8 if starter_news_available else 5,
            "max_score": 15,
            "status": "strong" if premium_news_provider_configured else "starter_live" if starter_news_available else "starter",
            "detail": build_news_context_detail(news_check, premium_news_provider_configured),
        },
        "options": {
            "score": 10 if options_provider_configured else 4,
            "max_score": 10,
            "status": "strong" if options_provider_configured else "starter",
            "detail": "Options provider configured." if options_provider_configured else "Only starter Yahoo options checks are available.",
        },
        "macro_event_context": {
            "score": 10 if macro_provider_configured else 4,
            "max_score": 10,
            "status": "strong" if macro_provider_configured else "starter",
            "detail": build_macro_context_detail(fred_snapshot, economic_calendar, macro_provider_configured),
        },
        "provider_agreement_checks": {
            "score": 10 if provider_agreement_ok and live_checks else 7 if market_provider_working and live_checks else 4 if live_checks else 2,
            "max_score": 10,
            "status": build_provider_agreement_status(alpaca_price_check, market_provider_configured, market_provider_working),
            "detail": build_provider_agreement_detail(alpaca_price_check, market_provider_configured, market_provider_working),
        },
        "critical_errors": {
            "score": 5 if not has_critical_live_errors(live_price_checks, live_checks) else 0,
            "max_score": 5,
            "status": "ok" if not has_critical_live_errors(live_price_checks, live_checks) else "critical",
            "detail": "No critical live-check errors." if not has_critical_live_errors(live_price_checks, live_checks) else "Live price checks have critical missing/error results.",
        },
    }


def build_price_bars_detail(alpaca_price_check, market_provider_configured, market_provider_working):
    if market_provider_working:
        return (
            "Alpaca market-data check available "
            f"({alpaca_price_check.get('bar_count', 0)} latest bars, "
            f"feed {alpaca_price_check.get('feed', 'n/a')}); Yahoo remains fallback."
        )
    if market_provider_configured:
        return f"Alpaca configured but live check is not working: {alpaca_price_check.get('error', alpaca_price_check.get('status', 'n/a'))}."
    return "Using yfinance prototype market data."


def build_provider_agreement_status(alpaca_price_check, market_provider_configured, market_provider_working):
    if not market_provider_configured:
        return "missing_provider"
    if not market_provider_working:
        return "configured_unverified"
    if alpaca_price_check.get("agreement_status") == "ok":
        return "partial"
    if alpaca_price_check.get("agreement_status") == "conflict":
        return "provider_conflict"
    return "partial"


def build_provider_agreement_detail(alpaca_price_check, market_provider_configured, market_provider_working):
    if not market_provider_configured:
        return "Provider comparison can begin once Alpaca, Polygon, or another second market-data provider is configured."
    if not market_provider_working:
        return f"Second provider configured but not verified: {alpaca_price_check.get('error', alpaca_price_check.get('status', 'n/a'))}."

    comparisons = alpaca_price_check.get("comparisons") or []
    ok_count = len([item for item in comparisons if item.get("status") == "ok"])
    conflict_count = len([item for item in comparisons if item.get("status") == "conflict"])
    return (
        f"Compared Alpaca vs Yahoo on {len(comparisons)} symbols: "
        f"{ok_count} within tolerance, {conflict_count} conflicts."
    )


def build_coverage(live_price_checks, watchlist_count, live_checks):
    if not live_checks:
        return {
            "watchlist_symbols": watchlist_count,
            "checked_symbols": 0,
            "price_ok": 0,
            "price_stale": 0,
            "price_missing_or_error": 0,
            "checked_coverage_pct": None,
        }

    checked = len(live_price_checks)
    ok = len([item for item in live_price_checks if item["status"] == "ok"])
    stale = len([item for item in live_price_checks if item["status"] == "stale"])
    missing_or_error = checked - ok - stale

    return {
        "watchlist_symbols": watchlist_count,
        "checked_symbols": checked,
        "price_ok": ok,
        "price_stale": stale,
        "price_missing_or_error": missing_or_error,
        "checked_coverage_pct": round(100 * safe_ratio(ok, checked), 1) if checked else 0,
    }


def build_event_context_detail(economic_calendar, event_provider_configured):
    if economic_calendar and economic_calendar.get("status") in {"ok", "partial"}:
        summary = economic_calendar.get("summary") or {}
        return (
            f"{economic_calendar.get('provider', 'Economic calendar')} available "
            f"({summary.get('event_count', 0)} events in window)."
        )
    if economic_calendar and economic_calendar.get("error"):
        return economic_calendar["error"]
    if event_provider_configured:
        return "Event provider configured."
    return "Only starter Yahoo earnings checks are available."


def build_news_context_detail(news_check, premium_news_provider_configured):
    if premium_news_provider_configured and news_check and news_check.get("provider") == "Finnhub":
        if news_check.get("status") == "ok":
            return (
                "Finnhub company-news and recommendation-trend checks available "
                f"({news_check.get('headline_count', 0)} headlines, "
                f"{news_check.get('recommendation_count', 0)} recommendation snapshots)."
            )
        return news_check.get("message") or "Finnhub configured but live check failed."
    if premium_news_provider_configured:
        return "Premium news/analyst provider configured."
    if news_check and news_check.get("status") == "ok":
        return (
            "Starter Yahoo headline feed available "
            f"({news_check.get('headline_count', 0)} sample headlines)."
        )
    if news_check and news_check.get("message"):
        return news_check["message"]
    return "Only starter Yahoo headlines are available."


def build_finnhub_news_check_message(news, recommendations):
    details = []
    if news.get("status") in {"ok", "empty"}:
        details.append(f"company news {news.get('status')} ({len(news.get('items', []))} returned)")
    else:
        details.append(f"company news error: {news.get('error', 'n/a')}")

    if recommendations.get("status") in {"ok", "empty"}:
        details.append(
            f"recommendation trends {recommendations.get('status')} "
            f"({len(recommendations.get('items', []))} returned)"
        )
    else:
        details.append(f"recommendation trends error: {recommendations.get('error', 'n/a')}")

    return "Finnhub " + "; ".join(details) + "."


def build_macro_context_detail(fred_snapshot, economic_calendar, macro_provider_configured):
    details = []

    if fred_snapshot and fred_snapshot.get("status") in {"ok", "partial"}:
        details.append(f"FRED official macro data available ({fred_snapshot.get('status')}).")
    elif fred_snapshot and fred_snapshot.get("error") and fred_snapshot.get("status") != "not_configured":
        details.append(fred_snapshot["error"])

    if economic_calendar and economic_calendar.get("status") in {"ok", "partial"}:
        details.append(
            f"{economic_calendar.get('provider', 'Economic calendar')} available "
            f"({economic_calendar.get('status')})."
        )
    elif economic_calendar and economic_calendar.get("error") and economic_calendar.get("status") != "not_configured":
        details.append(economic_calendar["error"])

    if details:
        return " ".join(details)
    if macro_provider_configured:
        return "Macro/event provider configured."
    return "Current macro layer uses market proxies; official macro APIs not configured."


def classify_gate(quality_score, domain_scores, live_price_checks, live_checks):
    if has_critical_live_errors(live_price_checks, live_checks):
        return {
            "status": "Blocked",
            "decision": "No actionable recommendations until critical price data works.",
        }
    if quality_score >= 85:
        return {
            "status": "Pass",
            "decision": "Eligible for simulated trade review.",
        }
    if quality_score >= 70:
        return {
            "status": "Conditional",
            "decision": "Allow simulated trade review with data-quality warnings.",
        }
    if quality_score >= 50:
        return {
            "status": "Watch Only",
            "decision": "Allow watchlist ideas; block approved simulated trades.",
        }
    return {
        "status": "Needs Data",
        "decision": "No actionable recommendation; surface missing-data checklist.",
    }


def build_blockers(providers, domain_scores, live_price_checks, live_checks):
    blockers = []
    provider_by_name = {provider["name"]: provider for provider in providers}

    if not provider_by_name["SEC EDGAR"]["configured"]:
        blockers.append("SEC_USER_AGENT is not configured; SEC calls may be blocked or non-compliant.")

    weak_domains = [
        name
        for name, item in domain_scores.items()
        if item["score"] < item["max_score"] * 0.5
    ]
    for domain in weak_domains:
        blockers.append(f"{domain.replace('_', ' ').title()} is below half of target quality.")

    if live_checks:
        for item in live_price_checks:
            if item["status"] in {"missing", "error"}:
                blockers.append(f"{item['symbol']} price check failed: {item.get('message', item['status'])}")
            elif item["status"] == "stale":
                blockers.append(f"{item['symbol']} price data is stale: latest {item.get('latest_date')}")

    return blockers


def build_recommendations(providers, domain_scores):
    configured_names = {provider["name"] for provider in providers if provider["configured"]}
    recommendations = []

    recommendations.append("Build provider-status and data-quality checks into the morning brief before loosening trade recommendations.")

    if "FRED" not in configured_names:
        recommendations.append("Add FRED next for official rates, inflation, credit, and macro time series.")
    if not {"Alpaca", "Polygon"} & configured_names:
        recommendations.append("Run an Alpaca vs Polygon bakeoff for primary market data.")
    if domain_scores.get("earnings_events", {}).get("score", 0) < 10:
        recommendations.append("Add or repair economic calendar/event risk coverage.")
    elif "Trading Economics" not in configured_names:
        recommendations.append("Trading Economics remains optional for premium forecasts, actuals, and global impact scoring.")
    if not {"Benzinga", "Finnhub"} & configured_names:
        recommendations.append("Add Benzinga or Finnhub for overnight news, earnings, and analyst actions.")
    if not {"Tradier", "ORATS"} & configured_names:
        recommendations.append("Keep options ideas watch-only until Tradier, ORATS, Polygon, or another options source is connected.")

    return recommendations


def format_data_health_report(report):
    lines = [
        "# Data Health Report",
        "",
        f"Created At: {report['created_at']}",
        f"Data Quality Score: {report['data_quality_score']}/100",
        f"Gate: {report['gate']['status']}",
        f"Decision: {report['gate']['decision']}",
        f"Watchlist Symbols: {report['watchlist_count']}",
        "",
        "## Coverage",
    ]

    coverage = report["coverage"]
    lines.extend([
        f"- Live Checks Enabled: {report['live_checks_enabled']}",
        f"- Symbols Checked: {coverage['checked_symbols']} of {coverage['watchlist_symbols']}",
        f"- Price OK: {coverage['price_ok']}",
        f"- Price Stale: {coverage['price_stale']}",
        f"- Price Missing/Error: {coverage['price_missing_or_error']}",
        f"- Checked Coverage: {format_pct(coverage['checked_coverage_pct'])}",
        "",
        "## Domain Scores",
    ])

    for name, item in report["domain_scores"].items():
        lines.append(
            f"- {name.replace('_', ' ').title()}: {item['score']}/{item['max_score']} "
            f"({item['status']}) - {item['detail']}"
        )

    lines.extend(["", "## Provider Status"])
    for provider in report["providers"]:
        env_label = provider["env_key"] or "built-in"
        lines.append(
            f"- {provider['name']}: {provider['status']} [{env_label}] - {provider['domain']}"
        )

    cache = report.get("cache") or {}
    lines.extend([
        "",
        "## Local Data Cache",
        f"- Path: {cache.get('path', 'n/a')}",
        f"- Files: {cache.get('file_count', 0)}",
        f"- Size: {format_bytes(cache.get('size_bytes', 0))}",
        f"- Latest Update: {cache.get('latest_updated_at') or 'n/a'}",
    ])

    lines.extend(["", "## Economic Calendar"])
    economic_calendar = report.get("economic_calendar") or {}
    provider = economic_calendar.get("provider") or "Economic Calendar"
    if economic_calendar.get("status") == "not_configured":
        lines.append("- Economic calendar: not configured.")
    elif economic_calendar.get("error"):
        lines.append(f"- {provider}: {economic_calendar['error']}")
    else:
        summary = economic_calendar.get("summary") or {}
        lines.extend([
            f"- {provider}: {economic_calendar.get('status')}",
            f"- Window: {economic_calendar.get('start_date')} to {economic_calendar.get('end_date')}",
            f"- Events: {summary.get('event_count', 0)}",
            f"- High-Importance Events: {summary.get('high_importance_count', 0)}",
        ])

    lines.extend(["", "## News Provider Check"])
    news_check = report.get("starter_news_check") or {}
    if news_check.get("provider") == "Finnhub":
        lines.append(
            f"- {news_check.get('symbol')}: {news_check.get('status')} via Finnhub, "
            f"{news_check.get('headline_count', 0)} headlines, "
            f"{news_check.get('recommendation_count', 0)} recommendation snapshots."
        )
        lines.append(f"- Detail: {news_check.get('message', 'n/a')}")
    elif news_check.get("status") == "ok":
        lines.append(
            f"- {news_check.get('symbol')}: ok, {news_check.get('headline_count', 0)} starter headlines returned."
        )
    elif news_check.get("status"):
        lines.append(
            f"- {news_check.get('symbol', 'n/a')}: {news_check.get('status')} - {news_check.get('message', 'n/a')}"
        )
    else:
        lines.append("- Not checked.")

    if report["live_checks_enabled"]:
        lines.extend(["", "## Live Price Checks"])
        for item in report.get("live_price_checks", []):
            lines.append(format_live_price_check(item))

    lines.extend(["", "## Provider Agreement Check"])
    alpaca_check = report.get("alpaca_price_check") or {}
    lines.extend(format_alpaca_provider_check(alpaca_check))

    lines.extend(["", "## Blockers"])
    lines.extend([f"- {item}" for item in report["blockers"]] or ["- None."])

    lines.extend(["", "## Recommendations"])
    lines.extend([f"- {item}" for item in report["recommendations"]] or ["- None."])

    return "\n".join(lines) + "\n"


def format_live_price_check(item):
    if item["status"] == "ok":
        return (
            f"- {item['symbol']}: ok, latest {item['latest_date']}, "
            f"close {item['latest_close']:.2f}, rows {item['rows']}"
        )
    if item["status"] == "stale":
        return f"- {item['symbol']}: stale, latest {item.get('latest_date')}"
    return f"- {item['symbol']}: {item['status']} - {item.get('message', 'n/a')}"


def format_alpaca_provider_check(alpaca_check):
    if alpaca_check.get("status") == "not_configured":
        return ["- Alpaca: not configured."]
    if alpaca_check.get("status") == "skipped":
        return ["- Alpaca: skipped."]
    if alpaca_check.get("status") != "ok":
        return [f"- Alpaca: {alpaca_check.get('status')} - {alpaca_check.get('error', 'n/a')}"]

    lines = [
        (
            f"- Alpaca: ok, feed {alpaca_check.get('feed', 'n/a')}, "
            f"{alpaca_check.get('bar_count', 0)} latest bars."
        )
    ]
    comparisons = alpaca_check.get("comparisons") or []
    for item in comparisons[:8]:
        if item.get("status") in {"ok", "conflict"}:
            lines.append(
                f"- {item['symbol']}: {item['status']}, Alpaca {item.get('alpaca_close'):.2f} "
                f"vs Yahoo {item.get('yahoo_close'):.2f}, diff {item.get('diff_pct')}%."
            )
        else:
            lines.append(f"- {item.get('symbol', 'n/a')}: {item.get('status')} - {item.get('message', 'n/a')}")

    return lines


def safe_ratio(numerator, denominator):
    if not denominator:
        return 0
    return numerator / denominator


def has_critical_live_errors(live_price_checks, live_checks):
    if not live_checks:
        return False
    if not live_price_checks:
        return True
    failed = [item for item in live_price_checks if item["status"] in {"missing", "error"}]
    return len(failed) == len(live_price_checks)


def format_pct(value):
    if value is None:
        return "n/a"
    return f"{value:.1f}%"


def format_bytes(value):
    try:
        size = float(value)
    except (TypeError, ValueError):
        return "n/a"

    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def load_environment():
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        load_env_file_fallback(ENV_PATH)
        return

    load_dotenv(ENV_PATH)


def load_env_file_fallback(path):
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        clean = line.strip()
        if not clean or clean.startswith("#") or "=" not in clean:
            continue

        key, value = clean.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
