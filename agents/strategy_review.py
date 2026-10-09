from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path

from agents.feedback_loop import generate_feedback_report
from memory.research_memory import get_recent_daily_setup_reviews, save_agent_report


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports" / "strategy_review"


def generate_strategy_source_review(save_memory=True):
    reviews = get_recent_daily_setup_reviews(limit=100)
    feedback = generate_feedback_report()
    groups = summarize_groups(reviews)
    setup = feedback.get("setup_review_learning") or {}
    findings = build_findings(setup, groups)
    report = {
        "agent": "Strategy and Source Review",
        "system_role": "learning_and_governance",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "run_id": f"{datetime.now().date().isoformat()}-strategy-source-review",
        "reviewed_setups": len(reviews),
        "learning_score": setup.get("learning_score", 0),
        "overall": setup,
        "by_strategy_family": groups["strategy_family"],
        "by_category": groups["category"],
        "by_decision": groups["decision"],
        "findings": findings,
        "changes_authorized": [
            "Route every new idea through a named strategy family and holding horizon.",
            "Use family-specific target and risk parameters instead of one global swing template.",
            "Allow only defined-loss long calls and long puts after strict contract liquidity checks.",
            "Keep scalp/day strategies blocked until intraday data and monitoring requirements pass.",
            "Score future outcomes by strategy family so weak methods can be paused independently.",
        ],
        "source_limits": [
            "Free options snapshots do not replace OPRA-style intraday flow or historical chains.",
            "Quiver and public filings do not provide complete institutional crowding in real time.",
            "Full Wall Street research-note text requires licensed access and must not be inferred.",
        ],
    }
    if save_memory:
        save_agent_report(
            run_id=report["run_id"],
            agent_name=report["agent"],
            output=report,
            symbol="PORTFOLIO",
            stance="strategy_change_authorized",
            confidence=95,
        )
    report["report_path"] = str(save_strategy_source_review(report))
    return report


def format_strategy_source_review(report):
    lines = [
        "# AIFundOS Strategy and Source Review",
        "",
        f"Created At: {report.get('created_at')}",
        f"Reviewed Setups: {report.get('reviewed_setups')}",
        f"Learning Score: {float(report.get('learning_score') or 0):.1f}/100",
        "",
        "## Findings",
    ]
    lines.extend([f"- {item}" for item in report.get("findings", [])] or ["- No material finding."])
    lines.extend(["", "## Authorized Strategy Changes"])
    lines.extend([f"- {item}" for item in report.get("changes_authorized", [])])
    lines.extend(["", "## Source Limits"])
    lines.extend([f"- {item}" for item in report.get("source_limits", [])])
    lines.extend(["", "## Historical Strategy Families"])
    for row in report.get("by_strategy_family", []):
        lines.append(
            f"- {row['strategy_family']}: {row['reviewed_count']} reviewed, "
            f"{row['entered_count']} entered, Target 1 {row['target_1_hit_rate']:.1f}%, "
            f"partial {row['partial_win_rate']:.1f}%, average P&L {row['avg_pnl_pct']:.2f}%."
        )
    return "\n".join(lines) + "\n"


def save_strategy_source_review(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    latest_md = REPORTS_DIR / "strategy_source_review.md"
    latest_json = REPORTS_DIR / "strategy_source_review.json"
    archive_md = REPORTS_DIR / f"strategy_source_review_{stamp}.md"
    archive_json = REPORTS_DIR / f"strategy_source_review_{stamp}.json"
    markdown = format_strategy_source_review(report)
    payload = json.dumps(report, indent=2, default=str)
    latest_md.write_text(markdown, encoding="utf-8")
    latest_json.write_text(payload, encoding="utf-8")
    archive_md.write_text(markdown, encoding="utf-8")
    archive_json.write_text(payload, encoding="utf-8")
    return latest_md


def summarize_groups(reviews):
    output = {}
    for key in ("strategy_family", "category", "decision"):
        buckets = defaultdict(list)
        for review in reviews:
            details = review.get("output") or {}
            value = details.get(key) or ("legacy_swing" if key == "strategy_family" else "Unclassified")
            buckets[str(value)].append(review)
        output[key] = [summarize_bucket(key, name, rows) for name, rows in buckets.items()]
        output[key].sort(key=lambda row: (row["entered_count"], row["avg_pnl_pct"]), reverse=True)
    return output


def summarize_bucket(key, name, rows):
    entered = [row for row in rows if row.get("entered")]
    outputs = [row.get("output") or {} for row in entered]
    target_hits = sum(bool(row.get("hit_target_1")) for row in entered)
    partial_hits = sum(bool(item.get("hit_partial_win")) for item in outputs)
    stop_hits = sum(bool(row.get("hit_stop")) for row in entered)
    pnl = [float(row["pnl_pct"]) for row in entered if row.get("pnl_pct") is not None]
    return {
        key: name,
        "reviewed_count": len(rows),
        "entered_count": len(entered),
        "target_1_hit_rate": percent(target_hits, len(entered)),
        "partial_win_rate": percent(partial_hits, len(entered)),
        "stop_first_rate": percent(stop_hits, len(entered)),
        "avg_pnl_pct": sum(pnl) / len(pnl) if pnl else 0,
    }


def build_findings(setup, groups):
    findings = []
    if setup.get("reviewed_setups", 0) >= 100 and setup.get("learning_score", 0) < 60:
        findings.append("The sample is large enough to reject the idea that more identical swing reviews alone will solve the problem.")
    if setup.get("entries_triggered", 0) < setup.get("reviewed_setups", 0) * 0.25:
        findings.append("Too few reviewed ideas trigger entries; entry design or candidate selection is overly restrictive.")
    if setup.get("partial_win_rate", 0) > setup.get("target_1_hit_rate", 0):
        findings.append("Partial moves occur more often than Target 1 hits; nearer exits and strategy-specific targets are justified.")
    if abs(setup.get("avg_max_adverse_move_pct") or 0) > (setup.get("avg_max_favorable_move_pct") or 0):
        findings.append("Adverse excursion exceeds favorable excursion; improve timing and stop repeating weak entry patterns.")
    if all(row.get("strategy_family") == "legacy_swing" for row in groups.get("strategy_family", [])):
        findings.append("Historical reviews are effectively one strategy family, so they cannot show which trading style has edge.")
    return findings


def percent(numerator, denominator):
    return numerator / denominator * 100 if denominator else 0
