import sys
from pathlib import Path

from agents.research_analyst import research_company
from memory.research_memory import get_reports_for_ticker


def analyze(ticker):
    result = research_company(ticker)

    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)

    output_path = reports_dir / f"{ticker.upper()}_research_report.txt"
    output_path.write_text(result)

    print(result)
    print(f"\nSaved report to: {output_path}")


def history(ticker):
    reports = get_reports_for_ticker(ticker)

    if not reports:
        print(f"No saved research found for {ticker.upper()}.")
        return

    print(f"\nSaved research history for {ticker.upper()}:\n")

    for created_at, memo in reports:
        print("=" * 60)
        print(f"Date: {created_at}")
        print("=" * 60)
        print(memo[:1500])
        print("\n... memo preview truncated ...\n")


def main():
    if len(sys.argv) < 3:
        print("Usage:")
        print("  python3 main.py analyze MSFT")
        print("  python3 main.py history MSFT")
        return

    command = sys.argv[1]
    ticker = sys.argv[2]

    if command == "analyze":
        analyze(ticker)
    elif command == "history":
        history(ticker)
    else:
        print(f"Unknown command: {command}")


if __name__ == "__main__":
    main()