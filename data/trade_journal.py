from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pandas as pd
import yfinance as yf


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRADE_JOURNAL_PATH = PROJECT_ROOT / "portfolio" / "trade_journal.csv"

TRADE_COLUMNS = [
    "id",
    "opened_at",
    "symbol",
    "side",
    "status",
    "setup_type",
    "source",
    "agent_run_id",
    "entry",
    "stop",
    "target",
    "shares",
    "risk_per_share",
    "planned_risk",
    "current_price",
    "unrealized_pnl",
    "closed_at",
    "exit_price",
    "realized_pnl",
    "r_multiple",
    "outcome",
    "thesis",
    "exit_reason",
    "lessons",
    "notes",
]

OPEN_STATUSES = {"planned", "open"}
CLOSED_STATUS = "closed"


def ensure_trade_journal():
    TRADE_JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not TRADE_JOURNAL_PATH.exists():
        frame = pd.DataFrame(columns=TRADE_COLUMNS)
        frame.to_csv(TRADE_JOURNAL_PATH, index=False)
        return frame

    frame = pd.read_csv(TRADE_JOURNAL_PATH, dtype=str).fillna("")
    changed = False

    for column in TRADE_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
            changed = True

    for index, row in frame.iterrows():
        if not str(row.get("id", "")).strip():
            frame.at[index, "id"] = new_trade_id()
            changed = True
        if not str(row.get("status", "")).strip():
            frame.at[index, "status"] = "open"
            changed = True

    frame = frame[TRADE_COLUMNS]
    if changed:
        frame.to_csv(TRADE_JOURNAL_PATH, index=False)
    return frame


def load_trade_journal():
    return ensure_trade_journal()


def save_trade_journal(frame):
    TRADE_JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    frame = normalize_frame(frame)
    frame.to_csv(TRADE_JOURNAL_PATH, index=False)
    return frame


def append_trade(row):
    journal = load_trade_journal()
    trade = {column: row.get(column, "") for column in TRADE_COLUMNS}
    trade["id"] = trade.get("id") or new_trade_id()
    trade["opened_at"] = trade.get("opened_at") or now_iso()
    trade["symbol"] = str(trade.get("symbol", "")).upper().strip()
    trade["side"] = normalize_side(trade.get("side"))
    trade["status"] = normalize_status(trade.get("status")) or "planned"

    journal = pd.concat([journal, pd.DataFrame([trade])], ignore_index=True)
    journal = enrich_trade_metrics(journal)
    save_trade_journal(journal)
    return trade["id"]


def open_trade_from_plan(
    symbol,
    entry,
    stop,
    target,
    shares,
    side="long",
    status="planned",
    setup_type="manual",
    source="manual",
    agent_run_id="",
    thesis="",
    notes="",
):
    return append_trade({
        "symbol": symbol,
        "side": side,
        "status": status,
        "setup_type": setup_type,
        "source": source,
        "agent_run_id": agent_run_id,
        "entry": entry,
        "stop": stop,
        "target": target,
        "shares": shares,
        "thesis": thesis,
        "notes": notes,
    })


def close_trade(trade_id, exit_price, exit_reason="", lessons=""):
    journal = load_trade_journal()
    match = journal["id"].astype(str) == str(trade_id)
    if not match.any():
        raise ValueError(f"No trade found with id {trade_id}.")

    index = journal[match].index[0]
    journal.at[index, "status"] = CLOSED_STATUS
    journal.at[index, "closed_at"] = now_iso()
    journal.at[index, "exit_price"] = exit_price
    journal.at[index, "exit_reason"] = exit_reason
    journal.at[index, "lessons"] = lessons

    journal = enrich_trade_metrics(journal)
    save_trade_journal(journal)
    return journal.loc[index].to_dict()


def enrich_trade_metrics(frame, refresh_prices=False):
    frame = normalize_frame(frame)
    price_map = {}
    if refresh_prices:
        symbols = sorted({
            str(row.get("symbol", "")).upper().strip()
            for _, row in frame.iterrows()
            if normalize_status(row.get("status")) in OPEN_STATUSES
        })
        price_map = fetch_latest_prices(symbols)

    for index, row in frame.iterrows():
        side = normalize_side(row.get("side"))
        status = normalize_status(row.get("status"))
        entry = to_float(row.get("entry"))
        stop = to_float(row.get("stop"))
        shares = to_float(row.get("shares"))
        exit_price = to_float(row.get("exit_price"))

        risk_per_share = abs(entry - stop) if entry and stop else 0
        planned_risk = risk_per_share * shares if shares else 0
        frame.at[index, "risk_per_share"] = round_number(risk_per_share)
        frame.at[index, "planned_risk"] = round_number(planned_risk)

        symbol = str(row.get("symbol", "")).upper().strip()
        if symbol in price_map:
            frame.at[index, "current_price"] = round_number(price_map[symbol])

        current_price = to_float(frame.at[index, "current_price"])
        if status in OPEN_STATUSES and current_price and entry and shares:
            unrealized_pnl = directional_pnl(side, entry, current_price, shares)
            frame.at[index, "unrealized_pnl"] = round_number(unrealized_pnl)
        elif status not in OPEN_STATUSES:
            frame.at[index, "unrealized_pnl"] = ""

        if status == CLOSED_STATUS and exit_price and entry and shares:
            realized_pnl = directional_pnl(side, entry, exit_price, shares)
            frame.at[index, "realized_pnl"] = round_number(realized_pnl)
            frame.at[index, "r_multiple"] = round_number(
                realized_pnl / planned_risk if planned_risk else 0
            )
            frame.at[index, "outcome"] = classify_outcome(realized_pnl)
        elif status != CLOSED_STATUS:
            frame.at[index, "realized_pnl"] = ""
            frame.at[index, "r_multiple"] = ""
            frame.at[index, "outcome"] = ""

    return frame


