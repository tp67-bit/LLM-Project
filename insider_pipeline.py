"""
Insider Trading Analysis Pipeline
==================================
Pulls SEC EDGAR Form 4 filings + Yahoo Finance price/market-cap data to:
  1. Separate CEO buys vs. sells
  2. Compute forward price outlook (3M / 1Y / 2Y) relative to filing date
  3. Compute allocation of insider stock ownership within the company (% of shares outstanding)
  4. Flag "one-off" large trades (single transaction above a $ threshold)
  5. Apply a simple rule-based SIGNAL and filter universe to market cap <= $2B

Data sources:
  - SEC EDGAR Form 4 XML filings (issuer-level browse-edgar feed + raw XML transaction data)
  - Yahoo Finance (yfinance) for price history and market cap

Usage:
  python insider_pipeline.py --tickers AAPL,PLUG,SOFI ... (or supply a watchlist file)
"""

import re
import time
import json
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta

HEADERS = {"User-Agent": "Thomas Priest (Fordham Gabelli) research-project@fordham.edu"}

def _get_with_retry(url, params=None, retries=3, timeout=20):
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))

SEC_BROWSE = "https://www.sec.gov/cgi-bin/browse-edgar"
MARKET_CAP_CEILING = 2_000_000_000  # $2B universe filter
ONE_OFF_USD_THRESHOLD = 1_000_000   # single-transaction $ threshold to flag as a notable "one-off"
LOOKBACK_DAYS = 730                 # ~2 years of Form 4 history to scan per ticker

