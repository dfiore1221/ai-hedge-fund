import json
import math
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from agents.feedback_loop import generate_feedback_report
from agents.core_etf_sleeve import execute_autonomous_core_rebalance
from agents.options_contract_selector import select_defined_loss_contract
from agents.intraday_discovery import load_latest_intraday_candidates
from data.paper_fills import format_paper_fill_report, process_paper_fills
from data.options_fills import process_option_fills
from data.options_journal import append_option_trade, load_options_journal, normalize_status as normalize_option_status
from data.paper_ledger import build_paper_ledger
from data.trade_journal import (
    cancel_planned_trade,
    load_trade_journal,
    normalize_status,
    open_trade_from_plan,
    to_float,
)
from memory.research_memory import save_agent_report


PROJECT_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = PROJECT_ROOT / "framework" / "autonomy_policy.json"
MORNING_BRIEF_PATH = PROJECT_ROOT / "reports" / "morning_brief" / "daily_morning_brief.json"
REPORTS_DIR = PROJECT_ROOT / "reports" / "autonomy"
EASTERN = ZoneInfo("America/New_York")


def run_autonomy_cycle(action="cycle", now=None, save_memory=True):
    now = normalize_now(now)
    action = str(action or "cycle").lower()
    if action not in {"cycle", "plan", "execute", "status"}:
        raise ValueError("Autonomy supports: cycle, plan, execute, status")

    policy = load_policy()
    report = base_report(action, now, policy)
    if not policy.get("enabled"):
        report["status"] = "disabled"
        report["notes"].append("Autonomous paper mandate is disabled in policy.")
        return finalize_report(report, save_memory)

    if action in {"cycle", "plan", "execute"}:
        report["stale_order_cancellations"] = expire_stale_autonomous_orders(policy, now)
    if action in {"cycle", "plan"}:
        report["planning"] = plan_autonomous_orders(policy, now)
    if action in {"cycle", "execute"}:
        report["execution"] = execute_autonomous_fills(policy, now)
    if action == "status":
        report["status_snapshot"] = build_status_snapshot(policy)

    statuses = [
        (report.get("planning") or {}).get("status"),
        (report.get("execution") or {}).get("status"),
    ]
    if "blocked" in statuses:
        report["status"] = "blocked"
    elif "needs_attention" in statuses:
        report["status"] = "needs_attention"
    else:
        report["status"] = "ok"
    return finalize_report(report, save_memory)


def expire_stale_autonomous_orders(policy, now):
    """Cancel old, unfilled autonomous plans before they can execute on obsolete levels."""
    max_age_days = max(1, int(policy.get("max_planned_order_age_days", 5)))
    journal = load_trade_journal()
    cancellations = []
    if journal is None or journal.empty:
        return cancellations

    for _, row in journal.iterrows():
        if normalize_status(row.get("status")) != "planned":
            continue
        if "autonomous paper" not in str(row.get("source") or "").lower():
            continue
        opened_date = parse_date(row.get("opened_at"))
        if opened_date is None:
            continue
        age_days = (now.date() - opened_date).days
        if age_days < max_age_days:
            continue

        trade_id = str(row.get("id") or "")
        symbol = str(row.get("symbol") or "").upper().strip()
        reason = (
            f"Committee automatically canceled an unfilled autonomous plan after {age_days} days; "
            f"policy maximum is {max_age_days} days."
        )
        cancel_planned_trade(
            trade_id,
            reason=reason,
            lessons="Rebuild stale setups from current price, volatility, news, and risk data before re-entry.",
            canceled_at=now.isoformat(timespec="seconds"),
        )
        cancellations.append({
            "trade_id": trade_id,
            "symbol": symbol,
            "age_days": age_days,
            "reason": reason,
        })
    return cancellations


