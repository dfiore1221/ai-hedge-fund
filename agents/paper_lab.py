import json
from datetime import datetime
from pathlib import Path

from agents.feedback_loop import generate_feedback_report
from agents.options_flow import analyze_options_flow
from data.options_journal import append_option_trade, summarize_options_journal
from data.paper_ledger import build_paper_ledger
from data.trade_journal import load_trade_journal, normalize_status
from memory.research_memory import save_agent_report


PROJECT_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = PROJECT_ROOT / "framework" / "paper_lab_policy.json"
MORNING_BRIEF_JSON_PATH = PROJECT_ROOT / "reports" / "morning_brief" / "daily_morning_brief.json"
REPORTS_DIR = PROJECT_ROOT / "reports" / "paper_lab"
CONTRACT_MULTIPLIER = 100


def generate_paper_lab_report(save_memory=True):
    policy = load_policy()
    brief = load_latest_morning_brief()
    ledger = build_paper_ledger()
    account = ledger.get("account") or {}
    equity = account.get("net_liquidation_value") or account.get("starting_cash") or 0
    feedback = generate_feedback_report()
    options_summary = summarize_options_journal()
    open_experiments = count_open_lab_experiments()
    budget = build_budget(policy, equity, options_summary, open_experiments)

    report = {
        "agent": "Paper Lab",
        "system_role": "experimental_learning_layer",
        "layer": "Aggression Mode / Paper Lab",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "paper_only",
        "policy": policy,
        "budget": budget,
        "learning_readout": build_learning_readout(feedback),
        "stock_experiments": build_stock_experiments(brief, policy),
        "options_experiments": build_options_experiments(brief, policy),
        "bearish_experiments": build_bearish_experiments(brief, policy),
        "promotion_rules": policy.get("promotion_rules", []),
        "guardrails": build_guardrails(policy, budget),
    }
    report["summary"] = build_summary(report)

    if save_memory:
        save_agent_report(
            run_id=f"{report['created_at'][:10]}-paper-lab",
            agent_name="Paper Lab",
            output=report,
            symbol="PORTFOLIO",
            stance=report["summary"]["stance"],
            confidence=report["summary"]["confidence_score"],
        )

    return report


def load_policy():
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def load_latest_morning_brief():
    if not MORNING_BRIEF_JSON_PATH.exists():
        return {}
    return json.loads(MORNING_BRIEF_JSON_PATH.read_text(encoding="utf-8"))


def build_budget(policy, equity, options_summary, open_experiments):
    sleeve_pct = float(policy.get("paper_lab_sleeve_pct", 0.15))
    max_single_risk_pct = float(policy.get("max_single_experiment_risk_pct", 0.75))
    max_option_premium_pct = float(policy.get("max_options_experiment_premium_pct", 0.5))
    sleeve_value = equity * sleeve_pct
    max_single_risk = equity * max_single_risk_pct / 100
    max_option_premium = equity * max_option_premium_pct / 100
    open_options_risk = float(options_summary.get("open_premium_at_risk") or 0)
    return {
        "equity": round_money(equity),
        "paper_lab_sleeve_pct": sleeve_pct,
        "paper_lab_sleeve_value": round_money(sleeve_value),
        "max_single_experiment_risk_pct": max_single_risk_pct,
        "max_single_experiment_risk": round_money(max_single_risk),
        "max_options_experiment_premium_pct": max_option_premium_pct,
        "max_options_experiment_premium": round_money(max_option_premium),
        "open_options_premium_at_risk": round_money(open_options_risk),
        "open_lab_experiments": open_experiments,
        "max_open_experiments": int(policy.get("max_open_experiments", 8)),
    }


def count_open_lab_experiments():
    journal = load_trade_journal()
    if journal.empty:
        return 0
    count = 0
    for _, row in journal.iterrows():
        status = normalize_status(row.get("status"))
        source = str(row.get("source", "")).lower()
        setup_type = str(row.get("setup_type", "")).lower()
        if status in {"planned", "open"} and ("paper lab" in source or "paper lab" in setup_type):
            count += 1
    return count


