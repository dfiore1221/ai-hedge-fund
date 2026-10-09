import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter
from zoneinfo import ZoneInfo

from agents.morning_brief import (
    load_watchlist_entries,
    route_strategy,
    run_committee_scan,
    score_candidate,
    summarize_idea,
)
from agents.news_intelligence import collect_overnight_news
from data.benzinga_data import fetch_benzinga_market_news
from data.tiingo_data import fetch_latest_equity_prices
from delivery.email_delivery import send_email
from delivery.email_retry import queue_email
from memory.research_memory import save_agent_report


PROJECT_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = PROJECT_ROOT / "framework" / "intraday_discovery_policy.json"
MORNING_BRIEF_PATH = PROJECT_ROOT / "reports" / "morning_brief" / "daily_morning_brief.json"
REPORTS_DIR = PROJECT_ROOT / "reports" / "intraday_discovery"
LATEST_REPORT_PATH = REPORTS_DIR / "intraday_discovery.json"
STATE_PATH = REPORTS_DIR / "state.json"
EASTERN = ZoneInfo("America/New_York")


def run_intraday_discovery(
    review_material=True,
    send_alert=True,
    dry_run=False,
    now=None,
    save_memory=True,
):
    started = perf_counter()
    now = normalize_now(now)
    policy = load_policy()
    brief = load_latest_brief()
    configured_entries = load_watchlist_entries()
    entries = list(configured_entries)
    symbols = [entry["symbol"] for entry in entries]
    state = load_state(now)

    report = {
        "agent": "Intraday Opportunity Engine",
        "system_role": "market_surveillance_layer",
        "mode": "autonomous_paper_only",
        "created_at": now.isoformat(timespec="seconds"),
        "policy_version": policy.get("version"),
        "status": "ok",
        "market_session": market_session(now),
        "configured_universe_size": len(configured_entries),
        "dynamic_symbols": [],
        "universe_size": len(symbols),
        "market_news_discovery": {"status": "not_run", "story_count": 0, "symbol_count": 0},
        "quote_provider": "Tiingo",
        "quote_status": "not_run",
        "fresh_quote_count": 0,
        "fresh_quote_coverage_pct": 0.0,
        "ranked_opportunities": [],
        "material_events": [],
        "committee_reviews": [],
        "qualified_candidates": [],
        "errors": [],
        "notification": None,
        "guardrails": policy.get("immutable_guardrails", []),
    }

    if not policy.get("enabled", True):
        report["status"] = "disabled"
        return finalize_report(report, state, save_memory)
    if report["market_session"] != "regular":
        report["status"] = "market_closed"
        return finalize_report(report, state, save_memory)
    if not brief or parse_datetime(brief.get("created_at")).date() != now.date():
        report["status"] = "blocked"
        report["errors"].append("A current-day morning brief is required as the technical baseline.")
        return finalize_report(report, state, save_memory)

    market_news = fetch_market_news_safely()
    dynamic_entries = discover_market_news_symbols(
        market_news,
        existing_symbols={entry["symbol"] for entry in configured_entries},
        limit=int(policy.get("max_dynamic_news_symbols", 20)),
    )
    entries.extend(dynamic_entries)
    symbols = [entry["symbol"] for entry in entries]
    report["dynamic_symbols"] = [entry["symbol"] for entry in dynamic_entries]
    report["universe_size"] = len(symbols)
    report["market_news_discovery"] = {
        "status": market_news.get("status", "error"),
        "story_count": len(market_news.get("items") or []),
        "symbol_count": len(dynamic_entries),
        "error": market_news.get("error"),
    }

    quote_result = fetch_latest_equity_prices(symbols, individual_fallback_limit=8)
    quote_map = quote_result.get("prices") or {}
    report["quote_status"] = quote_result.get("status")
    report["quote_request_mode"] = quote_result.get("request_mode")
    report["quote_errors"] = quote_result.get("errors") or {}

    baselines = build_baselines(brief, entries)
    for entry in entries:
        if entry["symbol"] in baselines:
            continue
        baselines[entry["symbol"]] = {
            "symbol": entry["symbol"],
            "category": entry.get("category", "Uncategorized"),
            "display_symbol": entry.get("display_symbol", entry["symbol"]),
            "universe_role": entry.get("role", "configured_watchlist"),
            "morning_score": 50.0 if entry.get("role") == "dynamic_discovery" else 0.0,
            "morning_decision": "INTRADAY DISCOVERY" if entry.get("role") == "dynamic_discovery" else None,
            "technical_stance": None,
            "news_discovery_score": safe_float(entry.get("news_discovery_score")) or 0.0,
            "discovery_headlines": entry.get("discovery_headlines", []),
        }
    rows = []
    for symbol in symbols:
        quote = quote_map.get(symbol) or {}
        baseline = baselines.get(symbol) or {}
        row = build_discovery_row(symbol, quote, baseline, now, policy)
        if row:
            rows.append(row)

    configured_symbols = {entry["symbol"] for entry in configured_entries}
    configured_rows = [row for row in rows if row["symbol"] in configured_symbols]
    configured_fresh_rows = [row for row in configured_rows if row.get("quote_is_fresh")]
    fresh_rows = [row for row in rows if row.get("quote_is_fresh")]
    report["fresh_quote_count"] = len(fresh_rows)
    report["fresh_quote_coverage_pct"] = round(
        100 * len(configured_fresh_rows) / max(1, len(configured_entries)), 1
    )
    report["dynamic_fresh_quote_count"] = sum(
        row.get("quote_is_fresh") and row["symbol"] not in configured_symbols for row in rows
    )
    if report["fresh_quote_coverage_pct"] < float(policy.get("minimum_fresh_quote_coverage_pct", 75)):
        report["status"] = "degraded"
        report["errors"].append("Fresh quote coverage is below the discovery policy minimum.")

    ranked = sorted(rows, key=lambda item: item.get("discovery_score", 0), reverse=True)
    report["ranked_opportunities"] = ranked[: int(policy.get("top_ranked_count", 15))]
    material = [row for row in ranked if row.get("material") and row.get("quote_is_fresh")]

    news_limit = int(policy.get("max_news_checks_per_cycle", 4))
    for row in material[:news_limit]:
        try:
            news = collect_overnight_news(row["symbol"], limit=6, include_recommendation_trends=False)
        except Exception as exc:
            report["errors"].append(f"{row['symbol']} news check failed: {exc}")
            continue
        row["news"] = compact_news(news)
        news_score = float((news.get("summary") or {}).get("total_score") or 0)
        if abs(news_score) >= float(policy.get("news_score_threshold", 4)):
            add_event(row, "material_news")
            row["discovery_score"] = round(row["discovery_score"] + min(12, abs(news_score)), 2)

    material = sorted(material, key=lambda item: item.get("discovery_score", 0), reverse=True)
    report["material_events"] = material

    if review_material and report["status"] != "degraded":
        review_limit = int(policy.get("max_committee_reviews_per_cycle", 2))
        for row in material:
            if len(report["committee_reviews"]) >= review_limit:
                break
            if not review_is_due(row, state, now, policy):
                continue
            if not deep_review_is_warranted(row, policy):
                continue
            review = review_with_committee(row, brief, now)
            report["committee_reviews"].append(review)
            state.setdefault("last_reviews", {})[row["symbol"]] = {
                "reviewed_at": now.isoformat(timespec="seconds"),
                "event_signature": event_signature(row),
            }
            idea = review.get("qualified_candidate")
            if idea:
                report["qualified_candidates"].append(idea)

    state["date"] = now.date().isoformat()
    state["last_scan_at"] = now.isoformat(timespec="seconds")
    state["last_quotes"] = {
        row["symbol"]: {
            "price": row.get("latest_price"),
            "change_pct": row.get("change_pct"),
            "events": row.get("events", []),
        }
        for row in rows
    }

    if send_alert and should_notify(report, policy):
        report["notification"] = send_discovery_alert(report, dry_run=dry_run)

    report["elapsed_seconds"] = round(perf_counter() - started, 2)
    return finalize_report(report, state, save_memory)


