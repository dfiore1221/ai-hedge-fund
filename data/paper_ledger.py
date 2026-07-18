import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from data.trade_journal import (
    CLOSED_STATUS,
    enrich_trade_metrics,
    load_trade_journal,
    normalize_side,
    normalize_status,
    to_float,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO_PATH = PROJECT_ROOT / "portfolio" / "holdings.json"
DEFAULT_STARTING_CASH = 100000.0


def build_paper_ledger(frame=None):
    journal = enrich_trade_metrics(frame if frame is not None else load_trade_journal())
    starting_cash = load_starting_cash()
    raw_transactions = []
    planned_orders = []

    for _, trade in journal.iterrows():
        normalized = normalize_trade(trade)
        if not normalized["symbol"] or normalized["shares"] <= 0 or normalized["entry"] <= 0:
            continue
        status = normalized["status"]
        if status == "planned":
            planned_orders.append(build_planned_order(normalized))
            continue
        if status in {"open", CLOSED_STATUS}:
            raw_transactions.append(build_open_transaction(normalized))
        if status == CLOSED_STATUS and normalized["exit_price"] > 0:
            raw_transactions.append(build_close_transaction(normalized))

    transactions = add_cash_balances(raw_transactions, starting_cash)
    positions = build_open_positions(journal)
    account = summarize_account(starting_cash, transactions, positions, planned_orders, journal)

    return {
        "account": account,
        "positions": positions,
        "transactions": transactions,
        "planned_orders": planned_orders,
        "warnings": build_warnings(account),
    }


def normalize_trade(trade):
    side = normalize_side(trade.get("side"))
    status = normalize_status(trade.get("status"))
    shares = to_float(trade.get("shares"))
    entry = to_float(trade.get("entry"))
    current_price = to_float(trade.get("current_price")) or entry
    exit_price = to_float(trade.get("exit_price"))
    return {
        "id": str(trade.get("id", "")).strip(),
        "opened_at": str(trade.get("opened_at", "")).strip(),
        "closed_at": str(trade.get("closed_at", "")).strip(),
        "symbol": str(trade.get("symbol", "")).upper().strip(),
        "side": side,
        "status": status,
        "shares": shares,
        "entry": entry,
        "current_price": current_price,
        "exit_price": exit_price,
        "stop": to_float(trade.get("stop")),
        "target": to_float(trade.get("target")),
        "planned_risk": to_float(trade.get("planned_risk")),
        "realized_pnl": to_float(trade.get("realized_pnl")),
        "unrealized_pnl": to_float(trade.get("unrealized_pnl")),
        "source": str(trade.get("source", "")).strip(),
        "agent_run_id": str(trade.get("agent_run_id", "")).strip(),
    }


def build_open_transaction(trade):
    gross = trade["entry"] * trade["shares"]
    action = "BUY_TO_OPEN" if trade["side"] == "long" else "SELL_SHORT"
    cash_delta = -gross if trade["side"] == "long" else gross
    return build_transaction(trade, trade["opened_at"], action, trade["entry"], gross, cash_delta)


def build_close_transaction(trade):
    gross = trade["exit_price"] * trade["shares"]
    action = "SELL_TO_CLOSE" if trade["side"] == "long" else "BUY_TO_COVER"
    cash_delta = gross if trade["side"] == "long" else -gross
    return build_transaction(trade, trade["closed_at"], action, trade["exit_price"], gross, cash_delta)


def build_transaction(trade, timestamp, action, price, gross, cash_delta):
    return {
        "timestamp": timestamp or datetime.now().isoformat(timespec="seconds"),
        "trade_id": trade["id"],
        "symbol": trade["symbol"],
        "side": trade["side"],
        "action": action,
        "quantity": trade["shares"],
        "price": price,
        "gross_amount": gross,
        "fees": 0.0,
        "cash_delta": cash_delta,
        "cash_balance": None,
        "source": trade["source"],
        "agent_run_id": trade["agent_run_id"],
    }


def add_cash_balances(transactions, starting_cash):
    balance = starting_cash
    ordered = sorted(transactions, key=transaction_sort_key)
    for transaction in ordered:
        balance += transaction["cash_delta"] - transaction["fees"]
        transaction["cash_balance"] = balance
    return ordered


def build_open_positions(journal):
    positions = {}
    for _, trade in journal.iterrows():
        normalized = normalize_trade(trade)
        if normalized["status"] != "open":
            continue
        key = (normalized["symbol"], normalized["side"])
        current = positions.setdefault(key, {
            "symbol": normalized["symbol"],
            "side": normalized["side"],
            "quantity": 0.0,
            "average_cost": 0.0,
            "cost_basis": 0.0,
            "last_price": 0.0,
            "market_value": 0.0,
            "unrealized_pnl": 0.0,
            "planned_risk": 0.0,
            "lots": 0,
        })
        shares = normalized["shares"]
        cost = normalized["entry"] * shares
        market_value = normalized["current_price"] * shares
        current["quantity"] += shares
        current["cost_basis"] += cost
        current["last_price"] = normalized["current_price"]
        current["market_value"] += market_value
        current["unrealized_pnl"] += directional_pnl(
            normalized["side"],
            normalized["entry"],
            normalized["current_price"],
            shares,
        )
        current["planned_risk"] += normalized["planned_risk"]
        current["lots"] += 1

    rows = []
    for position in positions.values():
        if position["quantity"]:
            position["average_cost"] = position["cost_basis"] / position["quantity"]
        rows.append(round_position(position))
    return sorted(rows, key=lambda item: item["symbol"])


def build_planned_order(trade):
    return {
        "trade_id": trade["id"],
        "symbol": trade["symbol"],
        "side": trade["side"],
        "quantity": trade["shares"],
        "entry": trade["entry"],
        "stop": trade["stop"],
        "target": trade["target"],
        "notional": trade["entry"] * trade["shares"],
        "planned_risk": trade["planned_risk"],
        "source": trade["source"],
        "agent_run_id": trade["agent_run_id"],
    }


def summarize_account(starting_cash, transactions, positions, planned_orders, journal):
    cash_balance = transactions[-1]["cash_balance"] if transactions else starting_cash
    long_market_value = sum(
        position["market_value"] for position in positions if position["side"] == "long"
    )
    short_market_value = sum(
        position["market_value"] for position in positions if position["side"] == "short"
    )
    market_value = long_market_value + short_market_value
    unrealized_pnl = sum(position["unrealized_pnl"] for position in positions)
    realized_pnl = sum(
        to_float(row.get("realized_pnl"))
        for _, row in journal.iterrows()
        if normalize_status(row.get("status")) == CLOSED_STATUS
    )
    net_liquidation_value = cash_balance + long_market_value - short_market_value
    planned_order_value = sum(order["notional"] for order in planned_orders)
    open_risk = sum(position["planned_risk"] for position in positions)
    buying_power = max(0.0, cash_balance)

    return {
        "starting_cash": round_money(starting_cash),
        "cash_balance": round_money(cash_balance),
        "buying_power": round_money(buying_power),
        "net_liquidation_value": round_money(net_liquidation_value),
        "equity": round_money(net_liquidation_value),
        "long_market_value": round_money(long_market_value),
        "short_market_value": round_money(short_market_value),
        "market_value": round_money(market_value),
        "planned_order_value": round_money(planned_order_value),
        "open_risk": round_money(open_risk),
        "realized_pnl": round_money(realized_pnl),
        "unrealized_pnl": round_money(unrealized_pnl),
        "total_pnl": round_money(realized_pnl + unrealized_pnl),
        "open_positions": len(positions),
        "planned_orders": len(planned_orders),
        "transactions": len(transactions),
    }


def build_warnings(account):
    warnings = []
    if account["cash_balance"] < 0:
        warnings.append("Cash balance is negative; simulated account is using margin-like exposure.")
    if account["planned_order_value"] > account["buying_power"]:
        warnings.append("Planned order value exceeds current buying power.")
    warnings.append("Paper ledger excludes commissions, slippage, dividends, interest, borrow fees, and tax lots.")
    return warnings


def load_starting_cash():
    if not PORTFOLIO_PATH.exists():
        return DEFAULT_STARTING_CASH
    try:
        portfolio = json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return DEFAULT_STARTING_CASH
    return float(portfolio.get("cash", DEFAULT_STARTING_CASH))


def transaction_sort_key(transaction):
    return parse_datetime(transaction.get("timestamp")) or datetime.min


def parse_datetime(value):
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def directional_pnl(side, entry, current_or_exit, shares):
    if side == "short":
        return (entry - current_or_exit) * shares
    return (current_or_exit - entry) * shares


def round_position(position):
    return {
        key: round_money(value) if isinstance(value, float) else value
        for key, value in position.items()
    }


def round_money(value):
    return round(float(value or 0), 2)


def format_paper_ledger_summary(ledger):
    account = ledger["account"]
    lines = [
        "# Paper Account Ledger",
        "",
        f"Starting Cash: {account['starting_cash']:.2f}",
        f"Cash Balance: {account['cash_balance']:.2f}",
        f"Buying Power: {account['buying_power']:.2f}",
        f"Net Liquidation Value: {account['net_liquidation_value']:.2f}",
        f"Market Value: {account['market_value']:.2f}",
        f"Planned Order Value: {account['planned_order_value']:.2f}",
        f"Open Risk: {account['open_risk']:.2f}",
        f"Realized P&L: {account['realized_pnl']:.2f}",
        f"Unrealized P&L: {account['unrealized_pnl']:.2f}",
        f"Total P&L: {account['total_pnl']:.2f}",
        f"Open Positions: {account['open_positions']}",
        f"Planned Orders: {account['planned_orders']}",
        f"Transactions: {account['transactions']}",
        "",
        "## Warnings",
    ]
    lines.extend([f"- {item}" for item in ledger["warnings"]] or ["- None."])
    return "\n".join(lines) + "\n"
