import sys
import re
from pathlib import Path

from framework.report_validator import format_validation_report, validate_report
from memory.research_memory import (
    build_research_memory_context,
    get_reports_for_ticker,
    save_research_report,
)

PROJECT_ROOT = Path(__file__).resolve().parent
REPORTS_DIR = PROJECT_ROOT / "reports"
VALIDATION_DIR = PROJECT_ROOT / "reports" / "validation"
TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")


def normalize_ticker(ticker):
    ticker = ticker.strip().upper()
    if not TICKER_PATTERN.match(ticker):
        raise ValueError(
            "Ticker must be 1-10 characters using letters, numbers, dots, or hyphens."
        )
    return ticker


def analyze(ticker):
    try:
        from agents.research_analyst import research_company
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    ticker = normalize_ticker(ticker)
    result = research_company(ticker)

    REPORTS_DIR.mkdir(exist_ok=True)
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)

    output_path = REPORTS_DIR / f"{ticker}_research_report.txt"
    output_path.write_text(result, encoding="utf-8")
    save_research_report(ticker, result)

    validation = validate_report(result)
    validation_path = VALIDATION_DIR / f"{ticker}_validation_report.md"
    validation_path.write_text(format_validation_report(validation), encoding="utf-8")

    print(result)
    print(f"\nSaved report to: {output_path}")
    print(f"Saved validation to: {validation_path}")
    print(f"Framework quality score: {validation['quality_score']}/100")
    print("Saved report to memory database.")


def history(ticker):
    ticker = normalize_ticker(ticker)
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


def thesis(ticker):
    ticker = normalize_ticker(ticker)
    context = build_research_memory_context(ticker)

    print(f"\nResearch memory for {ticker}:\n")
    print(context["message"])
    print(f"Stored reports loaded: {context['report_count']}")

    if context["current_thesis"]:
        current = context["current_thesis"]
        print("\nCurrent structured thesis:")
        print(f"Updated: {current['updated_at']}")
        print(f"Rating: {current['rating']}")
        print(f"Overall score: {current['overall_score']}")
        if current["thesis"]:
            print(f"\nThesis:\n{current['thesis']}")
        if current["open_questions"]:
            print(f"\nOpen questions:\n{current['open_questions']}")

    for report in context["recent_reports"]:
        print("=" * 60)
        print(f"Date: {report['created_at']}")
        print("=" * 60)
        print(report["memo_preview"])
        print()


def validate(ticker):
    ticker = normalize_ticker(ticker)
    report_path = REPORTS_DIR / f"{ticker}_research_report.txt"

    if not report_path.exists():
        print(f"No saved report found at: {report_path}")
        return

    report = report_path.read_text(encoding="utf-8")
    validation = validate_report(report)

    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    validation_path = VALIDATION_DIR / f"{ticker}_validation_report.md"
    validation_path.write_text(format_validation_report(validation), encoding="utf-8")

    print(format_validation_report(validation))
    print(f"Saved validation to: {validation_path}")


def facts(ticker):
    try:
        from data.sec_data import format_structured_financial_facts, get_structured_financial_facts
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    ticker = normalize_ticker(ticker)
    print(format_structured_financial_facts(get_structured_financial_facts(ticker)))


def macro(period):
    if period.lower() != "today":
        raise ValueError("Macro command currently supports: today")

    try:
        from agents.market_intelligence import (
            format_market_intelligence_report,
            generate_daily_market_intelligence,
            save_market_intelligence_report,
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    report = generate_daily_market_intelligence()
    output_path = save_market_intelligence_report(report)
    print(format_market_intelligence_report(report))
    print(f"Saved market intelligence report to: {output_path}")


def main():
    if len(sys.argv) < 3:
        print("Usage:")
        print("  python3 main.py macro today")
        print("  python3 main.py analyze MSFT")
        print("  python3 main.py history MSFT")
        print("  python3 main.py thesis MSFT")
        print("  python3 main.py validate MSFT")
        print("  python3 main.py facts MSFT")
        return

    command = sys.argv[1].lower()
    ticker = sys.argv[2]

    try:
        if command == "analyze":
            analyze(ticker)
        elif command == "macro":
            macro(ticker)
        elif command == "history":
            history(ticker)
        elif command == "thesis":
            thesis(ticker)
        elif command == "validate":
            validate(ticker)
        elif command == "facts":
            facts(ticker)
        else:
            print(f"Unknown command: {command}")
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {exc}")


if __name__ == "__main__":
    main()
