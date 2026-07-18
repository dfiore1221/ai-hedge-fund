import json
import hmac
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
import yfinance as yf
from dotenv import load_dotenv

from agents.feedback_loop import generate_feedback_report
from data.data_quality import generate_data_health_report
from data.portfolio import analyze_portfolio_exposure
from data.trade_journal import (
    OPEN_STATUSES,
    TRADE_JOURNAL_PATH,
    append_trade,
    close_trade,
    enrich_trade_metrics,
    load_trade_journal,
    save_trade_journal,
    summarize_trade_journal,
)
from security.checks import build_security_report, redact_text


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "memory" / "hedge_fund_memory.db"
WATCHLIST_PATH = PROJECT_ROOT / "framework" / "watchlist.json"
MORNING_BRIEF_PATH = PROJECT_ROOT / "reports" / "morning_brief" / "daily_morning_brief.md"
ENV_PATH = PROJECT_ROOT / ".env"


st.set_page_config(
    page_title="AI Hedge Fund Cockpit",
    page_icon="",
    layout="wide",
)


def main():
    load_dotenv(ENV_PATH)
    st.title("AI Hedge Fund Cockpit")
    st.caption("Watch-only research, simulated trades, agent debate, and memory.")

    if not require_dashboard_auth():
        return

    tabs = st.tabs([
        "Morning Brief",
        "Data Quality",
        "Charts",
        "Watchlist",
        "Simulated Trades",
        "Feedback Loop",
        "Agent Debate",
        "Research Memory",
        "Settings",
    ])

    with tabs[0]:
        render_morning_brief()
    with tabs[1]:
        render_data_quality()
    with tabs[2]:
        render_stock_charts()
    with tabs[3]:
        render_watchlist()
    with tabs[4]:
        render_trade_journal()
    with tabs[5]:
        render_feedback_loop()
    with tabs[6]:
        render_agent_debate()
    with tabs[7]:
        render_research_memory()
    with tabs[8]:
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
            st.error(redact_text(result.stderr or result.stdout or "Morning brief failed."))

    brief = read_text(MORNING_BRIEF_PATH)
    if not brief:
        st.info("No morning brief found yet. Run the morning brief to populate this page.")
        return

    metrics = parse_morning_metrics(brief)
    with col_b:
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Market Regime", metrics.get("market_regime", "n/a"))
        c2.metric("Symbols", metrics.get("symbols_scanned", "n/a"))
        c3.metric("Paper Trades", metrics.get("paper_trade_candidates", "n/a"))
        c4.metric("Conditional", metrics.get("conditional_setups", "n/a"))
        c5.metric("Watchlist", metrics.get("watchlist_setups", "n/a"))

    st.markdown(brief)


def render_data_quality():
    st.subheader("Data Quality")
    st.caption("Checks whether the morning data packet is strong enough for simulated trade review.")

    watchlist_size = max(1, len(load_watchlist_entries()))
    controls = st.columns([1, 1, 3])
    live_checks = controls[0].toggle("Live checks", value=True)
    sample_size = controls[1].number_input(
        "Sample",
        min_value=1,
        max_value=watchlist_size,
        value=min(12, watchlist_size),
        step=1,
    )
    if controls[2].button("Refresh Data Quality", type="primary"):
        load_data_health.clear()

    with st.spinner("Checking provider status and watchlist data coverage..."):
        report = load_data_health(live_checks=live_checks, live_check_limit=int(sample_size))

    gate = report["gate"]
    coverage = report["coverage"]

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Score", f"{report['data_quality_score']}/100")
    c2.metric("Gate", gate["status"])
    c3.metric("Symbols", report["watchlist_count"])
    c4.metric("Price OK", coverage["price_ok"])
    c5.metric("Coverage", format_percent_display(coverage["checked_coverage_pct"]))

    if gate["status"] in {"Pass", "Conditional"}:
        st.success(gate["decision"])
    elif gate["status"] == "Watch Only":
        st.warning(gate["decision"])
    else:
        st.error(gate["decision"])

    left, right = st.columns([1, 1])
    with left:
        st.markdown("#### Domain Scores")
        domain_rows = []
        for name, item in report["domain_scores"].items():
            domain_rows.append({
                "domain": name.replace("_", " ").title(),
                "score": item["score"],
                "max": item["max_score"],
                "status": item["status"],
                "detail": item["detail"],
            })
        st.dataframe(pd.DataFrame(domain_rows), hide_index=True, width="stretch")

    with right:
        st.markdown("#### Provider Status")
        provider_rows = []
        for provider in report["providers"]:
            provider_rows.append({
                "provider": provider["name"],
                "status": provider["status"],
                "configured": provider["configured"],
                "key": provider["env_key"] or "built-in",
                "domain": provider["domain"],
            })
        st.dataframe(pd.DataFrame(provider_rows), hide_index=True, width="stretch")

    st.markdown("#### Live Price Sample")
    checks = pd.DataFrame(report.get("live_price_checks", []))
    if checks.empty:
        st.info("Live price checks are disabled.")
    else:
        display_cols = [
            column for column in [
                "symbol",
                "status",
                "latest_date",
                "calendar_age_days",
                "latest_close",
                "rows",
                "message",
            ]
            if column in checks.columns
        ]
        st.dataframe(checks[display_cols], hide_index=True, width="stretch")

    st.markdown("#### Blockers")
    if report["blockers"]:
        for blocker in report["blockers"]:
            st.write(f"- {blocker}")
    else:
        st.success("No blockers detected.")

    st.markdown("#### Recommended Next Fixes")
    for item in report["recommendations"]:
        st.write(f"- {item}")


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


