import re
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf

from data.finnhub_data import (
    fetch_company_news,
    fetch_recommendation_trends,
    is_finnhub_configured,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports" / "news"
FRESH_NEWS_HOURS = 36
ANALYST_ACTION_LOOKBACK_DAYS = 180

POSITIVE_KEYWORDS = {
    "beat",
    "beats",
    "boost",
    "buy",
    "upgrade",
    "upgraded",
    "raise",
    "raised",
    "record",
    "surge",
    "partnership",
    "contract",
    "approval",
    "expands",
    "growth",
    "outperform",
    "overweight",
}
NEGATIVE_KEYWORDS = {
    "miss",
    "misses",
    "downgrade",
    "downgraded",
    "cut",
    "cuts",
    "lawsuit",
    "probe",
    "investigation",
    "recall",
    "warning",
    "weak",
    "slump",
    "falls",
    "underperform",
    "underweight",
}
CATALYST_KEYWORDS = {
    "earnings": ["earnings", "revenue", "guidance", "profit", "margin", "eps"],
    "analyst_action": ["upgrade", "downgrade", "price target", "initiates", "coverage", "rating"],
    "deal_contract": ["contract", "deal", "partnership", "agreement", "order"],
    "regulatory_legal": ["regulator", "antitrust", "lawsuit", "probe", "investigation", "approval"],
    "product_ai": ["ai", "artificial intelligence", "chip", "cloud", "data center", "software"],
    "capital_markets": ["buyback", "dividend", "offering", "debt", "acquisition", "merger"],
}
SYMBOL_ALIASES = {
    "AAOI": ["applied optoelectronics"],
    "ABBV": ["abbvie"],
    "AMD": ["advanced micro devices"],
    "AMAT": ["applied materials"],
    "AVGO": ["broadcom"],
    "BA": ["boeing"],
    "BWXT": ["bwx technologies"],
    "CAT": ["caterpillar"],
    "CEG": ["constellation energy"],
    "COIN": ["coinbase"],
    "COHR": ["coherent"],
    "COP": ["conocophillips"],
    "CVX": ["chevron"],
    "DE": ["deere"],
    "EOG": ["eog resources"],
    "ETN": ["eaton"],
    "FCX": ["freeport-mcmoran", "freeport mcmoran"],
    "IONQ": ["ionq"],
    "IPGP": ["ipg photonics"],
    "ISRG": ["intuitive surgical"],
    "LITE": ["lumentum"],
    "LLY": ["eli lilly", "lilly"],
    "LMT": ["lockheed martin"],
    "MARA": ["marathon digital"],
    "MRK": ["merck"],
    "MSFT": ["microsoft"],
    "MSTR": ["microstrategy", "strategy"],
    "NEM": ["newmont"],
    "NEE": ["nextera energy"],
    "NOC": ["northrop grumman"],
    "NVDA": ["nvidia"],
    "OKLO": ["oklo"],
    "PLTR": ["palantir"],
    "QBTS": ["d-wave", "d wave"],
    "QUBT": ["quantum computing"],
    "RGTI": ["rigetti"],
    "RIOT": ["riot platforms"],
    "RKLB": ["rocket lab"],
    "SCCO": ["southern copper"],
    "SMR": ["nuscale"],
    "SPCE": ["virgin galactic"],
    "TER": ["teradyne"],
    "TSM": ["taiwan semiconductor", "tsmc"],
    "VALE": ["vale"],
    "XOM": ["exxon mobil", "exxonmobil"],
}


def collect_overnight_news(ticker, limit=10):
    ticker = ticker.upper().strip()
    finnhub_configured = is_finnhub_configured()
    provider_status = []

    try:
        yf_ticker = yf.Ticker(ticker)
        aliases = build_symbol_aliases(ticker, yf_ticker)
        news_items = yf_ticker.news or []
    except Exception as exc:
        return {
            "agent": "Overnight News Analyst",
            "symbol": ticker,
            "error": f"Could not fetch news: {exc}",
            "items": [],
            "analyst_actions": [],
            "summary": {},
            "providers": [{"name": "Yahoo Finance", "status": "error", "detail": str(exc)}],
            "missing_information": [
                "Starter Yahoo news feed failed.",
            ],
        }

    items = []
    finnhub_news = {"status": "not_configured", "items": []}
    if finnhub_configured:
        finnhub_news = fetch_company_news(ticker, limit=limit)
        provider_status.append({
            "name": "Finnhub",
            "status": finnhub_news.get("status"),
            "detail": build_provider_detail(finnhub_news, "company news"),
        })
        for item in finnhub_news.get("items", []):
            items.append(normalize_finnhub_news_item(item, aliases))
    else:
        provider_status.append({
            "name": "Finnhub",
            "status": "not_configured",
            "detail": "FINNHUB_API_KEY is not configured.",
        })

    provider_status.append({
        "name": "Yahoo Finance",
        "status": "ok" if news_items else "empty",
        "detail": f"{len(news_items)} starter headlines returned.",
    })
    for item in news_items[:limit]:
        items.append(normalize_news_item(item, aliases))

    items = dedupe_news_items(items)[:limit]
    analyst_actions = collect_analyst_actions(yf_ticker)
    finnhub_recommendations = {"status": "not_configured", "items": []}
    if finnhub_configured:
        finnhub_recommendations = fetch_recommendation_trends(ticker)
        provider_status.append({
            "name": "Finnhub Recommendations",
            "status": finnhub_recommendations.get("status"),
            "detail": build_provider_detail(finnhub_recommendations, "recommendation trends"),
        })
        analyst_actions.extend(normalize_finnhub_recommendations(finnhub_recommendations.get("items", [])))

    summary = summarize_news(items, analyst_actions)

    return {
        "agent": "Overnight News Analyst",
        "symbol": ticker,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "items": items,
        "analyst_actions": analyst_actions,
        "summary": summary,
        "stance": summary["stance"],
        "confidence": summary["confidence"],
        "providers": provider_status,
        "missing_information": build_missing_information(
            finnhub_configured,
            finnhub_news,
            finnhub_recommendations,
        ),
    }


def normalize_news_item(item, aliases):
    content = item.get("content", item)
    provider = content.get("provider")
    canonical_url = content.get("canonicalUrl")
    title = content.get("title") or item.get("title")
    publisher = provider.get("displayName") if isinstance(provider, dict) else item.get("publisher")
    url = canonical_url.get("url") if isinstance(canonical_url, dict) else item.get("link")
    published = parse_published_at(content.get("pubDate") or item.get("providerPublishTime"))
    text = title or ""
    symbol_relevant = is_symbol_relevant(text, aliases)
    sentiment = score_text_sentiment(text)
    tags = tag_catalysts(text)
    age_hours = hours_since(published)

    return {
        "title": title,
        "publisher": publisher,
        "url": url,
        "published": published.isoformat() if published else None,
        "age_hours": age_hours,
        "is_fresh": age_hours is not None and age_hours <= FRESH_NEWS_HOURS,
        "sentiment_score": sentiment,
        "sentiment_label": sentiment_label(sentiment),
        "catalyst_tags": tags,
        "symbol_relevant": symbol_relevant,
        "relevance_score": score_relevance(tags, age_hours, sentiment, symbol_relevant),
        "provider": "Yahoo Finance",
    }


def normalize_finnhub_news_item(item, aliases):
    title = item.get("headline")
    summary = item.get("summary")
    text = " ".join([value for value in [title, summary] if value])
    published = parse_published_at(item.get("datetime"))
    age_hours = hours_since(published)
    symbol_relevant = is_symbol_relevant(text, aliases)
    sentiment = score_text_sentiment(text)
    tags = tag_catalysts(text)

    return {
        "title": title,
        "publisher": item.get("source") or "Finnhub",
        "url": item.get("url"),
        "published": published.isoformat() if published else None,
        "age_hours": age_hours,
        "is_fresh": age_hours is not None and age_hours <= FRESH_NEWS_HOURS,
        "sentiment_score": sentiment,
        "sentiment_label": sentiment_label(sentiment),
        "catalyst_tags": tags,
        "symbol_relevant": symbol_relevant,
        "relevance_score": score_relevance(tags, age_hours, sentiment, symbol_relevant),
        "provider": "Finnhub",
    }


def collect_analyst_actions(yf_ticker, limit=8):
    try:
        actions = yf_ticker.upgrades_downgrades
    except Exception:
        return []

    if actions is None or actions.empty:
        return []

    records = []
    for index, row in actions.head(limit).iterrows():
        event_date = index.date() if hasattr(index, "date") else None
        if event_date and (datetime.now().date() - event_date).days > ANALYST_ACTION_LOOKBACK_DAYS:
            continue
        firm = safe_row_get(row, "Firm")
        to_grade = safe_row_get(row, "ToGrade")
        from_grade = safe_row_get(row, "FromGrade")
        action = safe_row_get(row, "Action")
        records.append({
            "date": event_date.isoformat() if event_date else str(index),
            "firm": firm,
            "action": normalize_analyst_action(action),
            "from_grade": from_grade,
            "to_grade": to_grade,
            "sentiment_score": score_analyst_action(action, from_grade, to_grade),
        })

    return records


def normalize_finnhub_recommendations(items):
    records = []
    for item in items:
        period = item.get("period")
        score = score_recommendation_trend(item)
        action = "positive consensus" if score > 0 else "negative consensus" if score < 0 else "neutral consensus"
        records.append({
            "date": period,
            "firm": "Finnhub consensus",
            "action": action,
            "from_grade": None,
            "to_grade": format_recommendation_mix(item),
            "sentiment_score": score,
            "provider": "Finnhub",
        })
    return records


def score_recommendation_trend(item):
    positive = int(item.get("strongBuy") or 0) * 2 + int(item.get("buy") or 0)
    negative = int(item.get("strongSell") or 0) * 2 + int(item.get("sell") or 0)
    hold = int(item.get("hold") or 0)
    total = positive + negative + hold
    if total == 0:
        return 0

    net = (positive - negative) / total
    if net >= 0.35:
        return 1
    if net <= -0.25:
        return -1
    return 0


def format_recommendation_mix(item):
    parts = [
        f"strong buy {item.get('strongBuy', 0)}",
        f"buy {item.get('buy', 0)}",
        f"hold {item.get('hold', 0)}",
        f"sell {item.get('sell', 0)}",
        f"strong sell {item.get('strongSell', 0)}",
    ]
    return ", ".join(parts)


def summarize_news(items, analyst_actions):
    fresh_items = [item for item in items if item.get("is_fresh")]
    relevant_items = [item for item in items if item.get("symbol_relevant")]
    positive_count = len([item for item in relevant_items if item.get("sentiment_score", 0) > 0])
    negative_count = len([item for item in relevant_items if item.get("sentiment_score", 0) < 0])
    catalyst_counts = {}

    for item in relevant_items:
        for tag in item.get("catalyst_tags", []):
            catalyst_counts[tag] = catalyst_counts.get(tag, 0) + 1

    news_score = sum(
        item.get("sentiment_score", 0) * item.get("relevance_score", 0)
        for item in relevant_items
    )
    analyst_score = sum(item.get("sentiment_score", 0) for item in analyst_actions[:5])
    total_score = news_score + analyst_score

    top_item = max(relevant_items, key=lambda item: item.get("relevance_score", 0), default=None)

    if total_score >= 6:
        stance = "positive_catalyst"
    elif total_score <= -4:
        stance = "negative_catalyst"
    elif fresh_items or analyst_actions:
        stance = "mixed_or_monitor"
    else:
        stance = "no_clear_catalyst"

    confidence = min(0.75, 0.25 + (len(fresh_items) * 0.06) + (len(analyst_actions) * 0.03))

    return {
        "item_count": len(items),
        "fresh_item_count": len(fresh_items),
        "relevant_item_count": len(relevant_items),
        "positive_item_count": positive_count,
        "negative_item_count": negative_count,
        "analyst_action_count": len(analyst_actions),
        "top_catalyst_tags": sorted(catalyst_counts, key=catalyst_counts.get, reverse=True)[:5],
        "catalyst_counts": catalyst_counts,
        "news_score": round(news_score, 2),
        "analyst_score": round(analyst_score, 2),
        "total_score": round(total_score, 2),
        "stance": stance,
        "confidence": round(confidence, 2),
        "top_headline": top_item.get("title") if top_item else None,
    }


def parse_published_at(value):
    if value in {None, ""}:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def hours_since(moment):
    if not moment:
        return None
    now = datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return round((now - moment).total_seconds() / 3600, 1)


def score_text_sentiment(text):
    lowered = text.lower()
    score = 0
    for keyword in POSITIVE_KEYWORDS:
        if keyword in lowered:
            score += 1
    for keyword in NEGATIVE_KEYWORDS:
        if keyword in lowered:
            score -= 1
    return max(-3, min(3, score))


def sentiment_label(score):
    if score > 0:
        return "positive"
    if score < 0:
        return "negative"
    return "neutral"


def tag_catalysts(text):
    lowered = text.lower()
    tags = []
    for tag, keywords in CATALYST_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            tags.append(tag)
    return tags


def score_relevance(tags, age_hours, sentiment, symbol_relevant):
    if not symbol_relevant:
        return 0

    score = 1
    score += min(3, len(tags))
    if age_hours is not None and age_hours <= FRESH_NEWS_HOURS:
        score += 2
    if sentiment != 0:
        score += 1
    return score


def build_symbol_aliases(ticker, yf_ticker):
    aliases = {ticker.lower()}
    for alias in SYMBOL_ALIASES.get(ticker.upper(), []):
        add_alias(aliases, alias)

    try:
        info = yf_ticker.get_info()
    except Exception:
        info = {}

    for key in ("shortName", "longName"):
        name = info.get(key)
        if not name:
            continue
        add_alias(aliases, name)
        words = [
            word.strip(".,:;()[]").lower()
            for word in name.split()
            if len(word.strip(".,:;()[]")) >= 4
        ]
        if words:
            add_alias(aliases, " ".join(words[:2]))
            add_alias(aliases, words[0])

    return {alias for alias in aliases if alias}


def add_alias(aliases, value):
    clean = value.lower().strip()
    if not clean:
        return
    aliases.add(clean)
    compact = clean.replace(" ", "")
    if compact != clean:
        aliases.add(compact)


def is_symbol_relevant(text, aliases):
    lowered = text.lower()
    for alias in aliases:
        if len(alias) <= 4:
            if re.search(rf"\b{re.escape(alias)}\b", lowered):
                return True
            continue
        if alias in lowered:
            return True
    return False


def score_analyst_action(action, from_grade, to_grade):
    action_text = str(action or "").lower()
    to_grade_text = str(to_grade or "").lower()

    if any(keyword in action_text for keyword in ["up", "upgrade"]):
        return 1
    if any(keyword in action_text for keyword in ["down", "downgrade"]):
        return -1
    if any(keyword in action_text for keyword in ["init", "new"]) and any(
        keyword in to_grade_text
        for keyword in ["buy", "outperform", "overweight"]
    ):
        return 1
    if any(keyword in action_text for keyword in ["init", "new"]) and any(
        keyword in to_grade_text
        for keyword in ["sell", "underperform", "underweight"]
    ):
        return -1
    return 0


def normalize_analyst_action(action):
    text = str(action or "").lower()
    if text == "main":
        return "maintained"
    if text:
        return text
    return None


def safe_row_get(row, key):
    value = row.get(key) if hasattr(row, "get") else None
    if value != value:
        return None
    return value


def dedupe_news_items(items):
    seen = set()
    deduped = []

    for item in items:
        title_key = normalize_dedupe_key(item.get("title"))
        url_key = normalize_dedupe_key(item.get("url"))
        keys = {key for key in [title_key, url_key] if key}
        if not keys or keys & seen:
            continue
        seen.update(keys)
        deduped.append(item)

    return sorted(
        deduped,
        key=lambda item: item.get("age_hours") if item.get("age_hours") is not None else 9999,
    )


def normalize_dedupe_key(value):
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value).strip().lower())


