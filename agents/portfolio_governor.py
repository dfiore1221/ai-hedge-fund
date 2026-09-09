import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from agents.core_etf_sleeve import normalize_regime, select_risk_profile
from agents.market_intelligence import generate_daily_market_intelligence
from data.paper_ledger import build_paper_ledger
from data.trade_journal import (
    CLOSED_STATUS,
    enrich_trade_metrics,
    load_trade_journal,
    normalize_status,
    save_trade_journal,
    to_float,
)
from memory.research_memory import save_agent_report


PROJECT_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = PROJECT_ROOT / "framework" / "portfolio_policy.json"
WATCHLIST_PATH = PROJECT_ROOT / "framework" / "watchlist.json"
CORE_POLICY_PATH = PROJECT_ROOT / "framework" / "core_etf_sleeve.json"
MORNING_BRIEF_JSON_PATH = PROJECT_ROOT / "reports" / "morning_brief" / "daily_morning_brief.json"
REPORTS_DIR = PROJECT_ROOT / "reports" / "portfolio_governor"
CORE_SETUP_TYPE = "core etf sleeve"


def generate_portfolio_governor_report(
    save_memory=True,
    refresh_market_data=True,
    persist_prices=True,
):
    created_at = datetime.now().isoformat(timespec="seconds")
    run_id = f"{created_at[:10]}-portfolio-governor"
    policy = load_json(POLICY_PATH)
    core_policy = load_json(CORE_POLICY_PATH)
    core_regime = resolve_core_sleeve_regime(created_at)
    metadata = load_watchlist_metadata()
    journal = enrich_trade_metrics(load_trade_journal(), refresh_prices=refresh_market_data)
    if refresh_market_data and persist_prices:
        save_trade_journal(journal)
    ledger = build_paper_ledger(journal)
    account = ledger["account"]
    equity = account.get("net_liquidation_value") or account.get("starting_cash") or 0

    positions = enrich_positions(ledger.get("positions", []), metadata, policy, equity)
    exposure = build_exposure_map(positions, equity)
    attribution = build_attribution(journal, positions, equity)
    costs = build_cost_model(journal, positions, policy)
    controls = evaluate_controls(account, exposure, positions, policy)
    core_rules = evaluate_core_rebalance_rules(positions, account, core_policy, policy, core_regime)
    action_plan = build_action_plan(controls, core_rules, costs)
    status = classify_portfolio_status(controls, core_rules)

    report = {
        "agent": "Portfolio Governor",
        "system_role": "tool_workflow",
        "layer": "Professional Portfolio Controls",
        "run_id": run_id,
        "created_at": created_at,
        "mode": "paper_portfolio_governance",
        "status": status,
        "policy_version": policy.get("version"),
        "core_sleeve_regime": core_regime,
        "account": account,
        "exposure": exposure,
        "core_rebalance_rules": core_rules,
        "risk_controls": controls,
        "attribution": attribution,
        "cost_model": costs,
        "action_plan": action_plan,
        "positions": positions,
        "warnings": build_warnings(policy, costs),
    }

    if save_memory:
        save_agent_report(
            run_id=run_id,
            agent_name="Portfolio Governor",
            output=report,
            symbol="PORTFOLIO",
            stance=status["stance"],
            confidence=status["confidence_score"],
        )

    return report


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_core_sleeve_regime(created_at):
    brief_regime = latest_brief_regime(created_at)
    if brief_regime:
        return brief_regime

    try:
        macro = generate_daily_market_intelligence()
        return normalize_regime((macro.get("assessment") or {}).get("market_regime"))
    except Exception:
        return "Neutral"


