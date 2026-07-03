import yfinance as yf


def get_company_data(ticker):
    ticker = ticker.upper()
    stock = yf.Ticker(ticker)

    info = stock.info
    financials = stock.financials
    balance_sheet = stock.balance_sheet
    cashflow = stock.cashflow

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
        "financials": financials.to_string(),
        "balance_sheet": balance_sheet.to_string(),
        "cashflow": cashflow.to_string(),
    }

    return data


if __name__ == "__main__":
    ticker = input("Enter ticker: ")
    data = get_company_data(ticker)

    for key, value in data.items():
        print(f"\n--- {key} ---")
        print(value)