def build_learning_readout(feedback):
    setup_learning = feedback.get("setup_review_learning") or {}
    expectancy = feedback.get("trade_expectancy") or {}
    lessons = []
    summary = setup_learning.get("summary") or setup_learning
    target_rate = float(summary.get("target_1_hit_rate") or summary.get("target_1_hit_rate_pct") or 0)
    partial_rate = float(summary.get("partial_win_rate") or summary.get("partial_win_rate_pct") or 0)
    avg_pnl = float(summary.get("average_entered_pnl") or summary.get("avg_entered_pnl_pct") or 0)

    if target_rate <= partial_rate:
        lessons.append("Test partial exits and trailing stops because partial wins are showing more promise than full Target 1 exits.")
    if avg_pnl > 0:
        lessons.append("Setup selection has some signal; Paper Lab can test smaller/faster profit capture.")
    lessons.append("Keep the main portfolio conservative until experiment samples are large enough to promote.")

    return {
        "closed_trade_count": expectancy.get("count", 0),
        "win_rate": expectancy.get("win_rate", 0),
        "average_r": expectancy.get("avg_r", 0),
        "setup_learning_score": setup_learning.get("learning_score") or summary.get("learning_score"),
        "target_1_hit_rate": target_rate,
        "partial_win_rate": partial_rate,
        "average_entered_pnl": avg_pnl,
        "lessons": lessons,
    }


def build_stock_experiments(brief, policy, limit=6):
    rows = []
    for idea in (brief.get("conditional_setups") or [])[:limit]:
        rows.append({
            "family": "pullback_starter",
            "symbol": idea.get("symbol"),
            "display_symbol": idea.get("display_symbol") or idea.get("symbol"),
            "category": idea.get("category"),
            "score": idea.get("score"),
            "status": "paper_lab_candidate",
            "experiment": "Buy a smaller starter only if price reaches the suggested pullback entry.",
            "entry": round_money(idea.get("suggested_entry")),
            "stop": round_money(idea.get("stop")),
            "partial": round_money(idea.get("partial_win_level")),
            "target_1": round_money(idea.get("target_1")),
            "lesson_to_measure": "Does a tighter first exit or trailing rule beat waiting for full Target 1?",
            "source_run_id": idea.get("run_id"),
            "guardrail": "Paper Lab only; do not promote until enough samples close.",
        })
    return rows


def build_options_experiments(brief, policy, limit=5):
    rows = []
    allowed = set(policy.get("allowed_options_strategies", []))
    for idea in (brief.get("conditional_setups") or [])[:limit]:
        if "long_call" in allowed:
            rows.append(option_experiment_from_idea(idea, "long_call"))
        if "call_debit_spread" in allowed:
            rows.append(option_experiment_from_idea(idea, "call_debit_spread"))
    return rows[:limit]


def build_bearish_experiments(brief, policy, limit=5):
    rows = []
    allowed = set(policy.get("allowed_options_strategies", []))
    for idea in (brief.get("bearish_fade_watch") or [])[:limit]:
        if "put_debit_spread" in allowed:
            rows.append(option_experiment_from_idea(idea, "put_debit_spread"))
        elif "long_put" in allowed:
            rows.append(option_experiment_from_idea(idea, "long_put"))
    return rows


def option_experiment_from_idea(idea, strategy):
    direction = "bearish" if strategy in {"long_put", "put_debit_spread"} else "bullish"
    return {
        "family": "defined_risk_options",
        "symbol": idea.get("symbol"),
        "display_symbol": idea.get("display_symbol") or idea.get("symbol"),
        "strategy": strategy,
        "direction": direction,
        "status": "watch_only_contract_selection_required",
        "source_run_id": idea.get("run_id"),
        "underlying_entry_context": round_money(idea.get("suggested_entry") or idea.get("entry_trigger")),
        "underlying_stop_context": round_money(idea.get("stop")),
        "underlying_target_context": round_money(idea.get("target_1")),
        "why": (
            "Use options to test a defined-risk expression of the Committee setup "
            "without changing main portfolio approval rules."
        ),
        "guardrail": "Paper options only; confirm liquidity, spread, expiration, and premium before saving.",
    }


