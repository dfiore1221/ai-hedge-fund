import json
import io
import contextlib
import os
import signal
from datetime import datetime
from pathlib import Path

from agents.cio import create_cio_summary
from agents.core_etf_sleeve import analyze_core_etf_sleeve
from agents.market_intelligence import generate_daily_market_intelligence
from data.paper_ledger import build_paper_ledger
from data.data_quality import generate_data_health_report
from data.trade_journal import load_trade_journal, summarize_trade_journal


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WATCHLIST_PATH = PROJECT_ROOT / "framework" / "watchlist.json"
REPORTS_DIR = PROJECT_ROOT / "reports" / "morning_brief"
DEFAULT_TOP_N = 10
DEFAULT_SYMBOL_TIMEOUT_SECONDS = 8
DEFAULT_MAX_IDEAS_PER_CATEGORY = 2


def load_watchlist_entries():
    if not WATCHLIST_PATH.exists():
        return [
            {"symbol": "MSFT", "display_symbol": "MSFT", "category": "AI Platforms"},
            {"symbol": "NVDA", "display_symbol": "NVDA", "category": "AI Semiconductors"},
            {"symbol": "GOOGL", "display_symbol": "GOOGL", "category": "AI Platforms"},
            {"symbol": "AMZN", "display_symbol": "AMZN", "category": "AI Platforms"},
            {"symbol": "META", "display_symbol": "META", "category": "AI Platforms"},
            {"symbol": "QQQ", "display_symbol": "QQQ", "category": "ETF"},
            {"symbol": "SPY", "display_symbol": "SPY", "category": "ETF"},
        ]

    data = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
    entries = []

    for item in data.get("symbols", []):
        if isinstance(item, str):
            symbol = item.upper().strip()
            if symbol:
                entries.append({
                    "symbol": symbol,
                    "display_symbol": symbol,
                    "category": "Uncategorized",
                })
            continue

        symbol = item.get("symbol", "").upper().strip()
        if not symbol:
            continue
        entries.append({
            "symbol": symbol,
            "display_symbol": item.get("display_symbol", symbol).upper().strip(),
            "category": item.get("category", "Uncategorized"),
            "role": item.get("role"),
            "bucket": item.get("bucket"),
            "notes": item.get("notes"),
        })

    return entries


def load_watchlist():
    return [entry["symbol"] for entry in load_watchlist_entries()]


def create_morning_brief(symbols=None, max_ideas=DEFAULT_TOP_N):
    entries = build_entries(symbols)
    symbols = [entry["symbol"] for entry in entries]
    metadata_by_symbol = {entry["symbol"]: entry for entry in entries}
    data_health = generate_data_health_report(symbols=symbols, live_checks=True)
    macro_report = generate_daily_market_intelligence()
    journal = load_trade_journal()
    journal_summary = summarize_trade_journal(journal)
    ledger = build_paper_ledger(journal)
    core_sleeve = analyze_core_etf_sleeve(macro_report, journal=journal, ledger=ledger)
    summaries = []

    for symbol in symbols:
        try:
            summary = run_committee_scan(symbol, macro_report)
        except Exception as exc:
            summary = build_error_summary(symbol, exc, macro_report)
        summary["watchlist"] = metadata_by_symbol.get(symbol, {
            "symbol": symbol,
            "display_symbol": symbol,
            "category": "Uncategorized",
        })
        summaries.append(summary)

    ranked = sorted(
        [summary for summary in summaries if not summary.get("error")],
        key=score_candidate,
        reverse=True,
    )
    approved = [
        summary
        for summary in ranked
        if summary.get("final_decision", {}).get("status") == "PAPER TRADE ONLY"
    ]
    conditional = [
        summary
        for summary in ranked
        if summary.get("final_decision", {}).get("status") == "CONDITIONAL SETUP"
    ]
    watch = [
        summary
        for summary in ranked
        if summary.get("final_decision", {}).get("status") == "WATCHLIST SETUP"
    ]
    rejected = [
        summary
        for summary in ranked
        if summary.get("final_decision", {}).get("status") == "NO TRADE"
    ]
    needs_data = [
        summary
        for summary in ranked
        if summary.get("final_decision", {}).get("status") == "NEEDS DATA"
    ]
    fade_watch = sorted(
        [
            summary
            for summary in ranked
            if failed_long_signal(summary).get("status") in {"watch", "strong_watch"}
        ],
        key=lambda item: failed_long_signal(item).get("score") or 0,
        reverse=True,
    )

    return {
        "agent": "Morning Brief",
        "system_role": "tool_workflow",
        "layer": "Decision Presentation",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "watch_only",
        "data_health": data_health,
        "journal_summary": journal_summary,
        "core_etf_sleeve": core_sleeve,
        "macro": macro_report,
        "symbols_scanned": symbols,
        "top_n": max_ideas,
        "approved_simulated_trades": [
            summarize_idea(summary)
            for summary in select_diversified_ideas(approved, max_ideas)
        ],
        "conditional_setups": [
            summarize_idea(summary)
            for summary in select_diversified_ideas(conditional, max_ideas)
        ],
        "worth_watching": [
            summarize_idea(summary)
            for summary in select_diversified_ideas(watch, max_ideas)
        ],
        "bearish_fade_watch": [
            summarize_idea(summary)
            for summary in select_diversified_ideas(fade_watch, max_ideas)
        ],
        "rejected_or_avoid": [
            summarize_idea(summary)
            for summary in select_diversified_ideas(rejected, max_ideas)
        ],
        "needs_data": [
            summarize_idea(summary)
            for summary in select_diversified_ideas(needs_data, max_ideas)
        ],
        "ideas": [
            summarize_idea(summary)
            for summary in select_diversified_ideas(ranked, max_ideas)
        ],
        "category_summary": build_category_summary(summaries),
        "universe_balance": build_universe_balance(entries, summaries),
        "committee_summaries": summaries,
        "missing_information": collect_missing_information(summaries, data_health),
    }


