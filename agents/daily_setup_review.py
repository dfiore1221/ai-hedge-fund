import json
import re
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from memory.research_memory import save_agent_report, save_daily_setup_review


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MORNING_BRIEF_DIR = PROJECT_ROOT / "reports" / "morning_brief"
LATEST_MORNING_BRIEF_JSON = MORNING_BRIEF_DIR / "daily_morning_brief.json"
REPORTS_DIR = PROJECT_ROOT / "reports" / "setup_review"
EASTERN = ZoneInfo("America/New_York")


def generate_daily_setup_review(review_day=None, source_path=None, top_n=None, save_memory=True):
    source = Path(source_path) if source_path else LATEST_MORNING_BRIEF_JSON
    morning = load_morning_brief_snapshot(source)
    review_date = parse_review_date(review_day, morning)
    ideas = (morning.get("ideas") or [])[:top_n or morning.get("top_n") or 10]

    reviewed = []
    for idea in ideas:
        reviewed.append(review_setup_idea(idea, review_date))

    report = {
        "agent": "Daily Setup Review",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "review_date": review_date.isoformat(),
        "source_morning_brief_path": str(source),
        "source_morning_brief_created_at": morning.get("created_at"),
        "top_n": len(ideas),
        "summary": summarize_reviews(reviewed),
        "reviewed_setups": reviewed,
        "self_review_lessons": build_self_review_lessons(reviewed),
    }

    if save_memory:
        save_daily_setup_review(report)
        save_agent_report(
            run_id=f"{review_date.isoformat()}-daily-setup-review",
            agent_name="Daily Setup Review",
            output=report,
            symbol="MARKET",
            stance=report["summary"]["dominant_result"],
            confidence=100,
        )

    return report


def load_morning_brief_snapshot(source):
    if not source.exists():
        markdown_fallback = source.with_suffix(".md")
        if markdown_fallback.exists():
            return parse_legacy_morning_brief_markdown(markdown_fallback)
        raise ValueError(
            "No morning brief JSON snapshot found. Run `python3 main.py morning today` first "
            "so the system has a clean recommendation snapshot to review."
        )
    return json.loads(source.read_text(encoding="utf-8"))


