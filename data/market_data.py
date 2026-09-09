import yfinance as yf

from data.tiingo_data import fetch_daily_equity_prices, is_tiingo_configured

MACRO_TICKERS = {
    "sp500": "^GSPC",
    "nasdaq": "^IXIC",
    "dow": "^DJI",
    "russell_2000": "^RUT",
    "vix": "^VIX",
    "dxy": "DX-Y.NYB",
    "ten_year_treasury": "^TNX",
    "two_year_treasury": "^IRX",
    "gold": "GC=F",
    "oil": "CL=F",
    "copper": "HG=F",
    "bitcoin": "BTC-USD",
}

MACRO_PROXY_TICKERS = {
    "sp500": "SPY",
    "nasdaq": "QQQ",
    "dow": "DIA",
    "russell_2000": "IWM",
    "vix": "VIXY",
    "dxy": "UUP",
    "ten_year_treasury": "TBT",
    "two_year_treasury": "SHY",
    "gold": "GLD",
    "oil": "USO",
    "copper": "CPER",
    "bitcoin": "BITO",
}

SECTOR_ETFS = {
    "Technology": "XLK",
    "Financials": "XLF",
    "Healthcare": "XLV",
    "Industrials": "XLI",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
}
YFINANCE_TIMEOUT_SECONDS = 5


def dataframe_preview(frame, max_rows=8):
    if frame is None or frame.empty:
        return "No data returned."
    return frame.head(max_rows).to_string()