def build_entries(symbols):
    if symbols is None:
        return load_watchlist_entries()

    entries = []
    for symbol in symbols:
        clean = symbol.upper().strip()
        if clean:
            entries.append({
                "symbol": clean,
                "display_symbol": clean,
                "category": "Ad Hoc Scan",
            })
    return entries


def run_committee_scan(symbol, macro_report):
    # yfinance can print harmless ETF metadata warnings directly; keep the brief clean.
    with symbol_scan_timeout(), contextlib.redirect_stderr(io.StringIO()):
        return create_cio_summary(
            symbol,
            macro_report=macro_report,
            include_options=False,
            include_alternative=False,
            include_recommendation_trends=False,
        )


@contextlib.contextmanager
def symbol_scan_timeout(seconds=None):
    seconds = seconds or int(os.getenv("MORNING_BRIEF_SYMBOL_TIMEOUT_SECONDS", DEFAULT_SYMBOL_TIMEOUT_SECONDS))
    if not hasattr(signal, "SIGALRM") or seconds <= 0:
        yield
        return

    def handle_timeout(signum, frame):
        raise TimeoutError(f"Committee scan exceeded {seconds} seconds.")

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    signal.signal(signal.SIGALRM, handle_timeout)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])


def build_error_summary(symbol, exc, macro_report):
    return {
        "agent": "Chief Investment Officer",
        "symbol": symbol.upper().strip(),
        "error": str(exc),
        "market_regime": macro_report["assessment"]["market_regime"],
        "final_decision": {
            "status": "ERROR",
            "confidence": 0,
            "reason": "Committee scan failed for this symbol.",
        },
    }


def score_candidate(summary):
    score = 0
    decision = summary.get("final_decision", {})
    technical_stance = summary.get("technical_stance")
    risk_decision = summary.get("risk_decision")
    options_stance = summary.get("options_stance")
    news_stance = summary.get("news_stance")
    news_score = summary.get("news_catalyst_score")
    thesis = summary.get("current_thesis") or {}
    plan = summary.get("trade_plan") or {}
    source_reports = summary.get("source_reports") or {}
    backtest = source_reports.get("backtest") or {}
    conflict_memo = summary.get("conflict_memo") or {}
    realism = calculate_setup_realism(summary)

    if decision.get("status") == "PAPER TRADE ONLY":
        score += 40
    elif decision.get("status") == "CONDITIONAL SETUP":
        score += 30
    elif decision.get("status") == "WATCHLIST SETUP":
        score += 20
    elif decision.get("status") == "NO TRADE":
        score -= 5
    elif decision.get("status") == "NEEDS DATA":
        score -= 25

    if risk_decision == "approved_for_paper_trade":
        score += 18
    elif risk_decision == "conditional_setup":
        score += 10
    elif risk_decision == "watchlist_setup":
        score += 4
    elif risk_decision == "veto":
        score -= 20

    if technical_stance == "bullish":
        score += 25
    elif technical_stance == "neutral":
        score += 12
    elif technical_stance == "no_trade":
        score -= 10
    elif technical_stance == "bearish":
        score -= 35

    reward_to_risk = get_reward_to_risk(summary)
    if reward_to_risk is not None:
        score += min(20, max(-10, (reward_to_risk - 1) * 12))

    expectancy = backtest.get("expectancy_pct")
    sample_size = backtest.get("sample_size") or 0
    if expectancy is not None:
        score += min(15, max(-15, expectancy * 5))
    if sample_size >= 20:
        score += 6
    elif sample_size:
        score -= 4

    if options_stance in {"bullish_positioning", "bullish_lean"}:
        score += 5
    elif options_stance in {"bearish_positioning", "bearish_or_hedging_positioning", "protective_or_bearish_lean"}:
        score -= 5

    if news_stance == "positive_catalyst":
        score += 8
    elif news_stance == "negative_catalyst":
        score -= 12
    elif news_stance == "mixed_or_monitor":
        score += 2

    if news_score is not None:
        score += min(8, max(-8, news_score))

    rating = realism.get("rating")
    tradability = (((source_reports.get("risk") or {}).get("setup_calibration") or {}).get("classification"))
    if tradability == "conditional_trade_candidate":
        score += 6
    elif tradability == "starter_only":
        score += 1
    elif tradability == "interesting_not_tradable":
        score -= 10

    if rating == "reasonable":
        score += 4
    elif rating == "extended_target":
        score -= 8
    elif rating in {"ambitious_target", "too_wide_stop", "too_tight_stop"}:
        score -= 12
    elif rating == "unknown":
        score -= 3

    setup_learning = ((source_reports.get("risk") or {}).get("setup_learning_memory") or {})
    learning_score = setup_learning.get("learning_score")
    if learning_score is not None and learning_score < 55:
        score -= 8
    target_rate = setup_learning.get("target_1_hit_rate")
    if target_rate is not None and target_rate < 20:
        score -= 6

    fade_signal = failed_long_signal(summary)
    if fade_signal.get("status") == "strong_watch":
        score -= 18
    elif fade_signal.get("status") == "watch":
        score -= 8

    if thesis.get("rating") == "Watchlist":
        score += 12
    elif thesis.get("rating") == "Deep Research Candidate":
        score += 8

    if summary.get("market_regime") == "Risk-Off" and decision.get("status") != "PAPER TRADE ONLY":
        score -= 8

    score -= 4 * conflict_memo.get("conflict_count", 0)

    if plan.get("entry_trigger") and plan.get("stop") and plan.get("target_1"):
        score += 4

    return round(score, 2)


