from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OPTIONS_JOURNAL_PATH = PROJECT_ROOT / "portfolio" / "options_journal.csv"
OPTIONS_OPEN_STATUSES = {"planned", "open"}
OPTIONS_CLOSED_STATUS = "closed"
CONTRACT_MULTIPLIER = 100

OPTIONS_COLUMNS = [
    "id",
    "opened_at",
    "symbol",
    "strategy",
    "direction",
    "status",
    "source",
    "agent_run_id",
    "option_type",
    "expiration",
    "strike",
    "contracts",
    "multiplier",
    "entry_premium",
    "current_premium",
    "exit_premium",
    "target_premium",
    "stop_premium",
    "max_loss",
    "break_even",
    "premium_paid",
    "unrealized_pnl",
    "realized_pnl",
    "return_pct",
    "closed_at",
    "outcome",
    "thesis",
    "exit_reason",
    "lessons",
    "notes",
]


def ensure_options_journal():
    OPTIONS_JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not OPTIONS_JOURNAL_PATH.exists():
        frame = pd.DataFrame(columns=OPTIONS_COLUMNS)
        frame.to_csv(OPTIONS_JOURNAL_PATH, index=False)
        return frame

    frame = pd.read_csv(OPTIONS_JOURNAL_PATH, dtype=str).fillna("")
    changed = False
    for column in OPTIONS_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
            changed = True
    for index, row in frame.iterrows():
        if not str(row.get("id", "")).strip():
            frame.at[index, "id"] = new_option_trade_id()
            changed = True
        if not str(row.get("status", "")).strip():
            frame.at[index, "status"] = "planned"
            changed = True
        if not str(row.get("multiplier", "")).strip():
            frame.at[index, "multiplier"] = CONTRACT_MULTIPLIER
            changed = True
    frame = normalize_frame(frame)
    if changed:
        frame.to_csv(OPTIONS_JOURNAL_PATH, index=False)
    return frame


def load_options_journal():
    return ensure_options_journal()


def save_options_journal(frame):
    OPTIONS_JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    frame = normalize_frame(frame)
    frame.to_csv(OPTIONS_JOURNAL_PATH, index=False)
    return frame


def append_option_trade(row):
    journal = load_options_journal()
    trade = {column: row.get(column, "") for column in OPTIONS_COLUMNS}
    trade["id"] = trade.get("id") or new_option_trade_id()
    trade["opened_at"] = trade.get("opened_at") or now_iso()
    trade["symbol"] = str(trade.get("symbol", "")).upper().strip()
    trade["strategy"] = normalize_strategy(trade.get("strategy"))
    trade["direction"] = normalize_direction(trade.get("direction"), trade["strategy"])
    trade["status"] = normalize_status(trade.get("status")) or "planned"
    trade["option_type"] = normalize_option_type(trade.get("option_type"), trade["strategy"])
    trade["multiplier"] = to_float(trade.get("multiplier")) or CONTRACT_MULTIPLIER

    journal = pd.concat([journal, pd.DataFrame([trade])], ignore_index=True)
    journal = enrich_options_metrics(journal)
    save_options_journal(journal)
    return trade["id"]


def close_option_trade(trade_id, exit_premium, exit_reason="", lessons=""):
    journal = load_options_journal()
    match = journal["id"].astype(str) == str(trade_id)
    if not match.any():
        raise ValueError(f"No options trade found with id {trade_id}.")

    index = journal[match].index[0]
    journal.at[index, "status"] = OPTIONS_CLOSED_STATUS
    journal.at[index, "closed_at"] = now_iso()
    journal.at[index, "exit_premium"] = exit_premium
    journal.at[index, "exit_reason"] = exit_reason
    journal.at[index, "lessons"] = lessons
    journal = enrich_options_metrics(journal)
    save_options_journal(journal)
    return journal.loc[index].to_dict()


def enrich_options_metrics(frame):
    frame = normalize_frame(frame)
    for index, row in frame.iterrows():
        strategy = normalize_strategy(row.get("strategy"))
        option_type = normalize_option_type(row.get("option_type"), strategy)
        status = normalize_status(row.get("status"))
        strike = to_float(row.get("strike"))
        contracts = to_float(row.get("contracts"))
        multiplier = to_float(row.get("multiplier")) or CONTRACT_MULTIPLIER
        entry = to_float(row.get("entry_premium"))
        current = to_float(row.get("current_premium")) or entry
        exit_premium = to_float(row.get("exit_premium"))

        premium_paid = entry * contracts * multiplier if entry and contracts else 0
        max_loss = premium_paid
        break_even = calculate_break_even(option_type, strike, entry)
        frame.at[index, "premium_paid"] = round_number(premium_paid)
        frame.at[index, "max_loss"] = round_number(max_loss)
        frame.at[index, "break_even"] = round_number(break_even)

        if status in OPTIONS_OPEN_STATUSES and current and entry and contracts:
            unrealized = (current - entry) * contracts * multiplier
            frame.at[index, "unrealized_pnl"] = round_number(unrealized)
        elif status not in OPTIONS_OPEN_STATUSES:
            frame.at[index, "unrealized_pnl"] = ""

        if status == OPTIONS_CLOSED_STATUS and exit_premium and entry and contracts:
            realized = (exit_premium - entry) * contracts * multiplier
            frame.at[index, "realized_pnl"] = round_number(realized)
            frame.at[index, "return_pct"] = round_number((realized / premium_paid * 100) if premium_paid else 0)
            frame.at[index, "outcome"] = classify_outcome(realized)
        elif status != OPTIONS_CLOSED_STATUS:
            frame.at[index, "realized_pnl"] = ""
            frame.at[index, "return_pct"] = ""
            frame.at[index, "outcome"] = ""
    return frame