def latest_brief_regime(created_at):
    if not MORNING_BRIEF_JSON_PATH.exists():
        return None

    try:
        brief = load_json(MORNING_BRIEF_JSON_PATH)
    except Exception:
        return None

    brief_created = str(brief.get("created_at") or "")
    if not brief_created:
        return None

    try:
        brief_date = datetime.fromisoformat(brief_created).date()
    except ValueError:
        return None

    try:
        report_date = datetime.fromisoformat(str(created_at)).date()
    except ValueError:
        report_date = date.today()

    if brief_date != report_date:
        return None

    core_sleeve = brief.get("core_etf_sleeve") or {}
    macro = brief.get("macro") or {}
    assessment = macro.get("assessment") or {}
    return normalize_regime(core_sleeve.get("regime") or assessment.get("market_regime"))


def load_watchlist_metadata():
    metadata = core_symbol_metadata()
    if not WATCHLIST_PATH.exists():
        return metadata
    data = load_json(WATCHLIST_PATH)
    for item in data.get("symbols", []):
        if isinstance(item, str):
            metadata[item.upper()] = {
                "symbol": item.upper(),
                "display_symbol": item.upper(),
                "category": "Uncategorized",
            }
            continue
        symbol = str(item.get("symbol", "")).upper().strip()
        if not symbol:
            continue
        if metadata.get(symbol, {}).get("role") == "core_sleeve":
            continue
        metadata[symbol] = {
            "symbol": symbol,
            "display_symbol": str(item.get("display_symbol", symbol)).upper().strip(),
            "category": item.get("category", "Uncategorized"),
            "bucket": item.get("bucket") or classify_bucket(item.get("category", "")),
            "role": item.get("role"),
        }
    return metadata


def core_symbol_metadata():
    return {
        "VOO": {
            "symbol": "VOO",
            "display_symbol": "VOO",
            "category": "Core ETF - S&P 500",
            "bucket": "Core ETF Sleeve",
            "role": "core_sleeve",
        },
        "QQQ": {
            "symbol": "QQQ",
            "display_symbol": "QQQ",
            "category": "Core ETF - Nasdaq 100",
            "bucket": "Core ETF Sleeve",
            "role": "core_sleeve",
        },
        "SMH": {
            "symbol": "SMH",
            "display_symbol": "SMH",
            "category": "Core ETF - Semiconductors",
            "bucket": "Core ETF Sleeve",
            "role": "core_sleeve",
        },
        "XLV": {
            "symbol": "XLV",
            "display_symbol": "XLV",
            "category": "Core ETF - Healthcare",
            "bucket": "Core ETF Sleeve",
            "role": "core_sleeve",
        },
        "XLE": {
            "symbol": "XLE",
            "display_symbol": "XLE",
            "category": "Core ETF - Energy",
            "bucket": "Core ETF Sleeve",
            "role": "core_sleeve",
        },
        "BIL": {
            "symbol": "BIL",
            "display_symbol": "BIL",
            "category": "Core ETF - T-Bills",
            "bucket": "Core ETF Sleeve",
            "role": "core_sleeve",
        },
    }


def enrich_positions(positions, metadata, policy, equity):
    rows = []
    for position in positions:
        symbol = str(position.get("symbol", "")).upper().strip()
        meta = metadata.get(symbol, {})
        category = meta.get("category", "Uncategorized")
        bucket = meta.get("bucket") or classify_bucket(category)
        market_value = to_float(position.get("market_value"))
        weight = safe_divide(market_value, equity)
        row = dict(position)
        row.update({
            "display_symbol": meta.get("display_symbol", symbol),
            "category": category,
            "bucket": bucket,
            "is_core_sleeve": is_core_symbol(symbol),
            "weight_pct": round_pct(weight),
            "beta_estimate": estimate_beta(category, policy),
            "liquidity_tier": estimate_liquidity_tier(symbol, category, bucket),
        })
        row["beta_weighted_exposure"] = round_number(weight * row["beta_estimate"])
        rows.append(row)
    return sorted(rows, key=lambda item: item.get("market_value", 0), reverse=True)