def select_diversified_ideas(candidates, max_ideas, max_per_category=None):
    max_per_category = max_per_category or int(
        os.getenv("MORNING_BRIEF_MAX_IDEAS_PER_CATEGORY", DEFAULT_MAX_IDEAS_PER_CATEGORY)
    )
    selected = []
    skipped = []
    category_counts = {}
    selected_ids = set()

    for summary in candidates:
        category = idea_category(summary)
        if category_counts.get(category, 0) >= max_per_category:
            skipped.append(summary)
            continue
        selected.append(summary)
        selected_ids.add(id(summary))
        category_counts[category] = category_counts.get(category, 0) + 1
        if len(selected) >= max_ideas:
            return selected

    for summary in skipped:
        if id(summary) in selected_ids:
            continue
        selected.append(summary)
        if len(selected) >= max_ideas:
            break

    return selected


def idea_category(summary):
    watchlist = summary.get("watchlist") or {}
    return watchlist.get("category", "Uncategorized")


def failed_long_signal(summary):
    source_reports = summary.get("source_reports") or {}
    risk = source_reports.get("risk") or {}
    return summary.get("failed_long_signal") or risk.get("failed_long_signal") or {}


def get_reward_to_risk(summary):
    source_reports = summary.get("source_reports") or {}
    risk = source_reports.get("risk") or {}
    calibration = risk.get("setup_calibration") or {}
    if calibration.get("calibrated_reward_to_risk") is not None:
        return calibration["calibrated_reward_to_risk"]
    if risk.get("reward_to_risk") is not None:
        return risk["reward_to_risk"]
    if risk.get("reward_to_risk_to_target_2") is not None:
        return risk["reward_to_risk_to_target_2"]
    if risk.get("pullback_reward_to_risk") is not None:
        return risk["pullback_reward_to_risk"]

    technical = source_reports.get("technical") or {}
    setup = technical.get("setup") or {}
    return setup.get("reward_to_risk")


def calculate_setup_realism(summary):
    source_reports = summary.get("source_reports") or {}
    technical = source_reports.get("technical") or {}
    risk = source_reports.get("risk") or {}
    momentum = technical.get("momentum") or {}
    atr = momentum.get("atr_14")
    setup = technical.get("setup") or {}
    conditional = risk.get("conditional_plan") or {}

    entry = conditional.get("suggested_entry") or risk.get("entry") or setup.get("entry_trigger")
    stop = risk.get("stop") or setup.get("stop")
    calibration = risk.get("setup_calibration") or {}
    target = calibration.get("calibrated_target_1") or risk.get("target_1") or setup.get("target_1")

    if not entry or not stop or not target:
        return {
            "rating": "unknown",
            "message": "Missing entry, stop, or target for ATR realism check.",
            "atr_14": atr,
            "stop_atr": None,
            "target_atr": None,
        }
    if not atr or atr <= 0:
        return {
            "rating": "unknown",
            "message": "ATR unavailable; target and stop realism cannot be judged.",
            "atr_14": atr,
            "stop_atr": None,
            "target_atr": None,
        }

    stop_atr = abs(float(entry) - float(stop)) / float(atr)
    target_atr = abs(float(target) - float(entry)) / float(atr)

    if stop_atr < 0.5:
        rating = "too_tight_stop"
        message = "Stop is inside 0.5 ATR; normal noise could trigger it."
    elif stop_atr > 2.75:
        rating = "too_wide_stop"
        message = "Stop is wider than 2.75 ATR; paper risk may be too loose."
    elif target_atr > 4.0:
        rating = "ambitious_target"
        message = "Target 1 is more than 4 ATR away; lower near-term hit-rate assumption."
    elif target_atr > 3.0:
        rating = "extended_target"
        message = "Target 1 is 3-4 ATR away; require stronger confirmation or more time."
    else:
        rating = "reasonable"
        message = "Stop and Target 1 fit a normal swing-trade volatility range."

    return {
        "rating": rating,
        "message": message,
        "atr_14": atr,
        "stop_atr": stop_atr,
        "target_atr": target_atr,
    }


