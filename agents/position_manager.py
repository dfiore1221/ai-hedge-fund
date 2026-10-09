import json
import math
import os
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from data.market_data import get_ohlcv_history
from data.paper_ledger import build_paper_ledger
from data.trade_journal import (
    enrich_trade_metrics,
    load_trade_journal,
    normalize_side,
    normalize_status,
    parse_date,
    to_float,
)
from memory.research_memory import save_agent_report


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports" / "position_manager"
OPENAI_PLACEHOLDER = "your_openai_api_key_here"
ENV_PATH = PROJECT_ROOT / ".env"


def generate_position_manager_report(use_llm=False, save_memory=True, refresh_market_data=True):
    created_at = datetime.now().isoformat(timespec="seconds")
    run_id = f"{date.today().isoformat()}-position-manager"
    journal = enrich_trade_metrics(load_trade_journal(), refresh_prices=refresh_market_data)
    ledger = build_paper_ledger(journal)

    open_trades = []
    planned_trades = []
    actions = []

    for _, row in journal.iterrows():
        status = normalize_status(row.get("status"))
        if status == "open":
            trade = analyze_open_trade(row, refresh_market_data=refresh_market_data)
            open_trades.append(trade)
            actions.append(build_open_trade_action(trade))
        elif status == "planned":
            trade = analyze_planned_trade(row, refresh_market_data=refresh_market_data)
            planned_trades.append(trade)
            actions.append(build_planned_trade_action(trade))

    actions = sorted(consolidate_core_actions(actions), key=lambda item: item["priority"])
    summary = summarize_position_book(open_trades, planned_trades, actions, ledger)
    deterministic_summary = build_cio_summary(summary, open_trades, planned_trades, actions)
    llm_summary = build_llm_cio_summary(summary, open_trades, planned_trades, actions) if use_llm else ""

    report = {
        "agent": "Position Manager",
        "system_role": "tool_workflow",
        "layer": "Paper Position Supervision",
        "run_id": run_id,
        "created_at": created_at,
        "mode": "paper_trading_position_management",
        "market_data_mode": "live_refresh" if refresh_market_data else "local_cached",
        "summary": summary,
        "cio_summary": llm_summary or deterministic_summary,
        "llm_summary_used": bool(llm_summary),
        "open_trades": open_trades,
        "planned_trades": planned_trades,
        "daily_action_list": actions,
        "rolled_up_positions": ledger.get("positions", []),
        "paper_account": ledger["account"],
        "warnings": ledger.get("warnings", []),
    }

    if save_memory:
        save_agent_report(
            run_id=run_id,
            agent_name="Position Manager",
            output=report,
            symbol="PORTFOLIO",
            stance=summary["portfolio_stance"],
            confidence=summary["confidence_score"],
        )

    return report


def analyze_open_trade(row, refresh_market_data=True):
    trade = base_trade(row)
    current = trade["current_price"] or trade["entry"]
    trade["current_price"] = current
    trade["days_open"] = days_since(trade["opened_at"])
    trade["unrealized_pnl"] = directional_pnl(trade["side"], trade["entry"], current, trade["shares"])
    trade["open_r_multiple"] = safe_divide(trade["unrealized_pnl"], trade["planned_risk"])
    trade["distance_to_stop_pct"] = distance_pct(current, trade["stop"])
    trade["distance_to_target_pct"] = distance_pct(current, trade["target"])
    trade["progress_to_target_pct"] = progress_to_target_pct(trade["side"], trade["entry"], current, trade["target"])
    trade["remaining_risk_dollars"] = remaining_risk_dollars(trade)
    trade["remaining_upside_dollars"] = remaining_upside_dollars(trade)
    trade["realism"] = evaluate_realism(trade, anchor_price=current, refresh_market_data=refresh_market_data)
    trade["time_stop"] = evaluate_time_stop(trade)
    return trade


def analyze_planned_trade(row, refresh_market_data=True):
    trade = base_trade(row)
    trade["days_planned"] = days_since(trade["opened_at"])
    trade["notional"] = trade["entry"] * trade["shares"]
    trade["realism"] = evaluate_realism(trade, anchor_price=trade["entry"], refresh_market_data=refresh_market_data)
    trade["trigger_distance_pct"] = distance_pct(trade["current_price"], trade["entry"]) if trade["current_price"] else None
    return trade


