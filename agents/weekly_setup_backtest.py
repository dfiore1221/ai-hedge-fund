import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

from agents.daily_setup_review import normalize_yfinance_columns, pnl_pct, to_float
from agents.risk_manager import load_risk_policy
from memory.research_memory import save_agent_report


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MORNING_BRIEF_DIR = PROJECT_ROOT / "reports" / "morning_brief"
REPORTS_DIR = PROJECT_ROOT / "reports" / "backtests"


def generate_weekly_setup_backtest(start_day=None, end_day=None, top_n=10, save_memory=True):
    end_date = parse_day(end_day) if end_day else date.today()
    start_date = parse_day(start_day) if start_day else end_date - timedelta(days=4)
    policy = load_risk_policy()
    brief_paths = select_daily_morning_briefs(start_date, end_date)

    rows = []
    for path in brief_paths:
        brief = json.loads(path.read_text(encoding="utf-8"))
        setup_date = datetime.fromisoformat(brief["created_at"]).date()
        for rank, idea in enumerate((brief.get("ideas") or [])[:top_n], start=1):
            rows.append(backtest_idea(idea, setup_date, end_date, rank, path, policy))

    report = {
        "agent": "Weekly Setup Backtest",
        "system_role": "memory_layer",
        "layer": "Target Calibration Backtest",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "top_n": top_n,
        "briefs_reviewed": len(brief_paths),
        "setups_reviewed": len(rows),
        "summary": summarize_rows(rows),
        "successful_setups": successful_setups(rows),
        "missed_or_failed_setups": missed_or_failed_setups(rows),
        "reviewed_setups": rows,
    }

    if save_memory:
        save_agent_report(
            run_id=f"{start_date.isoformat()}-{end_date.isoformat()}-weekly-setup-backtest",
            agent_name="Weekly Setup Backtest",
            output=report,
            symbol="MARKET",
            stance=report["summary"]["read"],
            confidence=100,
        )

    return report


def generate_top_pick_scenario_backtest(start_day=None, end_day=None, save_memory=True):
    end_date = parse_day(end_day) if end_day else date.today()
    start_date = parse_day(start_day) if start_day else end_date - timedelta(days=21)
    policy = load_risk_policy()
    brief_records = select_top_pick_brief_records(start_date, end_date)
    scenarios = top_pick_scenarios()

    rows = []
    for record in brief_records:
        idea = record["idea"]
        for scenario in scenarios:
            rows.append(backtest_top_pick_scenario(idea, record, end_date, scenario, policy))

    report = {
        "agent": "Top Pick Scenario Backtest",
        "system_role": "memory_layer",
        "layer": "Top Daily Idea Scenario Lab",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "briefs_reviewed": len(brief_records),
        "scenario_count": len(scenarios),
        "account_size": policy.get("paper_account_size"),
        "risk_policy": effective_backtest_risk_policy(policy),
        "summary_by_scenario": summarize_top_pick_scenarios(rows),
        "top_pick_days": summarize_top_pick_days(brief_records),
        "trades": rows,
        "learning_notes": build_top_pick_learning_notes(rows),
    }

    if save_memory:
        best = best_scenario(report["summary_by_scenario"])
        save_agent_report(
            run_id=f"{start_date.isoformat()}-{end_date.isoformat()}-top-pick-scenario-backtest",
            agent_name="Top Pick Scenario Backtest",
            output=report,
            symbol="MARKET",
            stance=best.get("scenario", "NO SCENARIO"),
            confidence=100,
        )

    return report


