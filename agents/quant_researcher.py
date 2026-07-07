from datetime import datetime
from pathlib import Path

from data.market_data import get_ohlcv_history


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports" / "backtests"


def backtest_sma_trend_strategy(ticker):
    ticker = ticker.upper().strip()
    history = get_ohlcv_history(ticker, period="2y")

    if history.get("error"):
        return {
            "agent": "Quantitative Researcher",
            "symbol": ticker,
            "strategy": "sma_20_50_trend",
            "error": history["error"],
        }

    rows = history["rows"]
    trades = []
    in_trade = False
    entry = None
    entry_date = None

    closes = [row["close"] for row in rows]
    for index in range(50, len(rows)):
        sma20 = average(closes[index - 20:index])
        sma50 = average(closes[index - 50:index])
        close = rows[index]["close"]

        if not in_trade and close > sma20 > sma50:
            in_trade = True
            entry = close
            entry_date = rows[index]["date"]
        elif in_trade and close < sma20:
            trades.append({
                "entry_date": entry_date,
                "exit_date": rows[index]["date"],
                "entry": entry,
                "exit": close,
                "return_pct": ((close - entry) / entry) * 100,
            })
            in_trade = False
            entry = None
            entry_date = None

    returns = [trade["return_pct"] for trade in trades]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    gross_wins = sum(wins)
    gross_losses = abs(sum(losses))

    return {
        "agent": "Quantitative Researcher",
        "symbol": ticker,
        "strategy": "sma_20_50_trend",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "sample_size": len(trades),
        "win_rate": len(wins) / len(trades) if trades else None,
        "average_return_pct": average(returns),
        "average_win_pct": average(wins),
        "average_loss_pct": average(losses),
        "expectancy_pct": average(returns),
        "profit_factor": gross_wins / gross_losses if gross_losses else None,
        "tested": bool(trades),
        "warning": "Starter backtest. Does not include slippage, costs, shorting, survivorship controls, or out-of-sample validation.",
        "recent_trades": trades[-5:],
    }


def format_backtest_report(report):
    lines = [
        "# Backtest Expectancy Report",
        "",
        f"Symbol: {report['symbol']}",
        f"Strategy: {report['strategy']}",
    ]

    if report.get("error"):
        lines.append(f"Error: {report['error']}")
        return "\n".join(lines) + "\n"

    lines.extend([
        f"Timestamp: {report['timestamp']}",
        f"Tested: {report['tested']}",
        f"Sample Size: {report['sample_size']}",
        f"Win Rate: {format_pct(report['win_rate'])}",
        f"Expectancy: {format_number(report['expectancy_pct'])}%",
        f"Average Win: {format_number(report['average_win_pct'])}%",
        f"Average Loss: {format_number(report['average_loss_pct'])}%",
        f"Profit Factor: {format_number(report['profit_factor'])}",
        "",
        f"Warning: {report['warning']}",
    ])
    return "\n".join(lines) + "\n"


def save_backtest_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{report['symbol']}_backtest_report.md"
    path.write_text(format_backtest_report(report), encoding="utf-8")
    return path


def average(values):
    if not values:
        return None
    return sum(values) / len(values)


def format_number(value):
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def format_pct(value):
    if value is None:
        return "n/a"
    return f"{value * 100:.2f}%"