def plan_autonomous_orders(policy, now):
    brief = load_latest_brief()
    if not brief:
        return planning_result("blocked", reason="No morning brief is available.")

    brief_date = parse_date(brief.get("created_at"))
    if brief_date != now.date():
        return planning_result(
            "blocked",
            reason=f"Latest morning brief is from {brief_date}; no stale brief orders were created.",
        )

    data_score = float((brief.get("data_health") or {}).get("data_quality_score") or 0)
    minimum_data_score = float(policy.get("minimum_data_quality_score", 90))
    if data_score < minimum_data_score:
        return planning_result(
            "blocked",
            reason=f"Data quality {data_score:.0f}/100 is below autonomous minimum {minimum_data_score:.0f}/100.",
            data_quality_score=data_score,
        )

    journal = load_trade_journal()
    ledger = build_paper_ledger(journal)
    account = ledger.get("account") or {}
    equity = float(account.get("net_liquidation_value") or account.get("starting_cash") or 0)
    cash = float(account.get("cash_balance") or 0)
    options_journal = load_options_journal()
    feedback = generate_feedback_report()
    completed_tactical_trades = completed_autonomous_tactical_count(journal, options_journal)
    adaptive = build_adaptive_parameters(feedback, policy, completed_tactical_trades)
    active = active_autonomous_orders(journal)
    active_options = active_autonomous_options(options_journal)
    active_symbols = active_trade_symbols(journal) | active_option_symbols(options_journal)
    active_run_ids = active_trade_run_ids(journal) | active_option_run_ids(options_journal)
    capacity = max(0, int(policy.get("max_open_autonomous_experiments", 8)) - len(active) - len(active_options))
    daily_limit = min(capacity, int(adaptive["max_new_orders_per_day"]))
    if macro_event_risk(brief):
        daily_limit = min(daily_limit, int(policy.get("max_new_orders_during_macro_event_risk", 1)))

    existing_today = autonomous_orders_created_on(journal, now.date()) + autonomous_option_orders_created_on(options_journal, now.date())
    daily_limit = max(0, daily_limit - len(existing_today))
    if daily_limit <= 0:
        return planning_result(
            "ok",
            reason="Autonomous order capacity is already filled for today.",
            data_quality_score=data_score,
            adaptive_parameters=adaptive,
            created_orders=[],
        )

    max_sleeve_value = equity * float(policy.get("max_autonomous_sleeve_pct", 0.15))
    active_value = sum(
        to_float(row.get("entry")) * to_float(row.get("shares"))
        for row in active
    )
    active_value += sum(to_float(row.get("premium_paid")) for row in active_options)
    remaining_sleeve = max(0, max_sleeve_value - active_value)
    reserve_floor = equity * 0.20
    available_cash = max(0, cash - reserve_floor)
    available_notional = min(remaining_sleeve, available_cash)
    if available_notional <= 0:
        return planning_result(
            "blocked",
            reason="Autonomous sleeve or cash-reserve capacity is exhausted.",
            data_quality_score=data_score,
            adaptive_parameters=adaptive,
        )

    intraday_candidates = load_latest_intraday_candidates(now)
    candidates = list(intraday_candidates)
    candidates.extend(brief.get("approved_simulated_trades") or [])
    candidates.extend(brief.get("conditional_setups") or [])
    candidates.extend(brief.get("bearish_fade_watch") or [])
    candidates = dedupe_candidates(candidates)
    selected = []
    rejected = []
    used_categories = category_counts_for_symbols(brief, active_symbols)

    for idea in candidates:
        if len(selected) >= daily_limit:
            break
        reason = candidate_rejection_reason(
            idea,
            policy,
            adaptive,
            active_symbols,
            active_run_ids,
            used_categories,
        )
        if reason:
            rejected.append({"symbol": idea.get("symbol"), "reason": reason})
            continue

        strategy = (idea.get("strategy_plan") or {}).get("selected_family") or "swing"
        if strategy in {"long_call", "long_put"}:
            order = build_option_order_plan(idea, equity, policy)
            option_error = str((order or {}).get("error") or "")
            if (
                option_error
                and policy.get("allow_equity_fallback_when_options_data_unavailable", False)
                and option_data_unavailable(option_error)
            ):
                fallback_idea = build_equity_fallback_idea(idea, strategy, option_error)
                order = build_order_plan(fallback_idea, equity, available_notional, policy, adaptive)
                if order:
                    order["fallback_from_strategy"] = strategy
                    order["fallback_reason"] = option_error
        else:
            order = build_order_plan(idea, equity, available_notional, policy, adaptive)
        if order and order.get("error"):
            rejected.append({"symbol": idea.get("symbol"), "strategy": strategy, "reason": order["error"]})
            continue
        if not order:
            rejected.append({"symbol": idea.get("symbol"), "reason": "Risk/data gates did not produce a valid order."})
            continue

        if order.get("instrument") == "option":
            trade_id = append_option_trade(order["journal_fields"])
        else:
            trade_id = open_trade_from_plan(**order["journal_fields"])
        order["trade_id"] = trade_id
        order.pop("journal_fields", None)
        selected.append(order)
        category = str(idea.get("category") or "Uncategorized")
        used_categories[category] = used_categories.get(category, 0) + 1
        active_symbols.add(order["symbol"])
        if idea.get("run_id"):
            active_run_ids.add(idea["run_id"])
        available_notional = max(0, available_notional - order.get("notional", order.get("premium_at_risk", 0)))

    return planning_result(
        "ok",
        reason=(
            f"Created {len(selected)} autonomous paper order(s)."
            if selected else "No setup passed the autonomous paper mandate today."
        ),
        data_quality_score=data_score,
        adaptive_parameters=adaptive,
        created_orders=selected,
        rejected_candidates=rejected[:20],
        macro_event_risk=macro_event_risk(brief),
        intraday_candidates_considered=len(intraday_candidates),
    )


