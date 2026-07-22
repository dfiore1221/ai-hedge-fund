import json
from pathlib import Path

from data.paper_fills import fetch_price_map
from data.paper_ledger import build_paper_ledger
from data.trade_journal import append_trade, load_trade_journal, normalize_status, to_float


PROJECT_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = PROJECT_ROOT / "framework" / "core_etf_sleeve.json"
CORE_SETUP_TYPE = "core etf sleeve"
CORE_REBALANCE_MIN_NOTIONAL = 100


def analyze_core_etf_sleeve(macro_report, journal=None, ledger=None):
    policy = load_policy()
    regime = macro_report.get("assessment", {}).get("market_regime", "Neutral")
    profile = policy.get("risk_profiles", {}).get(regime) or policy["risk_profiles"]["Neutral"]
    ledger = ledger or build_paper_ledger(journal)
    journal = journal if journal is not None else load_trade_journal()
    account = ledger["account"]
    equity = account.get("net_liquidation_value") or account.get("starting_cash") or 0
    target_sleeve_pct = profile.get("target_sleeve_pct", policy.get("target_sleeve_pct", 0.5))
    target_sleeve_value = equity * target_sleeve_pct
    holdings = current_core_holdings(journal)
    current_value = sum(item["market_value"] for item in holdings.values())
    drift_value = target_sleeve_value - current_value
    drift_pct = pct(drift_value, equity)
    rebalance_band = policy.get("rebalance_band_pct", 0.05)
    sleeve_status = classify_status(current_value, target_sleeve_value, equity, rebalance_band)
    prices = fetch_price_map(profile["weights"].keys())
    desired = build_desired_allocations(profile["weights"], target_sleeve_value, holdings, prices)
    actions = build_actions(desired, sleeve_status)
    sector_context = top_sector_context(macro_report)

    return {
        "agent": "Core ETF Sleeve",
        "regime": regime,
        "equity": round_money(equity),
        "target_sleeve_pct": round_pct(target_sleeve_pct),
        "target_sleeve_value": round_money(target_sleeve_value),
        "current_sleeve_value": round_money(current_value),
        "current_sleeve_pct": round_pct(pct(current_value, equity)),
        "drift_value": round_money(drift_value),
        "drift_pct": round_pct(drift_pct),
        "rebalance_band_pct": round_pct(rebalance_band),
        "cash_reserve_pct": round_pct(policy.get("cash_reserve_pct", 0.2)),
        "status": sleeve_status,
        "desired_allocations": desired,
        "current_holdings": list(holdings.values()),
        "actions": actions,
        "sector_context": sector_context,
        "notes": policy.get("notes", []),
    }


def load_policy():
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def current_core_holdings(journal):
    holdings = {}
    if journal is None or journal.empty:
        return holdings

    for _, row in journal.iterrows():
        if normalize_status(row.get("status")) != "open":
            continue
        if str(row.get("setup_type", "")).strip().lower() != CORE_SETUP_TYPE:
            continue

        symbol = str(row.get("symbol", "")).upper().strip()
        shares = to_float(row.get("shares"))
        if not symbol or shares <= 0:
            continue
        entry = to_float(row.get("entry"))
        current_price = to_float(row.get("current_price")) or entry
        market_value = current_price * shares
        current = holdings.setdefault(symbol, {
            "symbol": symbol,
            "shares": 0.0,
            "market_value": 0.0,
            "last_price": current_price,
        })
        current["shares"] += shares
        current["market_value"] += market_value
        current["last_price"] = current_price

    return {
        symbol: {
            "symbol": item["symbol"],
            "shares": round_money(item["shares"]),
            "market_value": round_money(item["market_value"]),
            "last_price": round_money(item["last_price"]),
        }
        for symbol, item in holdings.items()
    }


def classify_status(current_value, target_value, equity, rebalance_band):
    if target_value <= 0:
        return "No target allocation."
    if current_value <= 0:
        return "Not invested; build core sleeve."
    if abs(current_value - target_value) / equity > rebalance_band:
        return "Rebalance needed."
    return "Within rebalance band."


