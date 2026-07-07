from datetime import datetime
from pathlib import Path

import yfinance as yf


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports" / "news"


def collect_overnight_news(ticker, limit=10):
    ticker = ticker.upper().strip()

    try:
        news_items = yf.Ticker(ticker).news or []
    except Exception as exc:
        return {
            "agent": "Overnight News Analyst",
            "symbol": ticker,
            "error": f"Could not fetch news: {exc}",
            "items": [],
        }

    items = []
    for item in news_items[:limit]:
        content = item.get("content", item)
        title = content.get("title") or item.get("title")
        publisher = content.get("provider", {}).get("displayName") if isinstance(content.get("provider"), dict) else item.get("publisher")
        url = content.get("canonicalUrl", {}).get("url") if isinstance(content.get("canonicalUrl"), dict) else item.get("link")
        published = content.get("pubDate") or item.get("providerPublishTime")

        items.append({
            "title": title,
            "publisher": publisher,
            "url": url,
            "published": str(published),
        })

    return {
        "agent": "Overnight News Analyst",
        "symbol": ticker,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "items": items,
        "missing_information": [
            "Premium market-moving news and analyst action feeds are not connected yet.",
            "Sentiment/relevance scoring is not implemented yet.",
        ],
    }


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
        "",
        "## Headlines",
    ])

    if not report["items"]:
        lines.append("- None returned.")
    else:
        for item in report["items"]:
            lines.append(f"- {item.get('title')} ({item.get('publisher')}, {item.get('published')})")

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
