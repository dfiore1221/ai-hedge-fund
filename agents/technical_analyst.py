from datetime import datetime
from pathlib import Path

from data.market_data import get_ohlcv_history


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports" / "technical"


def analyze_technical_setup(ticker):
    ticker = ticker.upper().strip()
    history = get_ohlcv_history(ticker, period="6mo")
    spy_history = get_ohlcv_history("SPY", period="6mo")

    if history.get("error"):
        return {
            "agent": "Technical Analyst",
            "symbol": ticker,
            "stance": "no_trade",
            "confidence": 0.0,
            "error": history["error"],
        }

    rows = history["rows"]
    closes = [row["close"] for row in rows]
    highs = [row["high"] for row in rows]
    lows = [row["low"] for row in rows]
    volumes = [row["volume"] for row in rows if row["volume"] is not None]

    indicators = calculate_indicators(rows)
    levels = calculate_levels(rows, indicators)
    relative_strength = calculate_relative_strength(rows, spy_history.get("rows", []))
    stance, confidence, evidence, risks, missing = determine_stance(
        rows,
        indicators,
        levels,
        relative_strength,
    )

    return {
        "agent": "Technical Analyst",
        "run_id": datetime.now().strftime("%Y-%m-%d-technical"),
        "symbol": ticker,
        "stance": stance,
        "confidence": confidence,
        "time_horizon": "swing",
        "latest_date": rows[-1]["date"],
        "latest_close": closes[-1],
        "trend": {
            "above_20dma": closes[-1] > indicators["sma_20"] if indicators["sma_20"] else None,
            "above_50dma": closes[-1] > indicators["sma_50"] if indicators["sma_50"] else None,
            "above_200dma": closes[-1] > indicators["sma_200"] if indicators["sma_200"] else None,
            "sma_20": indicators["sma_20"],
            "sma_50": indicators["sma_50"],
            "sma_200": indicators["sma_200"],
        },
        "momentum": {
            "rsi_14": indicators["rsi_14"],
            "macd": indicators["macd"],
            "macd_signal": indicators["macd_signal"],
            "macd_histogram": indicators["macd_histogram"],
            "atr_14": indicators["atr_14"],
            "average_volume_20d": average(volumes[-20:]) if volumes else None,
        },
        "relative_strength": relative_strength,
        "setup": {
            "entry_trigger": levels["entry"]["breakout_trigger"],
            "alternative_entry": levels["entry"]["pullback_zone"],
            "stop": levels["stop"],
            "target_1": levels["target_1"],
            "target_2": levels["target_2"],
            "target_3": levels["target_3"],
            "reward_to_risk": levels["reward_to_risk_to_target_1"],
            "reward_to_risk_to_target_2": levels["reward_to_risk_to_target_2"],
            "reward_to_risk_to_target_3": levels["reward_to_risk_to_target_3"],
            "pullback_reward_to_risk": levels["pullback_reward_to_risk"],
            "no_trade_below": levels["stop"],
            "no_trade_reason": build_no_trade_reason(stance, risks),
        },
        "levels": levels,
        "key_evidence": evidence,
        "risks": risks,
        "missing_information": missing,
        "citations": [{
            "source": "Yahoo Finance via yfinance",
            "url": f"https://finance.yahoo.com/quote/{ticker}",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }],
    }


def calculate_indicators(rows):
    closes = [row["close"] for row in rows]
    highs = [row["high"] for row in rows]
    lows = [row["low"] for row in rows]

    macd_line, signal_line, histogram = calculate_macd(closes)

    return {
        "sma_20": sma(closes, 20),
        "sma_50": sma(closes, 50),
        "sma_200": sma(closes, 200),
        "rsi_14": calculate_rsi(closes, 14),
        "macd": macd_line,
        "macd_signal": signal_line,
        "macd_histogram": histogram,
        "atr_14": calculate_atr(highs, lows, closes, 14),
    }


