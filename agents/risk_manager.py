import json
from datetime import date, datetime
from pathlib import Path

from agents.technical_analyst import analyze_technical_setup
from data.economic_calendar import format_calendar_event, get_economic_calendar
from data.earnings_calendar import get_earnings_calendar
from data.portfolio import analyze_portfolio_exposure
from data.trade_journal import load_trade_journal, summarize_trade_journal
from memory.research_memory import get_recent_agent_reports, get_recent_daily_setup_reviews


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports" / "risk"
RISK_POLICY_PATH = PROJECT_ROOT / "framework" / "risk_policy.json"
_SETUP_LEARNING_CACHE = None
_TOP_PICK_SCENARIO_CACHE = None


def load_risk_policy():
    return json.loads(RISK_POLICY_PATH.read_text(encoding="utf-8"))


def evaluate_trade_risk(ticker, technical_report=None, policy=None):
    ticker = ticker.upper().strip()
    policy = policy or load_risk_policy()
    technical_report = technical_report or analyze_technical_setup(ticker)
    earnings = get_earnings_calendar(ticker)
    economic_calendar = get_economic_calendar(days_ahead=7, days_back=0)
    portfolio_exposure = analyze_portfolio_exposure(
        ticker,
        correlated_symbols=policy["ai_semi_correlated_symbols"],
    )
    journal_summary = summarize_trade_journal(load_trade_journal())
    weekly_memory = load_latest_weekly_memory()
    setup_learning_memory = load_setup_learning_memory()
    top_pick_scenario_memory = load_top_pick_scenario_memory()
    failed_long_memory = load_failed_long_memory(ticker)

    if technical_report.get("error"):
        return build_veto_report(
            ticker=ticker,
            policy=policy,
            technical_report=technical_report,
            reasons=[f"No reliable data for symbol: {technical_report['error']}"],
        )

    setup = technical_report["setup"]
    entry = setup.get("entry_trigger")
    alternative_entry = setup.get("alternative_entry")
    stop = setup.get("stop")
    target = setup.get("target_1")
    target_2 = setup.get("target_2")
    target_3 = setup.get("target_3")
    reward_to_risk_value = setup.get("reward_to_risk")
    reward_to_risk_to_target_2 = setup.get("reward_to_risk_to_target_2")
    reward_to_risk_to_target_3 = setup.get("reward_to_risk_to_target_3")
    pullback_reward_to_risk = setup.get("pullback_reward_to_risk")
    atr_14 = (technical_report.get("momentum") or {}).get("atr_14")

    vetoes = []
    conditional_issues = []
    warnings = []

    if entry is None or stop is None or target is None:
        vetoes.append("Missing entry, stop, or target.")
    elif stop >= entry:
        vetoes.append("Invalid stop relative to entry.")

    if reward_to_risk_value is None:
        conditional_issues.append("Reward-to-risk unavailable.")
    elif reward_to_risk_value < policy["minimum_reward_to_risk"]:
        conditional_issues.append(
            f"Original reward-to-risk {reward_to_risk_value:.2f} is below minimum "
            f"{policy['minimum_reward_to_risk']:.2f}."
        )

    if technical_report["stance"] == "no_trade":
        conditional_issues.append("Technical Analyst stance is no_trade.")
    elif technical_report["stance"] == "bearish":
        vetoes.append("Technical Analyst stance is bearish; long simulated trade is blocked.")

    apply_setup_learning_risk(
        policy=policy,
        setup_learning=setup_learning_memory,
        conditional_issues=conditional_issues,
        warnings=warnings,
    )
    apply_top_pick_scenario_risk(
        top_pick_scenario_memory=top_pick_scenario_memory,
        conditional_issues=conditional_issues,
        warnings=warnings,
    )

    position = calculate_position_size(entry, stop, policy)

    if position.get("error"):
        vetoes.append(position["error"])
    elif position.get("size_limited_by_exposure"):
        warnings.append(
            "Position size was capped by max single-position exposure."
        )

    if ticker in policy["ai_semi_correlated_symbols"]:
        warnings.append(
            "Ticker is in the AI/semi correlated universe; CIO/Portfolio Manager must check aggregate exposure."
        )

    earnings_days = earnings.get("days_until_earnings")
    if earnings_days is None:
        warnings.append("Earnings date unavailable; event risk is unknown.")
    elif 0 <= earnings_days <= 7:
        vetoes.append(f"Earnings are within {earnings_days} days; no new swing trade without explicit approval.")
    elif 0 <= earnings_days <= 14:
        warnings.append(f"Earnings are within {earnings_days} days; reduce confidence or require explicit approval.")

    calendar_missing_information = []
    evaluate_economic_event_risk(economic_calendar, warnings, calendar_missing_information)

    if portfolio_exposure["correlated_exposure_pct"] > 40:
        vetoes.append(
            f"Correlated AI/semi exposure is {portfolio_exposure['correlated_exposure_pct']:.2f}%, above 40% limit."
        )
    elif portfolio_exposure["correlated_exposure_pct"] > 25:
        warnings.append(
            f"Correlated AI/semi exposure is {portfolio_exposure['correlated_exposure_pct']:.2f}%; watch concentration."
        )

    evaluate_journal_risk(policy, journal_summary, vetoes, warnings)
    evaluate_weekly_memory_risk(weekly_memory, conditional_issues, warnings)

    conditional_plan = build_conditional_plan(
        entry=entry,
        alternative_entry=alternative_entry,
        stop=stop,
        target=target,
        target_2=target_2,
        target_3=target_3,
        minimum_reward_to_risk=policy["minimum_reward_to_risk"],
        reward_to_risk=reward_to_risk_value,
        reward_to_risk_to_target_2=reward_to_risk_to_target_2,
        reward_to_risk_to_target_3=reward_to_risk_to_target_3,
        pullback_reward_to_risk=pullback_reward_to_risk,
    )
    setup_calibration = calibrate_setup_structure(
        entry=conditional_plan.get("suggested_entry") or entry,
        stop=stop,
        target=target,
        atr=atr_14,
        technical_stance=technical_report.get("stance"),
        setup_learning=setup_learning_memory,
        policy=policy,
    )
    apply_setup_calibration_risk(setup_calibration, conditional_issues, warnings)
    failed_long_signal = evaluate_failed_long_signal(
        technical_report=technical_report,
        setup_calibration=setup_calibration,
        failed_long_memory=failed_long_memory,
        policy=policy,
    )
    if failed_long_signal.get("status") in {"watch", "strong_watch"}:
        warnings.append(failed_long_signal["summary"])

    effective_target_1 = setup_calibration.get("calibrated_target_1") or target
    effective_reward_to_risk = setup_calibration.get("calibrated_reward_to_risk")
    if effective_reward_to_risk is None:
        effective_reward_to_risk = reward_to_risk_value

    if vetoes:
        decision = "veto"
        confidence = 0.35
    elif conditional_issues:
        decision = "conditional_setup" if technical_report["stance"] in {"bullish", "neutral"} else "watchlist_setup"
        confidence = 0.5 if decision == "conditional_setup" else 0.4
    else:
        decision = "approved_for_paper_trade"
        confidence = 0.65

    return {
        "agent": "Risk Manager",
        "system_role": "committee_agent",
        "run_id": datetime.now().strftime("%Y-%m-%d-risk"),
        "symbol": ticker,
        "decision": decision,
        "confidence": confidence,
        "policy_version": policy["version"],
        "technical_stance": technical_report["stance"],
        "entry": entry,
        "alternative_entry": alternative_entry,
        "stop": stop,
        "target_1": effective_target_1,
        "original_target_1": target,
        "calibrated_target_1": effective_target_1,
        "target_2": target_2,
        "target_3": target_3,
        "reward_to_risk": effective_reward_to_risk,
        "original_reward_to_risk": reward_to_risk_value,
        "reward_to_risk_to_target_2": reward_to_risk_to_target_2,
        "reward_to_risk_to_target_3": reward_to_risk_to_target_3,
        "pullback_reward_to_risk": pullback_reward_to_risk,
        "position": position,
        "earnings": earnings,
        "economic_calendar": economic_calendar,
        "portfolio_exposure": portfolio_exposure,
        "journal_summary": journal_summary,
        "weekly_memory": weekly_memory,
        "setup_learning_memory": setup_learning_memory,
        "top_pick_scenario_memory": top_pick_scenario_memory,
        "failed_long_memory": failed_long_memory,
        "vetoes": vetoes,
        "conditional_issues": conditional_issues,
        "conditional_plan": conditional_plan,
        "setup_calibration": setup_calibration,
        "failed_long_signal": failed_long_signal,
        "trade_vehicle_options": build_trade_vehicle_options(ticker, setup_calibration, technical_report, failed_long_signal),
        "warnings": warnings,
        "missing_information": [
            *calendar_missing_information,
        ],
        "citations": technical_report.get("citations", []),
    }


