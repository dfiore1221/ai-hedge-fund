from datetime import datetime


def interpret_macro_event_context(macro_report):
    official_macro = macro_report.get("official_macro") or {}
    economic_calendar = macro_report.get("economic_calendar") or {}
    market_snapshot = macro_report.get("macro") or {}
    sector_rotation = macro_report.get("sector_rotation") or {}
    summary = official_macro.get("summary") or {}

    inflation = interpret_inflation(summary)
    consumer = interpret_consumer(summary)
    labor = interpret_labor(summary)
    rates = interpret_rates(summary, market_snapshot)
    credit = interpret_credit(summary)
    sector = interpret_sector_implications(sector_rotation, inflation, consumer, rates, credit)
    event_risk = interpret_event_risk(economic_calendar)
    portfolio = interpret_portfolio_implications(inflation, consumer, labor, rates, credit, sector, event_risk)

    conclusion = build_conclusion(inflation, consumer, labor, rates, credit, event_risk)

    return {
        "agent": "Macro Event Interpreter",
        "system_role": "shared_data_layer",
        "layer": "Shared Macro Event Context",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "status": "available" if official_macro.get("status") not in {None, "not_configured"} else "needs_data",
        "inflation": inflation,
        "consumer": consumer,
        "labor": labor,
        "rates": rates,
        "credit": credit,
        "event_risk": event_risk,
        "sector_implications": sector,
        "portfolio_implications": portfolio,
        "committee_summary": conclusion,
        "missing_information": build_missing_information(official_macro, economic_calendar),
    }


def interpret_inflation(summary):
    cpi_yoy = value(summary, "cpi_yoy")
    cpi_mom = value(summary, "cpi_mom")
    pce_yoy = value(summary, "pce_yoy")
    pce_mom = value(summary, "pce_mom")

    score = 0
    observations = []
    if cpi_mom is not None:
        if cpi_mom <= 0.1:
            score += 1
            observations.append("CPI monthly trend is cooling.")
        elif cpi_mom >= 0.35:
            score -= 1
            observations.append("CPI monthly trend is hot.")
    if cpi_yoy is not None:
        if cpi_yoy <= 3.0:
            score += 1
            observations.append("CPI YoY is near the Fed's comfort zone.")
        elif cpi_yoy >= 3.5:
            score -= 1
            observations.append("CPI YoY remains above comfort.")
    if pce_mom is not None:
        if pce_mom <= 0.15:
            score += 1
            observations.append("PCE monthly trend is cooling.")
        elif pce_mom >= 0.3:
            score -= 1
            observations.append("PCE monthly trend is sticky.")
    if pce_yoy is not None and pce_yoy >= 3.0:
        score -= 1
        observations.append("PCE YoY remains elevated.")

    if score >= 2:
        stance = "disinflationary_tailwind"
    elif score <= -2:
        stance = "inflation_pressure"
    else:
        stance = "mixed_inflation"

    return {
        "stance": stance,
        "score": score,
        "cpi_yoy": cpi_yoy,
        "cpi_mom": cpi_mom,
        "pce_yoy": pce_yoy,
        "pce_mom": pce_mom,
        "observations": observations or ["Inflation data is inconclusive."],
    }


def interpret_consumer(summary):
    retail_mom = value(summary, "retail_sales_mom")
    retail_yoy = value(summary, "retail_sales_yoy")
    sentiment = value(summary, "consumer_sentiment")

    score = 0
    observations = []
    if retail_mom is not None:
        if retail_mom > 0.2:
            score += 1
            observations.append("Retail sales are expanding month over month.")
        elif retail_mom < -0.2:
            score -= 1
            observations.append("Retail sales are contracting month over month.")
    if retail_yoy is not None:
        if retail_yoy > 3:
            score += 1
            observations.append("Retail sales YoY growth is constructive.")
        elif retail_yoy < 0:
            score -= 1
            observations.append("Retail sales YoY is negative.")
    if sentiment is not None:
        if sentiment >= 80:
            score += 1
            observations.append("Consumer sentiment is healthy.")
        elif sentiment <= 65:
            score -= 1
            observations.append("Consumer sentiment is weak.")

    if score >= 2:
        stance = "consumer_resilient"
    elif score <= -2:
        stance = "consumer_softening"
    else:
        stance = "consumer_mixed"

    return {
        "stance": stance,
        "score": score,
        "retail_sales_mom": retail_mom,
        "retail_sales_yoy": retail_yoy,
        "consumer_sentiment": sentiment,
        "observations": observations or ["Consumer data is inconclusive."],
    }