def build_provider_detail(response, label):
    if response.get("status") == "ok":
        return f"{response.get('item_count', len(response.get('items', [])))} {label} returned."
    if response.get("status") == "empty":
        return f"No {label} returned."
    return response.get("error") or response.get("status") or "No detail."


def build_missing_information(finnhub_configured, finnhub_news, finnhub_recommendations):
    missing = []

    if not finnhub_configured:
        missing.append("Finnhub is not connected; using Yahoo starter headlines and analyst actions only.")
    elif finnhub_news.get("status") not in {"ok", "empty"}:
        missing.append("Finnhub company news feed failed or was rate-limited.")
    elif finnhub_news.get("status") == "empty":
        missing.append("Finnhub company news returned no recent headlines for this symbol.")

    if finnhub_configured and finnhub_recommendations.get("status") not in {"ok", "empty"}:
        missing.append("Finnhub recommendation trend feed failed or was rate-limited.")

    missing.append("Premium real-time market-moving news and full analyst-note text are not connected yet.")
    return missing


def format_news_report(report):
    lines = [
        "# Overnight News Report",
        "",
        f"Symbol: {report['symbol']}",
    ]

    if report.get("error"):
        lines.append(f"Error: {report['error']}")
        return "\n".join(lines) + "\n"

    lines.extend([
        f"Timestamp: {report['timestamp']}",
        f"Stance: {report.get('stance', 'n/a')}",
        f"Confidence: {report.get('confidence', 'n/a')}",
        f"Providers: {format_providers(report.get('providers', []))}",
        "",
        "## Catalyst Summary",
    ])

    summary = report.get("summary") or {}
    lines.extend([
        f"- Headlines: {summary.get('item_count', 0)} total; {summary.get('fresh_item_count', 0)} fresh.",
        f"- Symbol-Relevant Headlines: {summary.get('relevant_item_count', 0)}",
        f"- Positive / Negative: {summary.get('positive_item_count', 0)} / {summary.get('negative_item_count', 0)}",
        f"- Analyst Actions: {summary.get('analyst_action_count', 0)}",
        f"- Catalyst Tags: {', '.join(summary.get('top_catalyst_tags', [])) or 'n/a'}",
        f"- News Score: {summary.get('news_score', 'n/a')}",
        f"- Analyst Score: {summary.get('analyst_score', 'n/a')}",
        "",
        "## Headlines",
    ])

    if not report["items"]:
        lines.append("- None returned.")
    else:
        for item in report["items"]:
            lines.append(
                f"- {item.get('title')} ({item.get('publisher')}, {item.get('published')}; "
                f"provider: {item.get('provider') or 'n/a'}, "
                f"{item.get('sentiment_label')}, "
                f"{'symbol-relevant' if item.get('symbol_relevant') else 'broad'}, "
                f"tags: {', '.join(item.get('catalyst_tags', [])) or 'none'})"
            )

    lines.extend([
        "",
        "## Analyst Actions",
    ])
    analyst_actions = report.get("analyst_actions") or []
    if not analyst_actions:
        lines.append("- None returned.")
    else:
        for item in analyst_actions:
            lines.append(
                f"- {item.get('date')}: {item.get('firm') or 'n/a'} "
                f"{item.get('action') or 'action n/a'} from {item.get('from_grade') or 'n/a'} "
                f"to {item.get('to_grade') or 'n/a'}"
            )

    lines.extend([
        "",
        "## Missing Information",
    ])
    lines.extend([f"- {item}" for item in report["missing_information"]])

    return "\n".join(lines) + "\n"


def format_providers(providers):
    if not providers:
        return "n/a"
    return "; ".join(
        f"{provider.get('name')}: {provider.get('status')}"
        for provider in providers
    )


def save_news_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{report['symbol']}_news_report.md"
    path.write_text(format_news_report(report), encoding="utf-8")
    return path