def dedupe_candidates(candidates):
    """Prefer the newest intraday review when a symbol appears more than once."""
    seen = set()
    deduped = []
    for idea in candidates:
        symbol = str(idea.get("symbol") or "").upper().strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        deduped.append(idea)
    return deduped


def execute_autonomous_fills(policy, now):
    if policy.get("market_hours_only_fills", True) and not is_regular_market_hours(now):
        return {
            "status": "ok",
            "market_session": "closed",
            "reason": "No simulated fills applied outside regular U.S. market hours.",
            "events": [],
            "applied_events": [],
        }

    result = process_paper_fills(
        apply=True,
        allow_entry_fills=True,
        allow_exit_fills=True,
    )
    options_result = process_option_fills(apply=True)
    core_rebalance = None
    if policy.get("manage_core_etf_sleeve", True):
        core_rebalance = execute_autonomous_core_rebalance(
            load_latest_brief(),
            now=now,
            minimum_data_quality_score=policy.get("minimum_data_quality_score", 90),
        )
    applied_events = list(result.get("applied_events", [])) + list(options_result.get("applied_events", []))
    core_actions = list((core_rebalance or {}).get("actions", []))
    status = "needs_attention" if (core_rebalance or {}).get("status") == "blocked" else "ok"
    return {
        "status": status,
        "market_session": "open",
        "reason": (
            f"Applied {len(applied_events)} simulated equity/options fill event(s) and "
            f"{len(core_actions)} core-sleeve rebalance action(s)."
        ),
        "events": result.get("events", []),
        "applied_events": applied_events,
        "options_events": options_result.get("events", []),
        "options_quote_errors": options_result.get("quote_errors", []),
        "core_rebalance": core_rebalance,
        "price_provider_status": result.get("price_provider_status"),
        "fill_report": format_paper_fill_report(result),
    }


def build_adaptive_parameters(feedback, policy, completed_tactical_trades=0):
    setup = feedback.get("setup_review_learning") or {}
    learning_score = float(setup.get("learning_score") or 0)
    target_rate = float(setup.get("target_1_hit_rate") or 0)
    partial_rate = float(setup.get("partial_win_rate") or 0)
    bounds = policy.get("adaptive_bounds") or {}

    if learning_score >= 70 and target_rate >= 30:
        risk_pct = 0.002
        target_r = 1.0
        max_new = 3
        minimum_score = 76
    elif learning_score >= 60 and max(target_rate, partial_rate) >= 20:
        risk_pct = 0.0015
        target_r = 0.85
        max_new = 2
        minimum_score = 78
    else:
        risk_pct = float(policy.get("base_risk_per_trade_pct", 0.001))
        target_r = 0.75
        max_new = int(policy.get("max_new_orders_per_day", 3))
        minimum_score = float(policy.get("minimum_candidate_score", 80))

    evidence_target = int(policy.get("minimum_completed_tactical_trades_for_risk_expansion", 30))
    evidence_unlocked = int(completed_tactical_trades or 0) >= evidence_target
    if not evidence_unlocked:
        risk_pct = min(risk_pct, float(policy.get("pre_evidence_max_risk_per_trade_pct", 0.0015)))
        max_new = min(max_new, int(policy.get("pre_evidence_max_new_orders_per_day", 3)))
        minimum_score = max(
            minimum_score,
            float(policy.get("pre_evidence_minimum_candidate_score", 78)),
        )

    risk_per_trade_pct = clamp_to_bounds(risk_pct, bounds.get("risk_per_trade_pct"))
    risk_per_trade_pct = min(
        risk_per_trade_pct,
        float(policy.get("hard_max_risk_per_trade_pct", risk_per_trade_pct)),
    )
    return {
        "learning_score": learning_score,
        "target_1_hit_rate": target_rate,
        "partial_win_rate": partial_rate,
        "risk_per_trade_pct": risk_per_trade_pct,
        "target_r_multiple": clamp_to_bounds(target_r, bounds.get("target_r_multiple")),
        "max_new_orders_per_day": int(clamp_to_bounds(max_new, bounds.get("max_new_orders_per_day"))),
        "minimum_candidate_score": clamp_to_bounds(minimum_score, bounds.get("minimum_candidate_score")),
        "completed_tactical_trades": int(completed_tactical_trades or 0),
        "minimum_evidence_target": evidence_target,
        "trades_remaining_to_unlock": max(0, evidence_target - int(completed_tactical_trades or 0)),
        "risk_expansion_unlocked": evidence_unlocked,
        "adaptation_note": (
            "Soft parameters were selected from observed setup outcomes. "
            + (
                "The evidence gate is unlocked. "
                if evidence_unlocked
                else "Risk expansion remains locked until the autonomous tactical sample reaches the evidence target. "
            )
            + "Hard portfolio and live-trading guardrails were not changed."
        ),
    }