CEO_TITLE_PATTERNS = re.compile(r"\b(chief executive officer|ceo)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# 1. EDGAR helpers
# ---------------------------------------------------------------------------

def get_cik_for_ticker(ticker: str) -> str | None:
    """Resolve ticker -> zero-padded CIK using SEC's company tickers JSON."""
    url = "https://www.sec.gov/files/company_tickers.json"
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    data = r.json()
    for _, row in data.items():
        if row["ticker"].upper() == ticker.upper():
            return str(row["cik_str"]).zfill(10)
    return None


def list_form4_filings(cik: str, count: int = 20) -> list[dict]:
    """List recent Form 4 filing index pages for a given CIK."""
    params = {
        "action": "getcompany",
        "CIK": cik,
        "type": "4",
        "dateb": "",
        "owner": "include",
        "count": count,
        "output": "atom",
    }
    r = _get_with_retry(SEC_BROWSE, params=params)
    text = r.text
    entries = []
    for block in re.findall(r"<entry>(.*?)</entry>", text, re.S):
        acc = re.search(r"<accession-number>(.*?)</accession-number>", block)
        fdate = re.search(r"<filing-date>(.*?)</filing-date>", block)
        href = re.search(r'filing-href>(.*?)</filing-href>', block)
        if acc and fdate and href:
            entries.append({"accession": acc.group(1), "filing_date": fdate.group(1), "index_url": href.group(1)})
    return entries


def get_form4_xml_url(index_url: str) -> str | None:
    try:
        r = _get_with_retry(index_url)
    except Exception:
        return None
    files = re.findall(r'href="([^"]+\.xml)"', r.text)
    # prefer the non-xsl-rendered raw xml (skip the xslF345X* rendered one)
    raw = [f for f in files if "xslF345" not in f]
    chosen = raw[0] if raw else (files[0] if files else None)
    if chosen and chosen.startswith("/"):
        return "https://www.sec.gov" + chosen
    return chosen


def parse_form4_xml(xml_text: str) -> list[dict]:
    """Extract transaction-level rows from a Form 4 XML document."""
    rows = []
    issuer_ticker = re.search(r"<issuerTradingSymbol>(.*?)</issuerTradingSymbol>", xml_text)
    issuer_name = re.search(r"<issuerName>(.*?)</issuerName>", xml_text)
    owner_name = re.search(r"<rptOwnerName>(.*?)</rptOwnerName>", xml_text)
    officer_title = re.search(r"<officerTitle>(.*?)</officerTitle>", xml_text)
    is_officer = re.search(r"<isOfficer>(.*?)</isOfficer>", xml_text)
    is_director = re.search(r"<isDirector>(.*?)</isDirector>", xml_text)

    for tx_block in re.findall(r"<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>", xml_text, re.S):
        code = re.search(r"<transactionCode>(.*?)</transactionCode>", tx_block)
        shares = re.search(r"<transactionShares>\s*<value>(.*?)</value>", tx_block, re.S)
        price = re.search(r"<transactionPricePerShare>\s*<value>(.*?)</value>", tx_block, re.S)
        ad_code = re.search(r"<transactionAcquiredDisposedCode>\s*<value>(.*?)</value>", tx_block, re.S)
        tx_date = re.search(r"<transactionDate>\s*<value>(.*?)</value>", tx_block, re.S)
        shares_after = re.search(r"<sharesOwnedFollowingTransaction>\s*<value>(.*?)</value>", tx_block, re.S)

        rows.append({
            "issuer_ticker": issuer_ticker.group(1) if issuer_ticker else None,
            "issuer_name": issuer_name.group(1) if issuer_name else None,
            "owner_name": owner_name.group(1) if owner_name else None,
            "officer_title": officer_title.group(1) if officer_title else None,
            "is_officer": (is_officer.group(1) == "true") if is_officer else False,
            "is_director": (is_director.group(1) == "true") if is_director else False,
            "transaction_code": code.group(1) if code else None,
            "shares": float(shares.group(1)) if shares else np.nan,
            "price": float(price.group(1)) if price else np.nan,
            "acquired_disposed": ad_code.group(1) if ad_code else None,
            "transaction_date": tx_date.group(1) if tx_date else None,
            "shares_owned_after": float(shares_after.group(1)) if shares_after else np.nan,
        })
    return rows


# ---------------------------------------------------------------------------
# 2. Yahoo Finance helpers
# ---------------------------------------------------------------------------

def get_market_cap(ticker: str) -> float | None:
    try:
        info = yf.Ticker(ticker).fast_info
        return info.get("marketCap") or info.get("market_cap")
    except Exception:
        return None


def get_price_outlook(ticker: str, tx_date: str) -> dict:
    """Return the closing price on filing date and forward returns at 3M/1Y/2Y."""
    try:
        base_date = pd.to_datetime(tx_date)
        end_date = min(base_date + timedelta(days=760), pd.Timestamp.today())
        hist = yf.Ticker(ticker).history(start=base_date - timedelta(days=5), end=end_date)
        if hist.empty:
            return {}
        hist.index = hist.index.tz_localize(None)
        base_price = hist.iloc[(hist.index - base_date).map(lambda x: abs(x.days)).argmin()]["Close"]

        def price_at(offset_days):
            target = base_date + timedelta(days=offset_days)
            if target > pd.Timestamp.today():
                return None
            sub = hist[hist.index <= target]
            return sub.iloc[-1]["Close"] if not sub.empty else None

        p3m, p1y, p2y = price_at(91), price_at(365), price_at(730)
        return {
            "base_price": base_price,
            "price_3m": p3m,
            "price_1y": p1y,
            "price_2y": p2y,
            "return_3m_pct": (p3m / base_price - 1) * 100 if p3m else None,
            "return_1y_pct": (p1y / base_price - 1) * 100 if p1y else None,
            "return_2y_pct": (p2y / base_price - 1) * 100 if p2y else None,
        }
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# 3. Signal logic (rule-based, TBD -> defined here)
# ---------------------------------------------------------------------------

def compute_signal(row: dict) -> str:
    """
    Simple rule-based signal:
      STRONG_BUY  : CEO open-market purchase (code 'P') >= $1M, market cap <= $2B
      NOTABLE_BUY : CEO purchase < $1M but still open-market buy
      SELL        : CEO disposition (code 'S')
      NEUTRAL     : anything else (grants, exercises, gifts)
    """
    if row.get("transaction_code") == "P" and row.get("acquired_disposed") == "A":
        value = (row.get("shares") or 0) * (row.get("price") or 0)
        return "STRONG_BUY" if value >= ONE_OFF_USD_THRESHOLD else "NOTABLE_BUY"
    if row.get("transaction_code") == "S" and row.get("acquired_disposed") == "D":
        return "SELL"
    return "NEUTRAL"


# ---------------------------------------------------------------------------
# 4. Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(tickers: list[str]) -> pd.DataFrame:
    all_rows = []
    for ticker in tickers:
        cik = get_cik_for_ticker(ticker)
        if not cik:
            print(f"[skip] No CIK found for {ticker}")
            continue

        mcap = get_market_cap(ticker)
        if mcap is not None and mcap > MARKET_CAP_CEILING:
            print(f"[skip] {ticker} market cap ${mcap:,.0f} exceeds $2B ceiling")
            continue

        filings = list_form4_filings(cik, count=40)
        cutoff = datetime.today() - timedelta(days=LOOKBACK_DAYS)
        filings = [f for f in filings if datetime.strptime(f["filing_date"], "%Y-%m-%d") >= cutoff]

        for f in filings:
            xml_url = get_form4_xml_url(f["index_url"])
            if not xml_url:
                continue
            try:
                xml_text = _get_with_retry(xml_url).text
            except Exception:
                continue
            tx_rows = parse_form4_xml(xml_text)
            for row in tx_rows:
                row["ticker"] = ticker
                row["market_cap"] = mcap
                row["filing_date"] = f["filing_date"]
                row["is_ceo"] = bool(row["officer_title"] and CEO_TITLE_PATTERNS.search(row["officer_title"]))
                row["signal"] = compute_signal(row)
                row["trade_value_usd"] = (row.get("shares") or 0) * (row.get("price") or 0)
                row["is_one_off"] = row["trade_value_usd"] >= ONE_OFF_USD_THRESHOLD
                if row["shares_owned_after"] and row["shares"]:
                    prior_shares = row["shares_owned_after"] - row["shares"] if row["acquired_disposed"] == "A" else row["shares_owned_after"] + row["shares"]
                    row["allocation_pct_change"] = (row["shares"] / prior_shares * 100) if prior_shares else None
                else:
                    row["allocation_pct_change"] = None
                all_rows.append(row)
            time.sleep(0.15)  # be polite to SEC servers

    df = pd.DataFrame(all_rows)
    if df.empty:
        return df

    outlook_cache = {}
    for _, r in df.iterrows():
        key = (r["ticker"], r["transaction_date"])
        if key not in outlook_cache and r["transaction_date"]:
            outlook_cache[key] = get_price_outlook(r["ticker"], r["transaction_date"])
    outlook_df = pd.DataFrame([
        {"ticker": k[0], "transaction_date": k[1], **v} for k, v in outlook_cache.items()
    ])
    df = df.merge(outlook_df, on=["ticker", "transaction_date"], how="left")
    return df


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", type=str, default="PLUG,SOFI,IONQ,RKLB,CLOV",
                         help="Comma-separated list of tickers to screen (should be <= $2B market cap)")
    args = parser.parse_args()
    tickers = [t.strip().upper() for t in args.tickers.split(",")]

    result = run_pipeline(tickers)
    if result.empty:
        print("No data returned.")
    else:
        result.to_csv("insider_trades.csv", index=False)
        print(f"Saved {len(result)} rows to insider_trades.csv")
        print(result.head(10).to_string())