def classify_bucket(category):
    category = str(category or "").lower()
    if "etf" in category:
        return "Market Map / Sector ETFs"
    if any(term in category for term in [
        "ai", "semiconductor", "software", "cybersecurity", "crypto", "quantum", "photonics",
    ]) or category == "space":
        return "AI / Growth / Innovation"
    if any(term in category for term in ["healthcare", "utilities", "real estate", "staples", "power"]):
        return "Defensive / Income"
    if any(term in category for term in [
        "energy", "industrial", "materials", "mining", "metals", "machinery",
        "aerospace", "defense", "financial", "bank", "consumer", "transport",
    ]):
        return "Cyclical / Value"
    return "Other / Special Situations"


def is_core_symbol(symbol):
    return symbol in {"VOO", "QQQ", "SMH", "XLV", "XLE", "BIL"}


def estimate_beta(category, policy):
    beta_map = policy.get("beta_assumptions", {})
    if category in beta_map:
        return float(beta_map[category])
    bucket = classify_bucket(category)
    if bucket == "AI / Growth / Innovation":
        return 1.3
    if bucket == "Cyclical / Value":
        return 1.05
    if bucket == "Defensive / Income":
        return 0.75
    return 1.0


def estimate_liquidity_tier(symbol, category, bucket):
    if is_core_symbol(symbol) or bucket == "Market Map / Sector ETFs":
        return "high"
    if "." in symbol or category in {"Quantum Computing", "Space", "Batteries & Grid"}:
        return "lower_confidence"
    return "normal"


def build_exposure_map(positions, equity):
    by_bucket = aggregate_exposure(positions, "bucket", equity)
    by_category = aggregate_exposure(positions, "category", equity)
    by_symbol = [
        {
            "symbol": item["symbol"],
            "display_symbol": item.get("display_symbol"),
            "category": item.get("category"),
            "bucket": item.get("bucket"),
            "market_value": round_money(item.get("market_value")),
            "weight_pct": item.get("weight_pct"),
            "beta_estimate": item.get("beta_estimate"),
            "beta_weighted_exposure": item.get("beta_weighted_exposure"),
            "liquidity_tier": item.get("liquidity_tier"),
        }
        for item in positions
    ]
    gross_exposure = sum(to_float(position.get("market_value")) for position in positions)
    weighted_beta = sum(to_float(position.get("beta_weighted_exposure")) for position in positions)
    concentration = max((item["weight_pct"] for item in by_symbol), default=0)

    return {
        "gross_exposure": round_money(gross_exposure),
        "gross_exposure_pct": round_pct(safe_divide(gross_exposure, equity)),
        "cash_pct": None,
        "weighted_beta_estimate": round_number(weighted_beta),
        "largest_position_pct": round_number(concentration),
        "by_bucket": by_bucket,
        "by_category": by_category,
        "by_symbol": by_symbol,
        "correlation_note": build_correlation_note(by_bucket),
    }


def aggregate_exposure(positions, key, equity):
    rows = defaultdict(lambda: {"market_value": 0.0, "unrealized_pnl": 0.0, "symbols": set()})
    for position in positions:
        group = position.get(key) or "Uncategorized"
        rows[group]["market_value"] += to_float(position.get("market_value"))
        rows[group]["unrealized_pnl"] += to_float(position.get("unrealized_pnl"))
        rows[group]["symbols"].add(position.get("symbol"))
    return sorted([
        {
            key: group,
            "market_value": round_money(values["market_value"]),
            "weight_pct": round_pct(safe_divide(values["market_value"], equity)),
            "unrealized_pnl": round_money(values["unrealized_pnl"]),
            "symbols": sorted(symbol for symbol in values["symbols"] if symbol),
        }
        for group, values in rows.items()
    ], key=lambda item: item["market_value"], reverse=True)


def build_correlation_note(by_bucket):
    growth = next((item for item in by_bucket if item.get("bucket") == "AI / Growth / Innovation"), None)
    if growth and growth["weight_pct"] > 0.45:
        return "High growth/innovation exposure implies elevated correlation during risk-off selloffs."
    return "No single bucket currently dominates enough to trigger a correlation warning."