def candidate_rejection_reason(idea, policy, adaptive, active_symbols, active_run_ids, used_categories):
    symbol = str(idea.get("symbol") or "").upper().strip()
    if not symbol:
        return "Missing symbol."
    if symbol in active_symbols:
        return "Symbol already has an open or planned paper position."
    if idea.get("run_id") and idea.get("run_id") in active_run_ids:
        return "Committee run is already represented in the journal."
    strategy_plan = idea.get("strategy_plan") or {}
    strategy = strategy_plan.get("selected_family") or "swing"
    if strategy not in set(policy.get("allowed_strategy_families", ["swing"])):
        return f"Strategy {strategy} is not authorized by the autonomous mandate."
    execution_status = strategy_plan.get("execution_status") or "eligible"
    if execution_status != "eligible":
        return f"Strategy {strategy} is not eligible: {strategy_plan.get('execution_status') or 'not routed'}."
    if strategy in {"long_call", "long_put"}:
        if float(idea.get("score") or 0) < float(adaptive.get("minimum_candidate_score") or 0):
            return "Committee strategy score is below the adaptive threshold."
        if strategy == "long_put" and str((idea.get("failed_long_signal") or {}).get("status")) not in {"watch", "strong_watch"}:
            return "Long put requires a confirmed failed-long watch signal."
        category = str(idea.get("category") or "Uncategorized")
        if category_capacity_reached(category, used_categories, policy):
            return "Autonomous category exposure is already at its active-experiment limit."
        return ""
    if str(idea.get("decision")) not in set(policy.get("allowed_decisions", [])):
        return "Decision tier is not authorized for autonomous paper planning."
    if float(idea.get("score") or 0) < float(adaptive.get("minimum_candidate_score") or 0):
        return "Committee score is below the adaptive threshold."
    if str(idea.get("technical_stance") or "").lower() != "bullish":
        return "Technical stance is not bullish."
    if str(idea.get("risk_decision") or "").lower() not in {"approved_for_paper_trade", "conditional_setup"}:
        return "Risk Manager did not authorize a paper or conditional setup."
    if str(idea.get("tradability") or "starter_only") not in set(policy.get("allowed_tradability", [])):
        return "Trade structure is not currently tradable."
    if float(idea.get("reward_to_risk") or 0) < float(policy.get("minimum_reward_to_risk", 0.6)):
        return "Reward/risk is below the autonomous experiment floor."
    category = str(idea.get("category") or "Uncategorized")
    if category_capacity_reached(category, used_categories, policy):
        return "Autonomous category exposure is already at its active-experiment limit."
    entry = to_float(idea.get("suggested_entry") or idea.get("entry_trigger"))
    stop = to_float(idea.get("stop"))
    side = str(idea.get("side") or "long").lower()
    if side not in set(policy.get("allowed_sides", ["long"])):
        return "Side is not authorized by the autonomous mandate."
    if entry <= 0 or stop <= 0 or (side == "long" and stop >= entry):
        return "Entry/stop structure is invalid."
    return ""


