import os
import sys
import re
import subprocess
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


def earnings(ticker):
    try:
        from data.earnings_calendar import format_earnings_calendar, get_earnings_calendar
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    ticker = normalize_ticker(ticker)
    print(format_earnings_calendar(get_earnings_calendar(ticker)))


def portfolio(ticker):
    try:
        from data.portfolio import (
            analyze_portfolio_exposure,
            format_portfolio_exposure,
            save_default_portfolio,
        )
        from agents.risk_manager import load_risk_policy
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    ticker = normalize_ticker(ticker)
    save_default_portfolio()
    policy = load_risk_policy()
    print(format_portfolio_exposure(
        analyze_portfolio_exposure(
            ticker,
            correlated_symbols=policy["ai_semi_correlated_symbols"],
        )
    ))


def journal(action):
    try:
        from data.trade_journal import (
            close_trade,
            format_trade_journal_summary,
            load_trade_journal,
            open_trade_from_plan,
            summarize_trade_journal,
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    action = action.lower()

    if action == "summary":
        print(format_trade_journal_summary(summarize_trade_journal(load_trade_journal())))
        return

    if action == "open":
        if len(sys.argv) < 8:
            raise ValueError(
                "Usage: python3 main.py journal open SYMBOL ENTRY STOP TARGET SHARES "
                "[--side long|short] [--status planned|open] [--run-id RUN_ID]"
            )
        symbol = normalize_ticker(sys.argv[3])
        trade_id = open_trade_from_plan(
            symbol=symbol,
            entry=float(sys.argv[4]),
            stop=float(sys.argv[5]),
            target=float(sys.argv[6]),
            shares=int(float(sys.argv[7])),
            side=get_cli_option("--side", "long"),
            status=get_cli_option("--status", "planned"),
            setup_type=get_cli_option("--setup-type", "manual"),
            source=get_cli_option("--source", "manual"),
            agent_run_id=get_cli_option("--run-id", ""),
            thesis=get_cli_option("--thesis", ""),
            notes=get_cli_option("--notes", ""),
        )
        print(f"Saved simulated trade: {trade_id}")
        print(format_trade_journal_summary(summarize_trade_journal(load_trade_journal())))
        return

    if action == "close":
        if len(sys.argv) < 5:
            raise ValueError(
                "Usage: python3 main.py journal close TRADE_ID EXIT_PRICE "
                "[--reason TEXT] [--lessons TEXT]"
            )
        trade = close_trade(
            trade_id=sys.argv[3],
            exit_price=float(sys.argv[4]),
            exit_reason=get_cli_option("--reason", ""),
            lessons=get_cli_option("--lessons", ""),
        )
        print(f"Closed simulated trade: {trade['id']} ({trade['symbol']})")
        print(format_trade_journal_summary(summarize_trade_journal(load_trade_journal())))
        return

    raise ValueError("Journal command supports: summary, open, close")


def ledger(action):
    if action.lower() != "summary":
        raise ValueError("Ledger command currently supports: summary")

    try:
        from data.paper_ledger import build_paper_ledger, format_paper_ledger_summary
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    print(format_paper_ledger_summary(build_paper_ledger()))


def portfolio_ticker(action):
    if action.lower() != "status":
        raise ValueError("Ticker command currently supports: status")

    try:
        from data.portfolio_ticker import (
            build_portfolio_ticker_status,
            format_portfolio_ticker_status,
            status_to_json,
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    status = build_portfolio_ticker_status(
        refresh_prices="--no-refresh" not in sys.argv[3:],
        save_prices="--no-save" not in sys.argv[3:],
    )
    if "--json" in sys.argv[3:]:
        print(status_to_json(status))
    else:
        print(format_portfolio_ticker_status(status))


def fills(action):
    if action.lower() not in {"check", "apply"}:
        raise ValueError("Fills command supports: check, apply")

    try:
        from data.paper_fills import format_paper_fill_report, process_paper_fills
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    result = process_paper_fills(apply=action.lower() == "apply")
    print(format_paper_fill_report(result))


def get_cli_option(name, default=""):
    if name not in sys.argv:
        return default
    index = sys.argv.index(name)
    if index + 1 >= len(sys.argv):
        return default
    return sys.argv[index + 1]


def feedback(action):
    if action.lower() != "summary":
        raise ValueError("Feedback command currently supports: summary")

    try:
        from agents.feedback_loop import (
            format_feedback_report,
            generate_feedback_report,
            save_feedback_report,
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    report = generate_feedback_report()
    output_path = save_feedback_report(report)
    print(format_feedback_report(report))
    print(f"Saved feedback report to: {output_path}")


def review(action):
    try:
        from agents.daily_setup_review import (
            format_daily_setup_review,
            generate_daily_setup_review,
            save_daily_setup_review_report,
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    review_day = action.lower() if action.lower() == "today" else action
    top_n_option = get_cli_option("--top", "")
    top_n = int(top_n_option) if top_n_option else None
    source_path = get_cli_option("--source", "")

    report = generate_daily_setup_review(
        review_day=review_day,
        source_path=source_path or None,
        top_n=top_n,
        save_memory=True,
    )
    output_path = save_daily_setup_review_report(report)
    print(format_daily_setup_review(report))
    print(f"Saved daily setup review to: {output_path}")


def security(action):
    if action.lower() != "check":
        raise ValueError("Security command currently supports: check")

    try:
        from security.checks import build_security_report, format_security_report
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    print(format_security_report(build_security_report()))


def data_health(period):
    if period.lower() != "today":
        raise ValueError("Data-health command currently supports: today")

    try:
        from data.data_quality import format_data_health_report, generate_data_health_report
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    print(format_data_health_report(generate_data_health_report()))


def project(action):
    if action.lower() != "status":
        raise ValueError("Project command currently supports: status")

    print("# Project Status")
    print("")
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Branch: {git_value(['branch', '--show-current'])}")
    print(f"Latest commit: {git_value(['log', '-1', '--oneline'])}")
    print(f"Remote: {git_value(['remote', 'get-url', 'origin'])}")
    print(f"Working tree: {'clean' if git_clean() else 'has local changes'}")
    print(f".env present: {(PROJECT_ROOT / '.env').exists()}")
    print(f"Dashboard port: {os.getenv('DASHBOARD_PORT', '8501')}")
    print(f"Morning email script: {PROJECT_ROOT / 'scripts' / 'run_morning_email.sh'}")
    print(f"LaunchAgent plist: {PROJECT_ROOT / 'automation' / 'com.dfiore.ai-hedge-fund.morning-brief.plist'}")
    print(f"Daily review script: {PROJECT_ROOT / 'scripts' / 'run_daily_setup_review.sh'}")
    print(f"Daily review plist: {PROJECT_ROOT / 'automation' / 'com.dfiore.ai-hedge-fund.daily-setup-review.plist'}")


def git_value(args):
    result = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return (result.stdout or result.stderr).strip() or "n/a"


def git_clean():
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and not result.stdout.strip()


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


def morning(period):
    if period.lower() != "today":
        raise ValueError("Morning command currently supports: today")

    try:
        from agents.morning_brief import (
            create_morning_brief,
            format_morning_brief,
            save_morning_brief,
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    report = create_morning_brief()
    output_path = save_morning_brief(report)
    print(format_morning_brief(report))
    print(f"Saved morning brief to: {output_path}")


def morning_email(period, dry_run=False):
    if period.lower() != "today":
        raise ValueError("Morning email command currently supports: today")

    try:
        from agents.morning_email import send_morning_brief_email
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    result = send_morning_brief_email(dry_run=dry_run)
    print(result["body"])
    print(f"Saved morning brief to: {result['report_path']}")
    if dry_run:
        print("Dry run complete. Email settings are present; no email was sent.")
    else:
        print(f"Sent morning brief email: {result['subject']}")


def dashboard(_arg=None):
    dashboard_path = PROJECT_ROOT / "dashboard" / "app.py"
    port = os.getenv("DASHBOARD_PORT", "8501")
    subprocess.run([
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(dashboard_path),
        "--server.port",
        port,
    ], cwd=PROJECT_ROOT, check=False)


def technical(ticker):
    try:
        from agents.technical_analyst import (
            analyze_technical_setup,
            format_technical_report,
            save_technical_report,
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    ticker = normalize_ticker(ticker)
    report = analyze_technical_setup(ticker)
    output_path = save_technical_report(report)
    print(format_technical_report(report))
    print(f"Saved technical report to: {output_path}")


def risk(ticker):
    try:
        from agents.risk_manager import (
            evaluate_trade_risk,
            format_risk_report,
            save_risk_report,
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    ticker = normalize_ticker(ticker)
    report = evaluate_trade_risk(ticker)
    output_path = save_risk_report(report)
    print(format_risk_report(report))
    print(f"Saved risk report to: {output_path}")


def options(ticker):
    try:
        from agents.options_flow import analyze_options_flow, format_options_report, save_options_report
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    ticker = normalize_ticker(ticker)
    report = analyze_options_flow(ticker)
    output_path = save_options_report(report)
    print(format_options_report(report))
    print(f"Saved options report to: {output_path}")


def news(ticker):
    try:
        from agents.news_intelligence import collect_overnight_news, format_news_report, save_news_report
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    ticker = normalize_ticker(ticker)
    report = collect_overnight_news(ticker)
    output_path = save_news_report(report)
    print(format_news_report(report))
    print(f"Saved news report to: {output_path}")


def backtest(ticker):
    try:
        from agents.quant_researcher import (
            backtest_sma_trend_strategy,
            format_backtest_report,
            save_backtest_report,
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    ticker = normalize_ticker(ticker)
    report = backtest_sma_trend_strategy(ticker)
    output_path = save_backtest_report(report)
    print(format_backtest_report(report))
    print(f"Saved backtest report to: {output_path}")


def cio(ticker):
    try:
        from agents.cio import create_cio_summary, format_cio_report, save_cio_report
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    ticker = normalize_ticker(ticker)
    report = create_cio_summary(ticker)
    output_path = save_cio_report(report)
    print(format_cio_report(report))
    print(f"Saved CIO summary to: {output_path}")


def main():
    if len(sys.argv) < 3:
        print("Usage:")
        print("  python3 main.py dashboard start")
        print("  python3 main.py morning today")
        print("  python3 main.py morning-email today")
        print("  python3 main.py morning-email today --dry-run")
        print("  python3 main.py macro today")
        print("  python3 main.py technical MSFT")
        print("  python3 main.py risk MSFT")
        print("  python3 main.py cio MSFT")
        print("  python3 main.py earnings MSFT")
        print("  python3 main.py portfolio MSFT")
        print("  python3 main.py journal summary")
        print("  python3 main.py journal open MSFT 400 380 430 10 --status planned --run-id RUN_ID")
        print("  python3 main.py journal close TRADE_ID 425 --reason target")
        print("  python3 main.py ledger summary")
        print("  python3 main.py ticker status")
        print("  python3 main.py ticker status --json")
        print("  python3 main.py fills check")
        print("  python3 main.py fills apply")
        print("  python3 main.py feedback summary")
        print("  python3 main.py review today")
        print("  python3 main.py security check")
        print("  python3 main.py data-health today")
        print("  python3 main.py project status")
        print("  python3 main.py options MSFT")
        print("  python3 main.py news MSFT")
        print("  python3 main.py backtest MSFT")
        print("  python3 main.py analyze MSFT")
        print("  python3 main.py history MSFT")
        print("  python3 main.py thesis MSFT")
        print("  python3 main.py validate MSFT")
        print("  python3 main.py facts MSFT")
        return

    command = sys.argv[1].lower()
    ticker = sys.argv[2]
    dry_run = "--dry-run" in sys.argv[3:]

    try:
        if command == "analyze":
            analyze(ticker)
        elif command == "dashboard":
            dashboard(ticker)
        elif command == "morning":
            morning(ticker)
        elif command == "morning-email":
            morning_email(ticker, dry_run=dry_run)
        elif command == "macro":
            macro(ticker)
        elif command == "technical":
            technical(ticker)
        elif command == "risk":
            risk(ticker)
        elif command == "cio":
            cio(ticker)
        elif command == "earnings":
            earnings(ticker)
        elif command == "portfolio":
            portfolio(ticker)
        elif command == "journal":
            journal(ticker)
        elif command == "ledger":
            ledger(ticker)
        elif command == "ticker":
            portfolio_ticker(ticker)
        elif command == "fills":
            fills(ticker)
        elif command == "feedback":
            feedback(ticker)
        elif command == "review":
            review(ticker)
        elif command == "security":
            security(ticker)
        elif command == "data-health":
            data_health(ticker)
        elif command == "project":
            project(ticker)
        elif command == "options":
            options(ticker)
        elif command == "news":
            news(ticker)
        elif command == "backtest":
            backtest(ticker)
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