def render_stock_charts():
    st.subheader("Live Stock Charts")
    st.caption("Market data is pulled from Yahoo Finance and refreshed on demand. Charts are for research only.")

    entries = load_watchlist_entries()
    labels = [format_watchlist_label(entry) for entry in entries]
    label_to_symbol = {format_watchlist_label(entry): entry["symbol"] for entry in entries}

    controls = st.columns([2, 1, 1, 1])
    selected_label = controls[0].selectbox("Watchlist Symbol", labels, index=0 if labels else None)
    custom_symbol = controls[1].text_input("Custom", placeholder="e.g. SPY").upper().strip()
    period = controls[2].selectbox("Period", ["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y"], index=2)
    interval = controls[3].selectbox("Interval", ["1m", "5m", "15m", "30m", "1h", "1d"], index=5)

    symbol = custom_symbol or label_to_symbol.get(selected_label)
    if st.button("Refresh Chart"):
        load_chart_history.clear()

    if not symbol:
        st.info("Select a symbol to chart.")
        return

    with st.spinner(f"Loading chart for {symbol}..."):
        history, error = load_chart_history(symbol, period, interval)

    if error:
        st.error(error)
        return
    if history.empty:
        st.info(f"No chart data returned for {symbol}.")
        return

    history = history.copy()
    history.index = pd.to_datetime(history.index)
    latest = float(history["Close"].dropna().iloc[-1])
    previous = float(history["Close"].dropna().iloc[-2]) if len(history["Close"].dropna()) > 1 else latest
    change = latest - previous
    change_pct = (change / previous) * 100 if previous else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Symbol", symbol)
    c2.metric("Last", f"{latest:.2f}", f"{change:.2f} / {change_pct:.2f}%")
    c3.metric("Rows", len(history))
    c4.metric("Last Update", str(history.index[-1]))

    chart_data = history[["Close"]].rename(columns={"Close": symbol})
    st.line_chart(chart_data, height=420)

    with st.expander("Volume and OHLC Data"):
        if "Volume" in history.columns:
            st.bar_chart(history[["Volume"]], height=180)
        st.dataframe(
            history[["Open", "High", "Low", "Close", "Volume"]].tail(100),
            width="stretch",
        )


