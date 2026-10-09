import json
import math
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from data.paper_fills import execution_price_is_fresh, fetch_price_map, fetch_price_snapshot
from data.paper_ledger import build_paper_ledger
from data.trade_journal import (
    append_trade,
    close_trade,
    load_trade_journal,
    normalize_status,
    partial_close_trade,
    save_trade_journal,
    to_float,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = PROJECT_ROOT / "framework" / "core_etf_sleeve.json"
CORE_SETUP_TYPE = "core etf sleeve"
CORE_REBALANCE_MIN_NOTIONAL = 100
EASTERN = ZoneInfo("America/New_York")


def analyze_core_etf_sleeve(macro_report, journal=None, ledger=None):
    policy = load_policy()
    regime = macro_report.get("assessment", {}).get("market_regime", "Neutral")
    profile = select_risk_profile(policy, regime)
    ledger = ledger or build_paper_ledger(journal)
    journal = journal if journal is not None else load_trade_journal()
    account = ledger["account"]
    equity = account.get("net_liquidation_value") or account.get("starting_cash") or 0
    target_sleeve_pct = profile.get("target_sleeve_pct", policy.get("target_sleeve_pct", 0.5))
    target_sleeve_value = equity * target_sleeve_pct
    holdings = current_core_holdings(journal)
    current_value = sum(item["market_value"] for item in holdings.values())
    drift_value = target_sleeve_value - current_value
    drift_pct = pct(drift_value, equity)
    rebalance_band = policy.get("rebalance_band_pct", 0.05)
    sleeve_status = classify_status(current_value, target_sleeve_value, equity, rebalance_band)
    prices = fetch_price_map(profile["weights"].keys())
    desired = build_desired_allocations(profile["weights"], target_sleeve_value, holdings, prices)
    actions = build_actions(desired, sleeve_status)
    sector_context = top_sector_context(macro_report)

    return {
        "agent": "Core ETF Sleeve",
        "system_role": "tool_workflow",
        "layer": "Portfolio Construction Tool",
        "regime": regime,
        "equity": round_money(equity),
        "target_sleeve_pct": round_pct(target_sleeve_pct),
        "target_sleeve_value": round_money(target_sleeve_value),
        "current_sleeve_value": round_money(current_value),
        "current_sleeve_pct": round_pct(pct(current_value, equity)),
        "drift_value": round_money(drift_value),
        "drift_pct": round_pct(drift_pct),
        "rebalance_band_pct": round_pct(rebalance_band),
        "cash_reserve_pct": round_pct(policy.get("cash_reserve_pct", 0.2)),
        "status": sleeve_status,
        "desired_allocations": desired,
        "current_holdings": list(holdings.values()),
        "actions": actions,
        "sector_context": sector_context,
        "notes": policy.get("notes", []),
    }


def load_policy():
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def select_risk_profile(policy, regime):
    profiles = policy.get("risk_profiles", {})
    normalized = normalize_regime(regime)
    return profiles.get(normalized) or profiles.get("Neutral") or {
        "target_sleeve_pct": policy.get("target_sleeve_pct", 0.5),
        "weights": {},
    }


def normalize_regime(regime):
    label = str(regime or "Neutral").strip().lower().replace("_", "-")
    if label in {"risk-on", "risk on", "riskon"}:
        return "Risk-On"
    if label in {"risk-off", "risk off", "riskoff"}:
        return "Risk-Off"
    return "Neutral"


def current_core_holdings(journal):
    holdings = {}
    if journal is None or journal.empty:
        return holdings

    for _, row in journal.iterrows():
        if normalize_status(row.get("status")) != "open":
            continue
        if str(row.get("setup_type", "")).strip().lower() != CORE_SETUP_TYPE:
            continue

        symbol = str(row.get("symbol", "")).upper().strip()
        shares = to_float(row.get("shares"))
        if not symbol or shares <= 0:
            continue
        entry = to_float(row.get("entry"))
        current_price = to_float(row.get("current_price")) or entry
        market_value = current_price * shares
        current = holdings.setdefault(symbol, {
            "symbol": symbol,
            "shares": 0.0,
            "market_value": 0.0,
            "last_price": current_price,
        })
        current["shares"] += shares
        current["market_value"] += market_value
        current["last_price"] = current_price

    return {
        symbol: {
            "symbol": item["symbol"],
            "shares": round_money(item["shares"]),
            "market_value": round_money(item["market_value"]),
            "last_price": round_money(item["last_price"]),
        }
        for symbol, item in holdings.items()
    }


def classify_status(current_value, target_value, equity, rebalance_band):
    if target_value <= 0:
        return "No target allocation."
    if current_value <= 0:
        return "Not invested; build core sleeve."
    if abs(current_value - target_value) / equity > rebalance_band:
        return "Rebalance needed."
    return "Within rebalance band."


def build_desired_allocations(weights, target_sleeve_value, holdings, prices=None):
    prices = prices or {}
    rows = []
    for symbol, weight in weights.items():
        target_value = target_sleeve_value * weight
        current_value = holdings.get(symbol, {}).get("market_value", 0)
        last_price = prices.get(symbol)
        suggested_shares = int(target_value // last_price) if last_price else 0
        rows.append({
            "symbol": symbol,
            "target_weight": round_pct(weight),
            "target_value": round_money(target_value),
            "current_value": round_money(current_value),
            "difference": round_money(target_value - current_value),
            "last_price": round_money(last_price) if last_price else None,
            "suggested_shares": suggested_shares,
        })
    return rows


def build_actions(desired, sleeve_status):
    if sleeve_status == "Within rebalance band.":
        return ["Core ETF sleeve is within policy band; no rebalance required."]

    return [
        f"Plan paper allocation for {row['symbol']}: target ${row['target_value']:.2f} "
        f"(difference ${row['difference']:.2f}; approx {row['suggested_shares']} shares"
        f"{' at $' + format_money_plain(row['last_price']) if row.get('last_price') else ''})."
        for row in desired
        if abs(row["difference"]) >= 100
    ]


def approve_core_rebalance_from_brief(brief_report, min_notional=CORE_REBALANCE_MIN_NOTIONAL):
    core_sleeve = brief_report.get("core_etf_sleeve") or {}
    created_at = str(brief_report.get("created_at") or "").strip()
    approval_id = build_core_rebalance_approval_id(created_at)
    journal = load_trade_journal()
    existing_keys = {
        (
            str(row.get("agent_run_id", "")).strip(),
            str(row.get("symbol", "")).upper().strip(),
        )
        for _, row in journal.iterrows()
    }

    created = []
    skipped = []
    if core_sleeve.get("status") == "Within rebalance band.":
        return {
            "approval_id": approval_id,
            "created": created,
            "skipped": [{
                "symbol": "CORE",
                "reason": "Core ETF sleeve is already within the rebalance band.",
            }],
        }

    for row in core_sleeve.get("desired_allocations", []):
        symbol = str(row.get("symbol", "")).upper().strip()
        difference = to_float(row.get("difference"))
        last_price = to_float(row.get("last_price"))
        if not symbol:
            continue

        if difference < -min_notional:
            skipped.append({
                "symbol": symbol,
                "reason": "Trim needed, but partial trim automation is not enabled yet.",
                "difference": round_money(difference),
            })
            continue

        if difference < min_notional:
            skipped.append({
                "symbol": symbol,
                "reason": "Difference is below the minimum rebalance notional.",
                "difference": round_money(difference),
            })
            continue

        if last_price <= 0:
            skipped.append({
                "symbol": symbol,
                "reason": "No usable last price was available.",
                "difference": round_money(difference),
            })
            continue

        shares = int(difference // last_price)
        if shares <= 0:
            skipped.append({
                "symbol": symbol,
                "reason": "Difference is not large enough to buy one whole share.",
                "difference": round_money(difference),
                "last_price": round_money(last_price),
            })
            continue

        key = (approval_id, symbol)
        if key in existing_keys:
            skipped.append({
                "symbol": symbol,
                "reason": "This brief approval already created a paper order for this symbol.",
                "approval_id": approval_id,
            })
            continue

        trade_id = append_trade({
            "symbol": symbol,
            "side": "long",
            "status": "open",
            "setup_type": CORE_SETUP_TYPE,
            "source": "core sleeve",
            "agent_run_id": approval_id,
            "entry": last_price,
            "stop": 0,
            "target": 0,
            "shares": shares,
            "current_price": last_price,
            "thesis": "Human-approved Core ETF Sleeve rebalance from AIFundOS morning brief.",
            "notes": (
                f"Core rebalance approval {approval_id}. "
                f"Target value ${format_money_plain(row.get('target_value'))}; "
                f"current value ${format_money_plain(row.get('current_value'))}; "
                f"difference ${format_money_plain(difference)}. "
                "Paper-only ledger action; no broker order was sent."
            ),
        })
        existing_keys.add(key)
        created.append({
            "trade_id": trade_id,
            "symbol": symbol,
            "shares": shares,
            "entry": round_money(last_price),
            "notional": round_money(shares * last_price),
            "difference": round_money(difference),
        })

    return {
        "approval_id": approval_id,
        "created": created,
        "skipped": skipped,
    }


def build_core_rebalance_plan(
    brief_report,
    journal=None,
    ledger=None,
    price_map=None,
    min_notional=CORE_REBALANCE_MIN_NOTIONAL,
):
    """Build a whole-share rebalance from current holdings, not stale brief balances."""
    core_sleeve = (brief_report or {}).get("core_etf_sleeve") or {}
    regime = normalize_regime(core_sleeve.get("regime") or "Neutral")
    policy = load_policy()
    profile = select_risk_profile(policy, regime)
    weights = profile.get("weights") or {}
    target_sleeve_pct = float(profile.get("target_sleeve_pct") or policy.get("target_sleeve_pct") or 0)
    journal = journal if journal is not None else load_trade_journal()
    ledger = ledger or build_paper_ledger(journal)
    account = ledger.get("account") or {}
    equity = to_float(account.get("net_liquidation_value") or account.get("starting_cash"))
    cash = to_float(account.get("cash_balance"))
    reserve_pct = float(policy.get("cash_reserve_pct") or 0)
    target_sleeve_value = equity * target_sleeve_pct
    holdings = current_core_holdings_at_prices(journal, price_map or {})
    current_value = sum(item["market_value"] for item in holdings.values())
    drift_value = target_sleeve_value - current_value
    drift_pct = pct(drift_value, equity)
    band = float(policy.get("rebalance_band_pct") or 0)
    requires_rebalance = bool(equity > 0 and abs(drift_pct) > band)

    rows = []
    sell_proceeds = 0.0
    symbols = list(weights)
    symbols.extend(symbol for symbol in holdings if symbol not in weights)
    for symbol in symbols:
        price = to_float((price_map or {}).get(symbol)) or to_float(holdings.get(symbol, {}).get("last_price"))
        current_shares = to_float(holdings.get(symbol, {}).get("shares"))
        target_value = target_sleeve_value * float(weights.get(symbol) or 0)
        target_shares = math.floor(target_value / price) if price > 0 else 0
        share_delta = int(target_shares - current_shares)
        notional = abs(share_delta) * price
        action = "hold"
        if requires_rebalance and price > 0 and notional >= min_notional:
            action = "buy" if share_delta > 0 else "sell"
            if action == "sell":
                sell_proceeds += notional
        rows.append({
            "symbol": symbol,
            "action": action,
            "shares": abs(share_delta) if action in {"buy", "sell"} else 0,
            "current_shares": round_money(current_shares),
            "target_shares": target_shares,
            "price": round_money(price),
            "approx_notional": round_money(notional if action in {"buy", "sell"} else 0),
            "current_value": round_money(current_shares * price),
            "target_value": round_money(target_value),
            "difference": round_money(target_value - current_shares * price),
        })

    available_buy_cash = max(0.0, cash + sell_proceeds - equity * reserve_pct)
    for row in rows:
        if row["action"] != "buy":
            continue
        affordable = math.floor(available_buy_cash / row["price"]) if row["price"] > 0 else 0
        approved_shares = min(int(row["shares"]), affordable)
        if approved_shares <= 0:
            row["action"] = "hold"
            row["shares"] = 0
            row["approx_notional"] = 0.0
            row["reason"] = "Cash reserve policy leaves no capacity for this purchase."
            continue
        row["shares"] = approved_shares
        row["approx_notional"] = round_money(approved_shares * row["price"])
        available_buy_cash -= approved_shares * row["price"]

    orders = [row for row in rows if row["action"] in {"buy", "sell"} and row["shares"] > 0]
    return {
        "regime": regime,
        "equity": round_money(equity),
        "cash_balance": round_money(cash),
        "cash_reserve_pct": round_pct(reserve_pct),
        "target_sleeve_pct": round_pct(target_sleeve_pct),
        "target_sleeve_value": round_money(target_sleeve_value),
        "current_sleeve_value": round_money(current_value),
        "current_sleeve_pct": round_pct(pct(current_value, equity)),
        "drift_value": round_money(drift_value),
        "drift_pct": round_pct(drift_pct),
        "rebalance_band_pct": round_pct(band),
        "requires_rebalance": requires_rebalance,
        "status": "Rebalance needed." if requires_rebalance else "Within rebalance band.",
        "orders": orders,
        "allocations": rows,
    }


def execute_autonomous_core_rebalance(
    brief_report,
    now=None,
    minimum_data_quality_score=90,
    price_snapshot=None,
):
    """Execute a Committee-owned rebalance in the local paper ledger only."""
    now = normalize_eastern(now)
    if not is_regular_market_hours(now):
        return {
            "status": "deferred",
            "reason": "Core rebalance waits for regular U.S. market hours.",
            "market_session": "closed",
            "actions": [],
        }

    brief_date = parse_brief_date((brief_report or {}).get("created_at"))
    if brief_date != now.date():
        return {
            "status": "blocked",
            "reason": "Core rebalance requires a current-day morning brief.",
            "market_session": "open",
            "actions": [],
        }

    data_score = to_float(((brief_report or {}).get("data_health") or {}).get("data_quality_score"))
    if data_score < float(minimum_data_quality_score):
        return {
            "status": "blocked",
            "reason": (
                f"Data quality {data_score:.0f}/100 is below the autonomous minimum "
                f"{float(minimum_data_quality_score):.0f}/100."
            ),
            "market_session": "open",
            "actions": [],
        }

    core_sleeve = (brief_report or {}).get("core_etf_sleeve") or {}
    profile = select_risk_profile(load_policy(), core_sleeve.get("regime") or "Neutral")
    journal = load_trade_journal()
    held_symbols = set(current_core_holdings(journal))
    symbols = sorted(set(profile.get("weights") or {}) | held_symbols)
    snapshot = price_snapshot or fetch_price_snapshot(symbols)
    prices = snapshot.get("prices") or {}
    metadata = snapshot.get("metadata") or {}
    invalid_quotes = [
        symbol for symbol in symbols
        if to_float(prices.get(symbol)) <= 0
        or not execution_price_is_fresh(metadata.get(symbol, {}), manual_price_map=price_snapshot is not None)
    ]
    if invalid_quotes:
        return {
            "status": "blocked",
            "reason": "Fresh execution-grade quotes are unavailable for: " + ", ".join(invalid_quotes),
            "market_session": "open",
            "actions": [],
            "quote_provider_status": snapshot.get("provider_status"),
        }

    journal = apply_core_prices(journal, prices)
    save_trade_journal(journal)
    plan = build_core_rebalance_plan(
        brief_report,
        journal=journal,
        ledger=build_paper_ledger(journal),
        price_map=prices,
    )
    if not plan["requires_rebalance"]:
        return {
            "status": "no_action",
            "reason": "Core ETF sleeve is already within the rebalance band.",
            "market_session": "open",
            "actions": [],
            "plan": plan,
        }

    timestamp = now.isoformat(timespec="seconds")
    run_id = f"core-rebalance-{now:%Y%m%d}-{plan['regime'].lower()}"
    actions = []
    for order in [item for item in plan["orders"] if item["action"] == "sell"]:
        actions.extend(trim_core_position(order["symbol"], order["shares"], order["price"], run_id, timestamp))
    for order in [item for item in plan["orders"] if item["action"] == "buy"]:
        trade_id = append_trade({
            "symbol": order["symbol"],
            "side": "long",
            "status": "open",
            "setup_type": CORE_SETUP_TYPE,
            "source": "committee autonomous core rebalance",
            "agent_run_id": run_id,
            "entry": order["price"],
            "stop": 0,
            "target": 0,
            "shares": order["shares"],
            "current_price": order["price"],
            "thesis": "Committee-managed Core ETF Sleeve allocation rebalance.",
            "notes": "Paper-only autonomous core rebalance; no broker order was sent.",
        })
        actions.append({
            "action": "buy",
            "symbol": order["symbol"],
            "shares": order["shares"],
            "price": order["price"],
            "notional": round_money(order["shares"] * order["price"]),
            "trade_id": trade_id,
        })

    refreshed = apply_core_prices(load_trade_journal(), prices)
    save_trade_journal(refreshed)
    post_plan = build_core_rebalance_plan(
        brief_report,
        journal=refreshed,
        ledger=build_paper_ledger(refreshed),
        price_map=prices,
    )
    return {
        "status": "executed" if actions else "no_action",
        "reason": (
            f"Committee applied {len(actions)} paper core-sleeve rebalance action(s)."
            if actions else "No whole-share action met the rebalance minimum."
        ),
        "market_session": "open",
        "run_id": run_id,
        "actions": actions,
        "plan": plan,
        "post_rebalance": post_plan,
        "quote_provider_status": snapshot.get("provider_status"),
    }


def current_core_holdings_at_prices(journal, price_map):
    holdings = current_core_holdings(journal)
    for symbol, item in holdings.items():
        price = to_float(price_map.get(symbol)) or to_float(item.get("last_price"))
        item["last_price"] = round_money(price)
        item["market_value"] = round_money(to_float(item.get("shares")) * price)
    return holdings


def apply_core_prices(journal, prices):
    updated = journal.copy()
    for index, row in updated.iterrows():
        symbol = str(row.get("symbol", "")).upper().strip()
        if (
            normalize_status(row.get("status")) == "open"
            and str(row.get("setup_type", "")).strip().lower() == CORE_SETUP_TYPE
            and to_float(prices.get(symbol)) > 0
        ):
            updated.at[index, "current_price"] = str(round(to_float(prices[symbol]), 4))
    return updated


def trim_core_position(symbol, shares_to_sell, price, run_id, timestamp):
    remaining = float(shares_to_sell)
    actions = []
    journal = load_trade_journal()
    lots = []
    for _, row in journal.iterrows():
        if normalize_status(row.get("status")) != "open":
            continue
        if str(row.get("setup_type", "")).strip().lower() != CORE_SETUP_TYPE:
            continue
        if str(row.get("symbol", "")).upper().strip() != symbol:
            continue
        lots.append(row)
    lots.sort(key=lambda row: (str(row.get("opened_at", "")), str(row.get("id", ""))))

    for lot in lots:
        if remaining <= 0:
            break
        lot_shares = to_float(lot.get("shares"))
        quantity = min(remaining, lot_shares)
        reason = f"Committee autonomous core rebalance {run_id}."
        lessons = "Allocation trim governed by sleeve drift, not a tactical profit target."
        if quantity >= lot_shares:
            closed = close_trade(lot.get("id"), price, reason, lessons, closed_at=timestamp)
            closed_trade_id = closed.get("id")
        else:
            result = partial_close_trade(
                lot.get("id"), quantity, price, reason, lessons, closed_at=timestamp
            )
            closed_trade_id = (result.get("closed_trade") or {}).get("id")
        actions.append({
            "action": "sell",
            "symbol": symbol,
            "shares": round_money(quantity),
            "price": round_money(price),
            "notional": round_money(quantity * price),
            "trade_id": closed_trade_id,
        })
        remaining -= quantity
    return actions


def parse_brief_date(value):
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).date()
    except ValueError:
        return None


def normalize_eastern(now=None):
    if now is None:
        return datetime.now(EASTERN)
    if now.tzinfo is None:
        return now.replace(tzinfo=EASTERN)
    return now.astimezone(EASTERN)


def is_regular_market_hours(now=None):
    now = normalize_eastern(now)
    if now.isoweekday() > 5:
        return False
    return time(9, 30) <= now.time().replace(tzinfo=None) < time(16, 0)


def build_core_rebalance_approval_id(created_at):
    clean = "".join(char for char in created_at if char.isdigit())
    return f"core-rebalance-{clean or 'manual'}"


def top_sector_context(macro_report):
    sectors = macro_report.get("sector_rotation", {}).get("sectors", [])[:3]
    return [
        {
            "sector": item.get("sector"),
            "ticker": item.get("ticker"),
            "relative_to_spy_20d": item.get("relative_to_spy_20d"),
        }
        for item in sectors
    ]


def pct(value, total):
    return 0 if not total else value / total


def round_money(value):
    return round(float(value or 0), 2)


def round_pct(value):
    return round(float(value or 0), 4)


def format_core_etf_sleeve_report(report):
    lines = [
        "# Core ETF Sleeve",
        "",
        f"Regime: {report['regime']}",
        f"Status: {report['status']}",
        f"Target Sleeve: {format_pct(report['target_sleeve_pct'])} / ${report['target_sleeve_value']:.2f}",
        f"Current Sleeve: {format_pct(report['current_sleeve_pct'])} / ${report['current_sleeve_value']:.2f}",
        f"Drift: ${report['drift_value']:.2f} ({format_pct(report['drift_pct'])})",
        "",
        "## Desired Allocation",
    ]

    for row in report["desired_allocations"]:
        lines.append(
            f"- {row['symbol']}: target {format_pct(row['target_weight'])}, "
            f"${row['target_value']:.2f}; current ${row['current_value']:.2f}; "
            f"difference ${row['difference']:.2f}; "
            f"approx shares {row['suggested_shares']}"
        )

    lines.extend(["", "## Actions"])
    lines.extend([f"- {item}" for item in report["actions"]] or ["- None."])
    return "\n".join(lines) + "\n"


def format_pct(value):
    return f"{float(value or 0) * 100:.1f}%"


def format_money_plain(value):
    return f"{float(value or 0):.2f}"