def calculate_levels(rows, indicators):
    latest_close = rows[-1]["close"]
    recent_20 = rows[-20:] if len(rows) >= 20 else rows
    recent_50 = rows[-50:] if len(rows) >= 50 else rows
    atr = indicators["atr_14"] or 0

    support = min(row["low"] for row in recent_20)
    resistance = max(row["high"] for row in recent_20)
    larger_support = min(row["low"] for row in recent_50)
    larger_resistance = max(row["high"] for row in recent_50)

    breakout_trigger = resistance * 1.005
    pullback_trigger = indicators["sma_20"] if indicators["sma_20"] else latest_close
    invalidation = max(larger_support, latest_close - (2 * atr)) if atr else larger_support
    target_1 = breakout_trigger + (2 * atr) if atr else larger_resistance
    target_2 = max(larger_resistance, breakout_trigger + (3 * atr)) if atr else larger_resistance
    target_3 = max(target_2, breakout_trigger + (4 * atr)) if atr else larger_resistance
    pullback_target = max(resistance, target_1)

    return {
        "support_20d": support,
        "resistance_20d": resistance,
        "support_50d": larger_support,
        "resistance_50d": larger_resistance,
        "entry": {
            "breakout_trigger": breakout_trigger,
            "pullback_zone": pullback_trigger,
        },
        "stop": invalidation,
        "target_1": target_1,
        "target_2": target_2,
        "target_3": target_3,
        "reward_to_risk_to_target_1": reward_to_risk(breakout_trigger, invalidation, target_1),
        "reward_to_risk_to_target_2": reward_to_risk(breakout_trigger, invalidation, target_2),
        "reward_to_risk_to_target_3": reward_to_risk(breakout_trigger, invalidation, target_3),
        "pullback_reward_to_risk": reward_to_risk(pullback_trigger, invalidation, pullback_target),
    }


def determine_stance(rows, indicators, levels, relative_strength):
    close = rows[-1]["close"]
    score = 0
    evidence = []
    risks = []
    missing = []

    if indicators["sma_20"] and close > indicators["sma_20"]:
        score += 1
        evidence.append("Price is above the 20-day moving average.")
    else:
        score -= 1
        risks.append("Price is below or near the 20-day moving average.")

    if indicators["sma_50"] and close > indicators["sma_50"]:
        score += 1
        evidence.append("Price is above the 50-day moving average.")
    else:
        score -= 1
        risks.append("Price is below or near the 50-day moving average.")

    if indicators["sma_20"] and indicators["sma_50"] and indicators["sma_20"] > indicators["sma_50"]:
        score += 1
        evidence.append("20-day moving average is above the 50-day moving average.")
    else:
        risks.append("Moving-average structure is not clearly bullish.")

    if indicators["rsi_14"] is None:
        missing.append("RSI could not be calculated.")
    elif 45 <= indicators["rsi_14"] <= 70:
        score += 1
        evidence.append("RSI is constructive without being extremely overbought.")
    elif indicators["rsi_14"] > 75:
        score -= 1
        risks.append("RSI is extended and may be vulnerable to mean reversion.")
    else:
        score -= 1
        risks.append("RSI is weak.")

    if indicators["macd_histogram"] is None:
        missing.append("MACD could not be calculated.")
    elif indicators["macd_histogram"] > 0:
        score += 1
        evidence.append("MACD histogram is positive.")
    else:
        score -= 1
        risks.append("MACD histogram is negative.")

    if relative_strength.get("relative_to_spy_20d") is not None:
        if relative_strength["relative_to_spy_20d"] > 0:
            score += 1
            evidence.append("20-day relative strength versus SPY is positive.")
        else:
            score -= 1
            risks.append("20-day relative strength versus SPY is negative.")
    else:
        missing.append("Relative strength versus SPY could not be calculated.")

    rr = levels.get("reward_to_risk_to_target_1")
    rr_to_target_2 = levels.get("reward_to_risk_to_target_2")
    pullback_rr = levels.get("pullback_reward_to_risk")
    if rr is not None and rr >= 1.5:
        score += 1
        evidence.append("Reward-to-risk to first target is acceptable.")
    elif rr_to_target_2 is not None and rr_to_target_2 >= 1.5:
        evidence.append("Reward-to-risk is acceptable only if using the second target.")
        risks.append("First target does not compensate for the stop distance.")
    elif pullback_rr is not None and pullback_rr >= 1.5:
        evidence.append("Pullback entry would create acceptable reward-to-risk.")
        risks.append("Breakout entry is not attractive enough; wait for a better entry.")
    else:
        risks.append("Reward-to-risk to first target is weak or unavailable.")

    if score >= 4:
        stance = "bullish"
    elif score <= -2:
        stance = "bearish"
    elif rr is None and pullback_rr is None:
        stance = "no_trade"
    else:
        stance = "neutral"

    confidence = min(0.9, max(0.25, 0.45 + (abs(score) * 0.06)))
    return stance, round(confidence, 2), evidence, risks, missing


