import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "memory" / "hedge_fund_memory.db"
WATCHLIST_PATH = PROJECT_ROOT / "framework" / "watchlist.json"
MORNING_BRIEF_PATH = PROJECT_ROOT / "reports" / "morning_brief" / "daily_morning_brief.md"
TRADE_JOURNAL_PATH = PROJECT_ROOT / "portfolio" / "trade_journal.csv"

TRADE_COLUMNS = [
    "opened_at",
    "symbol",
    "side",
    "status",
    "entry",
    "stop",
    "target",
    "shares",
    "thesis",
    "source",
    "closed_at",
    "exit_price",
    "pnl",
    "notes",
]


st.set_page_config(
    page_title="AI Hedge Fund Cockpit",
    page_icon="",
    layout="wide",
)


def main():
    st.title("AI Hedge Fund Cockpit")
    st.caption("Watch-only research, simulated trades, agent debate, and memory.")

    tabs = st.tabs([
        "Morning Brief",
        "Watchlist",
        "Simulated Trades",
        "Agent Debate",
        "Research Memory",
        "Settings",
    ])

    with tabs[0]:
        render_morning_brief()
    with tabs[1]:
        render_watchlist()
    with tabs[2]:
        render_trade_journal()
    with tabs[3]:
        render_agent_debate()
    with tabs[4]:
        render_research_memory()
    with tabs[5]:
        render_settings()


def render_morning_brief():
    st.subheader("Morning Brief")

    col_a, col_b = st.columns([1, 3])
    with col_a:
        if st.button("Run Morning Brief Now", type="primary"):
            with st.spinner("Running the 58-name committee scan..."):
                result = subprocess.run(
                    [sys.executable, "main.py", "morning", "today"],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                    timeout=240,
                )
            if result.returncode == 0:
                st.success("Morning brief refreshed.")
                st.rerun()
            st.error(result.stderr or result.stdout or "Morning brief failed.")

    brief = read_text(MORNING_BRIEF_PATH)
    if not brief:
        st.info("No morning brief found yet. Run the morning brief to populate this page.")
        return

    metrics = parse_morning_metrics(brief)
    with col_b:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Market Regime", metrics.get("market_regime", "n/a"))
        c2.metric("Symbols", metrics.get("symbols_scanned", "n/a"))
        c3.metric("Paper Trades", metrics.get("paper_trade_candidates", "n/a"))
        c4.metric("Watch Only", metrics.get("watch_only_candidates", "n/a"))

    st.markdown(brief)


def render_watchlist():
    st.subheader("Watchlist")
    entries = load_watchlist_entries()
    if not entries:
        st.info("No watchlist entries found.")
        return

    frame = pd.DataFrame(entries)
    category_counts = frame.groupby("category", dropna=False).size().reset_index(name="count")

    left, right = st.columns([1, 2])
    with left:
        st.markdown("#### Category Counts")
        st.dataframe(category_counts, hide_index=True, width="stretch")
    with right:
        st.markdown("#### Symbols")
        st.dataframe(frame, hide_index=True, width="stretch")


def render_trade_journal():
    st.subheader("Simulated Trade Journal")
    st.caption("Local journal only. This does not place trades.")

    journal = load_trade_journal()
    open_trades = journal[journal["status"].str.lower() == "open"] if not journal.empty else journal
    closed_trades = journal[journal["status"].str.lower() == "closed"] if not journal.empty else journal

    c1, c2, c3 = st.columns(3)
    c1.metric("Open Simulated Trades", len(open_trades))
    c2.metric("Closed Simulated Trades", len(closed_trades))
    c3.metric("Tracked P&L", format_number(pd.to_numeric(journal.get("pnl"), errors="coerce").sum()))

    with st.expander("Add Simulated Trade", expanded=False):
        with st.form("add_trade"):
            col1, col2, col3, col4 = st.columns(4)
            symbol = col1.text_input("Symbol").upper().strip()
            side = col2.selectbox("Side", ["long", "short"])
            status = col3.selectbox("Status", ["open", "closed"])
            source = col4.selectbox("Source", ["morning brief", "manual", "CIO", "research"])

            col5, col6, col7, col8 = st.columns(4)
            entry = col5.number_input("Entry", min_value=0.0, value=0.0)
            stop = col6.number_input("Stop", min_value=0.0, value=0.0)
            target = col7.number_input("Target", min_value=0.0, value=0.0)
            shares = col8.number_input("Shares", min_value=0, value=0)

            thesis = st.text_area("Thesis / Why")
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save Trade")

        if submitted:
            if not symbol:
                st.error("Symbol is required.")
            else:
                row = {
                    "opened_at": datetime.now().isoformat(timespec="seconds"),
                    "symbol": symbol,
                    "side": side,
                    "status": status,
                    "entry": entry,
                    "stop": stop,
                    "target": target,
                    "shares": shares,
                    "thesis": thesis,
                    "source": source,
                    "closed_at": "",
                    "exit_price": "",
                    "pnl": "",
                    "notes": notes,
                }
                save_trade_row(row)
                st.success(f"Saved simulated trade for {symbol}.")
                st.rerun()

    st.markdown("#### Journal")
    edited = st.data_editor(
        journal,
        hide_index=True,
        width="stretch",
        num_rows="dynamic",
    )
    if st.button("Save Journal Edits"):
        save_trade_journal(edited)
        st.success("Trade journal saved.")