def build_attribution(journal, positions, equity):
    journal = enrich_trade_metrics(journal)
    closed = journal[journal["status"].map(normalize_status) == CLOSED_STATUS]
    open_rows = journal[journal["status"].map(normalize_status) == "open"]
    by_symbol = defaultdict(lambda: {"realized_pnl": 0.0, "unrealized_pnl": 0.0, "planned_risk": 0.0, "trades": 0})
    by_source = defaultdict(lambda: {"realized_pnl": 0.0, "unrealized_pnl": 0.0, "trades": 0})
    by_setup = defaultdict(lambda: {"realized_pnl": 0.0, "unrealized_pnl": 0.0, "trades": 0})

    for _, row in journal.iterrows():
        status = normalize_status(row.get("status"))
        symbol = str(row.get("symbol", "")).upper().strip() or "UNKNOWN"
        source = str(row.get("source", "")).strip() or "unknown"
        setup = str(row.get("setup_type", "")).strip() or "unknown"
        realized = to_float(row.get("realized_pnl")) if status == CLOSED_STATUS else 0
        unrealized = to_float(row.get("unrealized_pnl")) if status == "open" else 0
        planned_risk = to_float(row.get("planned_risk"))
        by_symbol[symbol]["realized_pnl"] += realized
        by_symbol[symbol]["unrealized_pnl"] += unrealized
        by_symbol[symbol]["planned_risk"] += planned_risk
        by_symbol[symbol]["trades"] += 1
        by_source[source]["realized_pnl"] += realized
        by_source[source]["unrealized_pnl"] += unrealized
        by_source[source]["trades"] += 1
        by_setup[setup]["realized_pnl"] += realized
        by_setup[setup]["unrealized_pnl"] += unrealized
        by_setup[setup]["trades"] += 1

    total_realized = sum(to_float(row.get("realized_pnl")) for _, row in closed.iterrows())
    total_unrealized = sum(to_float(row.get("unrealized_pnl")) for _, row in open_rows.iterrows())
    total_pnl = total_realized + total_unrealized

    return {
        "realized_pnl": round_money(total_realized),
        "unrealized_pnl": round_money(total_unrealized),
        "total_pnl": round_money(total_pnl),
        "total_return_pct": round_pct(safe_divide(total_pnl, equity)),
        "by_symbol": attribution_rows(by_symbol),
        "by_source": attribution_rows(by_source, label="source"),
        "by_setup_type": attribution_rows(by_setup, label="setup_type"),
        "top_contributors": top_attribution(by_symbol, reverse=True),
        "top_detractors": top_attribution(by_symbol, reverse=False),
    }


def attribution_rows(groups, label="symbol"):
    rows = []
    for name, values in groups.items():
        total = values["realized_pnl"] + values["unrealized_pnl"]
        rows.append({
            label: name,
            "realized_pnl": round_money(values["realized_pnl"]),
            "unrealized_pnl": round_money(values["unrealized_pnl"]),
            "total_pnl": round_money(total),
            "planned_risk": round_money(values.get("planned_risk", 0)),
            "trades": values["trades"],
        })
    return sorted(rows, key=lambda item: item["total_pnl"], reverse=True)


def top_attribution(groups, reverse):
    rows = attribution_rows(groups)
    rows = sorted(rows, key=lambda item: item["total_pnl"], reverse=reverse)
    return rows[:5]