def base_trade(row):
    entry = to_float(row.get("entry"))
    stop = to_float(row.get("stop"))
    target = to_float(row.get("target"))
    shares = to_float(row.get("shares"))
    setup_type = str(row.get("setup_type", "")).strip()
    source = str(row.get("source", "")).strip()
    risk_per_share = abs(entry - stop) if entry and stop else 0
    return {
        "id": str(row.get("id", "")).strip(),
        "opened_at": str(row.get("opened_at", "")).strip(),
        "symbol": str(row.get("symbol", "")).upper().strip(),
        "side": normalize_side(row.get("side")),
        "status": normalize_status(row.get("status")),
        "setup_type": setup_type,
        "source": source,
        "is_core_sleeve": is_core_sleeve_trade(setup_type, source),
        "agent_run_id": str(row.get("agent_run_id", "")).strip(),
        "entry": entry,
        "stop": stop,
        "target": target,
        "shares": shares,
        "risk_per_share": risk_per_share,
        "planned_risk": risk_per_share * shares,
        "current_price": to_float(row.get("current_price")),
        "thesis": str(row.get("thesis", "")).strip(),
        "notes": str(row.get("notes", "")).strip(),
    }


def is_core_sleeve_trade(setup_type, source):
    combined = f"{setup_type} {source}".lower()
    return "core etf sleeve" in combined or "core sleeve" in combined


def build_open_trade_action(trade):
    current = trade["current_price"]
    stop = trade["stop"]
    target = trade["target"]
    side = trade["side"]
    time_stop = trade["time_stop"]
    realism = trade["realism"]
    has_stop = stop > 0
    has_target = target > 0

    if trade.get("is_core_sleeve"):
        return action(
            trade,
            6,
            "HOLD CORE / REBALANCE POLICY",
            "Core ETF sleeve holdings are managed by target allocation, drift, and portfolio-level risk rules, not tactical profit targets.",
        )
    if has_stop and side == "long" and current <= stop:
        return action(trade, 1, "EXIT", "Stop level has been hit or breached.")
    if has_stop and side == "short" and current >= stop:
        return action(trade, 1, "EXIT", "Stop level has been hit or breached.")
    if has_target and side == "long" and current >= target:
        return action(trade, 1, "TAKE PROFIT", "Target has been hit or exceeded.")
    if has_target and side == "short" and current <= target:
        return action(trade, 1, "TAKE PROFIT", "Target has been hit or exceeded.")
    if time_stop["status"] == "stale_review_exit":
        return action(trade, 2, "REVIEW EXIT", time_stop["message"])
    if time_stop["status"] == "reassess":
        return action(trade, 3, "REASSESS", time_stop["message"])
    if realism["rating"] in {"ambitious_target", "too_wide"}:
        return action(trade, 4, "REVIEW PLAN", realism["message"])
    if not has_stop and not has_target:
        return action(trade, 6, "HOLD CORE / NO TACTICAL LEVELS", "No stop or target is set; manage as a core sleeve holding unless policy changes.")
    if trade["open_r_multiple"] >= 0.5:
        return action(trade, 5, "HOLD / CONSIDER TRAIL", "Position is working; review whether stop should move closer to breakeven.")
    return action(trade, 6, "HOLD", "No exit trigger hit; maintain original paper plan.")


def build_planned_trade_action(trade):
    days_planned = trade["days_planned"]
    if days_planned >= 5:
        return action(trade, 7, "REVIEW PLANNED ORDER", "Planned order is stale; confirm thesis and levels before allowing a fill.")
    if trade["realism"]["rating"] in {"ambitious_target", "too_wide"}:
        return action(trade, 8, "CHECK LEVELS", trade["realism"]["message"])
    return action(trade, 9, "KEEP PLANNED", "Entry has not triggered yet; keep watch-only plan active.")