def render_agent_debate():
    st.subheader("Agent Debate")
    reports = load_agent_reports()
    if reports.empty:
        st.info("No agent debate logs found yet.")
        return

    run_ids = ["All"] + sorted(reports["run_id"].dropna().unique().tolist(), reverse=True)
    selected_run = st.selectbox("Run", run_ids)
    view = reports if selected_run == "All" else reports[reports["run_id"] == selected_run]

    summary_cols = ["created_at", "run_id", "symbol", "agent_name", "stance", "confidence"]
    st.dataframe(view[summary_cols], hide_index=True, width="stretch")

    st.markdown("#### Details")
    for _, row in view.head(25).iterrows():
        label = f"{row['created_at']} | {row['symbol']} | {row['agent_name']}"
        with st.expander(label):
            st.json(row["output"])


def render_research_memory():
    st.subheader("Research Memory")
    theses = load_ticker_theses()
    reports = load_research_reports()

    st.markdown("#### Current Theses")
    if theses.empty:
        st.info("No structured theses found yet.")
    else:
        st.dataframe(theses, hide_index=True, width="stretch")

    st.markdown("#### Recent Research Reports")
    if reports.empty:
        st.info("No research reports found yet.")
    else:
        st.dataframe(reports[["created_at", "ticker", "preview"]], hide_index=True, width="stretch")


def render_settings():
    st.subheader("Settings")
    st.write(f"Project root: `{PROJECT_ROOT}`")
    st.write(f"Morning brief: `{MORNING_BRIEF_PATH}`")
    st.write(f"Trade journal: `{TRADE_JOURNAL_PATH}`")
    st.write(f"Memory database: `{DB_PATH}`")

    st.markdown("#### Automation")
    out_log = PROJECT_ROOT / "reports" / "morning_brief" / "launchd.out.log"
    err_log = PROJECT_ROOT / "reports" / "morning_brief" / "launchd.err.log"
    automation_log = PROJECT_ROOT / "reports" / "morning_brief" / "automation.log"
    st.text_area("Automation log", read_text(automation_log)[-3000:], height=180)
    st.text_area("Launchd errors", read_text(err_log)[-3000:], height=140)
    st.text_area("Launchd output", read_text(out_log)[-3000:], height=100)


def read_text(path):
    path = Path(path)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def parse_morning_metrics(text):
    patterns = {
        "market_regime": r"Market Regime:\s*([^(\n]+)",
        "symbols_scanned": r"Symbols Scanned:\s*(\d+)",
        "paper_trade_candidates": r"Paper-Trade Candidates:\s*(\d+)",
        "watch_only_candidates": r"Watch-Only Candidates:\s*(\d+)",
    }
    metrics = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        metrics[key] = match.group(1).strip() if match else "n/a"
    return metrics


def load_watchlist_entries():
    if not WATCHLIST_PATH.exists():
        return []
    data = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
    rows = []
    for item in data.get("symbols", []):
        if isinstance(item, str):
            rows.append({"symbol": item, "display_symbol": item, "category": "Uncategorized", "notes": ""})
        else:
            rows.append({
                "symbol": item.get("symbol"),
                "display_symbol": item.get("display_symbol", item.get("symbol")),
                "category": item.get("category", "Uncategorized"),
                "notes": item.get("notes", ""),
            })
    return rows


def load_trade_journal():
    TRADE_JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not TRADE_JOURNAL_PATH.exists():
        frame = pd.DataFrame(columns=TRADE_COLUMNS)
        frame.to_csv(TRADE_JOURNAL_PATH, index=False)
        return frame
    frame = pd.read_csv(TRADE_JOURNAL_PATH, dtype=str).fillna("")
    for column in TRADE_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    return frame[TRADE_COLUMNS]


def save_trade_row(row):
    journal = load_trade_journal()
    journal = pd.concat([journal, pd.DataFrame([row])], ignore_index=True)
    save_trade_journal(journal)


def save_trade_journal(frame):
    TRADE_JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(TRADE_JOURNAL_PATH, index=False)


def load_agent_reports():
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT run_id, agent_name, symbol, created_at, stance, confidence, output_json
        FROM agent_reports
        ORDER BY created_at DESC
        LIMIT 300
    """).fetchall()
    conn.close()

    records = []
    for run_id, agent_name, symbol, created_at, stance, confidence, output_json in rows:
        try:
            output = json.loads(output_json)
        except json.JSONDecodeError:
            output = {"raw": output_json}
        records.append({
            "run_id": run_id,
            "agent_name": agent_name,
            "symbol": symbol,
            "created_at": created_at,
            "stance": stance,
            "confidence": confidence,
            "output": output,
        })
    return pd.DataFrame(records)


def load_ticker_theses():
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT ticker, updated_at, rating, overall_score, confidence, thesis, open_questions
        FROM ticker_theses
        ORDER BY updated_at DESC
    """).fetchall()
    conn.close()
    return pd.DataFrame(rows, columns=[
        "ticker", "updated_at", "rating", "overall_score", "confidence", "thesis", "open_questions"
    ])


def load_research_reports():
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT ticker, created_at, memo
        FROM research_reports
        ORDER BY created_at DESC
        LIMIT 100
    """).fetchall()
    conn.close()
    records = []
    for ticker, created_at, memo in rows:
        records.append({
            "ticker": ticker,
            "created_at": created_at,
            "preview": memo[:500],
        })
    return pd.DataFrame(records)


def format_number(value):
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "0.00"


if __name__ == "__main__":
    main()
