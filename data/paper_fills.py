from datetime import datetime

from data.tiingo_data import fetch_latest_equity_prices, is_tiingo_configured
from data.trade_journal import (
    CLOSED_STATUS,
    enrich_trade_metrics,
    fetch_latest_prices,
    load_trade_journal,
    normalize_side,
    normalize_status,
    save_trade_journal,
    to_float,
)


def process_paper_fills(frame=None, price_map=None, apply=False):
    """Evaluate simulated limit/stop/target rules against latest prices."""
    journal = enrich_trade_metrics(frame if frame is not None else load_trade_journal())
    symbols = symbols_to_check(journal)
    prices = price_map or fetch_price_map(symbols)
    now = datetime.now().isoformat(timespec="seconds")
    events = []
    updated = journal.copy()

    for index, row in updated.iterrows():
        symbol = str(row.get("symbol", "")).upper().strip()
        if not symbol or symbol not in prices:
            continue

        latest = to_float(prices[symbol])
        if latest <= 0:
            continue

        updated.at[index, "current_price"] = round_number(latest)
        status = normalize_status(row.get("status"))
        side = normalize_side(row.get("side"))
        entry = to_float(row.get("entry"))
        stop = to_float(row.get("stop"))
        target = to_float(row.get("target"))
        shares = to_float(row.get("shares"))

        if not entry or not shares:
            continue

        event = None
        if status == "planned":
            event = planned_fill_event(row, latest, side, entry, now)
        elif status == "open":
            event = open_exit_event(row, latest, side, stop, target, now)

        if not event:
            continue

        events.append(event)
        if apply:
            apply_event(updated, index, event)

    updated = enrich_trade_metrics(updated)
    if apply and events:
        save_trade_journal(updated)

    return {
        "applied": bool(apply),
        "events": events,
        "prices": prices,
        "checked_symbols": symbols,
        "journal": updated,
    }


def symbols_to_check(journal):
    symbols = []
    for _, row in journal.iterrows():
        status = normalize_status(row.get("status"))
        if status not in {"planned", "open"}:
            continue
        symbol = str(row.get("symbol", "")).upper().strip()
        entry = to_float(row.get("entry"))
        shares = to_float(row.get("shares"))
        if symbol and entry > 0 and shares > 0:
            symbols.append(symbol)
    return sorted(set(symbols))


def fetch_price_map(symbols):
    if not symbols:
        return {}

    if is_tiingo_configured():
        response = fetch_latest_equity_prices(symbols)
        prices = {}
        for symbol, item in response.get("prices", {}).items():
            close = item.get("close") if isinstance(item, dict) else None
            if close is not None:
                prices[symbol.upper()] = float(close)
        if prices:
            return prices

    return fetch_latest_prices(symbols)


def planned_fill_event(row, latest, side, entry, timestamp):
    hit = latest <= entry if side == "long" else latest >= entry
    if not hit:
        return None

    return {
        "trade_id": str(row.get("id", "")),
        "symbol": str(row.get("symbol", "")).upper().strip(),
        "side": side,
        "event_type": "entry_fill",
        "reason": "planned entry hit",
        "latest_price": round_number(latest),
        "fill_price": round_number(entry),
        "timestamp": timestamp,
        "old_status": "planned",
        "new_status": "open",
    }


def open_exit_event(row, latest, side, stop, target, timestamp):
    if side == "short":
        if stop and latest >= stop:
            return exit_event(row, latest, stop, "stop hit", timestamp)
        if target and latest <= target:
            return exit_event(row, latest, target, "target hit", timestamp)
        return None

    if stop and latest <= stop:
        return exit_event(row, latest, stop, "stop hit", timestamp)
    if target and latest >= target:
        return exit_event(row, latest, target, "target hit", timestamp)
    return None


def exit_event(row, latest, fill_price, reason, timestamp):
    return {
        "trade_id": str(row.get("id", "")),
        "symbol": str(row.get("symbol", "")).upper().strip(),
        "side": normalize_side(row.get("side")),
        "event_type": "exit_fill",
        "reason": reason,
        "latest_price": round_number(latest),
        "fill_price": round_number(fill_price),
        "timestamp": timestamp,
        "old_status": "open",
        "new_status": CLOSED_STATUS,
    }


def apply_event(journal, index, event):
    if event["event_type"] == "entry_fill":
        journal.at[index, "status"] = "open"
        journal.at[index, "opened_at"] = event["timestamp"]
        journal.at[index, "current_price"] = event["latest_price"]
        journal.at[index, "notes"] = append_note(
            journal.at[index, "notes"],
            f"Auto paper-filled at {event['fill_price']} on {event['timestamp']} "
            f"after latest price reached {event['latest_price']}.",
        )
        return

    if event["event_type"] == "exit_fill":
        journal.at[index, "status"] = CLOSED_STATUS
        journal.at[index, "closed_at"] = event["timestamp"]
        journal.at[index, "exit_price"] = event["fill_price"]
        journal.at[index, "current_price"] = event["latest_price"]
        journal.at[index, "exit_reason"] = event["reason"]


def append_note(existing, note):
    existing = str(existing or "").strip()
    return f"{existing}\n{note}" if existing else note


def round_number(value):
    return round(float(value), 4)


def format_paper_fill_report(result):
    lines = [
        "# Paper Fill Check",
        "",
        f"Mode: {'Applied' if result.get('applied') else 'Preview'}",
        f"Symbols Checked: {len(result.get('checked_symbols', []))}",
        f"Events: {len(result.get('events', []))}",
        "",
    ]

    events = result.get("events", [])
    if not events:
        lines.append("No paper fill conditions were hit.")
        return "\n".join(lines)

    for event in events:
        lines.append(
            "- {symbol} {event_type}: {old_status} -> {new_status} at {fill_price} "
            "(latest {latest_price}); {reason}; trade {trade_id}".format(**event)
        )
    return "\n".join(lines)
