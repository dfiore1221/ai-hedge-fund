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
            cancel_planned_trade,
            close_trade,
            format_trade_journal_summary,
            load_trade_journal,
            open_trade_from_plan,
            partial_close_trade,
            summarize_trade_journal,
            update_trade_levels,
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
            closed_at=get_cli_option("--closed-at", ""),
        )
        print(f"Closed simulated trade: {trade['id']} ({trade['symbol']})")
        print(format_trade_journal_summary(summarize_trade_journal(load_trade_journal())))
        return

    if action == "cancel":
        if len(sys.argv) < 4:
            raise ValueError(
                "Usage: python3 main.py journal cancel TRADE_ID "
                "[--reason TEXT] [--lessons TEXT]"
            )
        trade = cancel_planned_trade(
            trade_id=sys.argv[3],
            reason=get_cli_option("--reason", "Planned order canceled before fill."),
            lessons=get_cli_option("--lessons", ""),
        )
        print(f"Canceled planned simulated trade: {trade['id']} ({trade['symbol']})")
        print(format_trade_journal_summary(summarize_trade_journal(load_trade_journal())))
        return

    if action == "partial-close":
        if len(sys.argv) < 6:
            raise ValueError(
                "Usage: python3 main.py journal partial-close TRADE_ID SHARES EXIT_PRICE "
                "[--reason TEXT] [--lessons TEXT]"
            )
        result = partial_close_trade(
            trade_id=sys.argv[3],
            shares_to_close=float(sys.argv[4]),
            exit_price=float(sys.argv[5]),
            exit_reason=get_cli_option("--reason", ""),
            lessons=get_cli_option("--lessons", ""),
            closed_at=get_cli_option("--closed-at", ""),
        )
        closed = result["closed_trade"]
        remaining = result["remaining_trade"]
        print(
            f"Partially closed simulated trade: {closed['id']} ({closed['symbol']}) "
            f"for {closed['shares']} shares"
        )
        print(f"Remaining shares on {remaining['id']}: {remaining['shares']}")
        print(format_trade_journal_summary(summarize_trade_journal(load_trade_journal())))
        return

    if action == "update-levels":
        if len(sys.argv) < 4:
            raise ValueError(
                "Usage: python3 main.py journal update-levels TRADE_ID "
                "[--stop PRICE] [--target PRICE] [--notes TEXT]"
            )
        stop_value = get_cli_option("--stop", None)
        target_value = get_cli_option("--target", None)
        trade = update_trade_levels(
            trade_id=sys.argv[3],
            stop=float(stop_value) if stop_value not in {None, ""} else None,
            target=float(target_value) if target_value not in {None, ""} else None,
            notes=get_cli_option("--notes", ""),
        )
        print(
            f"Updated simulated trade levels: {trade['id']} ({trade['symbol']}) "
            f"stop {trade['stop']} target {trade['target']}"
        )
        print(format_trade_journal_summary(summarize_trade_journal(load_trade_journal())))
        return

    raise ValueError("Journal command supports: summary, open, close, cancel, partial-close, update-levels")


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