def load_latest_weekly_memory():
    reports = get_recent_agent_reports(agent_name="Weekly Review", symbol="MARKET", limit=1)
    if not reports:
        return {
            "status": "missing",
            "message": "No weekly review memory found yet.",
        }

    output = reports[0].get("output") or {}
    setup_summary = output.get("setup_summary") or {}
    accuracy = output.get("accuracy_review") or {}
    return {
        "status": "available",
        "run_id": reports[0].get("run_id"),
        "created_at": reports[0].get("created_at"),
        "week_start": output.get("week_start"),
        "week_end": output.get("week_end"),
        "dominant_read": accuracy.get("dominant_read"),
        "entries_triggered": setup_summary.get("entries_triggered"),
        "target_1_hit_rate": setup_summary.get("target_hit_rate_on_entries_pct"),
        "avg_entered_pnl_pct": setup_summary.get("avg_entered_pnl_pct"),
        "lessons": output.get("lessons", []),
    }


def load_setup_learning_memory():
    global _SETUP_LEARNING_CACHE
    if _SETUP_LEARNING_CACHE is not None:
        return _SETUP_LEARNING_CACHE

    reviews = get_recent_daily_setup_reviews(limit=100)
    if not reviews:
        _SETUP_LEARNING_CACHE = {
            "status": "missing",
            "reviewed_setups": 0,
            "entries_triggered": 0,
            "target_1_hit_rate": 0,
            "partial_win_rate": 0,
            "avg_entered_pnl_pct": 0,
            "avg_max_favorable_move_pct": None,
            "avg_max_adverse_move_pct": None,
            "learning_score": 0,
            "read": "No daily setup review memory available yet.",
        }
        return _SETUP_LEARNING_CACHE

    entered = [item for item in reviews if item.get("entered")]
    target_hits = [item for item in entered if item.get("hit_target_1")]
    partial_hits = [item for item in entered if item.get("hit_partial_win") or (item.get("output") or {}).get("hit_partial_win")]
    outputs = [item.get("output") or {} for item in reviews]
    pnl_values = [float(item.get("pnl_pct")) for item in entered if item.get("pnl_pct") is not None]
    mfe_values = [
        float(item.get("max_favorable_move_pct"))
        for item in outputs
        if item.get("entered") and item.get("max_favorable_move_pct") is not None
    ]
    mae_values = [
        float(item.get("max_adverse_move_pct"))
        for item in outputs
        if item.get("entered") and item.get("max_adverse_move_pct") is not None
    ]

    reviewed_count = len(reviews)
    entered_count = len(entered)
    target_rate = pct(len(target_hits), entered_count)
    partial_rate = pct(len(partial_hits), entered_count)
    avg_pnl = average(pnl_values) or 0
    avg_mfe = average(mfe_values)
    avg_mae = average(mae_values)
    learning_score = calculate_setup_learning_score(
        reviewed_count=reviewed_count,
        entered_count=entered_count,
        target_rate=target_rate,
        partial_rate=partial_rate,
        avg_pnl=avg_pnl,
        avg_mfe=avg_mfe,
        avg_mae=avg_mae,
    )

    _SETUP_LEARNING_CACHE = {
        "status": "available",
        "reviewed_setups": reviewed_count,
        "entries_triggered": entered_count,
        "target_1_hit_rate": target_rate,
        "partial_win_rate": partial_rate,
        "avg_entered_pnl_pct": avg_pnl,
        "avg_max_favorable_move_pct": avg_mfe,
        "avg_max_adverse_move_pct": avg_mae,
        "learning_score": learning_score,
        "read": setup_learning_read(learning_score, entered_count),
    }
    return _SETUP_LEARNING_CACHE


