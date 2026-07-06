import yfinance as yf


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


if __name__ == "__main__":
    ticker = input("Enter ticker: ")
    data = get_company_data(ticker)

    for key, value in data.items():
        print(f"\n--- {key} ---")
        print(value)