def build_baselines(brief, entries):
    metadata = {entry["symbol"]: entry for entry in entries}
    baselines = {}
    for summary in brief.get("committee_summaries", []) or []:
        symbol = str(summary.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        technical = ((summary.get("source_reports") or {}).get("technical") or {})
        setup = technical.get("setup") or {}
        momentum = technical.get("momentum") or {}
        try:
            morning_score = round(float(score_candidate(summary)), 2)
        except (TypeError, ValueError):
            morning_score = 0.0
        baselines[symbol] = {
            "symbol": symbol,
            "category": (metadata.get(symbol) or {}).get("category", "Uncategorized"),
            "display_symbol": (metadata.get(symbol) or {}).get("display_symbol", symbol),
            "universe_role": (metadata.get(symbol) or {}).get("role", "configured_watchlist"),
            "morning_score": morning_score,
            "morning_decision": (summary.get("final_decision") or {}).get("status"),
            "technical_stance": summary.get("technical_stance"),
            "reference_close": safe_float(technical.get("latest_close")),
            "atr_14": safe_float(momentum.get("atr_14")),
            "average_volume_20d": safe_float(momentum.get("average_volume_20d")),
            "entry_trigger": safe_float(setup.get("entry_trigger")),
            "pullback_entry": safe_float(setup.get("alternative_entry")),
            "stop": safe_float(setup.get("stop")),
            "target_1": safe_float(setup.get("target_1")),
        }
    return baselines


def build_discovery_row(symbol, quote, baseline, now, policy):
    latest = safe_float(quote.get("close"))
    if latest is None or latest <= 0:
        return None
    reference = safe_float(quote.get("prev_close")) or safe_float(baseline.get("reference_close"))
    change_pct = percent_change(latest, reference)
    atr = safe_float(baseline.get("atr_14"))
    atr_move = (latest - reference) / atr if reference and atr else None
    volume = safe_float(quote.get("volume"))
    average_volume = safe_float(baseline.get("average_volume_20d"))
    elapsed = market_elapsed_fraction(now)
    expected_volume = average_volume * elapsed if average_volume else None
    volume_pace = volume / expected_volume if volume is not None and expected_volume else None
    events = []
    if baseline.get("universe_role") == "dynamic_discovery":
        events.append("market_news_discovery")

    if change_pct is not None and abs(change_pct) >= float(policy.get("material_move_pct", 1.5)):
        events.append("material_price_move")
    if atr_move is not None and abs(atr_move) >= float(policy.get("material_atr_move", 0.75)):
        events.append("material_atr_move")
    if volume_pace is not None and volume_pace >= float(policy.get("volume_pace_ratio", 1.8)):
        events.append("volume_acceleration")

    entry = safe_float(baseline.get("entry_trigger"))
    pullback = safe_float(baseline.get("pullback_entry"))
    stop = safe_float(baseline.get("stop"))
    stance = str(baseline.get("technical_stance") or "").lower()
    if stance == "bullish" and entry and reference and reference < entry <= latest:
        events.append("breakout_trigger_crossed")
    if stance == "bullish" and pullback and stop and stop < latest <= pullback:
        events.append("pullback_zone_reached")
    if stop and latest <= stop:
        events.append("setup_invalidated")

    event_bonus = sum({
        "material_price_move": 5,
        "material_atr_move": 6,
        "volume_acceleration": 5,
        "breakout_trigger_crossed": 18,
        "pullback_zone_reached": 16,
        "setup_invalidated": -15,
        "market_news_discovery": 8,
    }.get(event, 0) for event in events)
    directional_bonus = 0
    if change_pct is not None and stance == "bullish":
        directional_bonus = max(-8, min(8, change_pct * 2))
    magnitude_bonus = min(20, abs(change_pct) * 2) if change_pct is not None else 0
    news_discovery_bonus = min(15, safe_float(baseline.get("news_discovery_score")) or 0)

    return {
        "symbol": symbol,
        "display_symbol": baseline.get("display_symbol", symbol),
        "category": baseline.get("category", "Uncategorized"),
        "universe_role": baseline.get("universe_role", "configured_watchlist"),
        "discovery_headlines": baseline.get("discovery_headlines", []),
        "latest_price": round(latest, 4),
        "quote_timestamp": quote.get("timestamp"),
        "quote_freshness": quote.get("freshness"),
        "quote_is_fresh": quote.get("freshness") == "intraday_or_latest",
        "reference_close": round_or_none(reference),
        "change_pct": round_or_none(change_pct),
        "atr_move": round_or_none(atr_move),
        "volume": round_or_none(volume),
        "volume_pace_ratio": round_or_none(volume_pace),
        "technical_stance": baseline.get("technical_stance"),
        "morning_decision": baseline.get("morning_decision"),
        "morning_score": baseline.get("morning_score", 0),
        "entry_trigger": round_or_none(entry),
        "pullback_entry": round_or_none(pullback),
        "stop": round_or_none(stop),
        "target_1": round_or_none(baseline.get("target_1")),
        "events": events,
        "material": bool(events),
        "discovery_score": round(
            float(baseline.get("morning_score") or 0)
            + event_bonus
            + directional_bonus
            + magnitude_bonus
            + news_discovery_bonus,
            2,
        ),
    }


def fetch_market_news_safely():
    try:
        return fetch_benzinga_market_news(limit=50)
    except Exception as exc:
        return {
            "provider": "Benzinga",
            "status": "error",
            "items": [],
            "error": str(exc),
        }


def discover_market_news_symbols(news_report, existing_symbols, limit=20):
    """Rank U.S.-listed symbols tagged in fresh market-wide Benzinga stories."""
    existing = {str(symbol).upper() for symbol in existing_symbols}
    allowed_exchanges = {"NASDAQ", "NYSE", "AMEX", "ARCA", "NYSEARCA"}
    candidates = {}

    for position, item in enumerate(news_report.get("items") or []):
        if not isinstance(item, dict):
            continue
        importance = safe_float(item.get("importance_rank")) or safe_float(item.get("importance")) or 0
        story_weight = max(1.0, 5.0 - position / 10) + max(0.0, importance)
        headline = str(item.get("title") or "").strip()
        for stock in item.get("stocks") or []:
            if isinstance(stock, str):
                symbol = stock.upper().strip()
                exchange = ""
            elif isinstance(stock, dict):
                symbol = str(stock.get("name") or stock.get("ticker") or "").upper().strip()
                exchange = str(stock.get("exchange") or "").upper().strip()
            else:
                continue
            if (
                not symbol
                or symbol.startswith("$")
                or symbol in existing
                or (exchange and exchange not in allowed_exchanges)
                or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,7}", symbol)
            ):
                continue
            candidate = candidates.setdefault(
                symbol,
                {"score": 0.0, "story_count": 0, "headlines": [], "exchange": exchange},
            )
            candidate["score"] += story_weight
            candidate["story_count"] += 1
            if headline and headline not in candidate["headlines"]:
                candidate["headlines"].append(headline)

    ranked = sorted(
        candidates.items(),
        key=lambda item: (item[1]["score"], item[1]["story_count"], item[0]),
        reverse=True,
    )[:max(0, limit)]
    return [
        {
            "symbol": symbol,
            "display_symbol": symbol,
            "category": "Breaking News Discovery",
            "role": "dynamic_discovery",
            "news_discovery_score": round(details["score"], 2),
            "discovery_story_count": details["story_count"],
            "discovery_headlines": details["headlines"][:3],
        }
        for symbol, details in ranked
    ]