def build_cost_model(journal, positions, policy):
    assumptions = policy.get("execution_assumptions", {})
    commission = float(assumptions.get("commission_per_trade", 0))
    closed_or_open = journal[journal["status"].map(normalize_status).isin({"open", CLOSED_STATUS})]
    estimated_costs = 0.0
    rows = []

    for _, row in closed_or_open.iterrows():
        symbol = str(row.get("symbol", "")).upper().strip()
        setup_type = str(row.get("setup_type", "")).strip().lower()
        shares = to_float(row.get("shares"))
        entry = to_float(row.get("entry"))
        exit_price = to_float(row.get("exit_price"))
        is_etf = setup_type == CORE_SETUP_TYPE or is_core_symbol(symbol)
        open_notional = shares * entry
        close_notional = shares * exit_price if exit_price else 0
        bps = assumptions.get("slippage_bps_etf" if is_etf else "slippage_bps_equity", 5)
        spread_bps = assumptions.get("spread_bps_etf" if is_etf else "spread_bps_equity", 8)
        open_cost = estimate_execution_cost(open_notional, bps, spread_bps, commission)
        close_cost = estimate_execution_cost(close_notional, bps, spread_bps, commission) if close_notional else 0
        total_cost = open_cost + close_cost
        estimated_costs += total_cost
        rows.append({
            "symbol": symbol,
            "trade_id": row.get("id", ""),
            "status": normalize_status(row.get("status")),
            "notional": round_money(open_notional + close_notional),
            "estimated_cost": round_money(total_cost),
            "assumption": "ETF/core sleeve" if is_etf else "single-name equity",
        })

    return {
        "estimated_total_costs": round_money(estimated_costs),
        "commission_per_trade": commission,
        "slippage_bps_equity": assumptions.get("slippage_bps_equity"),
        "spread_bps_equity": assumptions.get("spread_bps_equity"),
        "slippage_bps_etf": assumptions.get("slippage_bps_etf"),
        "spread_bps_etf": assumptions.get("spread_bps_etf"),
        "tax_model": assumptions.get("tax_model"),
        "dividend_model": assumptions.get("dividend_model"),
        "trade_costs": rows,
    }


def estimate_execution_cost(notional, slippage_bps, spread_bps, commission):
    if notional <= 0:
        return 0
    return commission + notional * ((float(slippage_bps) + float(spread_bps)) / 10000)


def evaluate_controls(account, exposure, positions, policy):
    limits = policy.get("risk_limits", {})
    equity = account.get("net_liquidation_value") or account.get("starting_cash") or 0
    starting_cash = account.get("starting_cash") or equity
    cash_pct = safe_divide(account.get("cash_balance"), equity)
    drawdown_pct = safe_divide(equity - starting_cash, starting_cash)
    open_risk_pct = safe_divide(account.get("open_risk"), equity)
    exposure["cash_pct"] = round_pct(cash_pct)
    issues = []
    warnings = []

    add_limit_check(
        issues,
        "cash_reserve",
        cash_pct < limits.get("cash_reserve_min_pct", 0.2),
        f"Cash reserve {format_pct(cash_pct)} is below policy minimum {format_pct(limits.get('cash_reserve_min_pct', 0.2))}.",
        severity="warning",
    )
    add_limit_check(
        issues,
        "open_planned_risk",
        open_risk_pct > limits.get("max_open_planned_risk_pct", 0.03),
        f"Open planned risk {format_pct(open_risk_pct)} exceeds policy max {format_pct(limits.get('max_open_planned_risk_pct', 0.03))}.",
        severity="hard",
    )
    add_limit_check(
        issues,
        "drawdown_warning",
        drawdown_pct <= limits.get("warning_drawdown_pct", -0.03),
        f"Portfolio drawdown {format_pct(drawdown_pct)} has crossed the warning threshold.",
        severity="warning",
    )
    add_limit_check(
        issues,
        "hard_drawdown",
        drawdown_pct <= limits.get("hard_drawdown_pct", -0.06),
        f"Portfolio drawdown {format_pct(drawdown_pct)} has crossed the hard risk threshold.",
        severity="hard",
    )

    for position in positions:
        limit = limits.get("max_single_etf_pct" if position.get("is_core_sleeve") else "max_single_name_pct")
        if position["weight_pct"] > limit:
            issues.append({
                "rule": "position_concentration",
                "severity": "warning",
                "message": f"{position['symbol']} is {format_pct(position['weight_pct'])}, above limit {format_pct(limit)}.",
            })
        if position.get("liquidity_tier") == "lower_confidence":
            warnings.append(f"{position['symbol']} has lower-confidence liquidity/reference data; size conservatively.")

    for bucket in exposure.get("by_bucket", []):
        if bucket.get("bucket") == "AI / Growth / Innovation" and bucket["weight_pct"] > limits.get("max_growth_bucket_pct", 0.45):
            issues.append({
                "rule": "growth_bucket_concentration",
                "severity": "warning",
                "message": f"Growth/innovation exposure is {format_pct(bucket['weight_pct'])}, above policy {format_pct(limits.get('max_growth_bucket_pct', 0.45))}.",
            })

    for category in exposure.get("by_category", []):
        if category["weight_pct"] > limits.get("max_sector_pct", 0.35):
            issues.append({
                "rule": "sector_concentration",
                "severity": "warning",
                "message": f"{category['category']} exposure is {format_pct(category['weight_pct'])}, above sector limit {format_pct(limits.get('max_sector_pct', 0.35))}.",
            })

    hedge_review = (
        drawdown_pct <= limits.get("hedge_review_drawdown_pct", -0.04)
        or any(
            bucket.get("bucket") == "AI / Growth / Innovation"
            and bucket["weight_pct"] > limits.get("hedge_review_growth_bucket_pct", 0.45)
            for bucket in exposure.get("by_bucket", [])
        )
    )

    return {
        "cash_pct": round_pct(cash_pct),
        "drawdown_pct": round_pct(drawdown_pct),
        "open_risk_pct": round_pct(open_risk_pct),
        "hedge_review_required": hedge_review,
        "issues": issues,
        "warnings": warnings,
    }