def action(trade, priority, recommendation, reason):
    return {
        "priority": priority,
        "symbol": trade["symbol"],
        "trade_id": trade["id"],
        "status": trade["status"],
        "side": trade["side"],
        "recommendation": recommendation,
        "reason": reason,
        "entry": round_money(trade["entry"]),
        "current_price": round_money(trade.get("current_price")),
        "stop": round_money(trade["stop"]),
        "target": round_money(trade["target"]),
        "shares": round_money(trade["shares"]),
        "planned_risk": round_money(trade["planned_risk"]),
        "open_r_multiple": round_money(trade.get("open_r_multiple")),
    }


def consolidate_core_actions(actions):
    grouped = {}
    passthrough = []
    for item in actions:
        if item.get("recommendation") != "HOLD CORE / REBALANCE POLICY":
            passthrough.append(item)
            continue
        key = (item.get("symbol"), item.get("side"))
        current = grouped.setdefault(key, {
            **item,
            "trade_id": "multiple lots",
            "shares": 0.0,
            "planned_risk": 0.0,
            "_entry_value": 0.0,
            "_lots": 0,
        })
        shares = float(item.get("shares") or 0)
        current["shares"] += shares
        current["planned_risk"] += float(item.get("planned_risk") or 0)
        current["_entry_value"] += float(item.get("entry") or 0) * shares
        current["_lots"] += 1
        current["current_price"] = item.get("current_price")

    consolidated = []
    for item in grouped.values():
        if item["shares"]:
            item["entry"] = round_money(item["_entry_value"] / item["shares"])
        item["shares"] = round_money(item["shares"])
        item["planned_risk"] = round_money(item["planned_risk"])
        item["reason"] = (
            f"{item['reason']} Rolled up from {item['_lots']} lot(s)."
        )
        item.pop("_entry_value", None)
        item.pop("_lots", None)
        consolidated.append(item)

    return passthrough + consolidated


def evaluate_realism(trade, anchor_price, refresh_market_data=True):
    if not trade["stop"] or not trade["target"]:
        return {
            "rating": "not_applicable",
            "message": "No tactical stop/target is set for this position.",
            "atr_14": None,
            "stop_atr": None,
            "target_atr": None,
            "expected_time_to_target": "not applicable",
        }

    if not refresh_market_data:
        return {
            "rating": "not_refreshed",
            "message": "Market-data refresh disabled; using stored current prices and skipping ATR realism checks.",
            "atr_14": None,
            "stop_atr": None,
            "target_atr": None,
            "expected_time_to_target": "unknown",
        }

    atr = fetch_atr(trade["symbol"])
    if not atr["atr"]:
        return {
            "rating": "unknown",
            "message": atr["error"] or "ATR unavailable; cannot judge stop/target realism.",
            "atr_14": None,
            "stop_atr": None,
            "target_atr": None,
            "expected_time_to_target": "unknown",
        }

    stop_distance = abs(anchor_price - trade["stop"])
    target_distance = abs(trade["target"] - anchor_price)
    stop_atr = stop_distance / atr["atr"] if atr["atr"] else None
    target_atr = target_distance / atr["atr"] if atr["atr"] else None

    if stop_atr is not None and stop_atr < 0.5:
        rating = "too_tight"
        message = "Stop is inside half an ATR, so normal volatility could shake it out."
    elif stop_atr is not None and stop_atr > 3.0:
        rating = "too_wide"
        message = "Stop is more than three ATRs away, so dollar risk may be too loose."
    elif target_atr is not None and target_atr > 4.0:
        rating = "ambitious_target"
        message = "Target is more than four ATRs away, so expect a longer swing or lower hit rate."
    else:
        rating = "reasonable"
        message = "Stop and target are within a normal swing-trade volatility range."

    return {
        "rating": rating,
        "message": message,
        "atr_14": round_money(atr["atr"]),
        "atr_date": atr["as_of"],
        "stop_atr": round_money(stop_atr),
        "target_atr": round_money(target_atr),
        "expected_time_to_target": expected_time_to_target(target_atr),
    }