def review_with_committee(row, brief, now):
    symbol = row["symbol"]
    try:
        summary = run_committee_scan(symbol, brief.get("macro") or {})
        summary["run_id"] = f"{now.date().isoformat()}-{symbol}-intraday-{now:%H%M}"
        summary["watchlist"] = {
            "symbol": symbol,
            "display_symbol": row.get("display_symbol", symbol),
            "category": row.get("category", "Uncategorized"),
        }
        summary["intraday_discovery"] = {
            key: row.get(key)
            for key in (
                "latest_price", "reference_close", "change_pct", "atr_move",
                "volume_pace_ratio", "events", "discovery_score",
            )
        }
        feedback = {"setup_review_learning": brief.get("strategy_learning") or {}}
        summary["strategy_plan"] = route_strategy(
            summary,
            data_health=brief.get("data_health") or {},
            feedback=feedback,
        )
        idea = summarize_idea(summary)
        qualified = (
            idea.get("decision") in {"PAPER TRADE ONLY", "CONDITIONAL SETUP"}
            and idea.get("technical_stance") in {"bullish", "bearish"}
        )
        return {
            "symbol": symbol,
            "status": "qualified" if qualified else "reviewed_no_trade",
            "events": row.get("events", []),
            "latest_price": row.get("latest_price"),
            "decision": idea.get("decision"),
            "score": idea.get("score"),
            "reason": idea.get("reason"),
            "qualified_candidate": idea if qualified else None,
        }
    except Exception as exc:
        return {
            "symbol": symbol,
            "status": "error",
            "events": row.get("events", []),
            "error": str(exc),
            "qualified_candidate": None,
        }