def add_limit_check(issues, rule, condition, message, severity):
    if condition:
        issues.append({"rule": rule, "severity": severity, "message": message})


def evaluate_core_rebalance_rules(positions, account, core_policy, portfolio_policy, regime=None):
    core_positions = [position for position in positions if position.get("is_core_sleeve")]
    equity = account.get("net_liquidation_value") or account.get("starting_cash") or 0
    current_value = sum(to_float(position.get("market_value")) for position in core_positions)
    regime = normalize_regime(regime)
    profile = select_risk_profile(core_policy, regime)
    target_pct = profile.get("target_sleeve_pct", core_policy.get("target_sleeve_pct", 0.5))
    target_value = equity * target_pct
    drift = target_value - current_value
    drift_pct = safe_divide(drift, equity)
    band = portfolio_policy.get("rebalance", {}).get(
        "core_sleeve_drift_band_pct",
        core_policy.get("rebalance_band_pct", 0.05),
    )
    action = "rebalance_review" if abs(drift_pct) > band else "within_band"

    return {
        "regime": regime,
        "target_sleeve_pct": round_pct(target_pct),
        "current_sleeve_pct": round_pct(safe_divide(current_value, equity)),
        "target_value": round_money(target_value),
        "current_value": round_money(current_value),
        "drift_value": round_money(drift),
        "drift_pct": round_pct(drift_pct),
        "rebalance_band_pct": round_pct(band),
        "cadence": portfolio_policy.get("rebalance", {}).get("review_cadence"),
        "minimum_action_notional": portfolio_policy.get("rebalance", {}).get("minimum_action_notional"),
        "profile_weights": profile.get("weights", {}),
        "action": action,
        "message": (
            "Core sleeve is outside the rebalance band; review buys/trims during market hours."
            if action == "rebalance_review"
            else "Core sleeve is within rebalance band."
        ),
    }