def fetch_atr(symbol, period="3mo", window=14):
    history = get_ohlcv_history(symbol, period=period)

    if not history or history.get("error"):
        return {"atr": None, "as_of": "", "error": history.get("error") if history else "No ATR data returned."}

    rows = history.get("rows") or []
    frame = pd.DataFrame(rows)
    if frame.empty:
        return {"atr": None, "as_of": "", "error": "No ATR rows returned."}

    required = {"high", "low", "close"}
    if not required.issubset(frame.columns):
        return {"atr": None, "as_of": "", "error": "ATR data is missing high, low, or close fields."}

    frame = frame.dropna(subset=["high", "low", "close"]).copy()
    if len(frame) < window + 1:
        return {"atr": None, "as_of": "", "error": "Not enough price history for ATR."}

    previous_close = frame["close"].shift(1)
    true_range = pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - previous_close).abs(),
        (frame["low"] - previous_close).abs(),
    ], axis=1).max(axis=1)
    atr = true_range.rolling(window).mean().dropna()

    if atr.empty:
        return {"atr": None, "as_of": "", "error": "ATR calculation returned no values."}

    return {
        "atr": float(atr.iloc[-1]),
        "as_of": str(frame.iloc[-1].get("date", "")),
        "error": "",
        "provider": history.get("provider"),
    }


def evaluate_time_stop(trade):
    if trade.get("is_core_sleeve"):
        return {
            "status": "core_sleeve_policy",
            "message": "Core ETF sleeve holding; review through sleeve drift/rebalance policy, not a swing-trade time stop.",
        }
    if "position" in str(trade.get("setup_type") or "").lower():
        days = trade.get("days_open") or 0
        open_r = trade.get("open_r_multiple") or 0
        if days >= 90:
            return {
                "status": "stale_review_exit",
                "message": "Position trade reached its 90-day review boundary; renew the thesis or exit.",
            }
        if days >= 20 and open_r <= -0.25:
            return {
                "status": "reassess",
                "message": "Position trade is 20+ days old and losing; require a renewed fundamental thesis before adding.",
            }
        return {
            "status": "ok",
            "message": "Within the 20-to-90-day position-trade review window.",
        }
    days = trade.get("days_open") or 0
    open_r = trade.get("open_r_multiple") or 0
    if days >= 10 and open_r < 0.25:
        return {
            "status": "stale_review_exit",
            "message": "Open 10+ calendar days without meaningful progress; review exit or resize.",
        }
    if days >= 5 and open_r <= 0:
        return {
            "status": "reassess",
            "message": "Open 5+ calendar days and not profitable; do not add without renewed Committee support.",
        }
    return {
        "status": "ok",
        "message": "Within normal multi-day swing review window.",
    }


def summarize_position_book(open_trades, planned_trades, actions, ledger):
    exits = count_actions(actions, {"EXIT", "TAKE PROFIT"})
    reviews = count_actions(actions, {"REVIEW EXIT", "REASSESS", "REVIEW PLAN", "CHECK LEVELS"})
    account = ledger["account"]

    if exits:
        stance = "action_required"
    elif reviews:
        stance = "review_required"
    elif open_trades or planned_trades:
        stance = "monitor"
    else:
        stance = "no_positions"

    confidence = 90
    if any((trade.get("realism") or {}).get("rating") == "unknown" for trade in open_trades + planned_trades):
        confidence -= 15

    return {
        "portfolio_stance": stance,
        "confidence_score": max(0, confidence),
        "open_trade_count": len(open_trades),
        "planned_trade_count": len(planned_trades),
        "action_required_count": exits,
        "review_required_count": reviews,
        "net_liquidation_value": account.get("net_liquidation_value"),
        "cash_balance": account.get("cash_balance"),
        "market_value": account.get("market_value"),
        "open_risk": account.get("open_risk"),
        "unrealized_pnl": account.get("unrealized_pnl"),
        "realized_pnl": account.get("realized_pnl"),
    }


def build_cio_summary(summary, open_trades, planned_trades, actions):
    if not open_trades and not planned_trades:
        return "No open or planned paper trades are active. Keep the account in observation mode until the next qualified setup."

    urgent = [item for item in actions if item["priority"] <= 2]
    review = [item for item in actions if 3 <= item["priority"] <= 5]
    planned = [item for item in actions if item["status"] == "planned"]

    lines = [
        f"AIFundOS is in {summary['portfolio_stance'].replace('_', ' ')} mode with "
        f"{summary['open_trade_count']} open trade(s), {summary['planned_trade_count']} planned order(s), "
        f"and {money(summary['open_risk'])} of open planned risk.",
    ]

    if urgent:
        symbols = ", ".join(item["symbol"] for item in urgent)
        lines.append(f"Immediate attention: {symbols}. Review these before adding any new exposure.")
    if review:
        symbols = ", ".join(item["symbol"] for item in review[:5])
        lines.append(f"Monitoring list: {symbols}. These need level or time-stop review, not fresh buying.")
    if planned:
        lines.append(f"Planned orders remain conditional: {', '.join(item['symbol'] for item in planned[:5])}.")
    lines.append("The daily action list below is the operating plan; it is paper-trading guidance only.")
    return " ".join(lines)


