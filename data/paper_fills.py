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


def process_paper_fills(frame=None, price_map=None, apply=False, allow_entry_fills=True, allow_exit_fills=True):
    """Evaluate simulated limit/stop/target rules against latest prices."""
    exit_order_result = process_human_exit_orders(apply=apply, price_map=price_map)
    journal = enrich_trade_metrics(frame if frame is not None else load_trade_journal())
    symbols = symbols_to_check(journal)
    price_snapshot = {"prices": price_map or {}, "metadata": {}, "provider_status": "manual"}
    if price_map is None:
        price_snapshot = fetch_price_snapshot(symbols)
    prices = price_snapshot["prices"]
    now = datetime.now().isoformat(timespec="seconds")
    events = []
    applied_events = []
    blocked_quote_events = []
    updated = journal.copy()
    human_exit_trade_ids = {
        str(event.get("trade_id", ""))
        for event in exit_order_result.get("events", [])
        if event.get("trade_id")
    }

    for index, row in updated.iterrows():
        trade_id = str(row.get("id", ""))
        if trade_id in human_exit_trade_ids:
            continue

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

        if status in {"planned", "open"} and not execution_price_is_fresh(
            price_snapshot.get("metadata", {}).get(symbol, {}),
            manual_price_map=price_map is not None,
        ):
            blocked_quote_events.append({
                "trade_id": trade_id,
                "symbol": symbol,
                "status": status,
                "latest_price": round_number(latest),
                "reason": "Execution blocked because only a stale or daily-fallback quote was available.",
                "price_metadata": price_snapshot.get("metadata", {}).get(symbol, {}),
            })
            continue

        event = None
        if status == "planned":
            event = planned_fill_event(row, latest, side, entry, now)
        elif status == "open":
            event = open_exit_event(row, latest, side, stop, target, now)

        if not event:
            continue

        events.append(event)
        is_entry = event.get("event_type") == "entry_fill"
        is_exit = event.get("event_type") == "exit_fill"
        can_apply = (is_entry and allow_entry_fills) or (is_exit and allow_exit_fills)
        if apply and can_apply:
            apply_event(updated, index, event)
            applied_events.append(event)

    updated = enrich_trade_metrics(updated)
    if apply and applied_events:
        save_trade_journal(updated)

    return {
        "applied": bool(apply),
        "allow_entry_fills": bool(allow_entry_fills),
        "allow_exit_fills": bool(allow_exit_fills),
        "exit_order_result": exit_order_result,
        "events": events,
        "applied_events": applied_events,
        "blocked_quote_events": blocked_quote_events,
        "prices": prices,
        "price_metadata": price_snapshot.get("metadata", {}),
        "price_provider_status": price_snapshot.get("provider_status", ""),
        "checked_symbols": symbols,
        "journal": updated,
    }


def process_human_exit_orders(apply=False, price_map=None):
    from data.exit_orders import process_exit_orders

    return process_exit_orders(apply=apply, price_map=price_map)


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
    return fetch_price_snapshot(symbols)["prices"]


def fetch_price_snapshot(symbols):
    if not symbols:
        return {"prices": {}, "metadata": {}, "provider_status": "skipped"}

    if is_tiingo_configured():
        response = fetch_latest_equity_prices(symbols)
        prices = {}
        metadata = {}
        for symbol, item in response.get("prices", {}).items():
            close = item.get("close") if isinstance(item, dict) else None
            if close is not None:
                normalized = symbol.upper()
                prices[normalized] = float(close)
                metadata[normalized] = {
                    "provider": item.get("provider", response.get("provider")),
                    "timestamp": item.get("timestamp"),
                    "freshness": item.get("freshness", "unknown"),
                    "source_field": item.get("source_field"),
                    "cache": response.get("cache"),
                    "fallback_reason": item.get("fallback_reason", ""),
                }
        if prices:
            return {
                "prices": prices,
                "metadata": metadata,
                "provider_status": response.get("status", "ok"),
                "provider_errors": response.get("errors", {}),
            }

    fallback_prices = fetch_latest_prices(symbols)
    return {
        "prices": fallback_prices,
        "metadata": {
            symbol: {
                "provider": "market_data_fallback",
                "freshness": "fallback",
                "timestamp": "",
                "source_field": "latest",
                "cache": {},
                "fallback_reason": "Tiingo latest unavailable",
            }
            for symbol in fallback_prices
        },
        "provider_status": "fallback" if fallback_prices else "error",
    }