def build_action_plan(controls, core_rules, costs):
    actions = []
    for issue in controls.get("issues", []):
        if issue["severity"] == "hard":
            actions.append(f"Hard risk review: {issue['message']}")
        else:
            actions.append(f"Risk warning: {issue['message']}")
    if controls.get("hedge_review_required"):
        actions.append("Review whether a paper hedge or exposure trim is needed before adding new growth risk.")
    if core_rules.get("action") == "rebalance_review":
        actions.append(core_rules["message"])
    if costs.get("estimated_total_costs", 0) > 0:
        actions.append(f"Track estimated execution friction of ${costs['estimated_total_costs']:.2f} in performance review.")
    if not actions:
        actions.append("No portfolio-level rule breach; maintain monitoring and normal paper-trade discipline.")
    return actions


def classify_portfolio_status(controls, core_rules):
    hard = [issue for issue in controls.get("issues", []) if issue.get("severity") == "hard"]
    warnings = [issue for issue in controls.get("issues", []) if issue.get("severity") != "hard"]
    if hard:
        return {"stance": "risk_reduction_required", "confidence_score": 95}
    if core_rules.get("action") == "rebalance_review" or controls.get("hedge_review_required"):
        return {"stance": "rebalance_or_hedge_review", "confidence_score": 90}
    if warnings:
        return {"stance": "monitor_with_warnings", "confidence_score": 85}
    return {"stance": "within_policy", "confidence_score": 90}


def build_warnings(policy, costs):
    warnings = list(policy.get("notes", []))
    if costs.get("dividend_model") == "not_accrued_yet":
        warnings.append("Dividend accrual is not modeled yet; ETF sleeve returns may be understated over longer windows.")
    if costs.get("tax_model"):
        warnings.append("Tax lots are tracked as paper assumptions only; this is not tax advice.")
    return warnings


def format_portfolio_governor_report(report):
    status = report["status"]
    account = report["account"]
    exposure = report["exposure"]
    core_rules = report["core_rebalance_rules"]
    controls = report["risk_controls"]
    attribution = report["attribution"]
    costs = report["cost_model"]

    lines = [
        "# Portfolio Governor Report",
        "",
        f"Created At: {report['created_at']}",
        f"Run ID: {report['run_id']}",
        f"Portfolio Stance: {status['stance']}",
        f"Confidence Score: {status['confidence_score']}/100",
        "",
        "## Account",
        f"- Net liquidation value: {money(account.get('net_liquidation_value'))}",
        f"- Cash balance: {money(account.get('cash_balance'))} ({format_pct(controls.get('cash_pct'))})",
        f"- Gross exposure: {money(exposure.get('gross_exposure'))} ({format_pct(exposure.get('gross_exposure_pct'))})",
        f"- Open planned risk: {money(account.get('open_risk'))} ({format_pct(controls.get('open_risk_pct'))})",
        f"- Drawdown from starting cash: {format_pct(controls.get('drawdown_pct'))}",
        "",
        "## Core ETF Sleeve Rules",
        f"- Cadence: {core_rules.get('cadence')}",
        f"- Regime used: {core_rules.get('regime')}",
        f"- Target sleeve: {format_pct(core_rules.get('target_sleeve_pct'))} / {money(core_rules.get('target_value'))}",
        f"- Current sleeve: {format_pct(core_rules.get('current_sleeve_pct'))} / {money(core_rules.get('current_value'))}",
        f"- Drift: {money(core_rules.get('drift_value'))} ({format_pct(core_rules.get('drift_pct'))})",
        f"- Band: {format_pct(core_rules.get('rebalance_band_pct'))}",
        f"- Action: {core_rules.get('action')} - {core_rules.get('message')}",
        "",
        "## Exposure Map",
        f"- Weighted beta estimate: {format_number(exposure.get('weighted_beta_estimate'))}",
        f"- Largest position: {format_pct(exposure.get('largest_position_pct'))}",
        f"- Correlation note: {exposure.get('correlation_note')}",
        "",
        "### Bucket Exposure",
    ]
    lines.extend(format_exposure_rows(exposure.get("by_bucket", []), "bucket"))
    lines.extend(["", "### Sector / Category Exposure"])
    lines.extend(format_exposure_rows(exposure.get("by_category", [])[:12], "category"))
    lines.extend(["", "## Portfolio-Level Risk Rules"])
    lines.extend([f"- {issue['severity'].upper()}: {issue['message']}" for issue in controls.get("issues", [])] or ["- No rule breaches."])
    lines.extend([f"- Warning: {item}" for item in controls.get("warnings", [])])
    if controls.get("hedge_review_required"):
        lines.append("- Hedge review required: consider paper hedge/trim before adding concentrated risk.")
    lines.extend(["", "## Attribution"])
    lines.append(f"- Realized P&L: {money(attribution.get('realized_pnl'))}")
    lines.append(f"- Unrealized P&L: {money(attribution.get('unrealized_pnl'))}")
    lines.append(f"- Total P&L: {money(attribution.get('total_pnl'))} ({format_pct(attribution.get('total_return_pct'))})")
    lines.extend(["", "### Top Contributors"])
    lines.extend(format_attribution_rows(attribution.get("top_contributors", [])))
    lines.extend(["", "### Top Detractors"])
    lines.extend(format_attribution_rows(attribution.get("top_detractors", [])))
    lines.extend(["", "## Friction / Modeling"])
    lines.append(f"- Estimated execution friction: {money(costs.get('estimated_total_costs'))}")
    lines.append(f"- Equity assumption: {costs.get('slippage_bps_equity')} bps slippage + {costs.get('spread_bps_equity')} bps spread.")
    lines.append(f"- ETF assumption: {costs.get('slippage_bps_etf')} bps slippage + {costs.get('spread_bps_etf')} bps spread.")
    lines.append(f"- Dividend model: {costs.get('dividend_model')}")
    lines.append(f"- Tax model: {costs.get('tax_model')}")
    lines.extend(["", "## Action Plan"])
    lines.extend([f"- {item}" for item in report.get("action_plan", [])])
    lines.extend(["", "## Warnings"])
    lines.extend([f"- {item}" for item in report.get("warnings", [])] or ["- None."])
    return "\n".join(lines) + "\n"