def load_top_pick_scenario_memory():
    global _TOP_PICK_SCENARIO_CACHE
    if _TOP_PICK_SCENARIO_CACHE is not None:
        return _TOP_PICK_SCENARIO_CACHE

    reports = get_recent_agent_reports(agent_name="Top Pick Scenario Backtest", symbol="MARKET", limit=1)
    if not reports:
        _TOP_PICK_SCENARIO_CACHE = {
            "status": "missing",
            "message": "No top-pick scenario backtest memory available yet.",
        }
        return _TOP_PICK_SCENARIO_CACHE

    output = reports[0].get("output") or {}
    summaries = output.get("summary_by_scenario") or []
    by_id = {item.get("scenario_id"): item for item in summaries}
    best = summaries[0] if summaries else {}
    wait_calibrated = by_id.get("wait_calibrated_target") or {}
    buy_open = by_id.get("buy_open_calibrated") or {}

    _TOP_PICK_SCENARIO_CACHE = {
        "status": "available",
        "run_id": reports[0].get("run_id"),
        "created_at": reports[0].get("created_at"),
        "window": {
            "start_date": output.get("start_date"),
            "end_date": output.get("end_date"),
        },
        "briefs_reviewed": output.get("briefs_reviewed"),
        "best_scenario": best.get("scenario"),
        "best_total_gross_pnl": best.get("total_gross_pnl_dollars"),
        "wait_calibrated_pnl": wait_calibrated.get("total_gross_pnl_dollars"),
        "wait_calibrated_target_hit_rate": wait_calibrated.get("target_hit_rate_pct"),
        "buy_open_pnl": buy_open.get("total_gross_pnl_dollars"),
        "buy_open_win_rate": buy_open.get("win_rate_pct"),
        "lesson": "Recent #1-pick backtest favored waiting for planned entry zones over buying/chasing at the open; use calibrated Target 1 as the first realistic exit and original targets as stretch objectives.",
        "learning_notes": output.get("learning_notes", []),
    }
    return _TOP_PICK_SCENARIO_CACHE


def load_failed_long_memory(ticker, limit=500):
    ticker = ticker.upper().strip()
    reviews = get_recent_daily_setup_reviews(limit=limit)
    symbol_rows = [
        item for item in reviews
        if item.get("symbol") == ticker and item.get("entered")
    ]
    categories = [
        (item.get("output") or {}).get("category")
        for item in symbol_rows
        if (item.get("output") or {}).get("category")
    ]
    category = categories[0] if categories else None
    category_rows = [
        item for item in reviews
        if item.get("entered") and category and (item.get("output") or {}).get("category") == category
    ]
    symbol_severe = severe_adverse_rows(symbol_rows)
    category_severe = severe_adverse_rows(category_rows)

    return {
        "symbol": ticker,
        "category": category,
        "symbol_reviewed_entries": len(symbol_rows),
        "symbol_severe_adverse_count": len(symbol_severe),
        "symbol_avg_max_adverse_move_pct": average([
            (item.get("output") or {}).get("max_adverse_move_pct")
            for item in symbol_rows
        ]),
        "category_reviewed_entries": len(category_rows),
        "category_severe_adverse_count": len(category_severe),
        "category_avg_max_adverse_move_pct": average([
            (item.get("output") or {}).get("max_adverse_move_pct")
            for item in category_rows
        ]),
        "examples": [
            {
                "review_date": item.get("review_date"),
                "symbol": item.get("symbol"),
                "pnl_pct": item.get("pnl_pct"),
                "max_adverse_move_pct": (item.get("output") or {}).get("max_adverse_move_pct"),
            }
            for item in symbol_severe[:3]
        ],
    }


def severe_adverse_rows(rows, threshold=-8):
    severe = []
    for item in rows:
        output = item.get("output") or {}
        adverse = output.get("max_adverse_move_pct")
        pnl = item.get("pnl_pct")
        if adverse is not None and adverse <= threshold:
            severe.append(item)
        elif pnl is not None and pnl <= threshold:
            severe.append(item)
    return severe


def calculate_setup_learning_score(reviewed_count, entered_count, target_rate, partial_rate, avg_pnl, avg_mfe, avg_mae):
    if reviewed_count == 0:
        return 0
    sample_score = min(25, reviewed_count / 40 * 25)
    target_score = min(20, target_rate * 0.20)
    partial_score = min(15, partial_rate * 0.15)
    pnl_score = clamp(15 + avg_pnl, 0, 25)
    excursion_score = 10
    if avg_mfe is not None and avg_mae is not None:
        excursion_score = clamp(10 + avg_mfe + avg_mae, 0, 25)
    score = sample_score + target_score + partial_score + pnl_score + excursion_score
    if entered_count == 0:
        score = min(score, 40)
    return round(clamp(score, 0, 100), 1)


def setup_learning_read(score, entered_count):
    if entered_count == 0:
        return "Setup memory exists, but no entries have triggered yet."
    if score >= 70:
        return "Setup-review behavior is constructive; normal paper sizing can be considered."
    if score >= 55:
        return "Setup-review behavior is improving, but still requires disciplined confirmation."
    return "Setup-review behavior is weak; tighten entries, target realism, and risk filters."


def apply_setup_learning_risk(policy, setup_learning, conditional_issues, warnings):
    proving = policy.get("proving_mode") or {}
    if not proving.get("enabled"):
        return

    learning_score = float((setup_learning or {}).get("learning_score") or 0)
    target_rate = float((setup_learning or {}).get("target_1_hit_rate") or 0)
    min_score = float(proving.get("minimum_learning_score_for_approval", 55))
    min_target_rate = float(proving.get("minimum_target_1_hit_rate_for_approval", 20))
    partial_rate = float((setup_learning or {}).get("partial_win_rate") or 0)

    warnings.append("Proving mode is active: paper trades use reduced size until setup edge improves.")
    if learning_score < min_score:
        conditional_issues.append(
            f"Committee learning score {learning_score:.1f}/100 is below approval threshold "
            f"{min_score:.1f}; require tighter entry confirmation."
        )
    if target_rate < min_target_rate and partial_rate < min_target_rate:
        conditional_issues.append(
            f"Recent Target 1 hit rate {target_rate:.1f}% is below approval threshold "
            f"{min_target_rate:.1f}%; discount ambitious targets."
        )
    elif target_rate < min_target_rate:
        warnings.append(
            f"Target 1 hit rate is low ({target_rate:.1f}%), but partial-win rate is {partial_rate:.1f}%; "
            "favor staged exits and calibrated first targets."
        )