def interpret_labor(summary):
    unemployment = value(summary, "unemployment_rate")
    payrolls_yoy = value(summary, "payrolls_yoy")
    claims = value(summary, "initial_jobless_claims")

    score = 0
    observations = []
    if unemployment is not None:
        if unemployment < 4.5:
            score += 1
            observations.append("Unemployment remains contained.")
        elif unemployment > 5:
            score -= 1
            observations.append("Unemployment is rising into risk territory.")
    if payrolls_yoy is not None:
        if payrolls_yoy > 1:
            score += 1
            observations.append("Payroll growth remains healthy.")
        elif payrolls_yoy < 0.3:
            score -= 1
            observations.append("Payroll growth is thin.")
    if claims is not None:
        if claims < 250000:
            score += 1
            observations.append("Initial claims are not signaling labor stress.")
        elif claims > 325000:
            score -= 1
            observations.append("Initial claims suggest labor stress.")

    if score >= 2:
        stance = "labor_supportive"
    elif score <= -2:
        stance = "labor_warning"
    else:
        stance = "labor_mixed"

    return {
        "stance": stance,
        "score": score,
        "unemployment_rate": unemployment,
        "payrolls_yoy": payrolls_yoy,
        "initial_jobless_claims": claims,
        "observations": observations or ["Labor data is inconclusive."],
    }


def interpret_rates(summary, market_snapshot):
    ten_year = value(summary, "ten_year_yield")
    two_year = value(summary, "two_year_yield")
    curve = value(summary, "yield_curve_10y_2y")
    ten_year_trend = (market_snapshot.get("ten_year_treasury") or {}).get("twenty_day_change_pct")

    score = 0
    observations = []
    if ten_year is not None:
        if ten_year < 4:
            score += 1
            observations.append("10Y yield level is relatively supportive.")
        elif ten_year > 4.75:
            score -= 1
            observations.append("10Y yield level is pressuring duration assets.")
    if ten_year_trend is not None:
        if ten_year_trend < 0:
            score += 1
            observations.append("10Y yield trend is easing.")
        elif ten_year_trend > 2:
            score -= 1
            observations.append("10Y yield trend is rising.")
    if curve is not None:
        if curve > 0:
            score += 1
            observations.append("Yield curve is positive.")
        elif curve < -0.5:
            score -= 1
            observations.append("Yield curve remains deeply inverted.")

    if score >= 2:
        stance = "rates_supportive"
    elif score <= -2:
        stance = "rates_headwind"
    else:
        stance = "rates_mixed"

    return {
        "stance": stance,
        "score": score,
        "ten_year_yield": ten_year,
        "two_year_yield": two_year,
        "yield_curve_10y_2y": curve,
        "ten_year_20d_change_pct": ten_year_trend,
        "observations": observations or ["Rates data is inconclusive."],
    }


def interpret_credit(summary):
    high_yield = value(summary, "high_yield_spread")
    investment_grade = value(summary, "investment_grade_spread")

    score = 0
    observations = []
    if high_yield is not None:
        if high_yield < 3.5:
            score += 1
            observations.append("High-yield spreads are benign.")
        elif high_yield > 5:
            score -= 1
            observations.append("High-yield spreads are signaling stress.")
    if investment_grade is not None:
        if investment_grade < 1.1:
            score += 1
            observations.append("Investment-grade spreads are calm.")
        elif investment_grade > 1.6:
            score -= 1
            observations.append("Investment-grade spreads are widening.")

    if score >= 2:
        stance = "credit_supportive"
    elif score <= -1:
        stance = "credit_warning"
    else:
        stance = "credit_mixed"

    return {
        "stance": stance,
        "score": score,
        "high_yield_spread": high_yield,
        "investment_grade_spread": investment_grade,
        "observations": observations or ["Credit data is inconclusive."],
    }


