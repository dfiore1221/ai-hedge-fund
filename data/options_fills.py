from datetime import date, datetime

import yfinance as yf

from data.options_journal import (
    enrich_options_metrics,
    load_options_journal,
    normalize_status,
    save_options_journal,
    to_float,
)


def process_option_fills(apply=False):
    journal = enrich_options_metrics(load_options_journal())
    updated = journal.copy()
    events = []
    applied_events = []
    quote_errors = []

    for index, row in updated.iterrows():
        status = normalize_status(row.get("status"))
        if status not in {"planned", "open"}:
            continue
        quote = fetch_contract_quote(row)
        if quote.get("error"):
            quote_errors.append({"trade_id": row.get("id"), "symbol": row.get("symbol"), "error": quote["error"]})
            continue
        write_quote(updated, index, quote)
        event = planned_event(row, quote) if status == "planned" else exit_event(row, quote)
        if not event:
            continue
        events.append(event)
        if apply:
            apply_event(updated, index, event)
            applied_events.append(event)

    updated = enrich_options_metrics(updated)
    if apply and (applied_events or not updated.equals(journal)):
        save_options_journal(updated)
    return {
        "applied": bool(apply),
        "events": events,
        "applied_events": applied_events,
        "quote_errors": quote_errors,
        "journal": updated,
    }


def fetch_contract_quote(row):
    symbol = str(row.get("symbol") or "").upper().strip()
    expiration = str(row.get("expiration") or "").strip()
    contract_symbol = str(row.get("contract_symbol") or "").strip()
    option_type = str(row.get("option_type") or "call").lower()
    strike = to_float(row.get("strike"))
    if not symbol or not expiration:
        return {"error": "Missing underlying symbol or expiration."}
    try:
        chain = yf.Ticker(symbol).option_chain(expiration)
    except Exception as exc:
        return {"error": f"Option chain unavailable: {exc}"}
    frame = chain.puts if option_type == "put" else chain.calls
    if frame is None or frame.empty:
        return {"error": "Option chain is empty."}
    match = frame[frame["contractSymbol"].astype(str) == contract_symbol] if contract_symbol else frame.iloc[0:0]
    if match.empty and strike:
        match = frame[(frame["strike"].astype(float) - strike).abs() < 0.001]
    if match.empty:
        return {"error": "Selected contract was not found in the current chain."}
    contract = match.iloc[0]
    bid = safe_float(contract.get("bid"))
    ask = safe_float(contract.get("ask"))
    if not bid or not ask or ask < bid:
        return {"error": "Contract has no usable two-sided quote."}
    midpoint = (bid + ask) / 2
    return {
        "contract_symbol": str(contract.get("contractSymbol") or contract_symbol),
        "bid": bid,
        "ask": ask,
        "midpoint": midpoint,
        "spread_pct": (ask - bid) / midpoint if midpoint else None,
        "volume": safe_float(contract.get("volume")) or 0,
        "open_interest": safe_float(contract.get("openInterest")) or 0,
        "implied_volatility": safe_float(contract.get("impliedVolatility")),
        "provider": "Yahoo Finance / yfinance",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "underlying_price": underlying_price(symbol),
    }


def planned_event(row, quote):
    limit = to_float(row.get("entry_premium"))
    trigger = to_float(row.get("underlying_entry_trigger"))
    trigger_direction = str(row.get("underlying_trigger_direction") or "at_or_below")
    underlying = quote.get("underlying_price")
    if trigger and underlying:
        triggered = underlying <= trigger if trigger_direction == "at_or_below" else underlying >= trigger
        if not triggered:
            return None
    if not limit or quote["ask"] > limit:
        return None
    return base_event(row, quote, "entry_fill", quote["ask"], "option premium limit reached", "planned", "open")


def exit_event(row, quote):
    target = to_float(row.get("target_premium"))
    stop = to_float(row.get("stop_premium"))
    conservative_exit = quote["bid"]
    expiration = parse_date(row.get("expiration"))
    if expiration and (expiration - date.today()).days <= 14:
        return base_event(row, quote, "exit_fill", conservative_exit, "option time stop reached at 14 DTE", "open", "closed")
    if stop and conservative_exit <= stop:
        return base_event(row, quote, "exit_fill", conservative_exit, "option premium stop reached", "open", "closed")
    if target and conservative_exit >= target:
        return base_event(row, quote, "exit_fill", conservative_exit, "option premium target reached", "open", "closed")
    return None


def base_event(row, quote, event_type, fill_price, reason, old_status, new_status):
    return {
        "trade_id": str(row.get("id") or ""),
        "symbol": str(row.get("symbol") or "").upper(),
        "contract_symbol": quote.get("contract_symbol"),
        "strategy": str(row.get("strategy") or ""),
        "event_type": event_type,
        "reason": reason,
        "fill_price": round(fill_price, 4),
        "bid": round(quote["bid"], 4),
        "ask": round(quote["ask"], 4),
        "timestamp": quote["timestamp"],
        "old_status": old_status,
        "new_status": new_status,
    }


def write_quote(frame, index, quote):
    frame.at[index, "contract_symbol"] = quote.get("contract_symbol")
    frame.at[index, "current_premium"] = round(quote.get("midpoint") or 0, 4)
    frame.at[index, "quote_provider"] = quote.get("provider")
    frame.at[index, "quote_timestamp"] = quote.get("timestamp")
    frame.at[index, "underlying_price"] = quote.get("underlying_price") or ""
    frame.at[index, "bid_ask_spread_pct"] = round(quote.get("spread_pct") or 0, 4)
    frame.at[index, "volume"] = quote.get("volume")
    frame.at[index, "open_interest"] = quote.get("open_interest")
    frame.at[index, "implied_volatility"] = quote.get("implied_volatility") or ""


def apply_event(frame, index, event):
    frame.at[index, "status"] = event["new_status"]
    if event["event_type"] == "entry_fill":
        frame.at[index, "entry_premium"] = event["fill_price"]
        frame.at[index, "current_premium"] = event["fill_price"]
        frame.at[index, "filled_at"] = event["timestamp"]
    else:
        frame.at[index, "exit_premium"] = event["fill_price"]
        frame.at[index, "closed_at"] = event["timestamp"]
        frame.at[index, "exit_reason"] = event["reason"]


def safe_float(value):
    try:
        if value is None or str(value) == "nan":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def underlying_price(symbol):
    try:
        value = getattr(yf.Ticker(symbol).fast_info, "last_price", None)
        return safe_float(value)
    except Exception:
        return None


def parse_date(value):
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None