def build_order_plan(idea, equity, available_notional, policy, adaptive):
    symbol = str(idea.get("symbol") or "").upper().strip()
    side = str(idea.get("side") or "long").lower()
    entry = to_float(idea.get("suggested_entry") or idea.get("entry_trigger"))
    stop = to_float(idea.get("stop"))
    risk_per_share = abs(entry - stop)
    if not symbol or entry <= 0 or stop <= 0 or risk_per_share <= 0:
        return None

    risk_budget = equity * float(adaptive.get("risk_per_trade_pct") or 0)
    max_notional = min(
        equity * float(policy.get("max_position_notional_pct", 0.03)),
        available_notional,
    )
    shares_by_risk = math.floor(risk_budget / risk_per_share)
    shares_by_notional = math.floor(max_notional / entry)
    shares = int(min(shares_by_risk, shares_by_notional))
    if shares < 1:
        return None

    strategy_plan = idea.get("strategy_plan") or {}
    target_r = float(strategy_plan.get("target_r") or adaptive.get("target_r_multiple") or 0.75)
    adaptive_target = entry + risk_per_share * target_r if side == "long" else entry - risk_per_share * target_r
    committee_target = to_float(idea.get("target_1"))
    if side == "long" and committee_target > entry:
        target = min(committee_target, adaptive_target)
    elif side == "short" and 0 < committee_target < entry:
        target = max(committee_target, adaptive_target)
    else:
        target = adaptive_target
    partial = entry + risk_per_share * 0.5 if side == "long" else entry - risk_per_share * 0.5
    notional = entry * shares
    planned_risk = risk_per_share * shares
    run_id = str(idea.get("run_id") or f"{date.today().isoformat()}-{symbol}-autonomous")
    notes = (
        f"Autonomous paper mandate v{policy.get('version')}. Committee score {float(idea.get('score') or 0):.2f}; "
        f"adaptive risk {float(adaptive.get('risk_per_trade_pct') or 0):.3%}; "
        f"adaptive Target 1 {target:.2f} ({target_r:.2f}R); partial reference {partial:.2f}. "
        "No live broker order is authorized."
    )

    return {
        "symbol": symbol,
        "side": side,
        "entry": round(entry, 4),
        "stop": round(stop, 4),
        "target": round(target, 4),
        "partial": round(partial, 4),
        "shares": shares,
        "notional": round(notional, 2),
        "planned_risk": round(planned_risk, 2),
        "source_run_id": run_id,
        "journal_fields": {
            "symbol": symbol,
            "entry": round(entry, 4),
            "stop": round(stop, 4),
            "target": round(target, 4),
            "shares": shares,
            "side": side,
            "status": "planned",
            "setup_type": f"autonomous_{strategy_plan.get('selected_family') or 'swing'}_experiment",
            "source": "autonomous paper mandate",
            "agent_run_id": run_id,
            "thesis": str(idea.get("reason") or "Committee-qualified autonomous paper experiment."),
            "notes": notes,
        },
    }


def build_option_order_plan(idea, equity, policy):
    strategy_plan = idea.get("strategy_plan") or {}
    strategy = strategy_plan.get("selected_family")
    symbol = str(idea.get("symbol") or "").upper().strip()
    selection = select_defined_loss_contract(symbol, strategy, equity)
    if selection.get("status") != "eligible":
        return {"error": selection.get("reason") or "No option contract passed the execution gate."}
    run_id = str(idea.get("run_id") or f"{date.today().isoformat()}-{symbol}-autonomous-option")
    notes = (
        f"Autonomous paper-only {strategy}; Committee score {float(idea.get('score') or 0):.2f}. "
        f"Contract passed strict DTE, volume, open-interest, spread, IV, and moneyness gates. "
        "Entry is modeled at ask and exits at bid. No live broker order is authorized."
    )
    return {
        "instrument": "option",
        "symbol": symbol,
        "strategy": strategy,
        "contract_symbol": selection["contract_symbol"],
        "expiration": selection["expiration"],
        "strike": selection["strike"],
        "contracts": selection["contracts"],
        "entry_premium": selection["entry_premium"],
        "target_premium": selection["target_premium"],
        "stop_premium": selection["stop_premium"],
        "premium_at_risk": selection["premium_at_risk"],
        "notional": selection["premium_at_risk"],
        "planned_risk": selection["premium_at_risk"],
        "source_run_id": run_id,
        "journal_fields": {
            **selection,
            "status": "planned",
            "source": "autonomous paper mandate",
            "agent_run_id": run_id,
            "thesis": str(idea.get("reason") or "Committee-qualified defined-loss options experiment."),
            "notes": notes,
            "quote_provider": selection.get("provider"),
            "underlying_entry_trigger": (
                to_float(idea.get("stop")) if strategy == "long_put"
                else to_float(idea.get("suggested_entry") or idea.get("entry_trigger"))
            ),
            "underlying_trigger_direction": "at_or_below",
        },
    }


def option_data_unavailable(reason):
    text = str(reason or "").lower()
    markers = (
        "could not fetch",
        "failed to perform",
        "could not resolve",
        "not configured",
        "provider unavailable",
        "quote unavailable",
        "options data unavailable",
    )
    return any(marker in text for marker in markers)