def summarize_options_journal(frame=None):
    frame = enrich_options_metrics(frame if frame is not None else load_options_journal())
    if frame.empty:
        return empty_summary()

    statuses = frame["status"].map(normalize_status)
    open_frame = frame[statuses.isin(OPTIONS_OPEN_STATUSES)]
    closed_frame = frame[statuses == OPTIONS_CLOSED_STATUS]
    today_closed = filter_closed_since(closed_frame, date.today())
    week_closed = filter_closed_since(closed_frame, start_of_week(date.today()))
    wins = len(closed_frame[closed_frame["outcome"] == "win"])
    graded = len(closed_frame[closed_frame["outcome"].isin(["win", "loss", "breakeven"])])

    return {
        "planned_or_open": len(open_frame),
        "closed": len(closed_frame),
        "open_premium_at_risk": numeric_sum(open_frame, "max_loss"),
        "open_unrealized_pnl": numeric_sum(open_frame, "unrealized_pnl"),
        "total_realized_pnl": numeric_sum(closed_frame, "realized_pnl"),
        "today_realized_pnl": numeric_sum(today_closed, "realized_pnl"),
        "week_realized_pnl": numeric_sum(week_closed, "realized_pnl"),
        "win_rate": (wins / graded * 100) if graded else 0,
        "avg_return_pct": numeric_mean(closed_frame, "return_pct"),
        "open_symbols": sorted(open_frame["symbol"].dropna().astype(str).str.upper().unique().tolist()),
    }


def format_options_journal_summary(summary):
    return "\n".join([
        "# Paper Options Journal Summary",
        "",
        f"Planned/Open options ideas: {summary['planned_or_open']}",
        f"Closed options ideas: {summary['closed']}",
        f"Open premium at risk: {summary['open_premium_at_risk']:.2f}",
        f"Open unrealized P&L: {summary['open_unrealized_pnl']:.2f}",
        f"Total realized P&L: {summary['total_realized_pnl']:.2f}",
        f"Today realized P&L: {summary['today_realized_pnl']:.2f}",
        f"Week realized P&L: {summary['week_realized_pnl']:.2f}",
        f"Win rate: {summary['win_rate']:.1f}%",
        f"Average return: {summary['avg_return_pct']:.1f}%",
        f"Open symbols: {', '.join(summary['open_symbols']) if summary['open_symbols'] else 'None'}",
    ]) + "\n"


def empty_summary():
    return {
        "planned_or_open": 0,
        "closed": 0,
        "open_premium_at_risk": 0,
        "open_unrealized_pnl": 0,
        "total_realized_pnl": 0,
        "today_realized_pnl": 0,
        "week_realized_pnl": 0,
        "win_rate": 0,
        "avg_return_pct": 0,
        "open_symbols": [],
    }


def normalize_frame(frame):
    if frame is None or frame.empty:
        return pd.DataFrame(columns=OPTIONS_COLUMNS)
    frame = frame.copy().fillna("")
    for column in OPTIONS_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    for column in OPTIONS_COLUMNS:
        frame[column] = frame[column].astype("object")
    frame["symbol"] = frame["symbol"].astype(str).str.upper().str.strip()
    frame["strategy"] = frame["strategy"].map(normalize_strategy)
    frame["direction"] = frame.apply(
        lambda row: normalize_direction(row.get("direction"), row.get("strategy")),
        axis=1,
    )
    frame["status"] = frame["status"].map(normalize_status)
    frame["option_type"] = frame.apply(
        lambda row: normalize_option_type(row.get("option_type"), row.get("strategy")),
        axis=1,
    )
    return frame[OPTIONS_COLUMNS]


def calculate_break_even(option_type, strike, premium):
    if not strike or not premium:
        return 0
    if option_type == "put":
        return strike - premium
    return strike + premium


def normalize_strategy(value):
    value = str(value or "long_call").lower().strip().replace(" ", "_")
    allowed = {"long_call", "long_put", "call_debit_spread", "put_debit_spread"}
    return value if value in allowed else "long_call"


def normalize_direction(value, strategy):
    value = str(value or "").lower().strip()
    if value in {"bullish", "bearish"}:
        return value
    return "bearish" if normalize_strategy(strategy) in {"long_put", "put_debit_spread"} else "bullish"


def normalize_option_type(value, strategy):
    value = str(value or "").lower().strip()
    if value in {"call", "put"}:
        return value
    return "put" if normalize_strategy(strategy) in {"long_put", "put_debit_spread"} else "call"


def normalize_status(value):
    value = str(value or "").lower().strip()
    if value in {"cancelled", "canceled"}:
        return "cancelled"
    if value == OPTIONS_CLOSED_STATUS:
        return OPTIONS_CLOSED_STATUS
    if value == "open":
        return "open"
    if value == "planned":
        return "planned"
    return value


def classify_outcome(pnl):
    if pnl > 0:
        return "win"
    if pnl < 0:
        return "loss"
    return "breakeven"


def filter_closed_since(frame, start_date):
    if frame is None or frame.empty:
        return pd.DataFrame(columns=OPTIONS_COLUMNS)
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


def numeric_sum(frame, column):
    return float(pd.to_numeric(frame.get(column), errors="coerce").fillna(0).sum())


def numeric_mean(frame, column):
    values = pd.to_numeric(frame.get(column), errors="coerce").dropna()
    return float(values.mean()) if not values.empty else 0


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
    return round(float(value or 0), 4)


def new_option_trade_id():
    return f"O-{datetime.now().strftime('%Y%m%d')}-{uuid4().hex[:6].upper()}"


def now_iso():
    return datetime.now().isoformat(timespec="seconds")
