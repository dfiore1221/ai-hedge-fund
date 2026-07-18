from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports" / "devils_advocate"


def write_countercase(symbol, agent_outputs, conflicts):
    symbol = symbol.upper().strip()
    technical = agent_outputs.get("technical", {})
    risk = agent_outputs.get("risk", {})
    macro = agent_outputs.get("macro", {})
    options = agent_outputs.get("options", {})
    backtest = agent_outputs.get("backtest", {})
    news = agent_outputs.get("news", {})
    memory = agent_outputs.get("memory", {})

    counterpoints = []

    if macro.get("assessment", {}).get("market_regime") == "Risk-Off":
        counterpoints.append("Macro regime is risk-off, so long setups need a higher burden of proof.")

    if technical.get("stance") in {"no_trade", "bearish"}:
        counterpoints.append(f"Technical stance is {technical.get('stance')}; timing does not support action.")

    if risk.get("decision") == "veto":
        counterpoints.append("Risk Manager vetoed the setup; this should block trade approval.")

    if backtest.get("tested") and backtest.get("expectancy_pct") is not None and backtest["expectancy_pct"] <= 0:
        counterpoints.append(
            f"Starter backtest expectancy is weak ({backtest['expectancy_pct']:.2f}%)."
        )

    if backtest.get("sample_size") is not None and backtest.get("sample_size", 0) < 20:
        counterpoints.append("Backtest sample size is small, so confidence should be discounted.")

    if options.get("stance") and options.get("confidence", 0) < 0.5:
        counterpoints.append("Options flow is low-confidence and should not override technical or risk evidence.")

    if news.get("stance") == "negative_catalyst":
        counterpoints.append("News layer flags a negative catalyst; bullish technical signals need confirmation.")
    elif news.get("stance") == "positive_catalyst" and technical.get("stance") in {"no_trade", "bearish"}:
        counterpoints.append("Positive headline catalyst conflicts with weak price action.")

    if news.get("missing_information"):
        counterpoints.append("News layer is still a starter feed and can miss premium analyst/action signals.")

    thesis = memory.get("current_thesis") or {}
    if thesis.get("open_questions"):
        counterpoints.append("Research memory still has open thesis questions.")

    for conflict in conflicts:
        counterpoints.append(f"Conflict to resolve: {conflict}")

    if not counterpoints:
        counterpoints.append("No major countercase found, but trade still requires entry, stop, target, and risk approval.")

    return {
        "agent": "Behavioral / Devil's Advocate",
        "run_id": datetime.now().strftime("%Y-%m-%d-devils-advocate"),
        "symbol": symbol,
        "stance": "challenge",
        "confidence": 0.75,
        "countercase": counterpoints,
        "bias_flags": identify_bias_flags(agent_outputs, conflicts),
        "missing_information": [
            "Crowding metrics are not connected yet.",
            "User behavioral state and recent trading performance are not connected yet.",
        ],
    }


def identify_bias_flags(agent_outputs, conflicts):
    flags = []
    risk = agent_outputs.get("risk", {})
    options = agent_outputs.get("options", {})
    news = agent_outputs.get("news", {})
    memory = agent_outputs.get("memory", {})

    if risk.get("decision") == "veto" and memory.get("current_thesis"):
        flags.append("Confirmation bias risk: constructive thesis may tempt override of risk veto.")
    if options.get("stance") == "bullish_positioning" and options.get("confidence", 0) < 0.5:
        flags.append("FOMO risk: bullish options clue is low confidence.")
    if options.get("stance") in {"bearish_or_hedging_positioning", "protective_or_bearish_lean"} and options.get("confidence", 0) < 0.5:
        flags.append("Options fear signal risk: protective put activity can reflect hedging, not necessarily directional conviction.")
    if news.get("stance") == "positive_catalyst" and risk.get("decision") != "approved_for_paper_trade":
        flags.append("Headline-chasing risk: positive news should not override entry quality or risk controls.")
    if conflicts:
        flags.append("Conflict risk: specialist agents disagree.")

    return flags or ["None identified."]


def format_devils_advocate_report(report):
    lines = [
        "# Devil's Advocate Report",
        "",
        f"Symbol: {report['symbol']}",
        f"Stance: {report['stance']}",
        f"Confidence: {report['confidence']}",
        "",
        "## Strongest Countercase",
    ]
    lines.extend([f"- {item}" for item in report["countercase"]])
    lines.extend(["", "## Bias Flags"])
    lines.extend([f"- {item}" for item in report["bias_flags"]])
    lines.extend(["", "## Missing Information"])
    lines.extend([f"- {item}" for item in report["missing_information"]])
    return "\n".join(lines) + "\n"


def save_devils_advocate_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{report['symbol']}_devils_advocate.md"
    path.write_text(format_devils_advocate_report(report), encoding="utf-8")
    return path