def planned_fill_event(row, latest, side, entry, timestamp):
    hit = latest <= entry if side == "long" else latest >= entry
    if not hit:
        return None

    fill_price = min(latest, entry) if side == "long" else max(latest, entry)

    return {
        "trade_id": str(row.get("id", "")),
        "symbol": str(row.get("symbol", "")).upper().strip(),
        "side": side,
        "event_type": "entry_fill",
        "reason": "planned entry hit",
        "latest_price": round_number(latest),
        "fill_price": round_number(fill_price),
        "timestamp": timestamp,
        "old_status": "planned",
        "new_status": "open",
    }


def execution_price_is_fresh(metadata, manual_price_map=False):
    if manual_price_map:
        return True
    metadata = metadata or {}
    freshness = str(metadata.get("freshness") or "").lower()
    source_field = str(metadata.get("source_field") or "").lower()
    if "fallback" in freshness or "stale" in freshness or source_field in {"daily_close", "previous_close"}:
        return False
    return freshness in {"intraday", "intraday_or_latest", "live", "real_time", "realtime"}


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
        journal.at[index, "entry"] = str(event["fill_price"])
        journal.at[index, "current_price"] = str(event["latest_price"])
        journal.at[index, "notes"] = append_note(
            journal.at[index, "notes"],
            f"Auto paper-filled at {event['fill_price']} on {event['timestamp']} "
            f"after latest price reached {event['latest_price']}.",
        )
        return

    if event["event_type"] == "exit_fill":
        journal.at[index, "status"] = CLOSED_STATUS
        journal.at[index, "closed_at"] = event["timestamp"]
        journal.at[index, "exit_price"] = str(event["fill_price"])
        journal.at[index, "current_price"] = str(event["latest_price"])
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
        f"Mode: {format_fill_mode(result)}",
        f"Symbols Checked: {len(result.get('checked_symbols', []))}",
        f"Events: {len(result.get('events', []))}",
        f"Applied Events: {len(result.get('applied_events', []))}",
        f"Quote-Blocked Events: {len(result.get('blocked_quote_events', []))}",
        "",
    ]

    for blocked in result.get("blocked_quote_events", []):
        lines.append(
            f"- {blocked.get('symbol')}: {blocked.get('reason')} Latest {blocked.get('latest_price')}."
        )

    exit_order_result = result.get("exit_order_result") or {}
    exit_events = exit_order_result.get("events", [])
    if exit_order_result:
        lines.extend([
            "## Human-Approved Exit Orders",
            f"Events: {len(exit_events)}",
            f"Applied: {len(exit_order_result.get('applied_events', []))}",
            "",
        ])
        for event in exit_events:
            lines.append(
                "- {symbol} human exit: trade {trade_id}, order {order_id}, "
                "{shares:g} shares at {fill_price} (latest {latest_price}); {reason}".format(**event)
            )
        if exit_events:
            lines.append("")

    events = result.get("events", [])
    metadata = result.get("price_metadata") or {}
    fallback_symbols = [
        symbol for symbol, item in metadata.items()
        if item.get("freshness") and item.get("freshness") != "intraday_or_latest"
    ]
    if fallback_symbols:
        lines.append(
            "Price Source Note: fallback/cached/EOD prices used for "
            + ", ".join(sorted(fallback_symbols))
            + "."
        )
        lines.append("")

    if not events:
        lines.append("No paper fill conditions were hit.")
        return "\n".join(lines)

    for event in events:
        lines.append(
            "- {symbol} {event_type}: {old_status} -> {new_status} at {fill_price} "
            "(latest {latest_price}); {reason}; trade {trade_id}".format(**event)
        )
    return "\n".join(lines)


def format_fill_mode(result):
    if not result.get("applied"):
        return "Preview"
    if not result.get("allow_entry_fills") and result.get("allow_exit_fills"):
        return "Applied exits only; entries are alert-only"
    if result.get("allow_entry_fills") and not result.get("allow_exit_fills"):
        return "Applied entries only"
    return "Applied"