def build_equity_fallback_idea(idea, original_strategy, option_error):
    fallback = dict(idea)
    strategy_plan = dict(idea.get("strategy_plan") or {})
    strategy_plan.update({
        "selected_family": "swing",
        "vehicle": "equity",
        "holding_horizon": "2_to_10_trading_days",
        "target_r": None,
        "fallback_from_strategy": original_strategy,
        "fallback_reason": option_error,
    })
    fallback["strategy_plan"] = strategy_plan
    original_reason = str(idea.get("reason") or "Committee-qualified setup.")
    fallback["reason"] = (
        f"{original_reason} Defined-loss {original_strategy} data was unavailable, so the autonomous "
        "paper mandate used a smaller equity fallback with the original stop and adaptive target."
    )
    return fallback


def build_status_snapshot(policy):
    journal = load_trade_journal()
    feedback = generate_feedback_report()
    active = active_autonomous_orders(journal)
    options_journal = load_options_journal()
    active_options = active_autonomous_options(options_journal)
    closed_count = completed_autonomous_equity_count(journal)
    total_closed = int((feedback.get("trade_expectancy") or {}).get("count") or 0)
    closed_option_count = completed_autonomous_option_count(options_journal)
    total_closed += int((feedback.get("options_expectancy") or {}).get("count") or 0)
    target_samples = int(policy.get("minimum_completed_tactical_trades_for_risk_expansion", 30))
    completed_tactical = closed_count + closed_option_count
    return {
        "enabled": bool(policy.get("enabled")),
        "mode": policy.get("mode"),
        "active_autonomous_orders": len(active),
        "active_autonomous_option_orders": len(active_options),
        "closed_autonomous_trades": closed_count,
        "closed_autonomous_option_trades": closed_option_count,
        "total_closed_trades": total_closed,
        "minimum_evidence_target": target_samples,
        "completed_autonomous_tactical_trades": completed_tactical,
        "trades_remaining_to_target": max(0, target_samples - completed_tactical),
        "risk_expansion_unlocked": completed_tactical >= target_samples,
        "adaptive_parameters": build_adaptive_parameters(feedback, policy, completed_tactical),
        "immutable_guardrails": policy.get("immutable_guardrails", []),
    }


def completed_autonomous_tactical_count(journal=None, options_journal=None):
    journal = load_trade_journal() if journal is None else journal
    options_journal = load_options_journal() if options_journal is None else options_journal
    return completed_autonomous_equity_count(journal) + completed_autonomous_option_count(options_journal)


def completed_autonomous_equity_count(journal):
    if journal is None or journal.empty:
        return 0
    return sum(
        1 for _, row in journal.iterrows()
        if normalize_status(row.get("status")) == "closed"
        and "autonomous paper" in str(row.get("source") or "").lower()
        and "core" not in str(row.get("setup_type") or "").lower()
    )


def completed_autonomous_option_count(journal):
    if journal is None or journal.empty:
        return 0
    return sum(
        1 for _, row in journal.iterrows()
        if normalize_option_status(row.get("status")) == "closed"
        and "autonomous paper" in str(row.get("source") or "").lower()
    )


def active_autonomous_orders(journal):
    rows = []
    if journal is None or journal.empty:
        return rows
    for _, row in journal.iterrows():
        if normalize_status(row.get("status")) not in {"planned", "open"}:
            continue
        if "autonomous paper" not in str(row.get("source") or "").lower():
            continue
        rows.append(row.to_dict())
    return rows


def active_autonomous_options(journal):
    if journal is None or journal.empty:
        return []
    return [
        row.to_dict()
        for _, row in journal.iterrows()
        if normalize_option_status(row.get("status")) in {"planned", "open"}
        and "autonomous paper" in str(row.get("source") or "").lower()
    ]


def autonomous_option_orders_created_on(journal, day):
    if journal is None or journal.empty:
        return []
    return [
        row.to_dict()
        for _, row in journal.iterrows()
        if "autonomous paper" in str(row.get("source") or "").lower()
        and parse_date(row.get("opened_at")) == day
    ]


def active_option_symbols(journal):
    return {
        str(row.get("symbol") or "").upper().strip()
        for _, row in journal.iterrows()
        if normalize_option_status(row.get("status")) in {"planned", "open"}
    } if journal is not None and not journal.empty else set()


def active_option_run_ids(journal):
    return {
        str(row.get("agent_run_id") or "").strip()
        for _, row in journal.iterrows()
        if normalize_option_status(row.get("status")) in {"planned", "open"}
        and str(row.get("agent_run_id") or "").strip()
    } if journal is not None and not journal.empty else set()