def summarize_idea(summary):
    source_reports = summary.get("source_reports") or {}
    technical = source_reports.get("technical") or {}
    risk = source_reports.get("risk") or {}
    backtest = source_reports.get("backtest") or {}
    thesis = summary.get("current_thesis") or {}
    watchlist = summary.get("watchlist") or {}

    calibration = risk.get("setup_calibration") or {}
    fade_signal = summary.get("failed_long_signal") or risk.get("failed_long_signal") or {}
    target_1 = calibration.get("calibrated_target_1") or risk.get("target_1") or (technical.get("setup") or {}).get("target_1")

    return {
        "symbol": summary["symbol"],
        "run_id": summary.get("run_id"),
        "display_symbol": watchlist.get("display_symbol", summary["symbol"]),
        "category": watchlist.get("category", "Uncategorized"),
        "score": score_candidate(summary),
        "decision": summary["final_decision"]["status"],
        "reason": build_idea_reason(summary),
        "technical_stance": summary.get("technical_stance"),
        "risk_decision": summary.get("risk_decision"),
        "reward_to_risk": get_reward_to_risk(summary),
        "setup_realism": calculate_setup_realism(summary),
        "tradability": calibration.get("classification"),
        "trade_structure_note": calibration.get("message"),
        "partial_win_level": calibration.get("partial_win_level"),
        "original_target_1": calibration.get("original_target_1") or risk.get("target_1") or (technical.get("setup") or {}).get("target_1"),
        "trade_vehicle_options": risk.get("trade_vehicle_options", []),
        "failed_long_signal": fade_signal,
        "bearish_trade_options": fade_signal.get("possible_trade_options", []),
        "entry_trigger": risk.get("entry") or (technical.get("setup") or {}).get("entry_trigger"),
        "suggested_entry": (risk.get("conditional_plan") or {}).get("suggested_entry"),
        "condition": (risk.get("conditional_plan") or {}).get("condition"),
        "stop": risk.get("stop") or (technical.get("setup") or {}).get("stop"),
        "target_1": target_1,
        "target_2": risk.get("target_2") or (technical.get("setup") or {}).get("target_2"),
        "target_3": risk.get("target_3") or (technical.get("setup") or {}).get("target_3"),
        "backtest_expectancy": backtest.get("expectancy_pct"),
        "backtest_sample_size": backtest.get("sample_size"),
        "news_stance": summary.get("news_stance"),
        "news_catalyst_score": summary.get("news_catalyst_score"),
        "news_top_headline": summary.get("news_top_headline"),
        "thesis_rating": thesis.get("rating"),
        "conflict_count": (summary.get("conflict_memo") or {}).get("conflict_count", 0),
    }


def build_idea_reason(summary):
    reasons = []
    source_reports = summary.get("source_reports") or {}
    technical = source_reports.get("technical") or {}
    risk = source_reports.get("risk") or {}
    backtest = source_reports.get("backtest") or {}
    thesis = summary.get("current_thesis") or {}
    news = source_reports.get("news") or {}
    news_summary = news.get("summary") or {}

    if summary.get("risk_decision") == "approved_for_paper_trade":
        reasons.append("risk checks passed")
    elif summary.get("risk_decision") == "conditional_setup":
        plan = risk.get("conditional_plan") or {}
        reasons.append(plan.get("condition") or "conditional setup")
    elif summary.get("risk_decision") == "watchlist_setup":
        issues = risk.get("conditional_issues") or []
        reasons.append(issues[0] if issues else "watchlist setup")
    elif risk.get("vetoes"):
        reasons.append(f"risk veto: {risk['vetoes'][0]}")

    if summary.get("technical_stance"):
        reasons.append(f"technical stance is {summary['technical_stance']}")

    rr = get_reward_to_risk(summary)
    if rr is not None:
        reasons.append(f"reward/risk {rr:.2f}")

    realism = calculate_setup_realism(summary)
    if realism.get("rating") in {"extended_target", "ambitious_target", "too_wide_stop", "too_tight_stop"}:
        reasons.append(realism.get("message", "ATR realism check is unfavorable").lower())

    expectancy = backtest.get("expectancy_pct")
    if expectancy is not None:
        reasons.append(f"starter backtest expectancy {expectancy:.2f}%")

    if thesis.get("rating"):
        reasons.append(f"thesis rating {thesis['rating']}")

    if news.get("stance") and news.get("stance") != "no_clear_catalyst":
        reasons.append(
            f"news {news['stance']} score {format_number(news_summary.get('total_score'))}"
        )

    evidence = technical.get("key_evidence") or []
    if evidence:
        reasons.append(evidence[0].lower())

    return "; ".join(reasons[:4])