def apply_top_pick_scenario_risk(top_pick_scenario_memory, conditional_issues, warnings):
    if not top_pick_scenario_memory or top_pick_scenario_memory.get("status") != "available":
        warnings.append("No top-pick scenario backtest memory available yet; do not assume chasing the open has edge.")
        return

    wait_pnl = top_pick_scenario_memory.get("wait_calibrated_pnl")
    buy_open_pnl = top_pick_scenario_memory.get("buy_open_pnl")
    if wait_pnl is not None and buy_open_pnl is not None and float(wait_pnl) > float(buy_open_pnl):
        warnings.append(
            "Top-pick scenario memory favors waiting for the planned entry zone over buying/chasing at the open."
        )
    target_rate = top_pick_scenario_memory.get("wait_calibrated_target_hit_rate")
    if target_rate is not None:
        warnings.append(
            f"Recent #1-pick calibrated Target 1 hit rate was {float(target_rate):.1f}%; "
            "treat calibrated Target 1 as the first realistic objective and original targets as stretch targets."
        )


def calibrate_setup_structure(entry, stop, target, atr, technical_stance, setup_learning, policy):
    proving = policy.get("proving_mode") or {}
    max_target_atr = float(proving.get("max_target_1_atr", 2.25))
    target_r = float(proving.get("target_1_r_multiple", 1.0))
    min_stop_atr = float(proving.get("minimum_stop_atr", 0.5))
    max_stop_atr = float(proving.get("maximum_stop_atr", 2.75))
    partial_r = float(proving.get("partial_win_r_multiple", 0.5))
    learning_score = float((setup_learning or {}).get("learning_score") or 0)
    target_rate = float((setup_learning or {}).get("target_1_hit_rate") or 0)
    partial_rate = float((setup_learning or {}).get("partial_win_rate") or 0)

    if not entry or not stop or not target:
        return {
            "classification": "unstructured",
            "message": "Missing entry, stop, or target; monitor only.",
            "calibrated_target_1": target,
            "partial_win_level": None,
            "stop_atr": None,
            "target_atr": None,
        }

    entry = float(entry)
    stop = float(stop)
    target = float(target)
    risk_per_share = entry - stop
    partial_win_level = entry + (risk_per_share * partial_r) if risk_per_share > 0 else None

    if not atr or atr <= 0:
        return {
            "classification": "interesting_not_tradable",
            "message": "ATR unavailable; cannot calibrate target realism.",
            "calibrated_target_1": target,
            "partial_win_level": partial_win_level,
            "stop_atr": None,
            "target_atr": None,
            "learning_score": learning_score,
            "target_1_hit_rate": target_rate,
        }

    atr = float(atr)
    stop_atr = abs(entry - stop) / atr
    target_atr = abs(target - entry) / atr
    target_distance = min(max_target_atr * atr, target_r * risk_per_share)
    calibrated_target = min(target, entry + target_distance)
    calibrated_target_atr = abs(calibrated_target - entry) / atr
    calibrated_reward_to_risk = (calibrated_target - entry) / risk_per_share if risk_per_share > 0 else None
    issues = []

    if stop_atr < min_stop_atr:
        issues.append("stop_too_tight")
    if stop_atr > max_stop_atr:
        issues.append("stop_too_wide")
    if target_atr > max_target_atr:
        issues.append("target_too_far")
    if technical_stance not in {"bullish", "neutral"}:
        issues.append("weak_technical_stance")

    structural_issues = [issue for issue in issues if issue != "target_too_far"]
    if structural_issues:
        classification = "interesting_not_tradable"
    elif learning_score < float(proving.get("minimum_learning_score_for_approval", 55)):
        classification = "starter_only"
    elif (
        target_rate < float(proving.get("minimum_target_1_hit_rate_for_approval", 20))
        and partial_rate < float(proving.get("minimum_target_1_hit_rate_for_approval", 20))
    ):
        classification = "starter_only"
    else:
        classification = "conditional_trade_candidate"

    messages = {
        "conditional_trade_candidate": "Structure is realistic enough for conditional paper-trade review.",
        "starter_only": "Structure is realistic, but AIFundOS is still proving edge; use starter size or wait for confirmation.",
        "interesting_not_tradable": "Interesting idea, but current entry/stop/target geometry is not clean enough.",
    }
    if "target_too_far" in issues:
        target_message = (
            f"Original Target 1 is {target_atr:.2f} ATR away; use calibrated first target "
            f"near {calibrated_target:.2f} before treating it as tradable."
        )
        messages["conditional_trade_candidate"] = target_message
        messages["starter_only"] = target_message
        messages["interesting_not_tradable"] = target_message
    if "stop_too_tight" in issues:
        messages["interesting_not_tradable"] = "Stop is too tight versus ATR; normal noise could trigger it."
    if "stop_too_wide" in issues:
        messages["interesting_not_tradable"] = "Stop is too wide versus ATR; position sizing/edge is poor."

    return {
        "classification": classification,
        "message": messages.get(classification),
        "issues": issues,
        "learning_score": learning_score,
        "target_1_hit_rate": target_rate,
        "partial_win_rate": partial_rate,
        "atr_14": atr,
        "stop_atr": stop_atr,
        "target_atr": target_atr,
        "max_target_1_atr": max_target_atr,
        "target_1_r_multiple": target_r,
        "calibrated_target_1": calibrated_target,
        "calibrated_target_1_atr": calibrated_target_atr,
        "calibrated_reward_to_risk": calibrated_reward_to_risk,
        "original_target_1": target,
        "partial_win_level": partial_win_level,
        "partial_win_r_multiple": partial_r,
    }


def apply_setup_calibration_risk(calibration, conditional_issues, warnings):
    classification = (calibration or {}).get("classification")
    message = (calibration or {}).get("message")
    if classification == "interesting_not_tradable":
        conditional_issues.append(message or "Setup is interesting, but not tradable under current calibration.")
    elif classification == "starter_only":
        warnings.append(message or "Use starter size until setup edge improves.")