def build_options_contract_plan(symbol, strategy="long_call", save=False):
    policy = load_policy()
    if strategy not in set(policy.get("allowed_options_strategies", [])):
        raise ValueError(f"Strategy is not allowed in Paper Lab: {strategy}")

    ledger = build_paper_ledger()
    account = ledger.get("account") or {}
    equity = account.get("net_liquidation_value") or account.get("starting_cash") or 0
    max_premium = equity * float(policy.get("max_options_experiment_premium_pct", 0.5)) / 100
    snapshot = analyze_options_flow(symbol, max_expirations=12)
    plan = {
        "agent": "Paper Lab",
        "layer": "Options Contract Experiment",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "symbol": symbol.upper().strip(),
        "strategy": strategy,
        "status": "unavailable",
        "max_options_experiment_premium": round_money(max_premium),
        "snapshot": snapshot,
        "trade_id": None,
    }
    if snapshot.get("error"):
        plan["reason"] = snapshot.get("error")
        return plan

    contract = select_contract(snapshot, strategy)
    if not contract:
        plan["reason"] = "No starter contract passed the basic liquidity/price screen."
        return plan

    premium = contract_price(contract)
    if not premium:
        plan["reason"] = "Selected contract has no usable premium."
        return plan

    short_strike = suggested_short_strike(contract, snapshot, strategy)
    if strategy in {"call_debit_spread", "put_debit_spread"} and not short_strike:
        plan["reason"] = "Could not infer a simple short strike for the debit spread."
        return plan

    max_loss = premium * CONTRACT_MULTIPLIER
    over_budget = max_loss > max_premium if max_premium else False

    plan.update({
        "status": "preview_over_budget" if over_budget else "planned",
        "contract": contract,
        "expiration": contract.get("expiration"),
        "strike": contract.get("strike"),
        "short_strike": short_strike,
        "entry_premium": round_money(premium),
        "target_premium": round_money(premium * 1.5),
        "stop_premium": round_money(premium * 0.5),
        "contracts": 1,
        "max_loss": round_money(max_loss),
        "budget_warning": (
            f"Max loss exceeds Paper Lab option premium cap of {money(max_premium)}."
            if over_budget else ""
        ),
        "warning": "Starter/free options data only; paper experiment, not execution-grade.",
    })

    if save and over_budget:
        plan["trade_id"] = None
        plan["reason"] = "Not saved because the contract exceeds the Paper Lab option premium cap."
    elif save:
        plan["trade_id"] = append_option_trade({
            "symbol": plan["symbol"],
            "strategy": strategy,
            "status": "planned",
            "source": "paper lab",
            "expiration": plan["expiration"],
            "strike": plan["strike"],
            "short_strike": plan["short_strike"] or "",
            "entry_premium": plan["entry_premium"],
            "contracts": plan["contracts"],
            "target_premium": plan["target_premium"],
            "stop_premium": plan["stop_premium"],
            "thesis": "Paper Lab defined-risk options experiment.",
            "notes": plan["warning"],
        })

    return plan


def select_contract(snapshot, strategy):
    side = "put" if strategy in {"long_put", "put_debit_spread"} else "call"
    rows = []
    for expiration in snapshot.get("expiration_summaries", []):
        dte = expiration.get("days_to_expiration") or 0
        if dte < 14:
            continue
        key = "top_put_contracts" if side == "put" else "top_call_contracts"
        for contract in expiration.get(key, []):
            row = dict(contract)
            row["days_to_expiration"] = dte
            rows.append(row)

    clean = [
        row for row in rows
        if contract_price(row)
        and (row.get("spread_pct") is None or row.get("spread_pct") <= 0.25)
        and (row.get("open_interest") or 0) >= 100
    ]
    if not clean:
        return None

    return sorted(
        clean,
        key=lambda row: (
            abs((row.get("moneyness") or 0)),
            row.get("spread_pct") if row.get("spread_pct") is not None else 9,
            -float(row.get("open_interest") or 0),
        ),
    )[0]


def suggested_short_strike(contract, snapshot, strategy):
    strike = contract.get("strike")
    if not strike:
        return None
    price = snapshot.get("underlying_price") or strike
    width = max(1, round(price * 0.03, 0))
    if strategy == "call_debit_spread":
        return round_money(strike + width)
    if strategy == "put_debit_spread":
        return round_money(max(0, strike - width))
    return ""


def contract_price(contract):
    ask = contract.get("ask")
    last = contract.get("last_price")
    bid = contract.get("bid")
    if ask and ask > 0:
        return float(ask)
    if last and last > 0:
        return float(last)
    if bid and bid > 0:
        return float(bid)
    return None


def build_guardrails(policy, budget):
    return [
        "Paper Lab ideas are experiments, not promoted portfolio rules.",
        f"Experiment sleeve is capped at {budget['paper_lab_sleeve_pct']:.0%} of paper equity.",
        f"Single experiment risk cap is {budget['max_single_experiment_risk_pct']:.2f}% of paper equity.",
        "Defined-risk options only; no naked short option strategies.",
        "Record the result and lesson after every experiment closes.",
    ]


def build_summary(report):
    return {
        "stance": "aggressive_paper_experiments_allowed",
        "confidence_score": 85,
        "stock_experiment_count": len(report.get("stock_experiments", [])),
        "options_experiment_count": len(report.get("options_experiments", [])),
        "bearish_experiment_count": len(report.get("bearish_experiments", [])),
        "read": (
            "AIFundOS can push harder in Paper Lab while keeping the main portfolio rules intact. "
            "Focus on partial exits, trailing stops, defined-risk options, and failed-long/fade tests."
        ),
    }