def build_category_summary(summaries):
    categories = {}

    for summary in summaries:
        watchlist = summary.get("watchlist") or {}
        category = watchlist.get("category", "Uncategorized")
        decision = (summary.get("final_decision") or {}).get("status", "ERROR")

        if category not in categories:
            categories[category] = {
                "category": category,
                "symbols": 0,
                "paper_trade": 0,
                "conditional": 0,
                "watchlist": 0,
                "no_trade": 0,
                "needs_data": 0,
                "errors": 0,
            }

        categories[category]["symbols"] += 1
        if decision == "PAPER TRADE ONLY":
            categories[category]["paper_trade"] += 1
        elif decision == "CONDITIONAL SETUP":
            categories[category]["conditional"] += 1
        elif decision == "WATCHLIST SETUP":
            categories[category]["watchlist"] += 1
        elif decision == "NO TRADE":
            categories[category]["no_trade"] += 1
        elif decision == "NEEDS DATA":
            categories[category]["needs_data"] += 1
        else:
            categories[category]["errors"] += 1

    return sorted(categories.values(), key=lambda item: item["category"])


def build_universe_balance(entries, summaries):
    buckets = {}
    by_symbol = {summary.get("symbol"): summary for summary in summaries}

    for entry in entries:
        symbol = entry["symbol"]
        bucket = classify_universe_bucket(entry)
        category = entry.get("category", "Uncategorized")
        summary = by_symbol.get(symbol) or {}
        decision = (summary.get("final_decision") or {}).get("status")

        if bucket not in buckets:
            buckets[bucket] = {
                "bucket": bucket,
                "symbols": 0,
                "scanned": 0,
                "paper_trade": 0,
                "conditional": 0,
                "watchlist": 0,
                "no_trade": 0,
                "needs_data": 0,
                "errors": 0,
                "categories": {},
            }

        buckets[bucket]["symbols"] += 1
        buckets[bucket]["categories"][category] = buckets[bucket]["categories"].get(category, 0) + 1
        if summary:
            buckets[bucket]["scanned"] += 1
        if decision == "PAPER TRADE ONLY":
            buckets[bucket]["paper_trade"] += 1
        elif decision == "CONDITIONAL SETUP":
            buckets[bucket]["conditional"] += 1
        elif decision == "WATCHLIST SETUP":
            buckets[bucket]["watchlist"] += 1
        elif decision == "NO TRADE":
            buckets[bucket]["no_trade"] += 1
        elif decision == "NEEDS DATA":
            buckets[bucket]["needs_data"] += 1
        elif summary:
            buckets[bucket]["errors"] += 1

    bucket_rows = sorted(buckets.values(), key=lambda item: item["symbols"], reverse=True)
    total_symbols = sum(bucket["symbols"] for bucket in bucket_rows)
    market_map_symbols = sum(
        bucket["symbols"]
        for bucket in bucket_rows
        if bucket["bucket"] == "Market Map / Sector ETFs"
    )

    return {
        "total_symbols": total_symbols,
        "market_map_symbols": market_map_symbols,
        "bucket_summary": [
            {
                **bucket,
                "top_categories": top_bucket_categories(bucket["categories"]),
            }
            for bucket in bucket_rows
        ],
        "notes": build_universe_balance_notes(bucket_rows, total_symbols, market_map_symbols),
    }


def classify_universe_bucket(entry):
    if entry.get("bucket"):
        return entry["bucket"]

    category = str(entry.get("category", "Uncategorized")).lower()
    role = str(entry.get("role") or "").lower()

    if role == "market_map" or "etf" in category:
        return "Market Map / Sector ETFs"
    if any(keyword in category for keyword in [
        "healthcare", "utilities", "real estate", "staples", "power",
    ]):
        return "Defensive / Income"
    if any(keyword in category for keyword in [
        "energy", "industrial", "materials", "mining", "metals",
        "machinery", "aerospace", "defense", "financial", "banks",
        "consumer", "transport",
    ]):
        return "Cyclical / Value"
    if any(keyword in category for keyword in [
        "ai", "semiconductor", "software", "cybersecurity", "crypto",
        "quantum", "photonics",
    ]) or category == "space":
        return "AI / Growth / Innovation"
    return "Other / Special Situations"


def top_bucket_categories(categories, limit=4):
    ranked = sorted(categories.items(), key=lambda item: (-item[1], item[0]))
    return [
        {"category": category, "symbols": symbols}
        for category, symbols in ranked[:limit]
    ]


def build_universe_balance_notes(bucket_rows, total_symbols, market_map_symbols):
    if not total_symbols:
        return ["No universe metadata available."]

    notes = []
    largest_bucket = bucket_rows[0] if bucket_rows else None
    if largest_bucket and largest_bucket["symbols"] / total_symbols > 0.45:
        notes.append(
            f"Universe remains concentrated in {largest_bucket['bucket']}; "
            "the Committee should discount crowded-theme agreement."
        )
    else:
        notes.append("Universe is broad enough to compare leading themes against defensive and cyclical alternatives.")

    if market_map_symbols >= 10:
        notes.append("Market-map ETFs are available as a cross-check before single-stock recommendations.")
    else:
        notes.append("Market-map ETF coverage is still thin; add more sector ETFs before loosening risk gates.")

    notes.append("Top idea lists are category-diversified so one theme cannot dominate the brief by score alone.")
    return notes


