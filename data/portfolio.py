import json
from pathlib import Path

from data.trade_journal import OPEN_STATUSES, enrich_trade_metrics, load_trade_journal, to_float


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO_PATH = PROJECT_ROOT / "portfolio" / "holdings.json"


def load_portfolio():
    if not PORTFOLIO_PATH.exists():
        return default_portfolio()

    return json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))


def save_default_portfolio():
    PORTFOLIO_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not PORTFOLIO_PATH.exists():
        PORTFOLIO_PATH.write_text(
            json.dumps(default_portfolio(), indent=2),
            encoding="utf-8",
        )
    return PORTFOLIO_PATH


def default_portfolio():
    return {
        "account_name": "paper",
        "cash": 100000,
        "positions": [],
        "notes": "Local paper portfolio placeholder. Edit this file when you want Risk Manager to see holdings.",
    }


def analyze_portfolio_exposure(ticker=None, portfolio=None, correlated_symbols=None, include_journal=True):
    portfolio = portfolio or load_portfolio()
    correlated_symbols = set(correlated_symbols or [])
    positions = list(portfolio.get("positions", []))
    if include_journal:
        positions.extend(journal_positions())
    cash = float(portfolio.get("cash", 0))
    gross_position_value = sum(position_value(position) for position in positions)
    open_position_value = sum(
        position_value(position)
        for position in positions
        if str(position.get("status", "open")).lower() == "open"
    )
    planned_position_value = sum(
        position_value(position)
        for position in positions
        if str(position.get("status", "")).lower() == "planned"
    )
    liquid_cash = max(0, cash - open_position_value)
    total_value = cash + unrealized_pnl_from_positions(positions)

    symbol = ticker.upper().strip() if ticker else None
    current_symbol_value = sum(
        position_value(position)
        for position in positions
        if position.get("symbol", "").upper() == symbol
    )
    correlated_value = sum(
        position_value(position)
        for position in positions
        if position.get("symbol", "").upper() in correlated_symbols
    )

    return {
        "account_name": portfolio.get("account_name"),
        "cash": cash,
        "liquid_cash": liquid_cash,
        "total_value": total_value,
        "gross_position_value": gross_position_value,
        "open_position_value": open_position_value,
        "planned_position_value": planned_position_value,
        "positions_count": len(positions),
        "symbol": symbol,
        "current_symbol_value": current_symbol_value,
        "current_symbol_exposure_pct": pct(current_symbol_value, total_value),
        "correlated_value": correlated_value,
        "correlated_exposure_pct": pct(correlated_value, total_value),
        "positions": positions,
    }


def position_value(position):
    quantity = float(position.get("quantity", 0))
    price = float(position.get("last_price", position.get("cost_basis", 0)))
    return abs(quantity * price)


def journal_positions():
    journal = enrich_trade_metrics(load_trade_journal())
    if journal.empty:
        return []

    positions = []
    for _, trade in journal.iterrows():
        if str(trade.get("status", "")).lower() not in OPEN_STATUSES:
            continue
        symbol = str(trade.get("symbol", "")).upper().strip()
        shares = to_float(trade.get("shares"))
        if not symbol or not shares:
            continue
        entry = to_float(trade.get("entry"))
        current_price = to_float(trade.get("current_price")) or entry
        side = str(trade.get("side", "long")).lower()
        signed_quantity = -shares if side == "short" else shares
        positions.append({
            "symbol": symbol,
            "quantity": signed_quantity,
            "cost_basis": entry,
            "last_price": current_price,
            "source": "trade_journal",
            "status": trade.get("status"),
            "trade_id": trade.get("id"),
            "unrealized_pnl": to_float(trade.get("unrealized_pnl")),
        })
    return positions


def unrealized_pnl_from_positions(positions):
    return sum(float(position.get("unrealized_pnl", 0) or 0) for position in positions)


def pct(value, total):
    if not total:
        return 0
    return (value / total) * 100


def format_portfolio_exposure(exposure):
    lines = [
        "# Portfolio Exposure",
        "",
        f"Account: {exposure['account_name']}",
        f"Starting Cash: {exposure['cash']:.2f}",
        f"Estimated Liquid Cash: {exposure['liquid_cash']:.2f}",
        f"Estimated Portfolio Equity: {exposure['total_value']:.2f}",
        f"Open Position Value: {exposure['open_position_value']:.2f}",
        f"Planned Position Value: {exposure['planned_position_value']:.2f}",
        f"Positions: {exposure['positions_count']}",
        f"Symbol: {exposure['symbol'] or 'n/a'}",
        f"Symbol Exposure: {exposure['current_symbol_exposure_pct']:.2f}%",
        f"Correlated Exposure: {exposure['correlated_exposure_pct']:.2f}%",
        "",
        "## Positions",
    ]

    if not exposure["positions"]:
        lines.append("- None.")
    else:
        for position in exposure["positions"]:
            lines.append(
                f"- {position.get('symbol')}: qty {position.get('quantity')}, "
                f"value {position_value(position):.2f}"
            )

    return "\n".join(lines) + "\n"
