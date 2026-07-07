from datetime import datetime
from pathlib import Path

from memory.research_memory import save_agent_report


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports" / "conflicts"


def build_run_id(symbol):
    return f"{datetime.now().strftime('%Y-%m-%d')}-{symbol.upper()}-committee"


def log_agent_outputs(run_id, symbol, agent_outputs):
    for key, output in agent_outputs.items():
        if output is None:
            continue
        save_agent_report(
            run_id=run_id,
            agent_name=output.get("agent", key),
            output=output,
            symbol=symbol,
        )


def generate_conflict_memo(symbol, agent_outputs):
    conflicts = []
    macro = agent_outputs.get("macro", {})
    technical = agent_outputs.get("technical", {})
    risk = agent_outputs.get("risk", {})
    options = agent_outputs.get("options", {})
    backtest = agent_outputs.get("backtest", {})
    memory = agent_outputs.get("memory", {})

    regime = macro.get("assessment", {}).get("market_regime")
    thesis = memory.get("current_thesis") or {}

    if regime == "Risk-Off" and thesis.get("rating") in {"Watchlist", "Deep Research Candidate"}:
        conflicts.append("Constructive thesis conflicts with risk-off macro regime.")

    if technical.get("stance") == "no_trade" and thesis.get("rating") in {"Watchlist", "Deep Research Candidate"}:
        conflicts.append("Constructive thesis conflicts with Technical Analyst no_trade stance.")

    if options.get("stance") == "bullish_positioning" and technical.get("stance") in {"no_trade", "bearish"}:
        conflicts.append("Bullish options positioning conflicts with weak technical setup.")

    if risk.get("decision") == "veto" and technical.get("stance") in {"bullish", "neutral"}:
        conflicts.append("Risk veto conflicts with non-negative technical stance.")

    if backtest.get("expectancy_pct") is not None and backtest["expectancy_pct"] <= 0 and technical.get("stance") == "bullish":
        conflicts.append("Bullish technical stance conflicts with weak backtested expectancy.")

    return {
        "agent": "Debate / Conflict Engine",
        "symbol": symbol.upper(),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
    }


def format_conflict_memo(memo):
    lines = [
        "# Conflict Memo",
        "",
        f"Symbol: {memo['symbol']}",
        f"Timestamp: {memo['timestamp']}",
        f"Conflict Count: {memo['conflict_count']}",
        "",
        "## Conflicts",
    ]
    lines.extend([f"- {item}" for item in memo["conflicts"]] or ["- None."])
    return "\n".join(lines) + "\n"


def save_conflict_memo(memo):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{memo['symbol']}_conflict_memo.md"
    path.write_text(format_conflict_memo(memo), encoding="utf-8")
    return path