def collect_missing_information(summaries, data_health=None):
    missing = []
    seen = set()
    suppressed = {
        "Starter Yahoo news feed failed.",
        "Starter Yahoo news feed failed and no Finnhub headlines were available.",
        "Finnhub company news returned no recent headlines for this symbol.",
        "Finnhub company news feed failed or was rate-limited.",
        "Finnhub recommendation trend feed failed or was rate-limited.",
        "Benzinga returned no recent market-moving headlines for this symbol.",
    }

    data_health = data_health or {}
    domains = data_health.get("domain_scores") or {}
    news_status = (domains.get("news_analyst") or {}).get("status")
    if news_status == "strong":
        suppressed.add("Benzinga is not connected; premium real-time market-moving news is unavailable.")
        suppressed.add("Benzinga news feed failed, was rate-limited, or is not included in the current plan.")
        suppressed.add("Benzinga analyst ratings feed failed, was rate-limited, or is not included in the current plan.")

    for summary in summaries:
        for item in summary.get("missing_information", []):
            if item in suppressed:
                continue
            item = normalize_missing_information(item)
            if item not in seen:
                missing.append(item)
                seen.add(item)

    return missing


def normalize_missing_information(item):
    if item == "Backtested expectancy unavailable or sample size is zero.":
        return "Some symbols lack enough backtest history; confidence should be discounted."
    if item == "Backtest sample size is small; confidence should be discounted.":
        return "Some backtest samples are small; confidence should be discounted."
    return item