def parse_legacy_morning_brief_markdown(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    created_at = parse_created_at(text)
    ideas = []
    current = None

    for line in text.splitlines():
        header = re.match(
            r"^\d+\.\s+([A-Z0-9.-]+)(?:\s+\(([A-Z0-9.-]+)\))?\s+-\s+(.+?)\s+\(score\s+([0-9.-]+),\s+(.+?)\)$",
            line.strip(),
        )
        if header:
            if current:
                ideas.append(current)
            display_symbol, symbol, decision, score, category = header.groups()
            current = {
                "display_symbol": display_symbol,
                "symbol": symbol or display_symbol,
                "decision": decision,
                "score": float(score),
                "category": category,
            }
            continue

        if not current:
            continue

        run_id = re.search(r"Run ID:\s*(.+)$", line)
        if run_id:
            current["run_id"] = run_id.group(1).strip()
            continue

        setup = re.search(
            r"Setup:\s*entry\s+([^,]+),\s*suggested\s+([^,]+),\s*stop\s+([^,]+),\s*target\s+([^,]+),",
            line,
        )
        if setup:
            entry, suggested, stop, target = setup.groups()
            current["entry_trigger"] = to_float(entry)
            current["suggested_entry"] = to_float(suggested)
            current["stop"] = to_float(stop)
            current["target_1"] = to_float(target)

    if current:
        ideas.append(current)

    return {
        "agent": "Morning Brief",
        "created_at": created_at,
        "top_n": 10,
        "ideas": ideas[:10],
        "legacy_markdown_source": str(path),
    }


def parse_created_at(text):
    match = re.search(r"Created At:\s*([^\n]+)", text)
    if match:
        return match.group(1).strip()
    return datetime.now().isoformat(timespec="seconds")


def parse_review_date(review_day, morning):
    if review_day and str(review_day).lower() != "today":
        return datetime.strptime(str(review_day), "%Y-%m-%d").date()

    created_at = morning.get("created_at")
    if created_at:
        return datetime.fromisoformat(created_at).date()
    return date.today()


def review_setup_idea(idea, review_date):
    symbol = idea.get("symbol", "").upper().strip()
    side = (idea.get("side") or "long").lower()
    entry = preferred_entry(idea)
    stop = to_float(idea.get("stop"))
    target_1 = to_float(idea.get("target_1"))

    base = {
        "symbol": symbol,
        "display_symbol": idea.get("display_symbol") or symbol,
        "run_id": idea.get("run_id"),
        "decision": idea.get("decision"),
        "score": idea.get("score"),
        "category": idea.get("category"),
        "side": side,
        "entry": entry,
        "stop": stop,
        "target_1": target_1,
        "entered": False,
        "hit_target_1": False,
        "hit_stop": False,
        "result": "UNREVIEWABLE",
        "entry_time": "",
        "exit_time": "",
        "pnl_pct": None,
        "max_favorable_move_pct": None,
        "max_adverse_move_pct": None,
        "data_source": "Yahoo Finance / yfinance",
        "data_error": "",
    }

    if not symbol or not entry or not stop or not target_1:
        base["data_error"] = "Setup is missing symbol, entry, stop, or target_1."
        return base

    intraday = fetch_intraday_regular_hours(symbol, review_date)
    if intraday.empty:
        return review_with_daily_bar(base, symbol, review_date)

    return review_with_intraday(base, intraday)


def preferred_entry(idea):
    suggested = to_float(idea.get("suggested_entry"))
    trigger = to_float(idea.get("entry_trigger"))
    return suggested or trigger


def fetch_intraday_regular_hours(symbol, review_date):
    history = yf.download(
        symbol,
        period="5d",
        interval="5m",
        auto_adjust=True,
        prepost=False,
        progress=False,
        threads=False,
    )
    if history is None or history.empty:
        return pd.DataFrame()

    history = normalize_yfinance_columns(history)
    index = history.index
    if index.tz is None:
        index = index.tz_localize("UTC")
    history = history.copy()
    history.index = index.tz_convert(EASTERN)

    return history[
        (history.index.date == review_date)
        & (history.index.time >= time(9, 30))
        & (history.index.time <= time(16, 0))
    ]


def review_with_intraday(base, intraday):
    entry = base["entry"]
    stop = base["stop"]
    target_1 = base["target_1"]
    side = base["side"]

    base.update(day_summary(intraday))
    entered_mask = entry_hit(intraday, side, entry)
    base["entered"] = bool(entered_mask.any())

    if not base["entered"]:
        base["result"] = "NO ENTRY"
        base["pnl_pct"] = 0
        base["target_touched_without_entry"] = target_hit(intraday, side, target_1)
        return base

    entry_index = intraday.index[entered_mask.argmax()]
    base["entry_time"] = entry_index.strftime("%H:%M")
    after_entry = intraday.loc[intraday.index >= entry_index]
    base["max_favorable_move_pct"] = round(max_favorable_move_pct(after_entry, side, entry), 2)
    base["max_adverse_move_pct"] = round(max_adverse_move_pct(after_entry, side, entry), 2)

    for timestamp, bar in after_entry.iterrows():
        # Conservative sequencing inside a 5-minute bar: stop first if both touched.
        if stop_hit_bar(bar, side, stop):
            base["hit_stop"] = True
            base["result"] = "STOP FIRST"
            base["exit_time"] = timestamp.strftime("%H:%M")
            base["pnl_pct"] = round(pnl_pct(side, entry, stop), 2)
            return base
        if target_hit_bar(bar, side, target_1):
            base["hit_target_1"] = True
            base["result"] = "TARGET 1 HIT"
            base["exit_time"] = timestamp.strftime("%H:%M")
            base["pnl_pct"] = round(pnl_pct(side, entry, target_1), 2)
            return base

    base["result"] = "OPEN / NO TARGET 1"
    base["pnl_pct"] = round(pnl_pct(side, entry, base["day_close"]), 2)
    return base


def review_with_daily_bar(base, symbol, review_date):
    history = yf.Ticker(symbol).history(period="1mo", auto_adjust=True)
    if history is None or history.empty:
        base["data_error"] = "No intraday or daily price data returned."
        return base

    history = normalize_yfinance_columns(history)
    matches = history[history.index.date == review_date]
    if matches.empty:
        base["data_error"] = f"No price data returned for {review_date.isoformat()}."
        return base

    daily = matches.iloc[[0]]
    base.update(day_summary(daily))
    base["data_source"] = "Yahoo Finance / yfinance daily fallback"
    base["entered"] = entry_hit(daily, base["side"], base["entry"]).any()
    base["hit_target_1"] = target_hit(daily, base["side"], base["target_1"])
    base["hit_stop"] = stop_hit(daily, base["side"], base["stop"])
    base["result"] = classify_daily_result(base)
    base["pnl_pct"] = round(
        pnl_pct(base["side"], base["entry"], base["day_close"]) if base["entered"] else 0,
        2,
    )
    return base


def day_summary(frame):
    return {
        "day_open": round(float(frame["Open"].iloc[0]), 2),
        "day_high": round(float(frame["High"].max()), 2),
        "day_low": round(float(frame["Low"].min()), 2),
        "day_close": round(float(frame["Close"].iloc[-1]), 2),
    }


def classify_daily_result(setup):
    if not setup["entered"]:
        return "NO ENTRY"
    if setup["hit_stop"] and setup["hit_target_1"]:
        return "AMBIGUOUS DAILY BAR"
    if setup["hit_stop"]:
        return "STOP TOUCHED"
    if setup["hit_target_1"]:
        return "TARGET 1 TOUCHED"
    return "OPEN / NO TARGET 1"


def summarize_reviews(reviews):
    entered = [item for item in reviews if item["entered"]]
    target_hits = [item for item in reviews if item["hit_target_1"]]
    stops = [item for item in reviews if item["hit_stop"]]
    no_entries = [item for item in reviews if item["result"] == "NO ENTRY"]
    pnl_values = [item["pnl_pct"] for item in entered if item["pnl_pct"] is not None]

    result_counts = {}
    for item in reviews:
        result_counts[item["result"]] = result_counts.get(item["result"], 0) + 1

    return {
        "setups_reviewed": len(reviews),
        "entries_triggered": len(entered),
        "target_1_hits": len(target_hits),
        "stop_hits": len(stops),
        "no_entries": len(no_entries),
        "avg_entered_pnl_pct": round(sum(pnl_values) / len(pnl_values), 2) if pnl_values else 0,
        "dominant_result": max(result_counts, key=result_counts.get) if result_counts else "n/a",
        "result_counts": result_counts,
    }


def build_self_review_lessons(reviews):
    summary = summarize_reviews(reviews)
    lessons = []

    if summary["setups_reviewed"] == 0:
        return ["No setups were available for review."]
    if summary["target_1_hits"] == 0 and summary["entries_triggered"] > 0:
        lessons.append(
            "Target 1 was not reached by any entered setup; treat these as multi-day swing setups unless intraday targets are explicitly generated."
        )
    if summary["no_entries"] >= summary["setups_reviewed"] / 2:
        lessons.append(
            "Entry discipline filtered out many names; review whether suggested entries are too conservative or correctly avoiding chase risk."
        )
    if summary["stop_hits"] > 0:
        lessons.append(
            "At least one setup touched its stop; risk sizing and stop placement should be reviewed before similar setups are repeated."
        )
    if summary["avg_entered_pnl_pct"] > 0:
        lessons.append(
            "Entered setups were positive on average by the close; monitor whether holding beyond one day improves realized Target 1 hit rate."
        )
    elif summary["entries_triggered"] > 0:
        lessons.append(
            "Entered setups were negative on average by the close; require stronger confirmation or smaller initial size in similar regimes."
        )

    return lessons or ["No strong lesson detected yet; continue collecting daily outcome samples."]


def format_daily_setup_review(report):
    summary = report["summary"]
    lines = [
        "# Daily Setup Self-Review",
        "",
        f"Created At: {report['created_at']}",
        f"Review Date: {report['review_date']}",
        f"Morning Brief: {report.get('source_morning_brief_created_at', 'n/a')}",
        "",
        "## Outcome Summary",
        f"- Setups Reviewed: {summary['setups_reviewed']}",
        f"- Entries Triggered: {summary['entries_triggered']}",
        f"- Target 1 Hits: {summary['target_1_hits']}",
        f"- Stop Hits: {summary['stop_hits']}",
        f"- No Entries: {summary['no_entries']}",
        f"- Average Entered P&L: {summary['avg_entered_pnl_pct']:.2f}%",
        f"- Dominant Result: {summary['dominant_result']}",
        "",
        "## Setup Results",
        "| Rank | Symbol | Decision | Entry | Stop | Target 1 | Day High | Day Low | Close | Result | P&L From Entry |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---|---:|",
    ]

    for index, setup in enumerate(report["reviewed_setups"], start=1):
        lines.append(
            f"| {index} | {setup['display_symbol']} | {setup.get('decision', '')} | "
            f"{fmt(setup.get('entry'))} | {fmt(setup.get('stop'))} | {fmt(setup.get('target_1'))} | "
            f"{fmt(setup.get('day_high'))} | {fmt(setup.get('day_low'))} | {fmt(setup.get('day_close'))} | "
            f"{setup['result']} | {fmt_pct(setup.get('pnl_pct'))} |"
        )

    lines.extend(["", "## Agent Self-Review Lessons"])
    lines.extend([f"- {lesson}" for lesson in report["self_review_lessons"]])

    lines.extend([
        "",
        "## Memory Update",
        f"- Saved {len(report['reviewed_setups'])} setup outcome(s) to the memory database.",
        "- These outcomes can be compared against future setup type, agent run ID, decision tier, and market regime.",
    ])

    return "\n".join(lines) + "\n"


def save_daily_setup_review_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    review_date = report["review_date"].replace("-", "")
    latest_md = REPORTS_DIR / "daily_setup_review.md"
    latest_json = REPORTS_DIR / "daily_setup_review.json"
    archive_md = REPORTS_DIR / f"setup_review_{review_date}.md"
    archive_json = REPORTS_DIR / f"setup_review_{review_date}.json"

    markdown = format_daily_setup_review(report)
    latest_md.write_text(markdown, encoding="utf-8")
    latest_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    archive_md.write_text(markdown, encoding="utf-8")
    archive_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return latest_md


def normalize_yfinance_columns(frame):
    if isinstance(frame.columns, pd.MultiIndex):
        frame = frame.copy()
        frame.columns = [column[0] for column in frame.columns]
    return frame.dropna(subset=["Close"])


def entry_hit(frame, side, entry):
    if side == "short":
        return frame["High"] >= entry
    return frame["Low"] <= entry


def target_hit(frame, side, target):
    if side == "short":
        return bool((frame["Low"] <= target).any())
    return bool((frame["High"] >= target).any())


def stop_hit(frame, side, stop):
    if side == "short":
        return bool((frame["High"] >= stop).any())
    return bool((frame["Low"] <= stop).any())


def target_hit_bar(bar, side, target):
    if side == "short":
        return float(bar["Low"]) <= target
    return float(bar["High"]) >= target


def stop_hit_bar(bar, side, stop):
    return float(bar["High"]) >= stop if side == "short" else float(bar["Low"]) <= stop


def max_favorable_move_pct(frame, side, entry):
    if side == "short":
        best = float(frame["Low"].min())
        return pnl_pct(side, entry, best)
    best = float(frame["High"].max())
    return pnl_pct(side, entry, best)


def max_adverse_move_pct(frame, side, entry):
    if side == "short":
        worst = float(frame["High"].max())
        return pnl_pct(side, entry, worst)
    worst = float(frame["Low"].min())
    return pnl_pct(side, entry, worst)


def pnl_pct(side, entry, exit_price):
    if not entry:
        return 0
    if side == "short":
        return ((entry - exit_price) / entry) * 100
    return ((exit_price - entry) / entry) * 100


def to_float(value):
    try:
        if value in {"", None}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def fmt(value):
    if value is None:
        return "n/a"
    return f"{float(value):.2f}"


def fmt_pct(value):
    if value is None:
        return "n/a"
    return f"{float(value):.2f}%"