def parse_day(value):
    if value in {None, "", "today"}:
        return date.today()
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def select_daily_morning_briefs(start_date, end_date):
    grouped = defaultdict(list)
    for path in MORNING_BRIEF_DIR.glob("morning_brief_*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            created_at = datetime.fromisoformat(data.get("created_at", ""))
        except Exception:
            continue
        if start_date <= created_at.date() <= end_date:
            grouped[created_at.date()].append((created_at, path))

    selected = []
    for day in sorted(grouped):
        candidates = grouped[day]
        premarket = [
            item for item in candidates
            if item[0].time() <= time(9, 30)
        ]
        selected.append(sorted(premarket or candidates, key=lambda item: item[0])[-1][1])
    return selected


def select_top_pick_brief_records(start_date, end_date):
    records = []
    for path in select_daily_morning_briefs(start_date, end_date):
        brief = json.loads(path.read_text(encoding="utf-8"))
        created_at = datetime.fromisoformat(brief["created_at"])
        ideas = brief.get("ideas") or []
        if not ideas:
            continue
        records.append({
            "source_brief": str(path),
            "created_at": created_at,
            "setup_date": created_at.date(),
            "idea": ideas[0],
        })
    return records


def top_pick_scenarios():
    return [
        {
            "id": "wait_original_target",
            "name": "Wait for Entry + Original Target",
            "entry_mode": "wait_for_pullback",
            "target_mode": "original",
            "partial": False,
            "time_stop_days": None,
        },
        {
            "id": "wait_calibrated_target",
            "name": "Wait for Entry + Calibrated Target 1",
            "entry_mode": "wait_for_pullback",
            "target_mode": "calibrated",
            "partial": False,
            "time_stop_days": None,
        },
        {
            "id": "wait_partial_calibrated",
            "name": "Wait + Half Out at Partial Win",
            "entry_mode": "wait_for_pullback",
            "target_mode": "calibrated",
            "partial": True,
            "time_stop_days": None,
        },
        {
            "id": "buy_open_calibrated",
            "name": "Buy Market Open + Calibrated Target 1",
            "entry_mode": "buy_open",
            "target_mode": "calibrated",
            "partial": False,
            "time_stop_days": None,
        },
        {
            "id": "buy_open_time_stop_3d",
            "name": "Buy Market Open + 3-Day Time Stop",
            "entry_mode": "buy_open",
            "target_mode": "calibrated",
            "partial": False,
            "time_stop_days": 3,
        },
    ]


def backtest_top_pick_scenario(idea, record, end_date, scenario, policy):
    symbol = str(idea.get("symbol", "")).upper().strip()
    side = str(idea.get("side") or "long").lower()
    planned_entry = preferred_entry(idea)
    stop = to_float(idea.get("stop"))
    original_target = to_float(idea.get("original_target_1")) or to_float(idea.get("target_1"))
    calibrated_target = corrected_first_target(idea, planned_entry, stop, to_float(idea.get("target_1")), policy)
    target = original_target if scenario["target_mode"] == "original" else calibrated_target
    partial = to_float(idea.get("partial_win_level")) or partial_win_level(side, planned_entry, stop)

    row = {
        "setup_date": record["setup_date"].isoformat(),
        "brief_created_at": record["created_at"].isoformat(timespec="seconds"),
        "source_brief": record["source_brief"],
        "scenario_id": scenario["id"],
        "scenario": scenario["name"],
        "symbol": symbol,
        "decision": idea.get("decision"),
        "score": idea.get("score"),
        "category": idea.get("category"),
        "run_id": idea.get("run_id"),
        "side": side,
        "planned_entry": round_number(planned_entry),
        "actual_entry": None,
        "stop": round_number(stop),
        "target": round_number(target),
        "partial_win_level": round_number(partial),
        "shares": 0,
        "entered": False,
        "entry_date": "",
        "exit_date": "",
        "exit_price": None,
        "exit_reason": "UNREVIEWABLE",
        "target_hit": False,
        "partial_hit": False,
        "stop_hit": False,
        "time_stop_hit": False,
        "no_entry": False,
        "gross_pnl_dollars": 0.0,
        "gross_pnl_pct_on_trade": 0.0,
        "r_multiple": 0.0,
        "max_favorable_move_pct": None,
        "max_adverse_move_pct": None,
        "data_error": "",
    }

    if not symbol or not planned_entry or not stop or not target:
        row["data_error"] = "Missing symbol, entry, stop, or target."
        return row
    if side not in {"long", "short"}:
        row["data_error"] = f"Unsupported side: {side}."
        return row

    history = fetch_history(symbol, record["setup_date"], end_date)
    if history.empty:
        row["data_error"] = "No daily price data returned."
        return row

    if scenario["entry_mode"] == "buy_open":
        return simulate_buy_open_scenario(row, history, record, scenario, policy)
    return simulate_wait_scenario(row, history, planned_entry, scenario, policy)


def simulate_wait_scenario(row, history, planned_entry, scenario, policy):
    for timestamp, bar in history.iterrows():
        if entry_hit_bar(bar, row["side"], planned_entry):
            return simulate_from_entry(row, history.loc[history.index >= timestamp], timestamp, planned_entry, scenario, policy)
    row["no_entry"] = True
    row["exit_reason"] = "NO ENTRY"
    return finalize_scenario_row(row)


def simulate_buy_open_scenario(row, history, record, scenario, policy):
    frame = history
    if record["created_at"].time() > time(9, 30):
        frame = history.loc[history.index.date > record["setup_date"]]
    if frame.empty:
        row["data_error"] = "No market-open bar available after brief."
        return row
    first_timestamp = frame.index[0]
    entry = float(frame.iloc[0]["Open"])
    return simulate_from_entry(row, frame, first_timestamp, entry, scenario, policy)


def simulate_from_entry(row, frame, entry_timestamp, entry, scenario, policy):
    side = row["side"]
    stop = float(row["stop"])
    target = float(row["target"])
    sizing = calculate_backtest_size(side, entry, stop, policy)
    if sizing.get("error"):
        row["data_error"] = sizing["error"]
        return row

    row["entered"] = True
    row["actual_entry"] = round_number(entry)
    row["entry_date"] = entry_timestamp.date().isoformat()
    row["shares"] = sizing["shares"]
    remaining_shares = sizing["shares"]
    realized = 0.0
    entry_bar_number = 0

    for entry_bar_number, (timestamp, bar) in enumerate(frame.iterrows(), start=1):
        day = timestamp.date().isoformat()
        if stop_hit_bar(bar, side, stop):
            row["stop_hit"] = True
            row["exit_reason"] = "STOP HIT"
            row["exit_date"] = day
            row["exit_price"] = round_number(stop)
            realized += trade_pnl_dollars(side, entry, stop, remaining_shares)
            break

        if scenario.get("partial") and not row["partial_hit"]:
            partial = to_float(row.get("partial_win_level"))
            if partial and target_hit_bar(bar, side, partial):
                partial_shares = max(1, remaining_shares // 2)
                remaining_shares -= partial_shares
                row["partial_hit"] = True
                realized += trade_pnl_dollars(side, entry, partial, partial_shares)

        if target_hit_bar(bar, side, target):
            row["target_hit"] = True
            row["exit_reason"] = "TARGET HIT"
            row["exit_date"] = day
            row["exit_price"] = round_number(target)
            realized += trade_pnl_dollars(side, entry, target, remaining_shares)
            break

        if scenario.get("time_stop_days") and entry_bar_number >= scenario["time_stop_days"]:
            close = float(bar["Close"])
            row["time_stop_hit"] = True
            row["exit_reason"] = f"{scenario['time_stop_days']}-DAY TIME STOP"
            row["exit_date"] = day
            row["exit_price"] = round_number(close)
            realized += trade_pnl_dollars(side, entry, close, remaining_shares)
            break

    if not row["exit_date"]:
        close = float(frame["Close"].iloc[-1])
        row["exit_reason"] = "OPEN AT WINDOW END"
        row["exit_date"] = frame.index[-1].date().isoformat()
        row["exit_price"] = round_number(close)
        realized += trade_pnl_dollars(side, entry, close, remaining_shares)

    row["gross_pnl_dollars"] = realized
    row["gross_pnl_pct_on_trade"] = pnl_pct(side, entry, row["exit_price"]) if row["exit_price"] else 0
    risk_dollars = sizing["risk_dollars"]
    row["r_multiple"] = realized / risk_dollars if risk_dollars else 0
    row["max_favorable_move_pct"] = max_favorable_move_pct(frame, side, entry)
    row["max_adverse_move_pct"] = max_adverse_move_pct(frame, side, entry)
    row["bars_held"] = entry_bar_number
    return finalize_scenario_row(row)


def calculate_backtest_size(side, entry, stop, policy):
    risk_per_share = abs(float(entry) - float(stop))
    if risk_per_share <= 0:
        return {"error": "Invalid stop relative to entry."}
    if side == "long" and stop >= entry:
        return {"error": "Long stop is not below entry."}
    if side == "short" and stop <= entry:
        return {"error": "Short stop is not above entry."}

    account_size = float(policy.get("paper_account_size", 100000))
    proving = policy.get("proving_mode") or {}
    risk_pct = float(policy.get("max_risk_per_trade_pct", 0.005))
    position_pct = float(policy.get("max_single_position_pct", 0.10))
    if proving.get("enabled"):
        risk_pct = min(risk_pct, float(proving.get("max_risk_per_trade_pct", risk_pct)))
        position_pct = min(position_pct, float(proving.get("max_single_position_pct", position_pct)))

    max_risk = account_size * risk_pct
    max_position = account_size * position_pct
    shares_by_risk = int(max_risk // risk_per_share)
    shares_by_exposure = int(max_position // float(entry))
    shares = min(shares_by_risk, shares_by_exposure)
    if shares <= 0:
        return {"error": "Position size rounds to zero under current risk and exposure limits."}
    return {
        "shares": shares,
        "risk_dollars": shares * risk_per_share,
        "max_risk_dollars": max_risk,
        "max_position_dollars": max_position,
    }


def partial_win_level(side, entry, stop):
    if not entry or not stop:
        return None
    risk = abs(float(entry) - float(stop))
    if side == "short":
        return float(entry) - (0.5 * risk)
    return float(entry) + (0.5 * risk)


def trade_pnl_dollars(side, entry, exit_price, shares):
    if side == "short":
        return (float(entry) - float(exit_price)) * int(shares)
    return (float(exit_price) - float(entry)) * int(shares)


def finalize_scenario_row(row):
    for key in [
        "actual_entry",
        "exit_price",
        "gross_pnl_dollars",
        "gross_pnl_pct_on_trade",
        "r_multiple",
        "max_favorable_move_pct",
        "max_adverse_move_pct",
    ]:
        if row.get(key) is not None:
            row[key] = round(float(row[key]), 2)
    return row


def effective_backtest_risk_policy(policy):
    proving = policy.get("proving_mode") or {}
    risk_pct = float(policy.get("max_risk_per_trade_pct", 0.005))
    position_pct = float(policy.get("max_single_position_pct", 0.10))
    if proving.get("enabled"):
        risk_pct = min(risk_pct, float(proving.get("max_risk_per_trade_pct", risk_pct)))
        position_pct = min(position_pct, float(proving.get("max_single_position_pct", position_pct)))
    return {
        "max_risk_per_trade_pct": risk_pct,
        "max_position_pct": position_pct,
        "gross_pnl_is_before_fees_taxes_slippage": True,
    }


def summarize_top_pick_scenarios(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["scenario_id"]].append(row)

    summaries = []
    for scenario_id, scenario_rows in grouped.items():
        entered = [row for row in scenario_rows if row.get("entered")]
        target_hits = [row for row in entered if row.get("target_hit")]
        stop_hits = [row for row in entered if row.get("stop_hit")]
        partial_hits = [row for row in entered if row.get("partial_hit")]
        time_stops = [row for row in entered if row.get("time_stop_hit")]
        winners = [row for row in entered if float(row.get("gross_pnl_dollars") or 0) > 0]
        pnl_values = [float(row.get("gross_pnl_dollars") or 0) for row in entered]
        r_values = [float(row.get("r_multiple") or 0) for row in entered]
        mfe = [row.get("max_favorable_move_pct") for row in entered if row.get("max_favorable_move_pct") is not None]
        mae = [row.get("max_adverse_move_pct") for row in entered if row.get("max_adverse_move_pct") is not None]
        scenario_name = scenario_rows[0]["scenario"] if scenario_rows else scenario_id

        summaries.append({
            "scenario_id": scenario_id,
            "scenario": scenario_name,
            "top_pick_days": len(scenario_rows),
            "entries": len(entered),
            "entry_rate_pct": pct(len(entered), len(scenario_rows)),
            "target_hits": len(target_hits),
            "target_hit_rate_pct": pct(len(target_hits), len(entered)),
            "stop_hits": len(stop_hits),
            "stop_hit_rate_pct": pct(len(stop_hits), len(entered)),
            "partial_hits": len(partial_hits),
            "partial_hit_rate_pct": pct(len(partial_hits), len(entered)),
            "time_stops": len(time_stops),
            "win_rate_pct": pct(len(winners), len(entered)),
            "total_gross_pnl_dollars": round(sum(pnl_values), 2),
            "avg_gross_pnl_dollars": average(pnl_values),
            "avg_r_multiple": average(r_values),
            "avg_max_favorable_move_pct": average(mfe),
            "avg_max_adverse_move_pct": average(mae),
            "data_errors": len([row for row in scenario_rows if row.get("data_error")]),
        })
    return sorted(summaries, key=lambda row: row["total_gross_pnl_dollars"], reverse=True)


def summarize_top_pick_days(brief_records):
    rows = []
    for record in brief_records:
        idea = record["idea"]
        rows.append({
            "date": record["setup_date"].isoformat(),
            "symbol": idea.get("symbol"),
            "decision": idea.get("decision"),
            "score": idea.get("score"),
            "entry": round_number(preferred_entry(idea)),
            "stop": round_number(to_float(idea.get("stop"))),
            "target_1": round_number(to_float(idea.get("target_1"))),
            "category": idea.get("category"),
        })
    return rows


def build_top_pick_learning_notes(rows):
    summaries = summarize_top_pick_scenarios(rows)
    if not summaries:
        return ["No tradable top-pick scenarios were available in the selected window."]
    best = summaries[0]
    notes = [
        f"Best scenario by gross paper P&L was {best['scenario']} at ${best['total_gross_pnl_dollars']:.2f}.",
        "This backtest uses daily bars, so same-day stop/target ordering is handled conservatively.",
    ]
    wait_rows = [row for row in summaries if row["scenario_id"] == "wait_calibrated_target"]
    chase_rows = [row for row in summaries if row["scenario_id"] == "buy_open_calibrated"]
    if wait_rows and chase_rows:
        wait = wait_rows[0]
        chase = chase_rows[0]
        if chase["total_gross_pnl_dollars"] > wait["total_gross_pnl_dollars"]:
            notes.append("Buying the open outperformed waiting for the pullback in this sample; the Committee should test whether entry filters were too strict.")
        else:
            notes.append("Waiting for the planned entry outperformed buying the open in this sample; the Committee should keep respecting buy zones.")
    return notes


def best_scenario(summaries):
    return summaries[0] if summaries else {}


def backtest_idea(idea, setup_date, end_date, rank, source_path, policy):
    symbol = str(idea.get("symbol", "")).upper().strip()
    side = str(idea.get("side") or "long").lower()
    entry = preferred_entry(idea)
    stop = to_float(idea.get("stop"))
    old_target = to_float(idea.get("target_1"))
    partial = to_float(idea.get("partial_win_level"))
    corrected_target = corrected_first_target(idea, entry, stop, old_target, policy)
    stretch_target = to_float(idea.get("original_target_1")) or old_target

    row = {
        "source_brief": str(source_path),
        "setup_date": setup_date.isoformat(),
        "rank": rank,
        "symbol": symbol,
        "decision": idea.get("decision"),
        "score": idea.get("score"),
        "category": idea.get("category"),
        "run_id": idea.get("run_id"),
        "side": side,
        "entry": round_number(entry),
        "stop": round_number(stop),
        "partial_win_level": round_number(partial),
        "corrected_target_1": round_number(corrected_target),
        "old_target_1": round_number(old_target),
        "stretch_target": round_number(stretch_target),
        "entered": False,
        "entry_date": "",
        "hit_corrected_target_1": False,
        "hit_old_target_1": False,
        "hit_partial_win": False,
        "hit_stop": False,
        "result": "UNREVIEWABLE",
        "exit_date": "",
        "corrected_target_pnl_pct": pnl_pct(side, entry, corrected_target) if entry and corrected_target else None,
        "old_target_pnl_pct": pnl_pct(side, entry, old_target) if entry and old_target else None,
        "close_pnl_pct": None,
        "max_favorable_move_pct": None,
        "max_adverse_move_pct": None,
        "data_error": "",
    }

    if not symbol or not entry or not stop or not corrected_target:
        row["data_error"] = "Missing symbol, entry, stop, or corrected target."
        return row

    history = fetch_history(symbol, setup_date, end_date)
    if history.empty:
        row["data_error"] = "No daily price data returned."
        return row

    after_entry = None
    for timestamp, bar in history.iterrows():
        day = timestamp.date().isoformat()
        if not row["entered"]:
            if entry_hit_bar(bar, side, entry):
                row["entered"] = True
                row["entry_date"] = day
                after_entry = history.loc[history.index >= timestamp]
            else:
                continue

        if stop_hit_bar(bar, side, stop):
            row["hit_stop"] = True
            row["result"] = "STOP FIRST"
            row["exit_date"] = day
            row["close_pnl_pct"] = pnl_pct(side, entry, stop)
            break
        if partial and target_hit_bar(bar, side, partial):
            row["hit_partial_win"] = True
        if target_hit_bar(bar, side, corrected_target):
            row["hit_corrected_target_1"] = True
            row["result"] = "CORRECTED TARGET 1 HIT"
            row["exit_date"] = day
            row["close_pnl_pct"] = pnl_pct(side, entry, corrected_target)
            if old_target and target_hit_bar(bar, side, old_target):
                row["hit_old_target_1"] = True
            break
        if old_target and target_hit_bar(bar, side, old_target):
            row["hit_old_target_1"] = True

    if not row["entered"]:
        row["result"] = "NO ENTRY"
        row["close_pnl_pct"] = 0
        return finalize_row(row)

    after_entry = after_entry if after_entry is not None else history
    row["max_favorable_move_pct"] = max_favorable_move_pct(after_entry, side, entry)
    row["max_adverse_move_pct"] = max_adverse_move_pct(after_entry, side, entry)

    if row["result"] == "UNREVIEWABLE":
        row["result"] = "ACTIVE / NO CORRECTED TARGET"
        row["close_pnl_pct"] = pnl_pct(side, entry, float(after_entry["Close"].iloc[-1]))

    if row["hit_corrected_target_1"] and old_target and corrected_target == old_target:
        row["hit_old_target_1"] = True

    return finalize_row(row)


def preferred_entry(idea):
    return to_float(idea.get("suggested_entry")) or to_float(idea.get("entry_trigger"))


def corrected_first_target(idea, entry, stop, old_target, policy):
    if not entry or not stop or not old_target:
        return old_target
    proving = policy.get("proving_mode") or {}
    max_atr = float(proving.get("max_target_1_atr", 1.25))
    target_r = float(proving.get("target_1_r_multiple", 1.0))
    atr = to_float((idea.get("setup_realism") or {}).get("atr_14"))
    risk = abs(float(entry) - float(stop))
    if risk <= 0:
        return old_target
    cap_by_r = target_r * risk
    cap_by_atr = max_atr * atr if atr else cap_by_r
    target_distance = min(cap_by_r, cap_by_atr)
    if str(idea.get("side") or "long").lower() == "short":
        return max(old_target, float(entry) - target_distance)
    return min(old_target, float(entry) + target_distance)


def fetch_history(symbol, start_date, end_date):
    try:
        history = yf.download(
            symbol,
            start=start_date.isoformat(),
            end=(end_date + timedelta(days=1)).isoformat(),
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=False,
        )
    except Exception:
        return pd.DataFrame()
    if history is None or history.empty:
        return pd.DataFrame()
    return normalize_yfinance_columns(history)


def entry_hit_bar(bar, side, entry):
    return float(bar["High"]) >= entry if side == "short" else float(bar["Low"]) <= entry


def target_hit_bar(bar, side, target):
    return float(bar["Low"]) <= target if side == "short" else float(bar["High"]) >= target


def stop_hit_bar(bar, side, stop):
    return float(bar["High"]) >= stop if side == "short" else float(bar["Low"]) <= stop


def max_favorable_move_pct(frame, side, entry):
    if side == "short":
        return pnl_pct(side, entry, float(frame["Low"].min()))
    return pnl_pct(side, entry, float(frame["High"].max()))


def max_adverse_move_pct(frame, side, entry):
    if side == "short":
        return pnl_pct(side, entry, float(frame["High"].max()))
    return pnl_pct(side, entry, float(frame["Low"].min()))


def finalize_row(row):
    for key in [
        "corrected_target_pnl_pct",
        "old_target_pnl_pct",
        "close_pnl_pct",
        "max_favorable_move_pct",
        "max_adverse_move_pct",
    ]:
        if row.get(key) is not None:
            row[key] = round(float(row[key]), 2)
    return row


def summarize_rows(rows):
    entered = [row for row in rows if row.get("entered")]
    corrected_hits = [row for row in entered if row.get("hit_corrected_target_1")]
    old_hits = [row for row in entered if row.get("hit_old_target_1")]
    partial_hits = [row for row in entered if row.get("hit_partial_win")]
    stops = [row for row in entered if row.get("hit_stop")]
    close_pnls = [row["close_pnl_pct"] for row in entered if row.get("close_pnl_pct") is not None]
    mfe = [row["max_favorable_move_pct"] for row in entered if row.get("max_favorable_move_pct") is not None]
    mae = [row["max_adverse_move_pct"] for row in entered if row.get("max_adverse_move_pct") is not None]

    corrected_rate = pct(len(corrected_hits), len(entered))
    old_rate = pct(len(old_hits), len(entered))
    read = "CALIBRATED TARGETS HELPED" if corrected_rate > old_rate else "NO TARGET IMPROVEMENT"
    if not entered:
        read = "NO ENTRIES"

    return {
        "setups_reviewed": len(rows),
        "entries_triggered": len(entered),
        "entry_rate_pct": pct(len(entered), len(rows)),
        "corrected_target_1_hits": len(corrected_hits),
        "corrected_target_1_hit_rate_pct": corrected_rate,
        "old_target_1_hits": len(old_hits),
        "old_target_1_hit_rate_pct": old_rate,
        "partial_win_hits": len(partial_hits),
        "partial_win_rate_pct": pct(len(partial_hits), len(entered)),
        "stop_hits": len(stops),
        "stop_hit_rate_pct": pct(len(stops), len(entered)),
        "avg_close_pnl_pct": average(close_pnls),
        "avg_max_favorable_move_pct": average(mfe),
        "avg_max_adverse_move_pct": average(mae),
        "read": read,
    }


def successful_setups(rows):
    hits = [row for row in rows if row.get("hit_corrected_target_1")]
    return sorted(hits, key=lambda row: row.get("corrected_target_pnl_pct") or 0, reverse=True)


def missed_or_failed_setups(rows):
    failed = [
        row for row in rows
        if row.get("hit_stop") or (row.get("entered") and not row.get("hit_corrected_target_1"))
    ]
    return sorted(failed, key=lambda row: row.get("close_pnl_pct") or 0)


def format_weekly_setup_backtest(report):
    summary = report["summary"]
    lines = [
        "# Weekly Setup Backtest",
        "",
        f"Created At: {report['created_at']}",
        f"Window: {report['start_date']} to {report['end_date']}",
        f"Morning Briefs Reviewed: {report['briefs_reviewed']}",
        f"Top Ideas Per Brief: {report['top_n']}",
        "",
        "## Summary",
        f"- Setups Reviewed: {summary['setups_reviewed']}",
        f"- Entries Triggered: {summary['entries_triggered']} ({fmt_pct(summary['entry_rate_pct'])})",
        f"- Corrected Target 1 Hits: {summary['corrected_target_1_hits']} ({fmt_pct(summary['corrected_target_1_hit_rate_pct'])})",
        f"- Old Target 1 Hits: {summary['old_target_1_hits']} ({fmt_pct(summary['old_target_1_hit_rate_pct'])})",
        f"- Partial-Win Hits: {summary['partial_win_hits']} ({fmt_pct(summary['partial_win_rate_pct'])})",
        f"- Stop Hits: {summary['stop_hits']} ({fmt_pct(summary['stop_hit_rate_pct'])})",
        f"- Avg Close/Exit P&L: {fmt_pct(summary['avg_close_pnl_pct'])}",
        f"- Avg Max Favorable Move: {fmt_pct(summary['avg_max_favorable_move_pct'])}",
        f"- Avg Max Adverse Move: {fmt_pct(summary['avg_max_adverse_move_pct'])}",
        f"- Read: {summary['read']}",
        "",
        "## Successful Under Corrected Target",
    ]
    hits = report.get("successful_setups") or []
    if hits:
        for row in hits[:15]:
            lines.append(
                f"- {row['setup_date']} {row['symbol']}: entry {fmt_num(row['entry'])}, "
                f"corrected target {fmt_num(row['corrected_target_1'])} hit "
                f"({fmt_pct(row['corrected_target_pnl_pct'])}); old target "
                f"{'also hit' if row.get('hit_old_target_1') else 'not hit'}."
            )
    else:
        lines.append("- None.")

    lines.extend(["", "## Missed / Failed / Still Active"])
    misses = report.get("missed_or_failed_setups") or []
    if misses:
        for row in misses[:15]:
            lines.append(
                f"- {row['setup_date']} {row['symbol']}: {row['result']}; "
                f"entry {fmt_num(row['entry'])}, stop {fmt_num(row['stop'])}, "
                f"corrected target {fmt_num(row['corrected_target_1'])}, "
                f"close/exit P&L {fmt_pct(row.get('close_pnl_pct'))}."
            )
    else:
        lines.append("- None.")

    lines.extend([
        "",
        "## Interpretation",
        "- Corrected Target 1 is now treated as the realistic first exit.",
        "- Old Target 1 is treated as a stretch target, not the primary success definition.",
        "- If corrected targets materially outperform old targets, AIFundOS should favor staged exits over all-or-nothing swing targets.",
    ])
    return "\n".join(lines)


def save_weekly_setup_backtest_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    latest_md = REPORTS_DIR / "weekly_setup_backtest.md"
    latest_json = REPORTS_DIR / "weekly_setup_backtest.json"
    stamp = f"{report['start_date']}_{report['end_date']}"
    archived_md = REPORTS_DIR / f"weekly_setup_backtest_{stamp}.md"
    archived_json = REPORTS_DIR / f"weekly_setup_backtest_{stamp}.json"
    markdown = format_weekly_setup_backtest(report)
    latest_md.write_text(markdown, encoding="utf-8")
    latest_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    archived_md.write_text(markdown, encoding="utf-8")
    archived_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return latest_md


def format_top_pick_scenario_backtest(report):
    summaries = report.get("summary_by_scenario") or []
    lines = [
        "# Top Pick Scenario Backtest",
        "",
        f"Created At: {report['created_at']}",
        f"Window: {report['start_date']} to {report['end_date']}",
        f"Morning Briefs Reviewed: {report['briefs_reviewed']}",
        f"Paper Account: ${float(report.get('account_size') or 0):,.2f}",
        (
            "Sizing: "
            f"{float((report.get('risk_policy') or {}).get('max_risk_per_trade_pct') or 0) * 100:.2f}% "
            "max risk per trade, "
            f"{float((report.get('risk_policy') or {}).get('max_position_pct') or 0) * 100:.2f}% "
            "max position size."
        ),
        "",
        "## Scenario Results",
    ]
    if not summaries:
        lines.append("- No scenario results were available.")
    else:
        for row in summaries:
            lines.append(
                f"- {row['scenario']}: total gross P&L ${row['total_gross_pnl_dollars']:,.2f}; "
                f"entries {row['entries']}/{row['top_pick_days']} ({fmt_pct(row['entry_rate_pct'])}); "
                f"target hits {row['target_hits']} ({fmt_pct(row['target_hit_rate_pct'])}); "
                f"stops {row['stop_hits']} ({fmt_pct(row['stop_hit_rate_pct'])}); "
                f"win rate {fmt_pct(row['win_rate_pct'])}; avg R {row['avg_r_multiple']:.2f}."
            )

    lines.extend(["", "## Daily Top Picks"])
    for day in report.get("top_pick_days") or []:
        lines.append(
            f"- {day['date']} {day['symbol']}: {day['decision']}, score {day['score']}, "
            f"entry {fmt_num(day['entry'])}, stop {fmt_num(day['stop'])}, target {fmt_num(day['target_1'])}."
        )

    lines.extend(["", "## Best / Worst Trade Examples"])
    entered = [row for row in report.get("trades") or [] if row.get("entered")]
    best_trades = sorted(entered, key=lambda row: row.get("gross_pnl_dollars") or 0, reverse=True)[:5]
    worst_trades = sorted(entered, key=lambda row: row.get("gross_pnl_dollars") or 0)[:5]
    lines.append("Best:")
    if best_trades:
        for row in best_trades:
            lines.append(
                f"- {row['setup_date']} {row['symbol']} under {row['scenario']}: "
                f"${row['gross_pnl_dollars']:,.2f}, exit {row['exit_reason']} at {fmt_num(row['exit_price'])}."
            )
    else:
        lines.append("- None.")
    lines.append("")
    lines.append("Worst:")
    if worst_trades:
        for row in worst_trades:
            lines.append(
                f"- {row['setup_date']} {row['symbol']} under {row['scenario']}: "
                f"${row['gross_pnl_dollars']:,.2f}, exit {row['exit_reason']} at {fmt_num(row['exit_price'])}."
            )
    else:
        lines.append("- None.")

    lines.extend(["", "## Learning Notes"])
    for note in report.get("learning_notes") or []:
        lines.append(f"- {note}")
    lines.extend([
        "",
        "## Important Limits",
        "- Results are gross paper results before fees, taxes, spreads, slippage, and real intraday sequencing.",
        "- Daily bars cannot prove whether a stop or target was hit first inside the same trading day; this report assumes the conservative outcome.",
        "- This is a learning tool for AIFundOS and the Committee, not live trading advice.",
    ])
    return "\n".join(lines)


def save_top_pick_scenario_backtest_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    latest_md = REPORTS_DIR / "top_pick_scenario_backtest.md"
    latest_json = REPORTS_DIR / "top_pick_scenario_backtest.json"
    stamp = f"{report['start_date']}_{report['end_date']}"
    archived_md = REPORTS_DIR / f"top_pick_scenario_backtest_{stamp}.md"
    archived_json = REPORTS_DIR / f"top_pick_scenario_backtest_{stamp}.json"
    markdown = format_top_pick_scenario_backtest(report)
    latest_md.write_text(markdown, encoding="utf-8")
    latest_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    archived_md.write_text(markdown, encoding="utf-8")
    archived_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return latest_md


def pct(part, whole):
    return round((part / whole * 100), 2) if whole else 0


def average(values):
    clean = [float(value) for value in values if value is not None]
    return round(sum(clean) / len(clean), 2) if clean else 0


def round_number(value):
    return round(float(value), 2) if value is not None else None


def fmt_pct(value):
    if value is None:
        return "n/a"
    return f"{float(value):.2f}%"


def fmt_num(value):
    if value is None:
        return "n/a"
    return f"{float(value):.2f}"