def format_morning_brief(report):
    assessment = report["macro"]["assessment"]
    summaries = report["committee_summaries"]
    top_n = report.get("top_n", DEFAULT_TOP_N)
    data_health = report.get("data_health") or {}
    data_gate = data_health.get("gate") or {}
    journal_summary = report.get("journal_summary") or {}
    core_sleeve = report.get("core_etf_sleeve") or {}
    macro_interpretation = report["macro"].get("macro_event_interpretation") or {}
    universe_balance = report.get("universe_balance") or {}

    lines = [
        "# AI Hedge Fund Morning Brief",
        "",
        f"Created At: {report['created_at']}",
        "Mode: Watch Only / No Live Trading",
        "",
        "## CIO Summary",
        f"- Market Regime: {assessment['market_regime']} ({assessment['macro_score']}/100)",
        f"- Macro Confidence: {assessment['confidence_score']}/100",
        f"- Symbols Scanned: {len(report['symbols_scanned'])}",
        f"- Paper-Trade Candidates: {count_decisions(summaries, 'PAPER TRADE ONLY')}",
        f"- Conditional Setups: {count_decisions(summaries, 'CONDITIONAL SETUP')}",
        f"- Watchlist Setups: {count_decisions(summaries, 'WATCHLIST SETUP')}",
        f"- Bearish/Fade Watch: {len(report.get('bearish_fade_watch', []))}",
        f"- No-Trade / Avoid Today: {count_decisions(summaries, 'NO TRADE')}",
        f"- Needs Data: {count_decisions(summaries, 'NEEDS DATA')}",
        "",
        "## Shared Macro Context",
        f"- {macro_interpretation.get('committee_summary', 'Macro event interpretation unavailable.')}",
    ]
    lines.extend([
        f"- {item}"
        for item in macro_interpretation.get("portfolio_implications", [])[:5]
    ] or ["- No specific portfolio implication flagged."])
    sector_implications = macro_interpretation.get("sector_implications") or {}
    if sector_implications.get("potential_beneficiaries"):
        lines.append(f"- Potential beneficiaries: {', '.join(sector_implications['potential_beneficiaries'])}")
    if sector_implications.get("potential_risks"):
        lines.append(f"- Potential risks: {', '.join(sector_implications['potential_risks'])}")
    lines.extend([
        "",
        "## Data Quality Gate",
        f"- Score: {data_health.get('data_quality_score', 'n/a')}/100",
        f"- Status: {data_gate.get('status', 'n/a')}",
        f"- Decision: {data_gate.get('decision', 'n/a')}",
        "- Note: Full live provider checks are available with `python3 main.py data-health today`.",
        "",
        "## Universe Balance",
        f"- Total Universe: {universe_balance.get('total_symbols', len(report['symbols_scanned']))} symbols",
        f"- Market-Map ETFs: {universe_balance.get('market_map_symbols', 0)} symbols",
    ])
    for bucket in (universe_balance.get("bucket_summary") or [])[:6]:
        lines.append(
            f"- {bucket['bucket']}: {bucket['symbols']} symbols | "
            f"paper {bucket['paper_trade']} | conditional {bucket['conditional']} | "
            f"watchlist {bucket['watchlist']} | no trade {bucket['no_trade']}"
        )
        top_categories = bucket.get("top_categories") or []
        if top_categories:
            lines.append(
                "  - Top categories: "
                + ", ".join(
                    f"{item['category']} ({item['symbols']})"
                    for item in top_categories
                )
            )
    lines.extend([
        "",
        "### Universe Notes",
    ])
    lines.extend([f"- {item}" for item in universe_balance.get("notes", [])] or ["- No universe balance notes."])
    lines.extend([
        "",
        "## Simulated Portfolio Memory",
        f"- Open / Planned Trades: {journal_summary.get('open_trades', 0)}",
        f"- Closed Trades: {journal_summary.get('closed_trades', 0)}",
        f"- Today Realized P&L: {format_money(journal_summary.get('today_realized_pnl', 0))}",
        f"- Week Realized P&L: {format_money(journal_summary.get('week_realized_pnl', 0))}",
        f"- Open Unrealized P&L: {format_money(journal_summary.get('open_unrealized_pnl', 0))}",
        f"- Open Planned Risk: {format_money(journal_summary.get('open_planned_risk', 0))}",
        f"- Open Symbols: {', '.join(journal_summary.get('open_symbols') or []) if journal_summary.get('open_symbols') else 'None'}",
        "",
        "## Core ETF Sleeve",
        f"- Status: {core_sleeve.get('status', 'n/a')}",
        f"- Target Sleeve: {format_pct(core_sleeve.get('target_sleeve_pct'))} / {format_money(core_sleeve.get('target_sleeve_value', 0))}",
        f"- Current Sleeve: {format_pct(core_sleeve.get('current_sleeve_pct'))} / {format_money(core_sleeve.get('current_sleeve_value', 0))}",
        f"- Drift: {format_money(core_sleeve.get('drift_value', 0))} ({format_pct(core_sleeve.get('drift_pct'))})",
        f"- Cash Reserve Policy: {format_pct(core_sleeve.get('cash_reserve_pct'))}",
        "- Desired Allocation: "
        + format_core_allocations(core_sleeve.get("desired_allocations", [])),
        "",
        "### Core ETF Actions",
    ])
    lines.extend([f"- {item}" for item in core_sleeve.get("actions", [])] or ["- None."])
    lines.extend([
        "",
        "## Approved Simulated Trades",
    ]
    )

    append_idea_section(lines, report["approved_simulated_trades"], empty_text="None today.")

    lines.extend([
        "",
        f"## Conditional Setups (Top {top_n})",
    ])
    append_idea_section(
        lines,
        report["conditional_setups"],
        empty_text="None today.",
        show_guardrail=True,
    )

    lines.extend([
        "",
        f"## Watchlist Setups (Top {top_n})",
    ])
    append_idea_section(
        lines,
        report["worth_watching"],
        empty_text="None today.",
        show_guardrail=True,
    )

    lines.extend([
        "",
        f"## Bearish / Failed-Long Watch (Top {top_n})",
    ])
    append_idea_section(
        lines,
        report.get("bearish_fade_watch", []),
        empty_text="None today.",
        show_guardrail=True,
    )

    lines.extend([
        "",
        f"## Rejected / Avoid Today (Top {top_n} by Review Score)",
    ])
    append_idea_section(
        lines,
        report["rejected_or_avoid"],
        empty_text="No rejected names surfaced.",
        show_guardrail=True,
    )

    lines.extend([
        "",
        "## Category Scan",
    ])

    for category in report.get("category_summary", []):
        lines.append(
            f"- {category['category']}: {category['symbols']} scanned | "
            f"paper {category['paper_trade']} | conditional {category['conditional']} | "
            f"watchlist {category['watchlist']} | no trade {category['no_trade']} | "
            f"needs data {category['needs_data']} | errors {category['errors']}"
        )

    errors = [summary for summary in summaries if summary.get("error")]
    if errors:
        lines.extend(["", "## Symbols Needing Cleanup"])
        for summary in errors[:top_n]:
            display = get_display_symbol(summary)
            lines.append(f"- {display}: {summary['error']}")

    lines.extend([
        "",
        "## Guardrails",
        "- This is a watch-only research brief, not a live trade instruction.",
        "- Risk vetoes override shared evidence such as thesis clues, news, options, or backtests.",
        "- Conditional setups require the stated entry, target, or confirmation before simulated trade approval.",
        "- Any paper trade still requires human review before action.",
        "",
        "## Missing Information",
    ])
    lines.extend([f"- {item}" for item in report["missing_information"]] or ["- None."])

    return "\n".join(lines) + "\n"