def get_company_data(ticker):
    ticker = ticker.upper()
    stock = yf.Ticker(ticker)

    try:
        info = stock.info or {}
    except Exception as exc:
        return {
            "ticker": ticker,
            "error": f"Could not fetch market data from Yahoo Finance: {exc}",
        }

    try:
        financials = stock.financials
    except Exception as exc:
        financials = f"Could not fetch income statement: {exc}"

    try:
        balance_sheet = stock.balance_sheet
    except Exception as exc:
        balance_sheet = f"Could not fetch balance sheet: {exc}"

    try:
        cashflow = stock.cashflow
    except Exception as exc:
        cashflow = f"Could not fetch cash flow statement: {exc}"

    data = {
        "ticker": ticker,
        "company_name": info.get("longName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "current_price": info.get("currentPrice"),
        "market_cap": info.get("marketCap"),
        "trailing_pe": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "profit_margin": info.get("profitMargins"),
        "return_on_equity": info.get("returnOnEquity"),
        "revenue_growth": info.get("revenueGrowth"),
        "total_cash": info.get("totalCash"),
        "total_debt": info.get("totalDebt"),
        "free_cash_flow": info.get("freeCashflow"),
        "financials": dataframe_preview(financials) if not isinstance(financials, str) else financials,
        "balance_sheet": dataframe_preview(balance_sheet) if not isinstance(balance_sheet, str) else balance_sheet,
        "cashflow": dataframe_preview(cashflow) if not isinstance(cashflow, str) else cashflow,
    }

    return data


def get_price_history(ticker, period="3mo"):
    fallback = get_tiingo_price_history(ticker, period)
    if fallback:
        return fallback

    try:
        history = yf.Ticker(ticker).history(
            period=period,
            auto_adjust=True,
            timeout=YFINANCE_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        history = None
        yahoo_error = str(exc)
    else:
        yahoo_error = None

    if history is None or history.empty:
        return {"ticker": ticker, "error": yahoo_error or "No price history returned."}

    close = history["Close"].dropna()
    if close.empty:
        return {"ticker": ticker, "error": "No close prices returned."}

    latest = float(close.iloc[-1])
    previous = float(close.iloc[-2]) if len(close) > 1 else latest
    first = float(close.iloc[0])
    twenty_day_start = float(close.iloc[-21]) if len(close) >= 21 else first

    return {
        "ticker": ticker,
        "latest": latest,
        "one_day_change_pct": pct_change(latest, previous),
        "period_change_pct": pct_change(latest, first),
        "twenty_day_change_pct": pct_change(latest, twenty_day_start),
        "latest_date": str(close.index[-1].date()),
        "rows": len(close),
        "provider": "Yahoo Finance / yfinance",
    }


def get_ohlcv_history(ticker, period="6mo"):
    fallback = get_tiingo_ohlcv_history(ticker, period)
    if fallback:
        return fallback

    try:
        history = yf.Ticker(ticker).history(
            period=period,
            auto_adjust=True,
            timeout=YFINANCE_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        history = None
        yahoo_error = str(exc)
    else:
        yahoo_error = None

    if history is None or history.empty:
        return {"ticker": ticker, "error": yahoo_error or "No OHLCV history returned.", "rows": []}

    rows = []
    for index, row in history.dropna(subset=["Close"]).iterrows():
        rows.append({
            "date": str(index.date()),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": int(row["Volume"]) if not row.isna()["Volume"] else None,
        })

    return {
        "ticker": ticker.upper(),
        "period": period,
        "provider": "Yahoo Finance / yfinance",
        "rows": rows,
    }


def get_tiingo_price_history(ticker, period):
    ohlcv = get_tiingo_ohlcv_history(ticker, period)
    if not ohlcv:
        return None
    rows = ohlcv.get("rows", [])
    closes = [row["close"] for row in rows if row.get("close") is not None]
    if not closes:
        return None
    latest = closes[-1]
    previous = closes[-2] if len(closes) > 1 else latest
    first = closes[0]
    twenty_day_start = closes[-21] if len(closes) >= 21 else first
    return {
        "ticker": ticker.upper(),
        "latest": latest,
        "one_day_change_pct": pct_change(latest, previous),
        "period_change_pct": pct_change(latest, first),
        "twenty_day_change_pct": pct_change(latest, twenty_day_start),
        "latest_date": rows[-1]["date"],
        "rows": len(rows),
        "provider": "Tiingo",
    }


def get_tiingo_ohlcv_history(ticker, period):
    if not is_tiingo_configured() or not is_tiingo_symbol_candidate(ticker):
        return None
    response = fetch_daily_equity_prices(ticker, period=period)
    if response.get("status") != "ok" or not response.get("rows"):
        return None
    return {
        "ticker": ticker.upper(),
        "period": period,
        "provider": "Tiingo",
        "rows": response["rows"],
    }


def is_tiingo_symbol_candidate(ticker):
    ticker = str(ticker or "").upper().strip()
    if not ticker:
        return False
    if ticker.startswith("^") or "=" in ticker:
        return False
    return True


def get_macro_market_snapshot():
    return {
        name: get_macro_price_history(name, ticker)
        for name, ticker in MACRO_TICKERS.items()
    }


def get_macro_price_history(name, ticker):
    proxy_ticker = MACRO_PROXY_TICKERS.get(name)
    if proxy_ticker:
        proxy = get_tiingo_price_history(proxy_ticker, "3mo")
        if proxy:
            proxy["proxy_for"] = ticker
            proxy["proxy_ticker"] = proxy_ticker
            proxy["proxy_note"] = f"Using {proxy_ticker} as market proxy for {ticker}."
            return proxy
        return {
            "ticker": proxy_ticker,
            "proxy_for": ticker,
            "proxy_ticker": proxy_ticker,
            "error": f"Macro proxy {proxy_ticker} unavailable from Tiingo.",
            "provider": "Tiingo",
        }

    data = get_price_history(ticker)
    if not data.get("error"):
        return data

    if not proxy_ticker:
        return data

    proxy = get_price_history(proxy_ticker)
    if proxy.get("error"):
        proxy["primary_error"] = data.get("error")
        proxy["proxy_for"] = ticker
        proxy["proxy_ticker"] = proxy_ticker
        return proxy

    proxy["proxy_for"] = ticker
    proxy["proxy_ticker"] = proxy_ticker
    proxy["proxy_note"] = f"Using {proxy_ticker} as market proxy because {ticker} was unavailable."
    return proxy


def get_sector_rotation_snapshot():
    spy = get_price_history("SPY")
    sectors = []

    for sector, ticker in SECTOR_ETFS.items():
        data = get_price_history(ticker)
        relative_to_spy = None

        if "error" not in data and "error" not in spy:
            relative_to_spy = data["twenty_day_change_pct"] - spy["twenty_day_change_pct"]

        sectors.append({
            "sector": sector,
            "ticker": ticker,
            "latest": data.get("latest"),
            "one_day_change_pct": data.get("one_day_change_pct"),
            "twenty_day_change_pct": data.get("twenty_day_change_pct"),
            "relative_to_spy_20d": relative_to_spy,
            "error": data.get("error"),
        })

    sectors.sort(
        key=lambda item: item["relative_to_spy_20d"] if item["relative_to_spy_20d"] is not None else -999,
        reverse=True,
    )

    return {
        "benchmark": spy,
        "sectors": sectors,
    }


def pct_change(latest, previous):
    if previous == 0:
        return None
    return ((latest - previous) / previous) * 100


if __name__ == "__main__":
    ticker = input("Enter ticker: ")
    data = get_company_data(ticker)

    for key, value in data.items():
        print(f"\n--- {key} ---")
        print(value)
