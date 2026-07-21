import json
import os
import socket
from datetime import datetime

from data.paper_fills import fetch_price_map
from data.paper_ledger import build_paper_ledger
from data.trade_journal import (
    OPEN_STATUSES,
    enrich_trade_metrics,
    load_trade_journal,
    normalize_status,
    save_trade_journal,
    to_float,
)


def build_portfolio_ticker_status(refresh_prices=True, save_prices=True):
    journal = load_trade_journal()
    price_errors = []
    refreshed_symbols = []

    if refresh_prices and not journal.empty:
        symbols = open_symbols(journal)
        try:
            prices = fetch_price_map(symbols)
        except Exception as exc:
            prices = {}
            price_errors.append(str(exc))

        if prices:
            journal = journal.copy()
            journal["current_price"] = journal["current_price"].astype("object")
            for index, row in journal.iterrows():
                symbol = str(row.get("symbol", "")).upper().strip()
                if normalize_status(row.get("status")) not in OPEN_STATUSES:
                    continue
                if symbol in prices:
                    journal.at[index, "current_price"] = round(float(prices[symbol]), 4)
                    refreshed_symbols.append(symbol)

            journal = enrich_trade_metrics(journal)
            if save_prices:
                save_trade_journal(journal)

    ledger = build_paper_ledger(journal)
    account = ledger["account"]
    starting_cash = account.get("starting_cash") or 0
    total_pnl = account.get("total_pnl") or 0
    total_pnl_pct = (total_pnl / starting_cash * 100) if starting_cash else 0
    dashboard_port = choose_dashboard_port(os.getenv("DASHBOARD_PORT", "8501"))

    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "net_liquidation_value": account["net_liquidation_value"],
        "cash_balance": account["cash_balance"],
        "buying_power": account["buying_power"],
        "market_value": account["market_value"],
        "realized_pnl": account["realized_pnl"],
        "unrealized_pnl": account["unrealized_pnl"],
        "total_pnl": total_pnl,
        "total_pnl_pct": round(total_pnl_pct, 2),
        "open_positions": account["open_positions"],
        "planned_orders": account["planned_orders"],
        "open_risk": account["open_risk"],
        "symbols": open_symbols(journal),
        "refreshed_symbols": sorted(set(refreshed_symbols)),
        "price_errors": price_errors,
        "dashboard_url": f"http://localhost:{dashboard_port}",
        "warnings": ledger.get("warnings", []),
    }


def open_symbols(journal):
    symbols = []
    if journal is None or journal.empty:
        return symbols

    for _, row in journal.iterrows():
        if normalize_status(row.get("status")) not in OPEN_STATUSES:
            continue
        symbol = str(row.get("symbol", "")).upper().strip()
        shares = to_float(row.get("shares"))
        if symbol and shares > 0:
            symbols.append(symbol)

    return sorted(set(symbols))


def format_portfolio_ticker_status(status):
    direction = "+" if status["total_pnl"] >= 0 else "-"
    return (
        f"AIHF {money(status['net_liquidation_value'])} "
        f"{direction}{money(abs(status['total_pnl']))} "
        f"({status['total_pnl_pct']:+.2f}%)"
    )


def status_to_json(status):
    return json.dumps(status, indent=2, sort_keys=True)


def choose_dashboard_port(default_port):
    for port in [default_port, "8501", "8502", "8503"]:
        if port and localhost_port_open(int(port)):
            return str(port)
    return str(default_port)


def localhost_port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def money(value):
    return f"${float(value or 0):,.2f}"
