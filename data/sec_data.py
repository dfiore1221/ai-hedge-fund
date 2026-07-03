import requests

SEC_HEADERS = {
    "User-Agent": "AI Hedge Fund Research App david@example.com",
    "Accept-Encoding": "gzip, deflate",
}


def get_company_tickers():
    url = "https://www.sec.gov/files/company_tickers.json"
    response = requests.get(url, headers=SEC_HEADERS)
    response.raise_for_status()
    return response.json()


def get_cik_for_ticker(ticker):
    ticker = ticker.upper()
    companies = get_company_tickers()

    for _, company in companies.items():
        if company["ticker"].upper() == ticker:
            return str(company["cik_str"]).zfill(10)

    return None


def get_recent_filings(ticker):
    cik = get_cik_for_ticker(ticker)

    if cik is None:
        return {"error": f"Could not find CIK for ticker {ticker}"}

    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    response = requests.get(url, headers=SEC_HEADERS)
    response.raise_for_status()
    data = response.json()

    filings = data["filings"]["recent"]

    results = []
    for i in range(min(10, len(filings["form"]))):
        results.append({
            "form": filings["form"][i],
            "filing_date": filings["filingDate"][i],
            "report_date": filings["reportDate"][i],
            "accession_number": filings["accessionNumber"][i],
            "primary_document": filings["primaryDocument"][i],
        })

    return {
        "ticker": ticker.upper(),
        "cik": cik,
        "company_name": data.get("name"),
        "recent_filings": results,
    }


if __name__ == "__main__":
    ticker = input("Enter ticker: ")
    data = get_recent_filings(ticker)

    print(data)