def portfolio_governor(period):
    try:
        from agents.portfolio_governor import (
            format_portfolio_governor_report,
            generate_portfolio_governor_report,
            save_portfolio_governor_report,
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    if str(period).lower() != "today":
        raise ValueError("Portfolio governor currently supports: today")

    report = generate_portfolio_governor_report()
    output_path = save_portfolio_governor_report(report)
    print(format_portfolio_governor_report(report))
    print(f"Saved portfolio governor report to: {output_path}")


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

    apply = action.lower() == "apply"
    if apply and "--force" not in sys.argv[3:]:
        from agents.autonomous_paper import is_regular_market_hours

        if not is_regular_market_hours():
            print("Paper Fill Check\n\nMarket is closed. No simulated fills were applied.")
            return

    result = process_paper_fills(apply=apply)
    print(format_paper_fill_report(result))


def exit_orders(action):
    try:
        from data.exit_orders import (
            cancel_exit_order,
            create_exit_order,
            ensure_exit_orders,
            format_exit_order_list,
            format_exit_order_report,
            mark_exit_order_filled,
            process_exit_orders,
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    action = action.lower()
    if action == "list":
        print(format_exit_order_list(ensure_exit_orders()))
        return

    if action == "open":
        if len(sys.argv) < 4:
            raise ValueError(
                "Usage: python3 main.py exit-orders open TRADE_ID "
                "[--shares all|NUMBER] [--scheduled-for YYYY-MM-DD] [--reason TEXT]"
            )
        order = create_exit_order(
            trade_id=sys.argv[3],
            shares=get_cli_option("--shares", "all"),
            scheduled_for=get_cli_option("--scheduled-for", ""),
            reason=get_cli_option("--reason", "Human-approved exit"),
            lessons=get_cli_option("--lessons", ""),
            notes=get_cli_option("--notes", ""),
        )
        print(f"Saved exit order: {order['id']} for {order['symbol']} trade {order['trade_id']}")
        print(format_exit_order_list(ensure_exit_orders()))
        return

    if action == "cancel":
        if len(sys.argv) < 4:
            raise ValueError("Usage: python3 main.py exit-orders cancel ORDER_ID [--notes TEXT]")
        order = cancel_exit_order(sys.argv[3], notes=get_cli_option("--notes", ""))
        print(f"Canceled exit order: {order['id']}")
        print(format_exit_order_list(ensure_exit_orders()))
        return

    if action == "fill":
        if len(sys.argv) < 5:
            raise ValueError(
                "Usage: python3 main.py exit-orders fill ORDER_ID FILL_PRICE "
                "[--filled-at YYYY-MM-DDTHH:MM:SS] [--notes TEXT]"
            )
        order = mark_exit_order_filled(
            order_id=sys.argv[3],
            fill_price=float(sys.argv[4]),
            filled_at=get_cli_option("--filled-at", ""),
            notes=get_cli_option("--notes", ""),
        )
        print(f"Marked exit order filled: {order['id']}")
        print(format_exit_order_list(ensure_exit_orders()))
        return

    if action in {"check", "apply"}:
        result = process_exit_orders(apply=action == "apply")
        print(format_exit_order_report(result))
        return

    raise ValueError("Exit-orders command supports: list, open, cancel, fill, check, apply")


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


def weekly_review(action):
    try:
        from agents.weekly_review import (
            format_weekly_review,
            generate_weekly_review,
            save_weekly_review_report,
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    review_day = action.lower() if action.lower() in {"today", "week"} else action
    report = generate_weekly_review(review_day)
    output_path = save_weekly_review_report(report)
    print(format_weekly_review(report))
    print(f"Saved weekly review to: {output_path}")


def setup_backtest(action):
    try:
        from agents.weekly_setup_backtest import (
            format_weekly_setup_backtest,
            generate_weekly_setup_backtest,
            save_weekly_setup_backtest_report,
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    start_day = action
    end_day = get_cli_option("--end", "")
    top_n_option = get_cli_option("--top", "")
    top_n = int(top_n_option) if top_n_option else 10
    report = generate_weekly_setup_backtest(
        start_day=start_day,
        end_day=end_day or None,
        top_n=top_n,
    )
    output_path = save_weekly_setup_backtest_report(report)
    print(format_weekly_setup_backtest(report))
    print(f"Saved weekly setup backtest to: {output_path}")


def automation_watchdog(action):
    if action.lower() not in {"run", "check", "now"}:
        raise ValueError("Automation watchdog supports: run")

    try:
        from agents.automation_watchdog import (
            format_automation_watchdog_report,
            run_automation_watchdog,
            save_automation_watchdog_report,
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    report = run_automation_watchdog()
    output_path = save_automation_watchdog_report(report)
    print(format_automation_watchdog_report(report))
    print(f"Saved automation watchdog report to: {output_path}")


def autonomy(action):
    try:
        from agents.autonomous_paper import format_autonomy_report, run_autonomy_cycle
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    report = run_autonomy_cycle(action=action)
    print(format_autonomy_report(report))
    print(f"Saved autonomy report to: {report['report_path']}")


def human_escalations(action):
    if str(action).lower() not in {"check", "notify", "status"}:
        raise ValueError("Human-escalations supports: check, notify")
    from agents.human_escalation import format_human_escalation_report, run_human_escalation_check

    report = run_human_escalation_check(notify=str(action).lower() == "notify")
    print(format_human_escalation_report(report))
    print(f"Saved human escalation report to: {report['report_path']}")


def top_pick_backtest(action):
    try:
        from agents.weekly_setup_backtest import (
            format_top_pick_scenario_backtest,
            generate_top_pick_scenario_backtest,
            save_top_pick_scenario_backtest_report,
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    start_day = action
    end_day = get_cli_option("--end", "")
    report = generate_top_pick_scenario_backtest(
        start_day=start_day,
        end_day=end_day or None,
    )
    output_path = save_top_pick_scenario_backtest_report(report)
    print(format_top_pick_scenario_backtest(report))
    print(f"Saved top-pick scenario backtest to: {output_path}")


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
    elif result.get("queued"):
        print("Morning brief email was queued for retry.")
        print(f"Pending email: {result['pending_path']}")
        print(f"Send error: {result['error']}")
    else:
        print(f"Sent morning brief email: {result['subject']}")


def email_retry(action):
    try:
        from delivery.email_retry import format_retry_report, retry_pending_emails
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    kind = None if str(action).lower() in {"all", "pending"} else str(action).lower()
    if kind == "morning":
        kind = "morning_brief"
    report = retry_pending_emails(kind=kind)
    print(format_retry_report(report))


def email_health(action):
    if str(action).lower() not in {"check", "status"}:
        raise ValueError("Email-health supports: check")
    from delivery.email_delivery import check_email_health

    result = check_email_health()
    print("# Email Delivery Health")
    print("")
    print(f"Status: {result.get('status')}")
    if result.get("host"):
        print(f"SMTP: {result.get('host')}:{result.get('port')}")
    if result.get("username"):
        print(f"Account: {result.get('username')}")
    if result.get("error"):
        print(f"Error: {result.get('error')}")
    print(f"Action: {result.get('action')}")


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


def options_ready(action):
    try:
        from agents.options_readiness import (
            format_options_readiness_report,
            generate_options_readiness_report,
            save_options_readiness_report,
        )
        from data.options_journal import (
            append_option_trade,
            close_option_trade,
            format_options_journal_summary,
            load_options_journal,
            summarize_options_journal,
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    action = str(action or "").lower().strip()
    if action == "status":
        symbol = get_cli_option("--symbol", "")
        report = generate_options_readiness_report(
            symbol=symbol or None,
            include_live_options=bool(symbol),
        )
        output_path = save_options_readiness_report(report)
        print(format_options_readiness_report(report))
        print(f"Saved options readiness report to: {output_path}")
        return

    if action == "summary":
        print(format_options_journal_summary(summarize_options_journal(load_options_journal())))
        return

    if action == "open":
        if len(sys.argv) < 9:
            raise ValueError(
                "Usage: python3 main.py options-ready open SYMBOL STRATEGY EXPIRATION STRIKE "
                "ENTRY_PREMIUM CONTRACTS [--status planned|open] [--target-premium PRICE] "
                "[--stop-premium PRICE] [--short-strike PRICE]"
            )
        symbol = normalize_ticker(sys.argv[3])
        strategy = sys.argv[4]
        expiration = sys.argv[5]
        strike = float(sys.argv[6])
        entry_premium = float(sys.argv[7])
        contracts = int(float(sys.argv[8]))
        trade_id = append_option_trade({
            "symbol": symbol,
            "strategy": strategy,
            "expiration": expiration,
            "strike": strike,
            "short_strike": get_cli_option("--short-strike", ""),
            "entry_premium": entry_premium,
            "contracts": contracts,
            "status": get_cli_option("--status", "planned"),
            "source": get_cli_option("--source", "manual"),
            "agent_run_id": get_cli_option("--run-id", ""),
            "target_premium": get_cli_option("--target-premium", ""),
            "stop_premium": get_cli_option("--stop-premium", ""),
            "thesis": get_cli_option("--thesis", ""),
            "notes": get_cli_option("--notes", ""),
        })
        print(f"Saved paper options idea: {trade_id}")
        print(format_options_journal_summary(summarize_options_journal(load_options_journal())))
        return

    if action == "close":
        if len(sys.argv) < 5:
            raise ValueError(
                "Usage: python3 main.py options-ready close OPTIONS_TRADE_ID EXIT_PREMIUM "
                "[--reason TEXT] [--lessons TEXT]"
            )
        close_option_trade(
            trade_id=sys.argv[3],
            exit_premium=float(sys.argv[4]),
            exit_reason=get_cli_option("--reason", ""),
            lessons=get_cli_option("--lessons", ""),
        )
        print("Paper options idea closed.")
        print(format_options_journal_summary(summarize_options_journal(load_options_journal())))
        return

    raise ValueError("Options-ready supports: status, summary, open, close")


def paper_lab(action):
    try:
        from agents.paper_lab import (
            build_options_contract_plan,
            format_options_contract_plan,
            format_paper_lab_report,
            generate_paper_lab_report,
            save_paper_lab_report,
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    action = str(action or "").lower().strip()
    if action in {"today", "summary"}:
        report = generate_paper_lab_report()
        output_path = save_paper_lab_report(report)
        print(format_paper_lab_report(report))
        print(f"Saved Paper Lab report to: {output_path}")
        return

    if action == "options-plan":
        if len(sys.argv) < 4:
            raise ValueError(
                "Usage: python3 main.py paper-lab options-plan SYMBOL "
                "[--strategy long_call|long_put|call_debit_spread|put_debit_spread] [--save]"
            )
        symbol = normalize_ticker(sys.argv[3])
        strategy = get_cli_option("--strategy", "long_call")
        plan = build_options_contract_plan(symbol, strategy=strategy, save="--save" in sys.argv[3:])
        print(format_options_contract_plan(plan))
        return

    raise ValueError("Paper Lab supports: today, options-plan")


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


def ask(action):
    try:
        from agents.committee_question import ask_committee
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    if action.lower() in {"portfolio", "market", "account", "macro"}:
        question = " ".join(sys.argv[3:]).strip()
        report = ask_committee(question, scope="portfolio")
    else:
        question = " ".join(sys.argv[3:]).strip()
        report = ask_committee(question, symbol=normalize_ticker(action), scope="ticker")

    print(report["answer_markdown"])
    print(f"Saved committee question to memory: {report['question_id']}")


def position_manager(period):
    try:
        from agents.position_manager import (
            format_position_manager_report,
            generate_position_manager_report,
            save_position_manager_report,
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    if str(period).lower() != "today":
        raise ValueError("Position manager currently supports: today")

    use_llm = "--llm" in sys.argv[3:]
    local_mode = "--local" in sys.argv[3:] or "--no-refresh" in sys.argv[3:]
    report = generate_position_manager_report(use_llm=use_llm, refresh_market_data=not local_mode)
    output_path = save_position_manager_report(report)
    print(format_position_manager_report(report))
    print(f"Saved position manager report to: {output_path}")


def intraday_monitor(period):
    try:
        from agents.intraday_monitor import (
            format_intraday_monitor_report,
            run_intraday_monitor,
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A required package is missing. Run `pip install -r requirements.txt` and try again."
        ) from exc

    if str(period).lower() not in {"now", "today"}:
        raise ValueError("Intraday monitor currently supports: now")

    dry_run = "--dry-run" in sys.argv[3:]
    no_email = "--no-email" in sys.argv[3:]
    apply_fills = "--apply-fills" in sys.argv[3:]
    report = run_intraday_monitor(
        send_alert=not no_email,
        dry_run=dry_run,
        apply_fills=apply_fills,
    )
    print(format_intraday_monitor_report(report))
    print(f"Saved intraday monitor report to: {report['report_path']}")


def intraday_discovery(period):
    if str(period).lower() not in {"now", "today"}:
        raise ValueError("Intraday discovery currently supports: now")
    from agents.intraday_discovery import (
        format_intraday_discovery_report,
        run_intraday_discovery,
    )

    report = run_intraday_discovery(
        review_material="--no-review" not in sys.argv[3:],
        send_alert="--no-email" not in sys.argv[3:],
        dry_run="--dry-run" in sys.argv[3:],
    )
    print(format_intraday_discovery_report(report))
    print(f"Saved intraday discovery report to: {report['report_path']}")


def strategy_review(action):
    if str(action).lower() not in {"run", "today"}:
        raise ValueError("Strategy review supports: run")
    from agents.strategy_review import format_strategy_source_review, generate_strategy_source_review

    report = generate_strategy_source_review(save_memory=True)
    print(format_strategy_source_review(report))


def trade_funnel(action):
    if str(action).lower() not in {"status", "run"}:
        raise ValueError("Trade funnel supports: status")
    from agents.trade_funnel import format_trade_funnel_report, generate_trade_funnel_report

    report = generate_trade_funnel_report(save=True)
    print(format_trade_funnel_report(report))
    print(f"Saved trade funnel report to: {report['report_path']}")


def main():
    if len(sys.argv) < 3:
        print("Usage:")
        print("  python3 main.py dashboard start")
        print("  python3 main.py morning today")
        print("  python3 main.py morning-email today")
        print("  python3 main.py morning-email today --dry-run")
        print("  python3 main.py email-retry morning")
        print("  python3 main.py email-health check")
        print("  python3 main.py macro today")
        print("  python3 main.py technical MSFT")
        print("  python3 main.py risk MSFT")
        print("  python3 main.py cio MSFT")
        print('  python3 main.py ask MSFT "Should we set up this trade?"')
        print('  python3 main.py ask portfolio "How is the account positioned?"')
        print("  python3 main.py earnings MSFT")
        print("  python3 main.py portfolio MSFT")
        print("  python3 main.py journal summary")
        print("  python3 main.py journal open MSFT 400 380 430 10 --status planned --run-id RUN_ID")
        print("  python3 main.py journal close TRADE_ID 425 --reason target")
        print("  python3 main.py ledger summary")
        print("  python3 main.py portfolio-governor today")
        print("  python3 main.py ticker status")
        print("  python3 main.py ticker status --json")
        print("  python3 main.py fills check")
        print("  python3 main.py fills apply")
        print("  python3 main.py exit-orders open TRADE_ID --shares all --scheduled-for YYYY-MM-DD")
        print("  python3 main.py exit-orders check")
        print("  python3 main.py exit-orders apply")
        print("  python3 main.py feedback summary")
        print("  python3 main.py review today")
        print("  python3 main.py weekly-review today")
        print("  python3 main.py setup-backtest 2026-08-03 --end 2026-08-07")
        print("  python3 main.py top-pick-backtest 2026-08-01 --end 2026-08-17")
        print("  python3 main.py position-manager today")
        print("  python3 main.py position-manager today --local")
        print("  python3 main.py position-manager today --llm")
        print("  python3 main.py intraday-monitor now")
        print("  python3 main.py intraday-monitor now --dry-run")
        print("  python3 main.py intraday-discovery now")
        print("  python3 main.py intraday-discovery now --dry-run")
        print("  python3 main.py automation-watchdog run")
        print("  python3 main.py autonomy plan")
        print("  python3 main.py autonomy execute")
        print("  python3 main.py autonomy status")
        print("  python3 main.py strategy-review run")
        print("  python3 main.py funnel status")
        print("  python3 main.py human-escalations check")
        print("  python3 main.py human-escalations notify")
        print("  python3 main.py security check")
        print("  python3 main.py data-health today")
        print("  python3 main.py project status")
        print("  python3 main.py options MSFT")
        print("  python3 main.py options-ready status")
        print("  python3 main.py options-ready status --symbol MSFT")
        print("  python3 main.py options-ready summary")
        print("  python3 main.py paper-lab today")
        print("  python3 main.py paper-lab options-plan MSFT --strategy long_call")
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
        elif command == "email-retry":
            email_retry(ticker)
        elif command == "email-health":
            email_health(ticker)
        elif command == "macro":
            macro(ticker)
        elif command == "technical":
            technical(ticker)
        elif command == "risk":
            risk(ticker)
        elif command == "cio":
            cio(ticker)
        elif command == "ask":
            ask(ticker)
        elif command == "earnings":
            earnings(ticker)
        elif command == "portfolio":
            portfolio(ticker)
        elif command == "journal":
            journal(ticker)
        elif command == "ledger":
            ledger(ticker)
        elif command == "portfolio-governor":
            portfolio_governor(ticker)
        elif command == "ticker":
            portfolio_ticker(ticker)
        elif command == "fills":
            fills(ticker)
        elif command == "exit-orders":
            exit_orders(ticker)
        elif command == "feedback":
            feedback(ticker)
        elif command == "review":
            review(ticker)
        elif command == "weekly-review":
            weekly_review(ticker)
        elif command == "setup-backtest":
            setup_backtest(ticker)
        elif command == "top-pick-backtest":
            top_pick_backtest(ticker)
        elif command == "position-manager":
            position_manager(ticker)
        elif command == "intraday-monitor":
            intraday_monitor(ticker)
        elif command == "intraday-discovery":
            intraday_discovery(ticker)
        elif command == "automation-watchdog":
            automation_watchdog(ticker)
        elif command == "autonomy":
            autonomy(ticker)
        elif command == "strategy-review":
            strategy_review(ticker)
        elif command == "funnel":
            trade_funnel(ticker)
        elif command == "human-escalations":
            human_escalations(ticker)
        elif command == "security":
            security(ticker)
        elif command == "data-health":
            data_health(ticker)
        elif command == "project":
            project(ticker)
        elif command == "options":
            options(ticker)
        elif command == "options-ready":
            options_ready(ticker)
        elif command == "paper-lab":
            paper_lab(ticker)
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