def build_desired_allocations(weights, target_sleeve_value, holdings, prices=None):
    prices = prices or {}
    rows = []
    for symbol, weight in weights.items():
        target_value = target_sleeve_value * weight
        current_value = holdings.get(symbol, {}).get("market_value", 0)
        last_price = prices.get(symbol)
        suggested_shares = int(target_value // last_price) if last_price else 0
        rows.append({
            "symbol": symbol,
            "target_weight": round_pct(weight),
            "target_value": round_money(target_value),
            "current_value": round_money(current_value),
            "difference": round_money(target_value - current_value),
            "last_price": round_money(last_price) if last_price else None,
            "suggested_shares": suggested_shares,
        })
    return rows


def build_actions(desired, sleeve_status):
    if sleeve_status == "Within rebalance band.":
        return ["Core ETF sleeve is within policy band; no rebalance required."]

    return [
        f"Plan paper allocation for {row['symbol']}: target ${row['target_value']:.2f} "
        f"(difference ${row['difference']:.2f}; approx {row['suggested_shares']} shares"
        f"{' at $' + format_money_plain(row['last_price']) if row.get('last_price') else ''})."
        for row in desired
        if abs(row["difference"]) >= 100
    ]


def approve_core_rebalance_from_brief(brief_report, min_notional=CORE_REBALANCE_MIN_NOTIONAL):
    core_sleeve = brief_report.get("core_etf_sleeve") or {}
    created_at = str(brief_report.get("created_at") or "").strip()
    approval_id = build_core_rebalance_approval_id(created_at)
    journal = load_trade_journal()
    existing_keys = {
        (
            str(row.get("agent_run_id", "")).strip(),
            str(row.get("symbol", "")).upper().strip(),
        )
        for _, row in journal.iterrows()
    }

    created = []
    skipped = []
    if core_sleeve.get("status") == "Within rebalance band.":
        return {
            "approval_id": approval_id,
            "created": created,
            "skipped": [{
                "symbol": "CORE",
                "reason": "Core ETF sleeve is already within the rebalance band.",
            }],
        }

    for row in core_sleeve.get("desired_allocations", []):
        symbol = str(row.get("symbol", "")).upper().strip()
        difference = to_float(row.get("difference"))
        last_price = to_float(row.get("last_price"))
        if not symbol:
            continue

        if difference < -min_notional:
            skipped.append({
                "symbol": symbol,
                "reason": "Trim needed, but partial trim automation is not enabled yet.",
                "difference": round_money(difference),
            })
            continue

        if difference < min_notional:
            skipped.append({
                "symbol": symbol,
                "reason": "Difference is below the minimum rebalance notional.",
                "difference": round_money(difference),
            })
            continue

        if last_price <= 0:
            skipped.append({
                "symbol": symbol,
                "reason": "No usable last price was available.",
                "difference": round_money(difference),
            })
            continue

        shares = int(difference // last_price)
        if shares <= 0:
            skipped.append({
                "symbol": symbol,
                "reason": "Difference is not large enough to buy one whole share.",
                "difference": round_money(difference),
                "last_price": round_money(last_price),
            })
            continue

        key = (approval_id, symbol)
        if key in existing_keys:
            skipped.append({
                "symbol": symbol,
                "reason": "This brief approval already created a paper order for this symbol.",
                "approval_id": approval_id,
            })
            continue

        trade_id = append_trade({
            "symbol": symbol,
            "side": "long",
            "status": "open",
            "setup_type": CORE_SETUP_TYPE,
            "source": "core sleeve",
            "agent_run_id": approval_id,
            "entry": last_price,
            "stop": 0,
            "target": 0,
            "shares": shares,
            "current_price": last_price,
            "thesis": "Human-approved Core ETF Sleeve rebalance from AIFundOS morning brief.",
            "notes": (
                f"Core rebalance approval {approval_id}. "
                f"Target value ${format_money_plain(row.get('target_value'))}; "
                f"current value ${format_money_plain(row.get('current_value'))}; "
                f"difference ${format_money_plain(difference)}. "
                "Paper-only ledger action; no broker order was sent."
            ),
        })
        existing_keys.add(key)
        created.append({
            "trade_id": trade_id,
            "symbol": symbol,
            "shares": shares,
            "entry": round_money(last_price),
            "notional": round_money(shares * last_price),
            "difference": round_money(difference),
        })

    return {
        "approval_id": approval_id,
        "created": created,
        "skipped": skipped,
    }


def build_core_rebalance_approval_id(created_at):
    clean = "".join(char for char in created_at if char.isdigit())
    return f"core-rebalance-{clean or 'manual'}"


def top_sector_context(macro_report):
    sectors = macro_report.get("sector_rotation", {}).get("sectors", [])[:3]
    return [
        {
            "sector": item.get("sector"),
            "ticker": item.get("ticker"),
            "relative_to_spy_20d": item.get("relative_to_spy_20d"),
        }
        for item in sectors
    ]


def pct(value, total):
    return 0 if not total else value / total


def round_money(value):
    return round(float(value or 0), 2)


def round_pct(value):
    return round(float(value or 0), 4)


def format_core_etf_sleeve_report(report):
    lines = [
        "# Core ETF Sleeve",
        "",
        f"Regime: {report['regime']}",
        f"Status: {report['status']}",
        f"Target Sleeve: {format_pct(report['target_sleeve_pct'])} / ${report['target_sleeve_value']:.2f}",
        f"Current Sleeve: {format_pct(report['current_sleeve_pct'])} / ${report['current_sleeve_value']:.2f}",
        f"Drift: ${report['drift_value']:.2f} ({format_pct(report['drift_pct'])})",
        "",
        "## Desired Allocation",
    ]

    for row in report["desired_allocations"]:
        lines.append(
            f"- {row['symbol']}: target {format_pct(row['target_weight'])}, "
            f"${row['target_value']:.2f}; current ${row['current_value']:.2f}; "
            f"difference ${row['difference']:.2f}; "
            f"approx shares {row['suggested_shares']}"
        )

    lines.extend(["", "## Actions"])
    lines.extend([f"- {item}" for item in report["actions"]] or ["- None."])
    return "\n".join(lines) + "\n"


def format_pct(value):
    return f"{float(value or 0) * 100:.1f}%"


def format_money_plain(value):
    return f"{float(value or 0):.2f}"