def review_is_due(row, state, now, policy):
    prior = (state.get("last_reviews") or {}).get(row["symbol"]) or {}
    reviewed_at = parse_datetime(prior.get("reviewed_at"))
    same_event = prior.get("event_signature") == event_signature(row)
    critical = bool({"breakout_trigger_crossed", "pullback_zone_reached"} & set(row.get("events", [])))
    if not reviewed_at:
        return True
    cooldown = timedelta(minutes=int(policy.get("committee_review_cooldown_minutes", 60)))
    return critical and not same_event or now - reviewed_at >= cooldown


def deep_review_is_warranted(row, policy):
    events = set(row.get("events", []))
    critical = {"breakout_trigger_crossed", "pullback_zone_reached", "material_news"}
    if events & critical:
        return True
    return float(row.get("discovery_score") or 0) >= float(
        policy.get("minimum_deep_review_score", 82)
    )


def event_signature(row):
    return "|".join(sorted(row.get("events", [])))


def add_event(row, event):
    if event not in row["events"]:
        row["events"].append(event)
    row["material"] = True


def compact_news(report):
    summary = report.get("summary") or {}
    return {
        "stance": report.get("stance"),
        "score": summary.get("total_score"),
        "fresh_items": summary.get("fresh_item_count"),
        "top_headline": summary.get("top_headline"),
        "providers": report.get("providers", []),
    }