def category_counts_for_symbols(brief, symbols):
    categories = {}
    matched_symbols = set()
    for section in (
        "approved_simulated_trades",
        "conditional_setups",
        "worth_watching",
        "bearish_fade_watch",
        "rejected_or_avoid",
        "ideas",
    ):
        for idea in brief.get(section, []) or []:
            symbol = str(idea.get("symbol") or "").upper().strip()
            if symbol in symbols and symbol not in matched_symbols:
                category = str(idea.get("category") or "Uncategorized")
                categories[category] = categories.get(category, 0) + 1
                matched_symbols.add(symbol)
    return categories


def category_capacity_reached(category, category_counts, policy):
    limit = max(1, int(policy.get("max_active_experiments_per_category", 1)))
    if isinstance(category_counts, dict):
        return int(category_counts.get(category, 0)) >= limit
    return category in category_counts and limit <= 1


def autonomous_orders_created_on(journal, day):
    rows = []
    if journal is None or journal.empty:
        return rows
    for _, row in journal.iterrows():
        if "autonomous paper" not in str(row.get("source") or "").lower():
            continue
        if parse_date(row.get("opened_at")) == day:
            rows.append(row.to_dict())
    return rows


def active_trade_symbols(journal):
    if journal is None or journal.empty:
        return set()
    return {
        str(row.get("symbol") or "").upper().strip()
        for _, row in journal.iterrows()
        if normalize_status(row.get("status")) in {"planned", "open"}
    }


def active_trade_run_ids(journal):
    if journal is None or journal.empty:
        return set()
    return {
        str(row.get("agent_run_id") or "").strip()
        for _, row in journal.iterrows()
        if normalize_status(row.get("status")) in {"planned", "open"}
        and str(row.get("agent_run_id") or "").strip()
    }


def macro_event_risk(brief):
    interpretation = ((brief.get("macro") or {}).get("macro_event_interpretation") or {})
    text = " ".join([
        str(interpretation.get("committee_summary") or ""),
        " ".join(interpretation.get("portfolio_implications") or []),
    ]).lower()
    return "event_risk_today" in text or "high-importance macro" in text


def is_regular_market_hours(now=None):
    now = normalize_now(now)
    if now.isoweekday() > 5:
        return False
    return time(9, 30) <= now.time().replace(tzinfo=None) <= time(16, 0)


def normalize_now(now):
    if now is None:
        return datetime.now(EASTERN)
    if now.tzinfo is None:
        return now.replace(tzinfo=EASTERN)
    return now.astimezone(EASTERN)


def load_latest_brief():
    if not MORNING_BRIEF_PATH.exists():
        return {}
    return json.loads(MORNING_BRIEF_PATH.read_text(encoding="utf-8"))


def load_policy():
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def planning_result(status, reason, **extra):
    result = {"status": status, "reason": reason}
    result.update(extra)
    return result


def base_report(action, now, policy):
    return {
        "agent": "Autonomous Paper Mandate",
        "system_role": "paper_execution_and_learning",
        "created_at": now.isoformat(timespec="seconds"),
        "run_id": f"{now.date().isoformat()}-autonomous-paper",
        "action": action,
        "mode": policy.get("mode", "autonomous_paper_only"),
        "status": "ok",
        "planning": None,
        "execution": None,
        "status_snapshot": None,
        "stale_order_cancellations": [],
        "notes": [
            "Autonomy is limited to the simulated ledger.",
            "No live brokerage order or live-money authority exists.",
            "The Committee manages Core ETF Sleeve drift during regular market hours.",
        ],
    }


def finalize_report(report, save_memory):
    output_path = save_autonomy_report(report)
    report["report_path"] = str(output_path)
    if save_memory:
        save_agent_report(
            run_id=report["run_id"],
            agent_name="Autonomous Paper Mandate",
            output=report,
            symbol="PORTFOLIO",
            stance=report["status"],
            confidence=95,
        )
    return report


def save_autonomy_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    latest_json = REPORTS_DIR / "autonomy.json"
    latest_md = REPORTS_DIR / "autonomy.md"
    archive_json = REPORTS_DIR / f"autonomy_{stamp}.json"
    archive_md = REPORTS_DIR / f"autonomy_{stamp}.md"
    markdown = format_autonomy_report(report)
    payload = json.dumps(report, indent=2, default=str)
    latest_json.write_text(payload, encoding="utf-8")
    latest_md.write_text(markdown, encoding="utf-8")
    archive_json.write_text(payload, encoding="utf-8")
    archive_md.write_text(markdown, encoding="utf-8")
    return latest_md