def format_paper_lab_report(report):
    summary = report["summary"]
    budget = report["budget"]
    lines = [
        "# Paper Lab / Aggression Mode",
        "",
        f"Created At: {report['created_at']}",
        f"Mode: {report['mode']}",
        f"Stance: {summary['stance']}",
        f"Confidence: {summary['confidence_score']}/100",
        "",
        "## Budget",
        f"- Paper Lab sleeve: {budget['paper_lab_sleeve_pct']:.0%} / {money(budget['paper_lab_sleeve_value'])}",
        f"- Max single experiment risk: {budget['max_single_experiment_risk_pct']:.2f}% / {money(budget['max_single_experiment_risk'])}",
        f"- Max options premium per experiment: {budget['max_options_experiment_premium_pct']:.2f}% / {money(budget['max_options_experiment_premium'])}",
        f"- Open lab experiments: {budget['open_lab_experiments']} of {budget['max_open_experiments']}",
        "",
        "## Learning Readout",
    ]
    learning = report["learning_readout"]
    lines.extend([
        f"- Closed trades: {learning.get('closed_trade_count')}",
        f"- Win rate: {learning.get('win_rate')}%",
        f"- Average R: {learning.get('average_r')}",
        f"- Target 1 hit rate: {learning.get('target_1_hit_rate')}%",
        f"- Partial-win rate: {learning.get('partial_win_rate')}%",
        f"- Average entered P&L: {learning.get('average_entered_pnl')}%",
    ])
    lines.extend([f"- Lesson: {item}" for item in learning.get("lessons", [])])

    lines.extend(["", "## Stock Experiments"])
    lines.extend(format_experiment_rows(report.get("stock_experiments", []), stock=True))

    lines.extend(["", "## Options Experiments"])
    lines.extend(format_experiment_rows(report.get("options_experiments", [])))

    lines.extend(["", "## Bearish / Fade Experiments"])
    lines.extend(format_experiment_rows(report.get("bearish_experiments", [])))

    lines.extend(["", "## Promotion Rules"])
    lines.extend([f"- {item}" for item in report.get("promotion_rules", [])])
    lines.extend(["", "## Guardrails"])
    lines.extend([f"- {item}" for item in report.get("guardrails", [])])
    return "\n".join(lines) + "\n"


def format_options_contract_plan(plan):
    lines = [
        "# Paper Lab Options Contract Plan",
        "",
        f"Created At: {plan['created_at']}",
        f"Symbol: {plan['symbol']}",
        f"Strategy: {plan['strategy']}",
        f"Status: {plan['status']}",
    ]
    if plan.get("reason") and not plan.get("contract"):
        lines.append(f"Reason: {plan['reason']}")
        return "\n".join(lines) + "\n"
    lines.extend([
        f"Expiration: {plan.get('expiration')}",
        f"Long strike: {plan.get('strike')}",
        f"Short strike: {plan.get('short_strike') or 'n/a'}",
        f"Entry premium: {money(plan.get('entry_premium'))}",
        f"Target premium: {money(plan.get('target_premium'))}",
        f"Stop premium: {money(plan.get('stop_premium'))}",
        f"Contracts: {plan.get('contracts')}",
        f"Max loss: {money(plan.get('max_loss'))}",
        f"Premium cap: {money(plan.get('max_options_experiment_premium'))}",
        f"Budget warning: {plan.get('budget_warning') or 'none'}",
        f"Reason: {plan.get('reason') or 'n/a'}",
        f"Trade ID: {plan.get('trade_id') or 'not saved'}",
        f"Warning: {plan.get('warning')}",
    ])
    return "\n".join(lines) + "\n"


def format_experiment_rows(rows, stock=False):
    if not rows:
        return ["- None."]
    lines = []
    for row in rows:
        if stock:
            lines.append(
                f"- {row['display_symbol']} ({row['family']}): entry {fmt(row['entry'])}, "
                f"partial {fmt(row['partial'])}, target {fmt(row['target_1'])}. "
                f"{row['lesson_to_measure']}"
            )
        else:
            lines.append(
                f"- {row['display_symbol']} {row.get('strategy')} ({row.get('direction')}): "
                f"{row.get('status')}. {row.get('guardrail')}"
            )
    return lines


def save_paper_lab_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    latest_md = REPORTS_DIR / "paper_lab.md"
    latest_json = REPORTS_DIR / "paper_lab.json"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_md = REPORTS_DIR / f"paper_lab_{stamp}.md"
    archive_json = REPORTS_DIR / f"paper_lab_{stamp}.json"
    markdown = format_paper_lab_report(report)
    payload = json.dumps(report, indent=2, default=str)
    latest_md.write_text(markdown, encoding="utf-8")
    latest_json.write_text(payload, encoding="utf-8")
    archive_md.write_text(markdown, encoding="utf-8")
    archive_json.write_text(payload, encoding="utf-8")
    return latest_md


def round_money(value):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0


def money(value):
    return f"${round_money(value):,.2f}"


def fmt(value):
    return f"{round_money(value):,.2f}"