def should_notify(report, policy):
    if report.get("qualified_candidates") and policy.get("notify_on_qualified_candidate", True):
        return True
    critical_events = {"breakout_trigger_crossed", "pullback_zone_reached", "material_news"}
    return policy.get("notify_on_critical_event", True) and any(
        critical_events & set(row.get("events", []))
        for row in report.get("material_events", [])
    )


def send_discovery_alert(report, dry_run=False):
    qualified = report.get("qualified_candidates", [])
    material = report.get("material_events", [])
    subject = f"AIFundOS intraday discovery: {len(qualified)} qualified, {len(material)} material"
    lines = [
        "AIFundOS scanned its configured and news-discovered universe and detected a material intraday change.",
        "",
        "Qualified Committee candidates:",
    ]
    if qualified:
        for idea in qualified:
            lines.append(
                f"- {idea.get('symbol')}: {idea.get('decision')} | score {safe_float(idea.get('score')) or 0:.1f} | "
                f"{idea.get('reason') or 'No reason supplied.'}"
            )
    else:
        lines.append("- None. Material events were detected, but no new setup passed Committee review.")
    lines.extend(["", "Top material events:"])
    for row in material[:5]:
        lines.append(
            f"- {row['symbol']}: {', '.join(row.get('events', []))}; "
            f"price {row.get('latest_price')}; change {row.get('change_pct')}%"
        )
    lines.extend(["", "Paper trading only. Existing risk and execution gates still apply."])
    body = "\n".join(lines)
    if dry_run:
        return {"dry_run": True, "subject": subject, "body_preview": body}
    try:
        return send_email(subject, body)
    except Exception as exc:
        pending = queue_email(
            subject,
            body,
            attachment_path=None,
            kind="intraday_discovery",
            error=exc,
            expiry_hours=4,
            dedupe_key=f"{report.get('created_at', '')[:10]}|{subject}",
        )
        return {"sent": False, "queued": True, "pending_path": str(pending), "error": str(exc)}


def format_intraday_discovery_report(report):
    lines = [
        "# Intraday Opportunity Engine",
        "",
        f"Created At: {report['created_at']}",
        f"Status: {report['status']}",
        f"Market Session: {report['market_session']}",
        f"Universe: {report['universe_size']} symbols",
        f"Configured Universe: {report.get('configured_universe_size', report['universe_size'])} symbols",
        f"News-Discovered Symbols: {len(report.get('dynamic_symbols', []))}",
        f"Fresh Quotes: {report['fresh_quote_count']} ({report['fresh_quote_coverage_pct']:.1f}%)",
        f"Material Events: {len(report.get('material_events', []))}",
        f"Committee Reviews: {len(report.get('committee_reviews', []))}",
        f"Qualified Candidates: {len(report.get('qualified_candidates', []))}",
        "",
        "## Ranked Opportunities",
    ]
    for row in report.get("ranked_opportunities", [])[:10]:
        lines.append(
            f"- {row['symbol']}: score {row['discovery_score']:.1f}, price {row['latest_price']:.2f}, "
            f"change {format_optional(row.get('change_pct'), '%')}, events {', '.join(row.get('events', [])) or 'none'}"
        )
    if not report.get("ranked_opportunities"):
        lines.append("- None.")
    lines.extend(["", "## Committee Reviews"])
    for item in report.get("committee_reviews", []):
        lines.append(
            f"- {item['symbol']}: {item['status']} | {item.get('decision') or item.get('error') or 'no decision'}"
        )
    if not report.get("committee_reviews"):
        lines.append("- No event-driven review was due.")
    lines.extend(["", "## Errors"])
    lines.extend([f"- {error}" for error in report.get("errors", [])] or ["- None."])
    return "\n".join(lines) + "\n"


