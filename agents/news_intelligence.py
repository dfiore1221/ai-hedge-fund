import re
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf


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
            "missing_information": [
                "Premium market-moving news and analyst action feeds are not connected yet.",
            ],
        }

    items = []
    for item in news_items[:limit]:
        items.append(normalize_news_item(item, aliases))

    analyst_actions = collect_analyst_actions(yf_ticker)
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
        "missing_information": [
            "Premium market-moving news and analyst action feeds are not connected yet.",
            "Yahoo starter feed can miss pre-market wires, paywalled analyst notes, and intraday updates.",
        ],
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


def save_news_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{report['symbol']}_news_report.md"
    path.write_text(format_news_report(report), encoding="utf-8")
    return path