def build_no_trade_reason(stance, risks):
    if stance != "no_trade":
        return None
    if not risks:
        return "Setup is not actionable."
    return "; ".join(risks[:3])


def calculate_relative_strength(rows, benchmark_rows):
    if len(rows) < 21 or len(benchmark_rows) < 21:
        return {"relative_to_spy_20d": None}

    symbol_return = pct_change(rows[-1]["close"], rows[-21]["close"])
    benchmark_return = pct_change(benchmark_rows[-1]["close"], benchmark_rows[-21]["close"])

    if symbol_return is None or benchmark_return is None:
        relative = None
    else:
        relative = symbol_return - benchmark_return

    return {
        "symbol_20d_return_pct": symbol_return,
        "spy_20d_return_pct": benchmark_return,
        "relative_to_spy_20d": relative,
    }


def format_technical_report(report):
    if report.get("error"):
        return f"# Technical Analyst Report\n\nError: {report['error']}\n"

    lines = [
        "# Technical Analyst Report",
        "",
        f"Run ID: {report['run_id']}",
        f"Symbol: {report['symbol']}",
        f"Stance: {report['stance']}",
        f"Confidence: {report['confidence']}",
        f"Time Horizon: {report['time_horizon']}",
        f"Latest Close: {format_number(report['latest_close'])} ({report['latest_date']})",
        "",
        "## Trend",
    ]

    trend = report["trend"]
    lines.extend([
        f"- 20DMA: {format_number(trend['sma_20'])}; above: {trend['above_20dma']}",
        f"- 50DMA: {format_number(trend['sma_50'])}; above: {trend['above_50dma']}",
        f"- 200DMA: {format_number(trend['sma_200'])}; above: {trend['above_200dma']}",
        "",
        "## Momentum",
    ])

    momentum = report["momentum"]
    lines.extend([
        f"- RSI 14: {format_number(momentum['rsi_14'])}",
        f"- MACD: {format_number(momentum['macd'])}",
        f"- MACD Signal: {format_number(momentum['macd_signal'])}",
        f"- MACD Histogram: {format_number(momentum['macd_histogram'])}",
        f"- ATR 14: {format_number(momentum['atr_14'])}",
        f"- 20D Average Volume: {format_number(momentum['average_volume_20d'])}",
        "",
        "## Levels",
    ])

    levels = report["levels"]
    setup = report["setup"]
    lines.extend([
        f"- 20D Support: {format_number(levels['support_20d'])}",
        f"- 20D Resistance: {format_number(levels['resistance_20d'])}",
        f"- Breakout Trigger: {format_number(levels['entry']['breakout_trigger'])}",
        f"- Pullback Zone: {format_number(levels['entry']['pullback_zone'])}",
        f"- Stop / Invalidation: {format_number(levels['stop'])}",
        f"- Target 1: {format_number(levels['target_1'])}",
        f"- Target 2: {format_number(levels['target_2'])}",
        f"- Target 3: {format_number(levels['target_3'])}",
        f"- Reward/Risk to Target 1: {format_number(levels['reward_to_risk_to_target_1'])}",
        f"- Reward/Risk to Target 2: {format_number(levels['reward_to_risk_to_target_2'])}",
        f"- Reward/Risk to Target 3: {format_number(levels['reward_to_risk_to_target_3'])}",
        f"- Pullback Reward/Risk: {format_number(levels['pullback_reward_to_risk'])}",
        "",
        "## Structured Setup",
        f"- Entry Trigger: {format_number(setup['entry_trigger'])}",
        f"- Alternative Entry: {format_number(setup['alternative_entry'])}",
        f"- Stop: {format_number(setup['stop'])}",
        f"- Target 1: {format_number(setup['target_1'])}",
        f"- Target 2: {format_number(setup['target_2'])}",
        f"- Target 3: {format_number(setup['target_3'])}",
        f"- Reward/Risk: {format_number(setup['reward_to_risk'])}",
        f"- Reward/Risk to Target 2: {format_number(setup['reward_to_risk_to_target_2'])}",
        f"- Pullback Reward/Risk: {format_number(setup['pullback_reward_to_risk'])}",
        f"- No-Trade Below: {format_number(setup['no_trade_below'])}",
        f"- No-Trade Reason: {setup['no_trade_reason'] or 'n/a'}",
        "",
        "## Relative Strength",
    ])

    rs = report["relative_strength"]
    lines.extend([
        f"- Symbol 20D Return: {format_pct(rs.get('symbol_20d_return_pct'))}",
        f"- SPY 20D Return: {format_pct(rs.get('spy_20d_return_pct'))}",
        f"- Relative to SPY: {format_pct(rs.get('relative_to_spy_20d'))}",
        "",
        "## Key Evidence",
    ])

    lines.extend([f"- {item}" for item in report["key_evidence"]] or ["- None."])
    lines.append("")
    lines.append("## Risks / No-Trade Conditions")
    lines.extend([f"- {item}" for item in report["risks"]] or ["- None."])
    lines.append("")
    lines.append("## Missing Information")
    lines.extend([f"- {item}" for item in report["missing_information"]] or ["- None."])

    return "\n".join(lines) + "\n"