def finalize_report(report, state, save_memory):
    path = save_report(report)
    save_state(state)
    report["report_path"] = str(path)
    if save_memory:
        save_agent_report(
            run_id=f"{report['created_at'][:10]}-intraday-discovery-{report['created_at'][11:16].replace(':', '')}",
            agent_name="Intraday Opportunity Engine",
            output={
                "status": report.get("status"),
                "coverage_pct": report.get("fresh_quote_coverage_pct"),
                "material_events": report.get("material_events", [])[:10],
                "committee_reviews": report.get("committee_reviews", []),
                "qualified_candidates": report.get("qualified_candidates", []),
            },
            symbol="MARKET",
            stance="opportunity" if report.get("qualified_candidates") else "surveillance",
            confidence=report.get("fresh_quote_coverage_pct", 0),
        )
    return report


def save_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(EASTERN).strftime("%Y%m%d_%H%M%S")
    archive_json = REPORTS_DIR / f"intraday_discovery_{timestamp}.json"
    archive_md = REPORTS_DIR / f"intraday_discovery_{timestamp}.md"
    latest_md = REPORTS_DIR / "intraday_discovery.md"
    payload = json.dumps(report, indent=2, default=str)
    markdown = format_intraday_discovery_report(report)
    LATEST_REPORT_PATH.write_text(payload, encoding="utf-8")
    latest_md.write_text(markdown, encoding="utf-8")
    archive_json.write_text(payload, encoding="utf-8")
    archive_md.write_text(markdown, encoding="utf-8")
    return latest_md


def load_latest_intraday_candidates(now=None):
    now = normalize_now(now)
    if not LATEST_REPORT_PATH.exists():
        return []
    try:
        report = json.loads(LATEST_REPORT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    created_at = parse_datetime(report.get("created_at"))
    if not created_at or created_at.date() != now.date():
        return []
    if report.get("status") not in {"ok"}:
        return []
    return report.get("qualified_candidates", []) or []


def load_latest_brief():
    if not MORNING_BRIEF_PATH.exists():
        return None
    try:
        return json.loads(MORNING_BRIEF_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_policy():
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def load_state(now):
    if not STATE_PATH.exists():
        return {"date": now.date().isoformat(), "last_reviews": {}, "last_quotes": {}}
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    if state.get("date") != now.date().isoformat():
        return {"date": now.date().isoformat(), "last_reviews": {}, "last_quotes": {}}
    state.setdefault("last_reviews", {})
    state.setdefault("last_quotes", {})
    return state


def save_state(state):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def market_session(now):
    if now.weekday() >= 5:
        return "closed"
    minutes = now.hour * 60 + now.minute
    return "regular" if 570 <= minutes <= 960 else "closed"


def market_elapsed_fraction(now):
    minutes = now.hour * 60 + now.minute
    elapsed = max(0, min(390, minutes - 570))
    return max(0.08, elapsed / 390)


def normalize_now(now):
    if now is None:
        return datetime.now(EASTERN)
    if now.tzinfo is None:
        return now.replace(tzinfo=EASTERN)
    return now.astimezone(EASTERN)


def parse_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=EASTERN)
    return parsed.astimezone(EASTERN)


def percent_change(latest, reference):
    if latest is None or reference in {None, 0}:
        return None
    return (latest / reference - 1) * 100


def safe_float(value):
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def round_or_none(value, digits=4):
    number = safe_float(value)
    return round(number, digits) if number is not None else None


def format_optional(value, suffix=""):
    number = safe_float(value)
    return f"{number:.2f}{suffix}" if number is not None else "n/a"