def build_llm_cio_summary(summary, open_trades, planned_trades, actions):
    load_env_if_available()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key or api_key == OPENAI_PLACEHOLDER:
        return ""

    try:
        from openai import OpenAI
    except ModuleNotFoundError:
        return ""

    prompt = {
        "summary": summary,
        "open_trades": compact_trades(open_trades),
        "planned_trades": compact_trades(planned_trades),
        "actions": actions[:12],
    }

    try:
        client = OpenAI()
        response = client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-5"),
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are the CIO of a watch-only paper trading system. "
                        "Write a concise plain-English daily position-management summary. "
                        "Do not give real financial advice. Do not invent prices or facts. "
                        "Core ETF sleeve positions must be managed only by sleeve drift, rebalance policy, "
                        "cash policy, and portfolio-level risk rules; never describe them as take-profit, "
                        "stop-loss, or tactical exit items unless the deterministic action explicitly says EXIT."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt, default=str)},
            ],
        )
        return response.output_text.strip()
    except Exception:
        return ""


def format_position_manager_report(report):
    summary = report["summary"]
    lines = [
        "# Position Manager Report",
        "",
        f"Created At: {report['created_at']}",
        f"Run ID: {report['run_id']}",
        f"Market Data Mode: {report.get('market_data_mode', 'unknown')}",
        f"Portfolio Stance: {summary['portfolio_stance']}",
        f"Confidence Score: {summary['confidence_score']}/100",
        "",
        "## CIO Summary",
        report["cio_summary"],
        "",
        "## Paper Account",
        f"- Net liquidation value: {money(summary['net_liquidation_value'])}",
        f"- Cash balance: {money(summary['cash_balance'])}",
        f"- Market value: {money(summary['market_value'])}",
        f"- Open risk: {money(summary['open_risk'])}",
        f"- Unrealized P&L: {money(summary['unrealized_pnl'])}",
        f"- Realized P&L: {money(summary['realized_pnl'])}",
        "",
        "## Daily Portfolio Action List",
    ]

    if not report["daily_action_list"]:
        lines.append("- No active actions.")
    else:
        for item in report["daily_action_list"]:
            lines.append(
                f"- {item['symbol']} ({item['status']}): {item['recommendation']} - {item['reason']} "
                f"Entry {item['entry']}, current {item['current_price']}, stop {item['stop']}, target {item['target']}, "
                f"shares {item['shares']}, risk {money(item['planned_risk'])}, open R {item['open_r_multiple']}"
            )

    lines.extend(["", "## Stop / Target Realism"])
    trades = collapse_core_review_trades(report["open_trades"] + report["planned_trades"])
    if not trades:
        lines.append("- No open or planned trades to review.")
    else:
        for trade in trades:
            realism = trade["realism"]
            lines.append(
                f"- {trade['symbol']}: {realism['rating']} - {realism['message']} "
                f"ATR14 {realism.get('atr_14') or 'n/a'}, stop {realism.get('stop_atr') or 'n/a'} ATR, "
                f"target {realism.get('target_atr') or 'n/a'} ATR, expected target window: "
                f"{realism.get('expected_time_to_target') or 'unknown'}."
            )

    lines.extend(["", "## Time Stop Review"])
    open_trades = collapse_core_review_trades(report["open_trades"])
    if not open_trades:
        lines.append("- No open trades.")
    else:
        for trade in open_trades:
            time_stop = trade["time_stop"]
            lines.append(
                f"- {trade['symbol']}: day {trade['days_open']}, open R {trade['open_r_multiple']:.2f}, "
                f"{time_stop['status']} - {time_stop['message']}"
            )

    if report.get("warnings"):
        lines.extend(["", "## Ledger Warnings"])
        lines.extend(f"- {warning}" for warning in report["warnings"])

    return "\n".join(lines)