def save_technical_report(report):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{report['symbol']}_technical_report.md"
    path.write_text(format_technical_report(report), encoding="utf-8")
    return path


def sma(values, window):
    if len(values) < window:
        return None
    return average(values[-window:])


def calculate_rsi(closes, window):
    if len(closes) <= window:
        return None

    gains = []
    losses = []
    for index in range(-window, 0):
        change = closes[index] - closes[index - 1]
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))

    avg_gain = average(gains)
    avg_loss = average(losses)
    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calculate_macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal:
        return None, None, None

    fast_ema = ema_series(closes, fast)
    slow_ema = ema_series(closes, slow)
    macd_series = [
        fast_value - slow_value
        for fast_value, slow_value in zip(fast_ema[-len(slow_ema):], slow_ema)
    ]
    signal_series = ema_series(macd_series, signal)
    if not signal_series:
        return None, None, None

    macd_line = macd_series[-1]
    signal_line = signal_series[-1]
    return macd_line, signal_line, macd_line - signal_line


def ema_series(values, window):
    if len(values) < window:
        return []

    multiplier = 2 / (window + 1)
    ema_values = [average(values[:window])]

    for value in values[window:]:
        ema_values.append((value - ema_values[-1]) * multiplier + ema_values[-1])

    return ema_values


def calculate_atr(highs, lows, closes, window):
    if len(closes) <= window:
        return None

    true_ranges = []
    for index in range(1, len(closes)):
        true_ranges.append(max(
            highs[index] - lows[index],
            abs(highs[index] - closes[index - 1]),
            abs(lows[index] - closes[index - 1]),
        ))

    if len(true_ranges) < window:
        return None
    return average(true_ranges[-window:])


def reward_to_risk(entry, stop, target):
    risk = entry - stop
    reward = target - entry
    if risk <= 0:
        return None
    return reward / risk


def pct_change(latest, previous):
    if previous == 0:
        return None
    return ((latest - previous) / previous) * 100


def average(values):
    values = [value for value in values if value is not None]
    if not values:
        return None
    return sum(values) / len(values)


def format_number(value):
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def format_pct(value):
    if value is None:
        return "n/a"
    return f"{value:.2f}%"