def interpret_event_risk(economic_calendar):
    summary = economic_calendar.get("summary") or {}
    high_today = summary.get("high_importance_events_today") or []
    next_event = summary.get("next_high_importance_event")

    if high_today:
        stance = "event_risk_today"
        instruction = "Reduce chase behavior and require cleaner entries around macro releases."
    elif next_event:
        stance = "upcoming_event_risk"
        instruction = "Be aware of upcoming macro catalysts before approving new swing risk."
    else:
        stance = "low_event_risk"
        instruction = "No major macro event pressure identified in the current calendar window."

    return {
        "stance": stance,
        "high_importance_today_count": len(high_today),
        "next_high_importance_event": next_event,
        "committee_instruction": instruction,
    }


def interpret_sector_implications(sector_rotation, inflation, consumer, rates, credit):
    leaders = [item.get("sector") for item in (sector_rotation.get("sectors") or [])[:3]]
    beneficiaries = []
    risks = []

    if inflation["stance"] == "disinflationary_tailwind" and rates["stance"] != "rates_headwind":
        beneficiaries.extend(["Technology", "Communication Services", "Consumer Discretionary"])
    if consumer["stance"] == "consumer_softening":
        risks.extend(["Consumer Discretionary", "Retail", "Banks"])
        beneficiaries.extend(["Healthcare", "Consumer Staples"])
    if credit["stance"] == "credit_warning":
        risks.extend(["Small Caps", "High Leverage", "Crypto-linked Equities"])
    if "Energy" in leaders:
        beneficiaries.append("Energy")

    return {
        "current_leaders": leaders,
        "potential_beneficiaries": sorted(set(beneficiaries)),
        "potential_risks": sorted(set(risks)),
    }


def interpret_portfolio_implications(inflation, consumer, labor, rates, credit, sector, event_risk):
    actions = []
    if rates["stance"] == "rates_supportive" and inflation["stance"] != "inflation_pressure":
        actions.append("Growth and AI duration assets can be considered, but only with clean technical entries.")
    if consumer["stance"] == "consumer_softening":
        actions.append("Discount consumer-cyclical setups unless news and technicals are unusually strong.")
    if credit["stance"] == "credit_warning":
        actions.append("Reduce tolerance for high-leverage or speculative balance-sheet names.")
    if event_risk["stance"] == "event_risk_today":
        actions.append("Avoid approving new paper trades immediately before high-importance macro releases.")
    if sector.get("current_leaders"):
        actions.append(f"Compare new ideas against current sector leadership: {', '.join(sector['current_leaders'])}.")

    return actions or ["Macro interpretation does not require a portfolio adjustment today."]


def build_conclusion(inflation, consumer, labor, rates, credit, event_risk):
    parts = [
        f"inflation={inflation['stance']}",
        f"consumer={consumer['stance']}",
        f"labor={labor['stance']}",
        f"rates={rates['stance']}",
        f"credit={credit['stance']}",
        f"event_risk={event_risk['stance']}",
    ]
    return "Macro event read: " + "; ".join(parts) + "."


def build_missing_information(official_macro, economic_calendar):
    missing = []
    if official_macro.get("status") == "not_configured":
        missing.append("FRED macro series are not configured.")
    if economic_calendar.get("status") == "not_configured":
        missing.append("Economic calendar is not configured.")
    missing.append("Macro surprise versus economist consensus is not connected yet.")
    missing.append("Fed funds futures / rate-cut probability feed is not connected yet.")
    return missing


def value(summary, key):
    item = summary.get(key)
    if not item:
        return None
    return item.get("value")


def format_macro_event_interpretation(report):
    lines = [
        "# Macro Event Interpretation",
        "",
        f"Created At: {report.get('created_at')}",
        f"Status: {report.get('status')}",
        "",
        "## Committee Summary",
        f"- {report.get('committee_summary')}",
        "",
        "## Key Reads",
    ]
    for section in ["inflation", "consumer", "labor", "rates", "credit"]:
        item = report.get(section) or {}
        lines.append(f"- {section.title()}: {item.get('stance')} (score {item.get('score')})")
        for observation in item.get("observations", [])[:3]:
            lines.append(f"  - {observation}")

    lines.extend(["", "## Portfolio Implications"])
    lines.extend([f"- {item}" for item in report.get("portfolio_implications", [])] or ["- None."])

    lines.extend(["", "## Missing Information"])
    lines.extend([f"- {item}" for item in report.get("missing_information", [])] or ["- None."])
    return "\n".join(lines) + "\n"