def build_trade_vehicle_options(ticker, calibration, technical_report, failed_long_signal=None):
    classification = (calibration or {}).get("classification")
    stance = technical_report.get("stance")
    failed_long_signal = failed_long_signal or {}
    options = [{
        "vehicle": "watch_only",
        "status": "available",
        "use_when": "Idea is interesting but entry, target, or stop is not clean enough.",
    }]

    if classification in {"conditional_trade_candidate", "starter_only"}:
        options.append({
            "vehicle": "stock_starter_position",
            "status": "conditional",
            "use_when": "Human approves the setup and price reaches the calibrated entry zone.",
        })

    if ticker in {"SPY", "VOO", "QQQ", "SMH", "XLK", "XLE", "XLF", "XLV", "XLI", "XLP", "XLRE", "XLU", "XLY", "IWM", "DIA"}:
        options.append({
            "vehicle": "core_etf_or_sector_etf",
            "status": "preferred_for_broad_theme",
            "use_when": "The thesis is about broad exposure rather than single-name edge.",
        })
    elif stance in {"bullish", "neutral"}:
        options.append({
            "vehicle": "theme_etf_expression",
            "status": "consider",
            "use_when": "Single-name setup is noisy but the sector/theme is acting well.",
        })

    if failed_long_signal.get("status") in {"watch", "strong_watch"}:
        options.extend([
            {
                "vehicle": "avoid_or_cancel_long",
                "status": "preferred_risk_control",
                "use_when": "Long thesis has weak structure or is starting to fail; avoid forcing a buy.",
            },
            {
                "vehicle": "bear_put_spread_watch",
                "status": "watch_only",
                "use_when": "Defined-risk bearish expression if breakdown confirmation and options liquidity are available.",
            },
            {
                "vehicle": "short_stock_watch_only",
                "status": "watch_only_high_risk",
                "use_when": "Only for confirmed breakdowns; short shares carry open-ended risk.",
            },
        ])

    options.append({
        "vehicle": "options_watch_only",
        "status": "not_execution_ready",
        "use_when": "Options may express defined risk, but AIFundOS lacks execution-grade greeks/flow data today.",
    })
    return options


def evaluate_failed_long_signal(technical_report, setup_calibration, failed_long_memory, policy):
    detector = policy.get("failed_long_detector") or {}
    if not detector.get("enabled", True):
        return {
            "status": "disabled",
            "score": 0,
            "summary": "Failed-long detector is disabled.",
            "evidence": [],
            "possible_trade_options": [],
        }

    setup = technical_report.get("setup") or {}
    trend = technical_report.get("trend") or {}
    momentum = technical_report.get("momentum") or {}
    relative_strength = technical_report.get("relative_strength") or {}
    calibration = setup_calibration or {}
    failed_long_memory = failed_long_memory or {}
    score = 0
    evidence = []

    reward_to_risk = (
        calibration.get("calibrated_reward_to_risk")
        or setup.get("pullback_reward_to_risk")
        or setup.get("reward_to_risk")
    )
    if reward_to_risk is not None and reward_to_risk < float(detector.get("weak_reward_to_risk_below", 0.8)):
        score += 18
        evidence.append(f"Long reward/risk is weak ({reward_to_risk:.2f}).")

    target_atr = calibration.get("target_atr")
    if target_atr is not None and target_atr >= float(detector.get("extended_target_atr", 3.0)):
        score += 10
        evidence.append(f"Original target is stretched ({target_atr:.2f} ATR).")

    calibration_issues = set(calibration.get("issues") or [])
    if calibration.get("classification") == "interesting_not_tradable":
        if calibration_issues.intersection({"target_too_far", "stop_too_wide", "weak_technical_stance"}):
            score += 10
            evidence.append("Long setup is interesting but not cleanly tradable.")
        elif "stop_too_tight" in calibration_issues:
            score += 4
            evidence.append("Long setup has a tight stop, but that is not enough by itself to imply a fade.")

    if trend.get("above_20dma") is False:
        score += 10
        evidence.append("Price is below the 20-day moving average.")
    if trend.get("above_50dma") is False:
        score += 10
        evidence.append("Price is below the 50-day moving average.")

    rsi = momentum.get("rsi_14")
    if rsi is not None and rsi > 72:
        score += 8
        evidence.append(f"RSI is extended ({rsi:.1f}); reversal risk is higher.")
    elif rsi is not None and rsi < 45:
        score += 8
        evidence.append(f"RSI is weak ({rsi:.1f}).")

    macd_histogram = momentum.get("macd_histogram")
    if macd_histogram is not None and macd_histogram < 0:
        score += 8
        evidence.append("MACD histogram is negative.")

    rel_spy = relative_strength.get("relative_to_spy_20d")
    if rel_spy is not None and rel_spy < 0:
        score += 10
        evidence.append(f"20-day relative strength versus SPY is negative ({rel_spy:.2f}%).")

    if failed_long_memory.get("symbol_severe_adverse_count", 0) > 0:
        score += 22
        evidence.append(
            f"Recent setup memory shows severe adverse movement in {failed_long_memory.get('symbol')} "
            f"after similar long ideas."
        )
    if failed_long_memory.get("category_severe_adverse_count", 0) >= 2:
        score += 10
        evidence.append(
            f"Recent setup memory shows repeated severe adverse movement in "
            f"{failed_long_memory.get('category') or 'this category'}."
        )

    stance = technical_report.get("stance")
    if stance == "bearish":
        score += 20
        evidence.append("Technical stance is bearish.")
    elif stance == "no_trade":
        score += 12
        evidence.append("Technical stance is no_trade.")
    elif stance == "bullish" and not calibration_issues.intersection({"target_too_far", "stop_too_wide", "weak_technical_stance"}):
        score -= 15
        evidence.append("Bullish stance offsets standalone fade risk.")

    strong_score = float(detector.get("strong_score", 75))
    watch_score = float(detector.get("watch_score", 55))
    score = max(0, score)
    if score >= strong_score:
        status = "strong_watch"
        summary = "Possible failed-long / bearish reversal candidate; avoid long unless evidence improves."
    elif score >= watch_score:
        status = "watch"
        summary = "Long setup has fade risk; monitor for failed breakout or breakdown confirmation."
    else:
        status = "low"
        summary = "No strong failed-long signal."

    return {
        "status": status,
        "score": round(score, 1),
        "summary": summary,
        "evidence": evidence,
        "memory": failed_long_memory,
        "possible_trade_options": build_failed_long_trade_options(status),
    }


