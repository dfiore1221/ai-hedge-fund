from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd

from data.paper_fills import fetch_price_snapshot
from data.trade_journal import (
    CLOSED_STATUS,
    close_trade,
    load_trade_journal,
    normalize_status,
    partial_close_trade,
    save_trade_journal,
    to_float,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXIT_ORDERS_PATH = PROJECT_ROOT / "portfolio" / "exit_orders.csv"

EXIT_ORDER_COLUMNS = [
    "id",
    "created_at",
    "trade_id",
    "symbol",
    "side",
    "status",
    "order_type",
    "scheduled_for",
    "shares",
    "reason",
    "lessons",
    "fill_price",
    "filled_at",
    "notes",
]

PENDING_STATUS = "pending"
FILLED_STATUS = "filled"
CANCELED_STATUS = "canceled"


def ensure_exit_orders():
    EXIT_ORDERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not EXIT_ORDERS_PATH.exists():
        frame = pd.DataFrame(columns=EXIT_ORDER_COLUMNS)
        frame.to_csv(EXIT_ORDERS_PATH, index=False)
        return frame

    frame = pd.read_csv(EXIT_ORDERS_PATH, dtype=str).fillna("")
    changed = False
    for column in EXIT_ORDER_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
            changed = True

    frame = frame[EXIT_ORDER_COLUMNS]
    if changed:
        frame.to_csv(EXIT_ORDERS_PATH, index=False)
    return frame


def save_exit_orders(frame):
    EXIT_ORDERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    frame = normalize_exit_orders(frame)
    frame.to_csv(EXIT_ORDERS_PATH, index=False)
    return frame


def normalize_exit_orders(frame):
    if frame is None or frame.empty:
        return pd.DataFrame(columns=EXIT_ORDER_COLUMNS)
    frame = frame.copy().fillna("")
    for column in EXIT_ORDER_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    for column in EXIT_ORDER_COLUMNS:
        frame[column] = frame[column].astype("object")
    frame["symbol"] = frame["symbol"].astype(str).str.upper().str.strip()
    frame["status"] = frame["status"].astype(str).str.lower().str.strip()
    return frame[EXIT_ORDER_COLUMNS]


def create_exit_order(
    trade_id,
    shares="all",
    order_type="market_at_open",
    scheduled_for=None,
    reason="Human-approved exit",
    lessons="",
    notes="",
):
    journal = load_trade_journal()
    match = journal["id"].astype(str) == str(trade_id)
    if not match.any():
        raise ValueError(f"No trade found with id {trade_id}.")

    trade = journal[match].iloc[0]
    if normalize_status(trade.get("status")) != "open":
        raise ValueError("Exit orders can only be created for open trades.")

    open_shares = to_float(trade.get("shares"))
    requested_shares = open_shares if str(shares).lower() == "all" else float(shares)
    if requested_shares <= 0:
        raise ValueError("Exit order shares must be greater than zero.")
    if requested_shares > open_shares:
        raise ValueError("Exit order shares cannot exceed the open share count.")

    orders = ensure_exit_orders()
    pending_match = (
        (orders["trade_id"].astype(str) == str(trade_id))
        & (orders["status"].astype(str).str.lower() == PENDING_STATUS)
    )
    if pending_match.any():
        raise ValueError(f"A pending exit order already exists for {trade_id}.")

    order = {
        "id": new_exit_order_id(),
        "created_at": now_iso(),
        "trade_id": str(trade_id),
        "symbol": str(trade.get("symbol", "")).upper().strip(),
        "side": str(trade.get("side", "")).lower().strip(),
        "status": PENDING_STATUS,
        "order_type": order_type,
        "scheduled_for": scheduled_for or date.today().isoformat(),
        "shares": f"{requested_shares:g}",
        "reason": reason,
        "lessons": lessons,
        "fill_price": "",
        "filled_at": "",
        "notes": notes,
    }
    orders = pd.concat([orders, pd.DataFrame([order])], ignore_index=True)
    save_exit_orders(orders)
    return order


def cancel_exit_order(order_id, notes=""):
    orders = ensure_exit_orders()
    match = orders["id"].astype(str) == str(order_id)
    if not match.any():
        raise ValueError(f"No exit order found with id {order_id}.")
    index = orders[match].index[0]
    if orders.at[index, "status"] != PENDING_STATUS:
        raise ValueError("Only pending exit orders can be canceled.")
    orders.at[index, "status"] = CANCELED_STATUS
    orders.at[index, "notes"] = append_note(orders.at[index, "notes"], notes or "Canceled.")
    save_exit_orders(orders)
    return orders.loc[index].to_dict()


def mark_exit_order_filled(order_id, fill_price, filled_at=None, notes=""):
    orders = ensure_exit_orders()
    match = orders["id"].astype(str) == str(order_id)
    if not match.any():
        raise ValueError(f"No exit order found with id {order_id}.")
    index = orders[match].index[0]
    orders.at[index, "status"] = FILLED_STATUS
    orders.at[index, "fill_price"] = str(fill_price)
    orders.at[index, "filled_at"] = filled_at or now_iso()
    orders.at[index, "notes"] = append_note(orders.at[index, "notes"], notes)
    save_exit_orders(orders)
    return orders.loc[index].to_dict()


def process_exit_orders(apply=False, price_map=None, today=None):
    orders = ensure_exit_orders()
    journal = load_trade_journal()
    today = today or date.today().isoformat()
    pending = due_pending_orders(orders, today)
    symbols = sorted(set(pending["symbol"].dropna().astype(str).str.upper())) if not pending.empty else []
    price_snapshot = {"prices": price_map or {}, "metadata": {}, "provider_status": "manual"}
    if price_map is None:
        price_snapshot = fetch_price_snapshot(symbols)
    prices = price_snapshot["prices"]

    events = []
    applied_events = []
    updated_orders = orders.copy()

    for index, order in pending.iterrows():
        symbol = str(order.get("symbol", "")).upper().strip()
        if not symbol or symbol not in prices:
            continue
        latest = to_float(prices[symbol])
        if latest <= 0:
            continue

        event = build_exit_order_event(order, latest)
        if not validate_open_trade(journal, event):
            event["reason"] = f"{event['reason']}; skipped because trade is not open"
            events.append(event)
            continue

        events.append(event)
        if apply:
            apply_exit_order_event(updated_orders, index, event)
            apply_trade_close(event)
            applied_events.append(event)
            journal = load_trade_journal()

    if apply and applied_events:
        save_exit_orders(updated_orders)

    return {
        "applied": bool(apply),
        "events": events,
        "applied_events": applied_events,
        "prices": prices,
        "price_metadata": price_snapshot.get("metadata", {}),
        "price_provider_status": price_snapshot.get("provider_status", ""),
        "checked_symbols": symbols,
        "orders": normalize_exit_orders(updated_orders),
    }


def due_pending_orders(orders, today):
    orders = normalize_exit_orders(orders)
    if orders.empty:
        return orders
    status_match = orders["status"].astype(str).str.lower() == PENDING_STATUS
    due_match = orders["scheduled_for"].astype(str).str.strip().le(str(today))
    return orders[status_match & due_match].copy()


def build_exit_order_event(order, latest):
    now = now_iso()
    return {
        "order_id": str(order.get("id", "")),
        "trade_id": str(order.get("trade_id", "")),
        "symbol": str(order.get("symbol", "")).upper().strip(),
        "side": str(order.get("side", "")).lower().strip(),
        "event_type": "human_exit_order",
        "reason": str(order.get("reason", "")).strip() or "Human-approved exit",
        "lessons": str(order.get("lessons", "")).strip(),
        "latest_price": round_number(latest),
        "fill_price": round_number(latest),
        "timestamp": now,
        "shares": to_float(order.get("shares")),
        "old_status": "open",
        "new_status": CLOSED_STATUS,
    }


def validate_open_trade(journal, event):
    match = journal["id"].astype(str) == str(event["trade_id"])
    if not match.any():
        return False
    trade = journal[match].iloc[0]
    return normalize_status(trade.get("status")) == "open"


def apply_exit_order_event(orders, index, event):
    orders.at[index, "status"] = FILLED_STATUS
    orders.at[index, "fill_price"] = str(event["fill_price"])
    orders.at[index, "filled_at"] = event["timestamp"]
    orders.at[index, "notes"] = append_note(
        orders.at[index, "notes"],
        f"Filled at {event['fill_price']} on {event['timestamp']}.",
    )


def apply_trade_close(event):
    journal = load_trade_journal()
    match = journal["id"].astype(str) == str(event["trade_id"])
    if not match.any():
        raise ValueError(f"No trade found with id {event['trade_id']}.")
    trade = journal[match].iloc[0]
    open_shares = to_float(trade.get("shares"))
    shares = to_float(event.get("shares"))
    if shares and shares < open_shares:
        partial_close_trade(
            event["trade_id"],
            shares,
            event["fill_price"],
            exit_reason=event["reason"],
            lessons=event["lessons"],
            closed_at=event["timestamp"],
        )
        return
    close_trade(
        event["trade_id"],
        event["fill_price"],
        exit_reason=event["reason"],
        lessons=event["lessons"],
        closed_at=event["timestamp"],
    )


def format_exit_order_report(result):
    lines = [
        "# Exit Order Check",
        "",
        f"Mode: {'Applied' if result.get('applied') else 'Preview'}",
        f"Symbols Checked: {len(result.get('checked_symbols', []))}",
        f"Events: {len(result.get('events', []))}",
        f"Applied Events: {len(result.get('applied_events', []))}",
        "",
    ]

    events = result.get("events", [])
    if not events:
        lines.append("No due human-approved exit orders were ready.")
        return "\n".join(lines)

    for event in events:
        lines.append(
            "- {symbol} human exit: trade {trade_id}, order {order_id}, "
            "{shares:g} shares at {fill_price} (latest {latest_price}); {reason}".format(**event)
        )
    return "\n".join(lines)


def format_exit_order_list(orders=None):
    orders = normalize_exit_orders(orders if orders is not None else ensure_exit_orders())
    lines = ["# Exit Orders", ""]
    if orders.empty:
        lines.append("No exit orders found.")
        return "\n".join(lines)
    for _, order in orders.iterrows():
        lines.append(
            "- {id}: {symbol} trade {trade_id}, {shares} shares, {status}, "
            "{order_type}, scheduled {scheduled_for}, fill {fill_price}".format(**order.to_dict())
        )
    return "\n".join(lines)


def append_note(existing, note):
    existing = str(existing or "").strip()
    note = str(note or "").strip()
    if not note:
        return existing
    return f"{existing}\n{note}" if existing else note


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def new_exit_order_id():
    today = date.today().strftime("%Y%m%d")
    return f"X-{today}-{uuid4().hex[:6].upper()}"


def round_number(value):
    return round(float(value), 4)
