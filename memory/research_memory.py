import sqlite3
import re
import json
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "memory" / "hedge_fund_memory.db"
MEMO_PREVIEW_CHARS = 2500


def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS research_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            created_at TEXT NOT NULL,
            memo TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ticker_theses (
            ticker TEXT PRIMARY KEY,
            updated_at TEXT NOT NULL,
            rating TEXT,
            overall_score REAL,
            confidence REAL,
            thesis TEXT,
            open_questions TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agent_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            symbol TEXT,
            created_at TEXT NOT NULL,
            stance TEXT,
            confidence REAL,
            output_json TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def save_research_report(ticker, memo):
    init_db()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO research_reports (ticker, created_at, memo)
        VALUES (?, ?, ?)
    """, (ticker.upper(), datetime.now().isoformat(), memo))

    conn.commit()
    conn.close()
    update_ticker_thesis_from_memo(ticker, memo)


def get_reports_for_ticker(ticker):
    init_db()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT created_at, memo
        FROM research_reports
        WHERE ticker = ?
        ORDER BY created_at DESC
    """, (ticker.upper(),))

    reports = cursor.fetchall()
    conn.close()

    return reports


def get_recent_reports_for_ticker(ticker, limit=3):
    init_db()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT created_at, memo
        FROM research_reports
        WHERE ticker = ?
        ORDER BY created_at DESC
        LIMIT ?
    """, (ticker.upper(), limit))

    reports = cursor.fetchall()
    conn.close()

    return reports


def build_research_memory_context(ticker, limit=3, preview_chars=MEMO_PREVIEW_CHARS):
    reports = get_recent_reports_for_ticker(ticker, limit=limit)
    thesis = get_ticker_thesis(ticker)

    if not reports:
        return {
            "ticker": ticker.upper(),
            "report_count": 0,
            "message": "No prior research reports found for this ticker.",
            "current_thesis": thesis,
            "recent_reports": [],
        }

    recent_reports = []
    for created_at, memo in reports:
        preview = memo[:preview_chars]
        if len(memo) > preview_chars:
            preview += "\n\n[Prior memo preview truncated.]"

        recent_reports.append({
            "created_at": created_at,
            "memo_preview": preview,
        })

    return {
        "ticker": ticker.upper(),
        "report_count": len(reports),
        "message": (
            "Use this prior research to maintain thesis continuity, identify what changed, "
            "and avoid repeating old conclusions without checking new evidence."
        ),
        "current_thesis": thesis,
        "recent_reports": recent_reports,
    }


def upsert_ticker_thesis(
    ticker,
    rating=None,
    overall_score=None,
    confidence=None,
    thesis=None,
    open_questions=None,
):
    init_db()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO ticker_theses (
            ticker,
            updated_at,
            rating,
            overall_score,
            confidence,
            thesis,
            open_questions
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            updated_at = excluded.updated_at,
            rating = excluded.rating,
            overall_score = excluded.overall_score,
            confidence = excluded.confidence,
            thesis = excluded.thesis,
            open_questions = excluded.open_questions
    """, (
        ticker.upper(),
        datetime.now().isoformat(),
        rating,
        overall_score,
        confidence,
        thesis,
        open_questions,
    ))

    conn.commit()
    conn.close()


def update_ticker_thesis_from_memo(ticker, memo):
    rating = extract_final_rating(memo)
    overall_score = extract_overall_score(memo)
    thesis = extract_section_excerpt(memo, "Executive Summary")
    open_questions = extract_section_excerpt(memo, "What Data We Still Need")

    upsert_ticker_thesis(
        ticker=ticker,
        rating=rating,
        overall_score=overall_score,
        confidence=None,
        thesis=thesis,
        open_questions=open_questions,
    )


def get_ticker_thesis(ticker):
    init_db()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT updated_at, rating, overall_score, confidence, thesis, open_questions
        FROM ticker_theses
        WHERE ticker = ?
    """, (ticker.upper(),))

    row = cursor.fetchone()
    conn.close()

    if row is None:
        return None

    updated_at, rating, overall_score, confidence, thesis, open_questions = row
    return {
        "ticker": ticker.upper(),
        "updated_at": updated_at,
        "rating": rating,
        "overall_score": overall_score,
        "confidence": confidence,
        "thesis": thesis,
        "open_questions": open_questions,
    }


def save_agent_report(run_id, agent_name, output, symbol=None, stance=None, confidence=None):
    init_db()

    if symbol is None:
        symbol = output.get("symbol") or output.get("ticker")
    if stance is None:
        stance = output.get("stance") or output.get("decision") or output.get("market_regime")
    if confidence is None:
        confidence = output.get("confidence") or output.get("confidence_score")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO agent_reports (
            run_id,
            agent_name,
            symbol,
            created_at,
            stance,
            confidence,
            output_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        run_id,
        agent_name,
        symbol.upper() if symbol else None,
        datetime.now().isoformat(),
        stance,
        confidence,
        json.dumps(output, default=str),
    ))

    conn.commit()
    conn.close()


def get_agent_reports_for_run(run_id):
    init_db()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT agent_name, symbol, created_at, stance, confidence, output_json
        FROM agent_reports
        WHERE run_id = ?
        ORDER BY id
    """, (run_id,))

    rows = cursor.fetchall()
    conn.close()

    reports = []
    for agent_name, symbol, created_at, stance, confidence, output_json in rows:
        reports.append({
            "agent_name": agent_name,
            "symbol": symbol,
            "created_at": created_at,
            "stance": stance,
            "confidence": confidence,
            "output": json.loads(output_json),
        })

    return reports


def extract_final_rating(memo):
    patterns = [
        r"Final Rating:\s*([^\n]+)",
        r"Final rating:\s*([^\n]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, memo)
        if match:
            return match.group(1).strip().strip("*")

    return None


def extract_overall_score(memo):
    match = re.search(r"Overall Research Score:\s*(\d+(?:\.\d+)?)", memo)
    if not match:
        return None

    return float(match.group(1))


def extract_section_excerpt(memo, heading, max_chars=1200):
    pattern = rf"(?:^|\n)(?:#+\s*)?(?:\d+\.\s*)?{re.escape(heading)}[^\n]*\n(?P<body>.*?)(?=\n(?:#+\s*)?\d+\.\s+|\Z)"
    match = re.search(pattern, memo, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        return None

    text = match.group("body").strip()
    if len(text) <= max_chars:
        return text

    return text[:max_chars].rstrip() + "\n\n[Excerpt truncated.]"
