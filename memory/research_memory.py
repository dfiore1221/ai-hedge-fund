import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path("memory") / "hedge_fund_memory.db"


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