def render_trade_journal():
    st.subheader("Simulated Trade Journal")
    st.caption("Local journal only. This does not place trades.")

    refresh_prices = st.button("Refresh Open Trade Prices")
    journal = enrich_trade_metrics(load_trade_journal(), refresh_prices=refresh_prices)
    if refresh_prices:
        save_trade_journal(journal)
    summary = summarize_trade_journal(journal)
    account = analyze_portfolio_exposure()
    summary = {
        "open_trades": 0,
        "closed_trades": 0,
        "total_realized_pnl": 0,
        "open_unrealized_pnl": 0,
        "open_planned_risk": 0,
        "avg_r_multiple": 0,
        "win_rate": 0,
        "today_realized_pnl": 0,
        "week_realized_pnl": 0,
        **summary,
    }
    account = {
        "cash": 100000,
        "liquid_cash": 100000,
        "total_value": 100000,
        "open_position_value": 0,
        "planned_position_value": 0,
        **account,
    }
    statuses = journal["status"].str.lower() if not journal.empty else pd.Series(dtype=str)
    open_trades = journal[statuses.isin(OPEN_STATUSES)] if not journal.empty else journal
    closed_trades = journal[statuses == "closed"] if not journal.empty else journal

    st.markdown("#### Paper Account")
    a1, a2, a3, a4, a5 = st.columns(5)
    a1.metric("Starting Capital", money(account["cash"]))
    a2.metric("Est. Liquid Cash", money(account["liquid_cash"]))
    a3.metric("Est. Equity", money(account["total_value"]))
    a4.metric("Open Value", money(account["open_position_value"]))
    a5.metric("Planned Value", money(account["planned_position_value"]))

    st.markdown("#### Trade Journal")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Open / Planned", summary["open_trades"])
    c2.metric("Closed", summary["closed_trades"])
    c3.metric("Realized P&L", money(summary["total_realized_pnl"]))
    c4.metric("Unrealized P&L", money(summary["open_unrealized_pnl"]))
    c5.metric("Open Risk", money(summary["open_planned_risk"]))
    c6.metric("Avg R", f"{summary['avg_r_multiple']:.2f}")

    st.caption(
        f"Win rate: {summary['win_rate']:.1f}% | "
        f"Today realized: {money(summary['today_realized_pnl'])} | "
        f"Week realized: {money(summary['week_realized_pnl'])}"
    )

    with st.expander("Add Simulated Trade", expanded=False):
        with st.form("add_trade"):
            col1, col2, col3, col4 = st.columns(4)
            symbol = col1.text_input("Symbol").upper().strip()
            side = col2.selectbox("Side", ["long", "short"])
            status = col3.selectbox("Status", ["planned", "open"])
            source = col4.selectbox("Source", ["morning brief", "CIO", "manual", "research"])

            col5, col6, col7, col8 = st.columns(4)
            entry = col5.number_input("Entry", min_value=0.0, value=0.0)
            stop = col6.number_input("Stop", min_value=0.0, value=0.0)
            target = col7.number_input("Target", min_value=0.0, value=0.0)
            shares = col8.number_input("Shares", min_value=0, value=0)

            col9, col10 = st.columns(2)
            setup_type = col9.selectbox(
                "Setup Type",
                ["breakout", "pullback", "trend continuation", "mean reversion", "event", "manual"],
            )
            agent_run_id = col10.text_input("Agent Run ID", placeholder="optional")

            thesis = st.text_area("Thesis / Why")
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save Trade")

        if submitted:
            if not symbol:
                st.error("Symbol is required.")
            else:
                row = {
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
                }
                trade_id = append_trade(row)
                st.success(f"Saved simulated trade for {symbol}.")
                st.caption(f"Trade ID: {trade_id}")
                st.rerun()

    if not open_trades.empty:
        with st.expander("Close Simulated Trade", expanded=False):
            trade_labels = {
                f"{row['id']} | {row['symbol']} | {row['side']} | entry {row['entry']}": row["id"]
                for _, row in open_trades.iterrows()
            }
            with st.form("close_trade"):
                selected = st.selectbox("Trade", list(trade_labels.keys()))
                exit_price = st.number_input("Exit Price", min_value=0.0, value=0.0)
                exit_reason = st.text_area("Exit Reason")
                lessons = st.text_area("Lesson / What changed")
                close_submitted = st.form_submit_button("Close Trade")

            if close_submitted:
                if exit_price <= 0:
                    st.error("Exit price is required.")
                else:
                    close_trade(trade_labels[selected], exit_price, exit_reason, lessons)
                    st.success("Trade closed.")
                    st.rerun()

    st.markdown("#### Open / Planned")
    if open_trades.empty:
        st.info("No open or planned simulated trades.")
    else:
        st.dataframe(
            open_trades[[
                "id",
                "symbol",
                "side",
                "status",
                "setup_type",
                "entry",
                "stop",
                "target",
                "shares",
                "planned_risk",
                "current_price",
                "unrealized_pnl",
                "source",
            ]],
            hide_index=True,
            width="stretch",
        )

    st.markdown("#### Closed")
    if closed_trades.empty:
        st.info("No closed simulated trades yet.")
    else:
        st.dataframe(
            closed_trades[[
                "id",
                "symbol",
                "side",
                "entry",
                "exit_price",
                "shares",
                "realized_pnl",
                "r_multiple",
                "outcome",
                "exit_reason",
            ]],
            hide_index=True,
            width="stretch",
        )

    st.markdown("#### Full Journal")
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