def format_exposure_rows(rows, key):
    if not rows:
        return ["- None."]
    return [
        f"- {row[key]}: {money(row['market_value'])} ({format_pct(row['weight_pct'])}); "
        f"unrealized {money(row.get('unrealized_pnl'))}; symbols {', '.join(row.get('symbols', [])) or 'n/a'}"
        for row in rows
    ]


def format_attribution_rows(rows):
    if not rows:
        return ["- None."]
    return [
        f"- {row.get('symbol', row.get('source', row.get('setup_type', 'n/a')))}: "
        f"total {money(row.get('total_pnl'))}; realized {money(row.get('realized_pnl'))}; "
        f"unrealized {money(row.get('unrealized_pnl'))}; trades {row.get('trades')}"
        for row in rows
    ]


def save_portfolio_governor_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    markdown = format_portfolio_governor_report(report)
    latest_md = REPORTS_DIR / "portfolio_governor.md"
    latest_json = REPORTS_DIR / "portfolio_governor.json"
    stamped_md = REPORTS_DIR / f"portfolio_governor_{timestamp}.md"
    stamped_json = REPORTS_DIR / f"portfolio_governor_{timestamp}.json"
    latest_md.write_text(markdown, encoding="utf-8")
    latest_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    stamped_md.write_text(markdown, encoding="utf-8")
    stamped_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return latest_md


def safe_divide(numerator, denominator):
    denominator = float(denominator or 0)
    if not denominator:
        return 0.0
    return float(numerator or 0) / denominator


def round_money(value):
    return round(float(value or 0), 2)


def round_number(value):
    return round(float(value or 0), 4)


def round_pct(value):
    return round(float(value or 0), 4)


def money(value):
    return f"${float(value or 0):,.2f}"


def format_pct(value):
    return f"{float(value or 0) * 100:.1f}%"


def format_number(value):
    return f"{float(value or 0):.2f}"