def summarize_trade_journal(frame):
    frame = enrich_trade_metrics(frame)
    if frame.empty:
        return {
            "open_trades": 0,
            "planned_trades": 0,
            "closed_trades": 0,
            "total_realized_pnl": 0,
            "today_realized_pnl": 0,
            "week_realized_pnl": 0,
            "open_unrealized_pnl": 0,
            "open_planned_risk": 0,
            "win_rate": 0,
            "avg_r_multiple": 0,
            "open_symbols": [],
        }

    statuses = frame["status"].map(normalize_status)
    open_frame = frame[statuses.isin(OPEN_STATUSES)]
    planned_frame = frame[statuses == "planned"]
    closed_frame = frame[statuses == CLOSED_STATUS]
    today_closed = filter_closed_since(closed_frame, date.today())
    week_closed = filter_closed_since(closed_frame, start_of_week(date.today()))
    closed_with_outcomes = closed_frame[closed_frame["outcome"].isin(["win", "loss", "breakeven"])]
    wins = len(closed_with_outcomes[closed_with_outcomes["outcome"] == "win"])

    return {
        "open_trades": len(open_frame),
        "planned_trades": len(planned_frame),
        "closed_trades": len(closed_frame),
        "total_realized_pnl": numeric_sum(closed_frame, "realized_pnl"),
        "today_realized_pnl": numeric_sum(today_closed, "realized_pnl"),
        "week_realized_pnl": numeric_sum(week_closed, "realized_pnl"),
        "open_unrealized_pnl": numeric_sum(open_frame, "unrealized_pnl"),
        "open_planned_risk": numeric_sum(open_frame, "planned_risk"),
        "win_rate": (wins / len(closed_with_outcomes) * 100) if len(closed_with_outcomes) else 0,
        "avg_r_multiple": numeric_mean(closed_frame, "r_multiple"),
        "open_symbols": sorted(open_frame["symbol"].dropna().astype(str).str.upper().unique().tolist()),
    }


def format_trade_journal_summary(summary):
    return "\n".join([
        "# Trade Journal Summary",
        "",
        f"Open trades: {summary['open_trades']}",
        f"Planned trades: {summary['planned_trades']}",
        f"Closed trades: {summary['closed_trades']}",
        f"Total realized P&L: {summary['total_realized_pnl']:.2f}",
        f"Today realized P&L: {summary['today_realized_pnl']:.2f}",
        f"Week realized P&L: {summary['week_realized_pnl']:.2f}",
        f"Open unrealized P&L: {summary['open_unrealized_pnl']:.2f}",
        f"Open planned risk: {summary['open_planned_risk']:.2f}",
        f"Win rate: {summary['win_rate']:.1f}%",
        f"Average R: {summary['avg_r_multiple']:.2f}",
        f"Open symbols: {', '.join(summary['open_symbols']) if summary['open_symbols'] else 'None'}",
    ])


def normalize_frame(frame):
    if frame is None or frame.empty:
        return pd.DataFrame(columns=TRADE_COLUMNS)

    frame = frame.copy().fillna("")
    for column in TRADE_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""

    for column in TRADE_COLUMNS:
        frame[column] = frame[column].astype("object")

    frame["symbol"] = frame["symbol"].astype(str).str.upper().str.strip()
    frame["side"] = frame["side"].map(normalize_side)
    frame["status"] = frame["status"].map(normalize_status)
    return frame[TRADE_COLUMNS]


def fetch_latest_prices(symbols):
    prices = {}
    for symbol in symbols:
        if not symbol:
            continue
        try:
            history = yf.Ticker(symbol).history(period="5d", interval="1d", auto_adjust=True)
        except Exception:
            continue
        if history is None or history.empty or "Close" not in history.columns:
            continue
        close = history["Close"].dropna()
        if close.empty:
            continue
        prices[symbol] = float(close.iloc[-1])
    return prices


def directional_pnl(side, entry, exit_or_current, shares):
    if side == "short":
        return (entry - exit_or_current) * shares
    return (exit_or_current - entry) * shares


def classify_outcome(pnl):
    if pnl > 0:
        return "win"
    if pnl < 0:
        return "loss"
    return "breakeven"


def normalize_side(value):
    value = str(value or "long").lower().strip()
    return "short" if value == "short" else "long"


def normalize_status(value):
    value = str(value or "").lower().strip()
    if value in {"cancelled", "canceled"}:
        return "cancelled"
    if value == "closed":
        return CLOSED_STATUS
    if value == "open":
        return "open"
    if value == "planned":
        return "planned"
    return value


def numeric_sum(frame, column):
    return float(pd.to_numeric(frame.get(column), errors="coerce").fillna(0).sum())


def numeric_mean(frame, column):
    values = pd.to_numeric(frame.get(column), errors="coerce").dropna()
    return float(values.mean()) if not values.empty else 0


def filter_closed_since(frame, start_date):
    if frame is None or frame.empty:
        return pd.DataFrame(columns=TRADE_COLUMNS)

    dates = frame["closed_at"].map(parse_date)
    return frame[dates.map(lambda value: value is not None and value >= start_date)]


def parse_date(value):
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def start_of_week(day):
    return day - timedelta(days=day.weekday())


def to_float(value):
    try:
        if value == "":
            return 0
        return float(value)
    except (TypeError, ValueError):
        return 0


def round_number(value):
    if value == "":
        return ""
    return round(float(value), 4)


def new_trade_id():
    return f"T-{datetime.now().strftime('%Y%m%d')}-{uuid4().hex[:6].upper()}"


def now_iso():
    return datetime.now().isoformat(timespec="seconds")