def render_feedback_loop():
    st.subheader("Decision Feedback Loop")
    st.caption("Scores simulated outcomes against setup type, source, decision tier, and linked agent calls.")

    report = generate_feedback_report()
    expectancy = report["trade_expectancy"]

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Closed Trades", report["closed_trades_count"])
    c2.metric("Linked Decisions", report["linked_trades_count"])
    c3.metric("Win Rate", f"{expectancy['win_rate']:.1f}%")
    c4.metric("Avg R", f"{expectancy['avg_r']:.2f}")
    c5.metric("Total R", f"{expectancy['total_r']:.2f}")
    c6.metric("Total P&L", money(expectancy["total_pnl"]))

    st.markdown("#### Agent Scorecard")
    agent_scorecard = pd.DataFrame(report["agent_scorecard"])
    if agent_scorecard.empty:
        st.info("No linked closed trades yet. Add agent run IDs to journal entries and close trades to score agents.")
    else:
        st.dataframe(agent_scorecard, hide_index=True, width="stretch")

    left, right = st.columns(2)
    with left:
        st.markdown("#### By Setup Type")
        st.dataframe(pd.DataFrame(report["by_setup_type"]), hide_index=True, width="stretch")
        st.markdown("#### By Source")
        st.dataframe(pd.DataFrame(report["by_source"]), hide_index=True, width="stretch")
    with right:
        st.markdown("#### By Decision Tier")
        st.dataframe(pd.DataFrame(report["by_decision_tier"]), hide_index=True, width="stretch")
        st.markdown("#### By Symbol")
        st.dataframe(pd.DataFrame(report["by_symbol"]), hide_index=True, width="stretch")

    st.markdown("#### Lessons")
    lessons = pd.DataFrame(report["lessons"])
    if lessons.empty:
        st.info("No lessons logged yet.")
    else:
        st.dataframe(lessons, hide_index=True, width="stretch")

    st.markdown("#### Missing Information")
    for item in report["missing_information"]:
        st.write(f"- {item}")


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

    st.markdown("#### Security")
    security_report = build_security_report()
    st.write(f"Required checks passed: `{security_report['passed']}`")
    st.write(f"Dashboard passcode: `{env_display('DASHBOARD_PASSCODE')}`")
    st.write(f"Email allowlist: `{env_display('APPROVED_EMAIL_RECIPIENTS')}`")
    if security_report["blockers"]:
        st.error("Security blockers found. Run `python3 main.py security check` for details.")
    elif security_report["warnings"]:
        st.warning("Security warnings found. Run `python3 main.py security check` for details.")
    else:
        st.success("Security check is clean.")


def require_dashboard_auth():
    passcode = os.getenv("DASHBOARD_PASSCODE", "").strip()
    if not passcode:
        st.warning("Dashboard passcode is not set. Add DASHBOARD_PASSCODE to .env to lock this cockpit.")
        return True

    if st.session_state.get("dashboard_authenticated"):
        return True

    st.subheader("Dashboard Locked")
    entered = st.text_input("Passcode", type="password")
    if st.button("Unlock Dashboard"):
        if hmac.compare_digest(entered, passcode):
            st.session_state["dashboard_authenticated"] = True
            st.rerun()
        st.error("Incorrect passcode.")

    return False


def env_display(key):
    value = os.getenv(key, "").strip()
    return "set" if value else "missing"


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
        "conditional_setups": r"Conditional Setups:\s*(\d+)",
        "watchlist_setups": r"Watchlist Setups:\s*(\d+)",
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


def format_watchlist_label(entry):
    symbol = entry.get("symbol", "")
    display = entry.get("display_symbol") or symbol
    category = entry.get("category", "Uncategorized")
    if display == symbol:
        return f"{symbol} - {category}"
    return f"{display} ({symbol}) - {category}"


@st.cache_data(ttl=60, show_spinner=False)
def load_chart_history(symbol, period, interval):
    try:
        history = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
    except Exception as exc:
        return pd.DataFrame(), f"Could not load chart data for {symbol}: {exc}"

    if history is None or history.empty:
        return pd.DataFrame(), None

    required = ["Open", "High", "Low", "Close"]
    for column in required:
        if column not in history.columns:
            return pd.DataFrame(), f"Chart data for {symbol} is missing {column}."

    if "Volume" not in history.columns:
        history["Volume"] = 0

    return history.dropna(subset=["Close"]), None


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


def format_percent_display(value):
    if value is None:
        return "n/a"
    return f"{value:.1f}%"


@st.cache_data(ttl=120, show_spinner=False)
def load_data_health(live_checks=True, live_check_limit=12):
    return generate_data_health_report(
        live_checks=live_checks,
        live_check_limit=live_check_limit,
    )


def money(value):
    return f"${format_number(value)}"


if __name__ == "__main__":
    main()