def format_autonomy_report(report):
    lines = [
        "# AIFundOS Autonomous Paper Mandate",
        "",
        f"Created At: {report.get('created_at')}",
        f"Action: {report.get('action')}",
        f"Mode: {report.get('mode')}",
        f"Status: {report.get('status')}",
    ]
    cancellations = report.get("stale_order_cancellations") or []
    if cancellations:
        lines.extend(["", "## Stale Orders Canceled"])
        for cancellation in cancellations:
            lines.append(
                f"- {cancellation.get('symbol')} ({cancellation.get('trade_id')}): "
                f"{cancellation.get('age_days')} days old. {cancellation.get('reason')}"
            )
    planning = report.get("planning") or {}
    if planning:
        lines.extend([
            "",
            "## Planning",
            f"- Status: {planning.get('status')}",
            f"- Result: {planning.get('reason')}",
            f"- Data quality: {planning.get('data_quality_score', 'n/a')}",
        ])
        adaptive = planning.get("adaptive_parameters") or {}
        if adaptive:
            lines.append(
                f"- Adaptive policy: score >= {adaptive.get('minimum_candidate_score')}; "
                f"risk {float(adaptive.get('risk_per_trade_pct') or 0):.3%}; "
                f"Target 1 {adaptive.get('target_r_multiple')}R; "
                f"max new orders {adaptive.get('max_new_orders_per_day')}"
            )
            lines.append(
                f"- Evidence gate: {adaptive.get('completed_tactical_trades', 0)}/"
                f"{adaptive.get('minimum_evidence_target', 30)} completed autonomous tactical trades; "
                f"risk expansion {'unlocked' if adaptive.get('risk_expansion_unlocked') else 'locked'}"
            )
        for order in planning.get("created_orders", []):
            if order.get("instrument") == "option":
                lines.append(
                    f"- {order['symbol']} {order['strategy']}: planned {order['contracts']} contract(s) "
                    f"{order['contract_symbol']} at premium {order['entry_premium']}; "
                    f"stop {order['stop_premium']}; target {order['target_premium']}; "
                    f"maximum premium risk ${order['premium_at_risk']:.2f}; trade {order['trade_id']}"
                )
            else:
                lines.append(
                    f"- {order['symbol']}: planned {order['shares']} shares at {order['entry']}; "
                    f"stop {order['stop']}; target {order['target']}; risk ${order['planned_risk']:.2f}; "
                    f"trade {order['trade_id']}"
                )
    execution = report.get("execution") or {}
    if execution:
        lines.extend([
            "",
            "## Execution",
            f"- Market session: {execution.get('market_session')}",
            f"- Result: {execution.get('reason')}",
        ])
        for event in execution.get("applied_events", []):
            lines.append(
                f"- {event.get('symbol')} {event.get('event_type')} at {event.get('fill_price')}: {event.get('reason')}"
                )
        core = execution.get("core_rebalance") or {}
        if core:
            lines.append(f"- Core ETF sleeve: {core.get('status')} - {core.get('reason')}")
            for action in core.get("actions", []):
                lines.append(
                    f"  - {str(action.get('action') or '').upper()} {action.get('shares')} "
                    f"{action.get('symbol')} at ${float(action.get('price') or 0):.2f}"
                )
    snapshot = report.get("status_snapshot") or {}
    if snapshot:
        lines.extend([
            "",
            "## Evidence Progress",
            f"- Active autonomous orders: {snapshot.get('active_autonomous_orders')}",
            f"- Active autonomous option orders: {snapshot.get('active_autonomous_option_orders')}",
            f"- Closed autonomous trades: {snapshot.get('closed_autonomous_trades')}",
            f"- Closed autonomous option trades: {snapshot.get('closed_autonomous_option_trades')}",
            f"- Completed autonomous tactical trades: {snapshot.get('completed_autonomous_tactical_trades')}",
            f"- Total closed trades: {snapshot.get('total_closed_trades')}",
            f"- Remaining to minimum evidence target: {snapshot.get('trades_remaining_to_target')}",
        ])
    lines.extend(["", "## Guardrails"])
    lines.extend([f"- {item}" for item in report.get("notes", [])])
    return "\n".join(lines) + "\n"


def parse_date(value):
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def clamp_to_bounds(value, bounds):
    value = float(value)
    if not bounds or len(bounds) != 2:
        return value
    return max(float(bounds[0]), min(float(bounds[1]), value))