def build_failed_long_trade_options(status):
    if status not in {"watch", "strong_watch"}:
        return []
    return [
        {
            "vehicle": "avoid_or_cancel_long",
            "status": "preferred_risk_control",
            "reason": "No capital has to be deployed when a long setup is structurally weak.",
        },
        {
            "vehicle": "bear_put_spread",
            "status": "watch_only",
            "reason": "Defined-risk downside expression if breakdown confirms and options liquidity is acceptable.",
        },
        {
            "vehicle": "long_put",
            "status": "watch_only_high_premium_risk",
            "reason": "Can benefit from sharp downside but may overpay without IV/greeks data.",
        },
        {
            "vehicle": "short_shares",
            "status": "watch_only_high_risk",
            "reason": "Clean downside expression but carries open-ended risk.",
        },
    ]


def evaluate_weekly_memory_risk(weekly_memory, conditional_issues, warnings):
    if not weekly_memory or weekly_memory.get("status") != "available":
        warnings.append("No weekly self-review memory available yet; do not loosen setup standards.")
        return

    dominant_read = str(weekly_memory.get("dominant_read") or "").upper()
    target_rate = weekly_memory.get("target_1_hit_rate")
    avg_pnl = weekly_memory.get("avg_entered_pnl_pct")

    if dominant_read == "MORE MISSES THAN ACCURATE":
        conditional_issues.append(
            "Recent weekly self-review showed more misses than accurate calls; require stricter entry confirmation."
        )
    if target_rate == 0:
        warnings.append(
            "Recent weekly review had 0% Target 1 hit rate on entered setups; discount ambitious targets."
        )
    if avg_pnl is not None and avg_pnl < 0:
        warnings.append(
            f"Recent weekly review average entered setup P&L was {avg_pnl:.2f}%; avoid easy pullback triggers."
        )


def evaluate_economic_event_risk(economic_calendar, warnings, missing_information):
    if not economic_calendar or economic_calendar.get("status") == "not_configured":
        missing_information.append("Economic event calendar is not connected yet.")
        return

    if economic_calendar.get("error"):
        warnings.append(f"Economic event calendar unavailable: {economic_calendar['error']}")
        return

    summary = economic_calendar.get("summary") or {}
    high_today = summary.get("high_importance_events_today") or []
    next_event = summary.get("next_high_importance_event")

    if high_today:
        warnings.append(
            "High-importance macro event risk today; require explicit approval for new swing trades."
        )
        return

    if not next_event:
        return

    days_until = days_until_event(next_event)
    if days_until is not None and 0 <= days_until <= 2:
        warnings.append(
            f"High-importance macro event within {days_until} day(s): "
            f"{format_calendar_event(next_event)}."
        )


def evaluate_journal_risk(policy, summary, vetoes, warnings):
    account_size = policy["paper_account_size"]
    daily_loss_limit = account_size * policy.get("max_daily_realized_loss_pct", 0.01)
    weekly_loss_limit = account_size * policy.get("max_weekly_realized_loss_pct", 0.02)
    open_risk_limit = account_size * policy.get("max_open_planned_risk_pct", 0.03)

    today_pnl = summary.get("today_realized_pnl", 0)
    week_pnl = summary.get("week_realized_pnl", 0)
    open_risk = summary.get("open_planned_risk", 0)

    if today_pnl <= -daily_loss_limit:
        vetoes.append(
            f"Daily simulated loss limit reached: {today_pnl:.2f} vs limit -{daily_loss_limit:.2f}."
        )
    elif today_pnl < 0:
        warnings.append(f"Simulated portfolio is down {today_pnl:.2f} today; reduce aggression.")

    if week_pnl <= -weekly_loss_limit:
        vetoes.append(
            f"Weekly simulated loss limit reached: {week_pnl:.2f} vs limit -{weekly_loss_limit:.2f}."
        )
    elif week_pnl < 0:
        warnings.append(f"Simulated portfolio is down {week_pnl:.2f} this week; require cleaner setups.")

    if open_risk > open_risk_limit:
        vetoes.append(
            f"Open planned risk is {open_risk:.2f}, above portfolio limit {open_risk_limit:.2f}."
        )
    elif open_risk > open_risk_limit * 0.75:
        warnings.append(
            f"Open planned risk is {open_risk:.2f}; nearing portfolio limit {open_risk_limit:.2f}."
        )


def days_until_event(event):
    raw_date = event.get("date")
    if not raw_date:
        return None
    try:
        event_date = datetime.fromisoformat(raw_date).date()
    except ValueError:
        return None
    return (event_date - date.today()).days