def save_position_manager_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    markdown = format_position_manager_report(report)
    latest_md = REPORTS_DIR / "position_manager.md"
    latest_json = REPORTS_DIR / "position_manager.json"
    stamped_md = REPORTS_DIR / f"position_manager_{timestamp}.md"
    stamped_json = REPORTS_DIR / f"position_manager_{timestamp}.json"
    latest_md.write_text(markdown, encoding="utf-8")
    latest_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    stamped_md.write_text(markdown, encoding="utf-8")
    stamped_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return latest_md


def collapse_core_review_trades(trades):
    collapsed = []
    seen_core = set()
    for trade in trades:
        if not trade.get("is_core_sleeve"):
            collapsed.append(trade)
            continue
        key = (trade.get("symbol"), trade.get("side"), trade.get("status"))
        if key in seen_core:
            continue
        seen_core.add(key)
        core_trade = dict(trade)
        same_lots = [
            item for item in trades
            if item.get("is_core_sleeve")
            and item.get("symbol") == trade.get("symbol")
            and item.get("side") == trade.get("side")
            and item.get("status") == trade.get("status")
        ]
        total_shares = sum(float(item.get("shares") or 0) for item in same_lots)
        if total_shares:
            core_trade["shares"] = total_shares
            core_trade["entry"] = sum(
                float(item.get("entry") or 0) * float(item.get("shares") or 0)
                for item in same_lots
            ) / total_shares
        core_trade["id"] = "multiple lots" if len(same_lots) > 1 else core_trade.get("id")
        core_trade["lot_count"] = len(same_lots)
        collapsed.append(core_trade)
    return collapsed


def count_actions(actions, names):
    return sum(1 for item in actions if item["recommendation"] in names)


def compact_trades(trades):
    fields = [
        "symbol",
        "side",
        "status",
        "entry",
        "current_price",
        "stop",
        "target",
        "shares",
        "planned_risk",
        "open_r_multiple",
        "days_open",
        "days_planned",
        "realism",
        "time_stop",
    ]
    return [{field: trade.get(field) for field in fields if field in trade} for trade in trades]


def expected_time_to_target(target_atr):
    if target_atr is None:
        return "unknown"
    if target_atr <= 1:
        return "1-2 trading days"
    if target_atr <= 2:
        return "3-5 trading days"
    if target_atr <= 4:
        return "1-3 trading weeks"
    return "longer swing / ambitious"


def days_since(value):
    parsed = parse_date(value)
    if parsed is None:
        return 0
    return max(0, (date.today() - parsed).days)


def directional_pnl(side, entry, exit_or_current, shares):
    if side == "short":
        return (entry - exit_or_current) * shares
    return (exit_or_current - entry) * shares


def remaining_risk_dollars(trade):
    if trade["side"] == "short":
        return max(0, trade["stop"] - trade["current_price"]) * trade["shares"]
    return max(0, trade["current_price"] - trade["stop"]) * trade["shares"]


def remaining_upside_dollars(trade):
    if trade["side"] == "short":
        return max(0, trade["current_price"] - trade["target"]) * trade["shares"]
    return max(0, trade["target"] - trade["current_price"]) * trade["shares"]


def progress_to_target_pct(side, entry, current, target):
    if not entry or not current or not target or entry == target:
        return None
    if side == "short":
        return round_money(((entry - current) / (entry - target)) * 100)
    return round_money(((current - entry) / (target - entry)) * 100)


def distance_pct(anchor, level):
    if not anchor:
        return None
    return round_money(abs(level - anchor) / anchor * 100)


def safe_divide(numerator, denominator):
    if not denominator:
        return 0
    return numerator / denominator


def round_money(value):
    if value is None:
        return None
    try:
        if math.isnan(float(value)):
            return None
    except (TypeError, ValueError):
        return None
    return round(float(value), 2)


def money(value):
    if value is None:
        return "n/a"
    return f"${float(value):,.2f}"


def load_env_if_available():
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        return
    load_dotenv(ENV_PATH)