def append_idea_section(lines, ideas, empty_text, show_guardrail=False):
    if not ideas:
        lines.append(f"- {empty_text}")
        return

    for index, idea in enumerate(ideas, start=1):
        label = format_idea_label(idea)
        lines.append(
            f"{index}. {label} - {idea['decision']} "
            f"(score {format_number(idea['score'])}, {idea['category']})"
        )
        lines.append(f"   - Why: {idea['reason'] or 'No clear positive setup.'}")
        if idea.get("run_id"):
            lines.append(f"   - Run ID: {idea['run_id']}")
        lines.append(
            f"   - Setup: entry {format_number(idea['entry_trigger'])}, "
            f"suggested {format_number(idea.get('suggested_entry'))}, "
            f"stop {format_number(idea['stop'])}, target {format_number(idea['target_1'])}, "
            f"reward/risk {format_number(idea['reward_to_risk'])}"
        )
        if idea.get("original_target_1") and idea.get("original_target_1") != idea.get("target_1"):
            lines.append(
                f"   - Target calibration: first target {format_number(idea.get('target_1'))}; "
                f"stretch target {format_number(idea.get('original_target_1'))}"
            )
        if idea.get("partial_win_level"):
            lines.append(f"   - Partial-win level: {format_number(idea.get('partial_win_level'))}")
        if idea.get("tradability"):
            lines.append(
                f"   - Tradability: {idea.get('tradability')} - "
                f"{idea.get('trade_structure_note') or 'n/a'}"
            )
        fade_signal = idea.get("failed_long_signal") or {}
        if fade_signal.get("status") in {"watch", "strong_watch"}:
            lines.append(
                f"   - Failed-long/fade watch: {fade_signal.get('status')} "
                f"(score {format_number(fade_signal.get('score'))}) - {fade_signal.get('summary')}"
            )
            evidence = fade_signal.get("evidence") or []
            if evidence:
                lines.append(f"   - Fade evidence: {evidence[0]}")
        realism = idea.get("setup_realism") or {}
        if realism:
            lines.append(
                f"   - ATR realism: {realism.get('rating', 'unknown')} "
                f"(stop {format_number(realism.get('stop_atr'))} ATR, "
                f"target {format_number(realism.get('target_atr'))} ATR) - "
                f"{realism.get('message', 'n/a')}"
            )
        if idea.get("condition"):
            lines.append(f"   - Condition: {idea['condition']}")
        if idea.get("target_2") or idea.get("target_3"):
            lines.append(
                f"   - Extra targets: target 2 {format_number(idea.get('target_2'))}, "
                f"target 3 {format_number(idea.get('target_3'))}"
            )
        vehicle_options = [
            option for option in idea.get("trade_vehicle_options", [])
            if option.get("status") in {"conditional", "preferred_for_broad_theme", "consider", "not_execution_ready"}
        ]
        if vehicle_options:
            vehicle_text = "; ".join(
                f"{option.get('vehicle')} ({option.get('status')})"
                for option in vehicle_options[:3]
            )
            lines.append(f"   - Other trade options: {vehicle_text}")
        bearish_options = [
            option for option in idea.get("bearish_trade_options", [])
            if option.get("status") in {"preferred_risk_control", "watch_only", "watch_only_high_premium_risk", "watch_only_high_risk"}
        ]
        if bearish_options:
            bearish_text = "; ".join(
                f"{option.get('vehicle')} ({option.get('status')})"
                for option in bearish_options[:3]
            )
            lines.append(f"   - Bearish/fade options: {bearish_text}")
        lines.append(
            f"   - Backtest: expectancy {format_number(idea['backtest_expectancy'])}%, "
            f"sample {idea['backtest_sample_size'] if idea['backtest_sample_size'] is not None else 'n/a'}"
        )
        if idea.get("news_stance"):
            lines.append(
                f"   - News: {idea['news_stance']} "
                f"(score {format_number(idea.get('news_catalyst_score'))})"
            )
        if idea.get("news_top_headline"):
            lines.append(f"   - Top headline: {idea['news_top_headline']}")
        if show_guardrail and idea["decision"] != "PAPER TRADE ONLY":
            lines.append("   - Guardrail: review only; not approved as a simulated trade.")


def format_idea_label(idea):
    if idea["display_symbol"] == idea["symbol"]:
        return idea["symbol"]
    return f"{idea['display_symbol']} ({idea['symbol']})"


def get_display_symbol(summary):
    watchlist = summary.get("watchlist") or {}
    display = watchlist.get("display_symbol", summary.get("symbol"))
    symbol = summary.get("symbol")
    if display == symbol:
        return symbol
    return f"{display} ({symbol})"


def save_morning_brief(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    markdown = format_morning_brief(report)
    timestamp = safe_timestamp(report.get("created_at") or datetime.now().isoformat())

    latest_md_path = REPORTS_DIR / "daily_morning_brief.md"
    latest_json_path = REPORTS_DIR / "daily_morning_brief.json"
    archive_md_path = REPORTS_DIR / f"morning_brief_{timestamp}.md"
    archive_json_path = REPORTS_DIR / f"morning_brief_{timestamp}.json"

    latest_md_path.write_text(markdown, encoding="utf-8")
    latest_json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    archive_md_path.write_text(markdown, encoding="utf-8")
    archive_json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    return latest_md_path


def safe_timestamp(value):
    return (
        str(value)
        .replace(":", "")
        .replace("-", "")
        .replace(".", "")
        .replace("T", "_")
        .replace(" ", "_")
    )


def count_decisions(summaries, status):
    return sum(
        1
        for summary in summaries
        if (summary.get("final_decision") or {}).get("status") == status
    )


def format_number(value):
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def format_money(value):
    if value is None:
        return "n/a"
    return f"${value:.2f}"


def format_pct(value):
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.1f}%"


def format_core_allocations(allocations):
    if not allocations:
        return "n/a"
    return ", ".join(
        f"{item['symbol']} {format_pct(item['target_weight'])}"
        f" (~{item.get('suggested_shares', 0)} sh)"
        for item in allocations
    )