def calculate_position_size(entry, stop, policy):
    if entry is None or stop is None:
        return {"error": "Cannot size position without entry and stop."}

    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return {"error": "Risk per share must be positive."}

    account_size = policy["paper_account_size"]
    proving = policy.get("proving_mode") or {}
    risk_pct = policy["max_risk_per_trade_pct"]
    position_pct = policy["max_single_position_pct"]
    if proving.get("enabled"):
        risk_pct = min(risk_pct, proving.get("max_risk_per_trade_pct", risk_pct))
        position_pct = min(position_pct, proving.get("max_single_position_pct", position_pct))

    max_dollar_risk = account_size * risk_pct
    max_position_value = account_size * position_pct
    shares_by_risk = int(max_dollar_risk // risk_per_share)
    shares_by_exposure = int(max_position_value // entry)
    shares = min(shares_by_risk, shares_by_exposure)

    if shares <= 0:
        return {"error": "Position size rounds to zero under current risk limits."}

    position_value = shares * entry

    return {
        "paper_account_size": account_size,
        "effective_risk_pct": risk_pct,
        "effective_max_position_pct": position_pct,
        "max_dollar_risk": max_dollar_risk,
        "risk_per_share": risk_per_share,
        "shares": shares,
        "shares_by_risk": shares_by_risk,
        "shares_by_exposure": shares_by_exposure,
        "size_limited_by_exposure": shares < shares_by_risk,
        "position_value": position_value,
        "max_position_value": max_position_value,
    }


def build_conditional_plan(
    entry,
    alternative_entry,
    stop,
    target,
    target_2,
    target_3,
    minimum_reward_to_risk,
    reward_to_risk,
    reward_to_risk_to_target_2,
    reward_to_risk_to_target_3,
    pullback_reward_to_risk,
):
    if entry is None or stop is None or target is None:
        return {}

    max_entry = max_entry_for_reward_to_risk(stop, target, minimum_reward_to_risk)
    pullback_viable = (
        pullback_reward_to_risk is not None
        and pullback_reward_to_risk >= minimum_reward_to_risk
    )
    suggested_entry = max_entry if max_entry is not None and entry > max_entry else entry
    if pullback_viable and alternative_entry:
        suggested_entry = min(float(alternative_entry), float(suggested_entry))

    plan = {
        "minimum_reward_to_risk": minimum_reward_to_risk,
        "current_entry": entry,
        "max_entry_for_target_1": max_entry,
        "better_entry_required": max_entry is not None and entry > max_entry,
        "suggested_entry": suggested_entry,
        "use_second_target": (
            reward_to_risk_to_target_2 is not None
            and reward_to_risk_to_target_2 >= minimum_reward_to_risk
            and (reward_to_risk is None or reward_to_risk < minimum_reward_to_risk)
        ),
        "use_third_target": (
            reward_to_risk_to_target_3 is not None
            and reward_to_risk_to_target_3 >= minimum_reward_to_risk
            and (reward_to_risk_to_target_2 is None or reward_to_risk_to_target_2 < minimum_reward_to_risk)
        ),
        "pullback_entry_viable": pullback_viable,
        "alternative_entry": alternative_entry,
        "target_1": target,
        "target_2": target_2,
        "target_3": target_3,
    }

    if plan["pullback_entry_viable"]:
        plan["condition"] = "Wait for pullback entry near the suggested level; do not chase breakout."
    elif plan["better_entry_required"]:
        plan["condition"] = "Only consider if price is at or below suggested entry."
    elif plan["use_second_target"]:
        plan["condition"] = "Monitor only unless Target 1 becomes realistic; do not justify entry using only Target 2."
    elif plan["use_third_target"]:
        plan["condition"] = "Monitor only; Target 3 is too ambitious for approval while setup memory is weak."
    else:
        plan["condition"] = "Monitor only; current setup does not meet risk structure."

    return plan


def max_entry_for_reward_to_risk(stop, target, minimum_reward_to_risk):
    if stop is None or target is None:
        return None
    return (target + (minimum_reward_to_risk * stop)) / (1 + minimum_reward_to_risk)


def build_veto_report(ticker, policy, technical_report, reasons):
    return {
        "agent": "Risk Manager",
        "system_role": "committee_agent",
        "run_id": datetime.now().strftime("%Y-%m-%d-risk"),
        "symbol": ticker,
        "decision": "veto",
        "confidence": 0.9,
        "policy_version": policy["version"],
        "technical_stance": technical_report.get("stance"),
        "entry": None,
        "alternative_entry": None,
        "stop": None,
        "target_1": None,
        "target_2": None,
        "target_3": None,
        "reward_to_risk": None,
        "reward_to_risk_to_target_2": None,
        "reward_to_risk_to_target_3": None,
        "pullback_reward_to_risk": None,
        "position": {},
        "earnings": {},
        "portfolio_exposure": {},
        "journal_summary": {},
        "vetoes": reasons,
        "conditional_issues": [],
        "conditional_plan": {},
        "warnings": [],
        "missing_information": [],
        "citations": technical_report.get("citations", []),
    }


def format_risk_report(report):
    lines = [
        "# Risk Manager Report",
        "",
        f"Run ID: {report['run_id']}",
        f"Symbol: {report['symbol']}",
        f"Decision: {report['decision']}",
        f"Confidence: {report['confidence']}",
        f"Policy Version: {report['policy_version']}",
        f"Technical Stance: {report['technical_stance']}",
        "",
        "## Setup",
        f"- Entry: {format_number(report['entry'])}",
        f"- Alternative Entry: {format_number(report.get('alternative_entry'))}",
        f"- Stop: {format_number(report['stop'])}",
        f"- Target 1 / First Exit: {format_number(report['target_1'])}",
        f"- Original / Stretch Target: {format_number(report.get('original_target_1'))}",
        f"- Target 2: {format_number(report.get('target_2'))}",
        f"- Target 3: {format_number(report.get('target_3'))}",
        f"- Reward/Risk: {format_number(report['reward_to_risk'])}",
        f"- Original Reward/Risk: {format_number(report.get('original_reward_to_risk'))}",
        f"- Reward/Risk to Target 2: {format_number(report.get('reward_to_risk_to_target_2'))}",
        f"- Pullback Reward/Risk: {format_number(report.get('pullback_reward_to_risk'))}",
        "",
        "## Position Sizing",
    ]

    position = report.get("position") or {}
    if position.get("error"):
        lines.append(f"- Error: {position['error']}")
    elif position:
        lines.extend([
            f"- Paper Account Size: {format_number(position['paper_account_size'])}",
            f"- Max Dollar Risk: {format_number(position['max_dollar_risk'])}",
            f"- Risk Per Share: {format_number(position['risk_per_share'])}",
            f"- Shares: {position['shares']}",
            f"- Shares by Risk Limit: {position.get('shares_by_risk')}",
            f"- Shares by Exposure Limit: {position.get('shares_by_exposure')}",
            f"- Size Limited by Exposure: {position.get('size_limited_by_exposure')}",
            f"- Position Value: {format_number(position['position_value'])}",
            f"- Max Position Value: {format_number(position['max_position_value'])}",
        ])
    else:
        lines.append("- Not available.")

    lines.extend([
        "",
        "## Event Risk",
        f"- Earnings Date: {report.get('earnings', {}).get('earnings_date') or 'n/a'}",
        f"- Days Until Earnings: {report.get('earnings', {}).get('days_until_earnings') if report.get('earnings', {}).get('days_until_earnings') is not None else 'n/a'}",
    ])

    economic_calendar = report.get("economic_calendar") or {}
    if not economic_calendar:
        lines.append("- Economic Calendar: n/a")
    elif economic_calendar.get("status") == "not_configured":
        lines.append("- Economic Calendar: not configured")
    elif economic_calendar.get("error"):
        lines.append(f"- Economic Calendar: {economic_calendar['error']}")
    else:
        summary = economic_calendar.get("summary") or {}
        lines.extend([
            f"- Economic Calendar: {economic_calendar.get('status')}",
            f"- Calendar Window: {economic_calendar.get('start_date')} to {economic_calendar.get('end_date')}",
            f"- High-Importance Events: {summary.get('high_importance_count', 0)}",
            f"- Next High-Importance Event: {format_calendar_event(summary.get('next_high_importance_event'))}",
        ])

    lines.extend([
        "",
        "## Portfolio Exposure",
        f"- Current Symbol Exposure: {format_number(report.get('portfolio_exposure', {}).get('current_symbol_exposure_pct'))}%",
        f"- Correlated Exposure: {format_number(report.get('portfolio_exposure', {}).get('correlated_exposure_pct'))}%",
        "",
        "## Simulated Portfolio Memory",
        f"- Open / Planned Trades: {report.get('journal_summary', {}).get('open_trades', 0)}",
        f"- Today Realized P&L: {format_number(report.get('journal_summary', {}).get('today_realized_pnl'))}",
        f"- Week Realized P&L: {format_number(report.get('journal_summary', {}).get('week_realized_pnl'))}",
        f"- Open Planned Risk: {format_number(report.get('journal_summary', {}).get('open_planned_risk'))}",
    ])
    weekly_memory = report.get("weekly_memory") or {}
    lines.extend(["", "## Weekly Self-Review Memory"])
    if weekly_memory.get("status") != "available":
        lines.append(f"- {weekly_memory.get('message', 'No weekly self-review memory available.')}")
    else:
        lines.extend([
            f"- Week: {weekly_memory.get('week_start')} to {weekly_memory.get('week_end')}",
            f"- Dominant Read: {weekly_memory.get('dominant_read')}",
            f"- Target 1 Hit Rate: {format_number(weekly_memory.get('target_1_hit_rate'))}%",
            f"- Average Entered P&L: {format_number(weekly_memory.get('avg_entered_pnl_pct'))}%",
        ])
        for lesson in weekly_memory.get("lessons", [])[:4]:
            lines.append(f"- Lesson: {lesson}")

    setup_learning = report.get("setup_learning_memory") or {}
    lines.extend([
        "",
        "## Setup Learning Gate",
        f"- Learning Score: {format_number(setup_learning.get('learning_score'))}/100",
        f"- Target 1 Hit Rate: {format_number(setup_learning.get('target_1_hit_rate'))}%",
        f"- Average Entered P&L: {format_number(setup_learning.get('avg_entered_pnl_pct'))}%",
        f"- Read: {setup_learning.get('read') or 'n/a'}",
    ])
    top_pick_memory = report.get("top_pick_scenario_memory") or {}
    lines.extend(["", "## Top-Pick Scenario Memory"])
    if top_pick_memory.get("status") != "available":
        lines.append(f"- {top_pick_memory.get('message', 'No top-pick scenario memory available.')}")
    else:
        window = top_pick_memory.get("window") or {}
        lines.extend([
            f"- Window: {window.get('start_date')} to {window.get('end_date')}",
            f"- Best Scenario: {top_pick_memory.get('best_scenario')}",
            f"- Wait-for-entry P&L: {format_number(top_pick_memory.get('wait_calibrated_pnl'))}",
            f"- Buy-open P&L: {format_number(top_pick_memory.get('buy_open_pnl'))}",
            f"- Lesson: {top_pick_memory.get('lesson')}",
        ])

    lines.extend([
        "",
        "## Vetoes",
    ])
    lines.extend([f"- {item}" for item in report["vetoes"]] or ["- None."])

    lines.extend([
        "",
        "## Conditional Issues",
    ])
    lines.extend([f"- {item}" for item in report.get("conditional_issues", [])] or ["- None."])

    conditional_plan = report.get("conditional_plan") or {}
    if conditional_plan:
        lines.extend([
            "",
            "## Conditional Plan",
            f"- Condition: {conditional_plan.get('condition')}",
            f"- Suggested Entry: {format_number(conditional_plan.get('suggested_entry'))}",
            f"- Max Entry for Target 1: {format_number(conditional_plan.get('max_entry_for_target_1'))}",
            f"- Pullback Entry Viable: {conditional_plan.get('pullback_entry_viable')}",
            f"- Use Second Target: {conditional_plan.get('use_second_target')}",
        ])

    failed_long_signal = report.get("failed_long_signal") or {}
    lines.extend([
        "",
        "## Failed-Long / Fade Watch",
        f"- Status: {failed_long_signal.get('status', 'n/a')}",
        f"- Score: {format_number(failed_long_signal.get('score'))}/100",
        f"- Summary: {failed_long_signal.get('summary', 'n/a')}",
    ])
    lines.extend([f"- Evidence: {item}" for item in failed_long_signal.get("evidence", [])] or ["- Evidence: None."])
    trade_options = failed_long_signal.get("possible_trade_options") or []
    if trade_options:
        lines.append("- Possible Trade Options: " + "; ".join(
            f"{item.get('vehicle')} ({item.get('status')})"
            for item in trade_options[:4]
        ))

    lines.extend([
        "",
        "## Warnings",
    ])
    lines.extend([f"- {item}" for item in report["warnings"]] or ["- None."])

    lines.extend([
        "",
        "## Missing Information",
    ])
    lines.extend([f"- {item}" for item in report["missing_information"]] or ["- None."])

    return "\n".join(lines) + "\n"


def save_risk_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{report['symbol']}_risk_report.md"
    path.write_text(format_risk_report(report), encoding="utf-8")
    return path


def format_number(value):
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def pct(numerator, denominator):
    if not denominator:
        return 0
    return (numerator / denominator) * 100


def average(values):
    values = [float(value) for value in values if value is not None]
    if not values:
        return None
    return sum(values) / len(values)


def clamp(value, low, high):
    return max(low, min(high, value))
