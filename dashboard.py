"""
Insider Trading Feed Dashboard — Streamlit App
================================================
Modes:
  1. MARKET SCAN — auto-discovers Form 4 filings market-wide for either a
     rolling recent window OR a specific DATE RANGE you pick (up to years
     back), filtered to companies under a market-cap ceiling.
  2. SINGLE TICKER SEARCH — type in one ticker to pull its full insider
     trading history directly, no date/market scan needed.

Both modes show CEO flags, buy/sell signal, allocation %, one-off flags,
and stock performance (%) since each transaction using Yahoo Finance data,
with a lookback window of up to 5 years.

Run with:
    streamlit run dashboard.py
"""

import re
import time
import requests
import pandas as pd
import numpy as np
import streamlit as st
import plotly.express as px
import yfinance as yf
from datetime import datetime, timedelta, date

st.set_page_config(page_title="Insider Trading Feed", layout="wide", page_icon="📈")

HEADERS = {"User-Agent": "Thomas Priest (Fordham Gabelli) research-project@fordham.edu"}
CEO_TITLE_PATTERNS = re.compile(r"\b(chief executive officer|ceo)\b", re.IGNORECASE)


def _get_with_retry(url, params=None, retries=3, timeout=20):
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(1.0 * (attempt + 1))


# ---------------------------------------------------------------------------
# 1. CIK <-> Ticker map
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False, ttl=86400)
def get_cik_ticker_map():
    r = _get_with_retry("https://www.sec.gov/files/company_tickers.json")
    data = r.json()
    return {str(v["cik_str"]): v["ticker"] for v in data.values()}


@st.cache_data(show_spinner=False, ttl=86400)
def get_ticker_cik_map():
    r = _get_with_retry("https://www.sec.gov/files/company_tickers.json")
    data = r.json()
    return {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in data.values()}


# ---------------------------------------------------------------------------
# 2. Daily index — fetch Form 4 filings for ANY specific date (recent or historical)
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False, ttl=3600)
def get_daily_form4_index(target_date: date):
    """Returns list of {company, cik, filename} for Form 4 filings on an exact date.
    Works for any date SEC has published an index for (recent or years back).
    Returns None if no index exists for that date (weekend/holiday/not yet published)."""
    ymd = target_date.strftime("%Y%m%d")
    qtr = (target_date.month - 1) // 3 + 1
    url = f"https://www.sec.gov/Archives/edgar/daily-index/{target_date.year}/QTR{qtr}/form.{ymd}.idx"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return None
    except Exception:
        return None
    lines = r.text.split("\n")
    entries = []
    for line in lines:
        if not line.startswith("4 "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        filename = parts[-1]
        cik = parts[-3]
        company = " ".join(parts[1:-3])
        entries.append({"company": company, "cik": cik, "filename": filename})
    return entries


# ---------------------------------------------------------------------------
# 3. Filing content fetch + parse
# ---------------------------------------------------------------------------
def fetch_and_parse_filing(cik: str, filename: str):
    url = f"https://www.sec.gov/Archives/{filename}"
    try:
        r = _get_with_retry(url, timeout=15)
        xml_text = r.text
    except Exception:
        return []

    rows = []
    issuer_ticker = re.search(r"<issuerTradingSymbol>(.*?)</issuerTradingSymbol>", xml_text)
    issuer_name = re.search(r"<issuerName>(.*?)</issuerName>", xml_text)
    issuer_cik = re.search(r"<issuerCik>(.*?)</issuerCik>", xml_text)
    owner_name = re.search(r"<rptOwnerName>(.*?)</rptOwnerName>", xml_text)
    officer_title = re.search(r"<officerTitle>(.*?)</officerTitle>", xml_text)

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
            "issuer_cik": issuer_cik.group(1) if issuer_cik else cik,
            "owner_name": owner_name.group(1) if owner_name else None,
            "officer_title": officer_title.group(1) if officer_title else None,
            "transaction_code": code.group(1) if code else None,
            "shares": float(shares.group(1)) if shares else np.nan,
            "price": float(price.group(1)) if price else np.nan,
            "acquired_disposed": ad_code.group(1) if ad_code else None,
            "transaction_date": tx_date.group(1) if tx_date else None,
            "shares_owned_after": float(shares_after.group(1)) if shares_after else np.nan,
        })
    return rows


# ---------------------------------------------------------------------------
# 3b. Single-ticker path — pull a company's own Form 4 filing history directly
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False, ttl=1800)
def list_form4_filings_for_ticker(cik: str, count: int = 40):
    params = {"action": "getcompany", "CIK": cik, "type": "4", "dateb": "",
              "owner": "include", "count": count, "output": "atom"}
    r = _get_with_retry("https://www.sec.gov/cgi-bin/browse-edgar", params=params)
    text = r.text
    entries = []
    for block in re.findall(r"<entry>(.*?)</entry>", text, re.S):
        acc = re.search(r"<accession-number>(.*?)</accession-number>", block)
        fdate = re.search(r"<filing-date>(.*?)</filing-date>", block)
        href = re.search(r'filing-href>(.*?)</filing-href>', block)
        if acc and fdate and href:
            entries.append({"accession": acc.group(1), "filing_date": fdate.group(1), "index_url": href.group(1)})
    return entries


def get_form4_xml_url(index_url: str):
    try:
        r = _get_with_retry(index_url)
    except Exception:
        return None
    files = re.findall(r'href="([^"]+\.xml)"', r.text)
    raw = [f for f in files if "xslF345" not in f]
    chosen = raw[0] if raw else (files[0] if files else None)
    if chosen and chosen.startswith("/"):
        return "https://www.sec.gov" + chosen
    return chosen


# ---------------------------------------------------------------------------
# 4. Yahoo Finance helpers
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False, ttl=1800)
def get_market_cap(ticker: str):
    try:
        info = yf.Ticker(ticker).fast_info
        return info.get("marketCap") or info.get("market_cap")
    except Exception:
        return None


@st.cache_data(show_spinner=False, ttl=1800)
def get_price_performance(ticker: str, tx_date: str, max_lookback_years: int = 5):
    try:
        base_date = pd.to_datetime(tx_date)
        today = pd.Timestamp.today()
        end_date = min(base_date + timedelta(days=max_lookback_years * 365 + 10), today)
        hist = yf.Ticker(ticker).history(start=base_date - timedelta(days=7), end=end_date + timedelta(days=1))
        if hist.empty:
            return {}
        hist.index = hist.index.tz_localize(None)

        base_idx = (hist.index - base_date).map(lambda x: abs(x.days)).argmin()
        base_price = hist.iloc[base_idx]["Close"]
        base_actual_date = hist.index[base_idx]

        current_price = hist.iloc[-1]["Close"]
        days_since = (today - base_actual_date).days

        def price_at(offset_days):
            target = base_date + timedelta(days=offset_days)
            if target > today:
                return None
            sub = hist[hist.index <= target]
            return sub.iloc[-1]["Close"] if not sub.empty else None

        p3m, p1y, p2y, p5y = price_at(91), price_at(365), price_at(730), price_at(1825)

        return {
            "base_price": base_price,
            "current_price": current_price,
            "days_since_transaction": days_since,
            "return_since_transaction_pct": (current_price / base_price - 1) * 100 if base_price else None,
            "return_3m_pct": (p3m / base_price - 1) * 100 if p3m and base_price else None,
            "return_1y_pct": (p1y / base_price - 1) * 100 if p1y and base_price else None,
            "return_2y_pct": (p2y / base_price - 1) * 100 if p2y and base_price else None,
            "return_5y_pct": (p5y / base_price - 1) * 100 if p5y and base_price else None,
        }
    except Exception:
        return {}


def compute_signal(row, one_off_threshold):
    if row.get("transaction_code") == "P" and row.get("acquired_disposed") == "A":
        value = (row.get("shares") or 0) * (row.get("price") or 0)
        return "STRONG_BUY" if value >= one_off_threshold else "NOTABLE_BUY"
    if row.get("transaction_code") == "S" and row.get("acquired_disposed") == "D":
        return "SELL"
    return "NEUTRAL"


def enrich_rows(tx_rows, ticker, mcap, one_off_threshold):
    out = []
    for row in tx_rows:
        row = dict(row)
        row["ticker"] = row.get("issuer_ticker") or ticker
        row["market_cap"] = mcap
        row["is_ceo"] = bool(row.get("officer_title") and CEO_TITLE_PATTERNS.search(row["officer_title"]))
        row["signal"] = compute_signal(row, one_off_threshold)
        row["trade_value_usd"] = (row.get("shares") or 0) * (row.get("price") or 0)
        row["is_one_off"] = row["trade_value_usd"] >= one_off_threshold
        if row.get("shares_owned_after") and row.get("shares"):
            prior = row["shares_owned_after"] - row["shares"] if row["acquired_disposed"] == "A" else row["shares_owned_after"] + row["shares"]
            row["allocation_pct_change"] = (row["shares"] / prior * 100) if prior else None
        else:
            row["allocation_pct_change"] = None
        out.append(row)
    return out


def attach_price_performance(df, lookback_years):
    if df.empty:
        return df
    perf_cache = {}
    for _, r in df.iterrows():
        key = (r["ticker"], r["transaction_date"])
        if key not in perf_cache and r["transaction_date"]:
            perf_cache[key] = get_price_performance(r["ticker"], r["transaction_date"], lookback_years)
    perf_df = pd.DataFrame([{"ticker": k[0], "transaction_date": k[1], **v} for k, v in perf_cache.items()])
    return df.merge(perf_df, on=["ticker", "transaction_date"], how="left")


# ---------------------------------------------------------------------------
# 5a. MARKET SCAN pipeline — supports a DATE RANGE or rolling recent window
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False, ttl=1800)
def run_market_scan_for_date_range(start_date: date, end_date: date, cap_ceiling: float,
                                    one_off_threshold: float, ceo_only: bool, max_filings: int,
                                    lookback_years: int):
    """Scan Form 4 filings across an entire date range (can span years back)."""
    cik_ticker_map = get_cik_ticker_map()

    all_entries = []
    d = start_date
    days_with_data = 0
    days_without_data = []
    while d <= end_date:
        if d.weekday() < 5:  # skip weekends
            entries = get_daily_form4_index(d)
            if entries is not None:
                all_entries.extend(entries)
                days_with_data += 1
            else:
                days_without_data.append(d)
        d += timedelta(days=1)

    seen = set()
    unique_entries = []
    for e in all_entries:
        if e["filename"] not in seen:
            seen.add(e["filename"])
            unique_entries.append(e)
    unique_entries = unique_entries[:max_filings]

    all_rows = []
    mcap_cache = {}
    for entry in unique_entries:
        cik = entry["cik"]
        ticker = cik_ticker_map.get(str(int(cik))) if cik.isdigit() else None
        if not ticker:
            continue
        if ticker not in mcap_cache:
            mcap_cache[ticker] = get_market_cap(ticker)
        mcap = mcap_cache[ticker]
        if mcap is None or mcap > cap_ceiling:
            continue
        tx_rows = fetch_and_parse_filing(cik, entry["filename"])
        enriched = enrich_rows(tx_rows, ticker, mcap, one_off_threshold)
        if ceo_only:
            enriched = [r for r in enriched if r["is_ceo"]]
        all_rows.extend(enriched)
        time.sleep(0.05)

    df = pd.DataFrame(all_rows)
    df = attach_price_performance(df, lookback_years)
    return df, len(unique_entries), days_with_data


@st.cache_data(show_spinner=False, ttl=1800)
def run_market_scan_recent(days_back: int, cap_ceiling: float, one_off_threshold: float,
                            ceo_only: bool, max_filings: int, lookback_years: int):
    """Scan a rolling window of the N most recent trading days."""
    cik_ticker_map = get_cik_ticker_map()
    all_entries = []
    d = datetime.today().date() - timedelta(days=1)
    days_scanned = 0
    attempts = 0
    while days_scanned < days_back and attempts < days_back + 10:
        attempts += 1
        if d.weekday() < 5:
            entries = get_daily_form4_index(d)
            if entries is not None:
                all_entries.extend(entries)
                days_scanned += 1
        d -= timedelta(days=1)

    seen = set()
    unique_entries = []
    for e in all_entries:
        if e["filename"] not in seen:
            seen.add(e["filename"])
            unique_entries.append(e)
    unique_entries = unique_entries[:max_filings]

    all_rows = []
    mcap_cache = {}
    for entry in unique_entries:
        cik = entry["cik"]
        ticker = cik_ticker_map.get(str(int(cik))) if cik.isdigit() else None
        if not ticker:
            continue
        if ticker not in mcap_cache:
            mcap_cache[ticker] = get_market_cap(ticker)
        mcap = mcap_cache[ticker]
        if mcap is None or mcap > cap_ceiling:
            continue
        tx_rows = fetch_and_parse_filing(cik, entry["filename"])
        enriched = enrich_rows(tx_rows, ticker, mcap, one_off_threshold)
        if ceo_only:
            enriched = [r for r in enriched if r["is_ceo"]]
        all_rows.extend(enriched)
        time.sleep(0.05)

    df = pd.DataFrame(all_rows)
    df = attach_price_performance(df, lookback_years)
    return df, len(unique_entries)


# ---------------------------------------------------------------------------
# 5b. SINGLE TICKER pipeline
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False, ttl=1800)
def run_single_ticker_search(ticker: str, one_off_threshold: float, lookback_years: int, history_count: int = 40):
    ticker = ticker.strip().upper()
    ticker_cik_map = get_ticker_cik_map()
    cik = ticker_cik_map.get(ticker)
    if not cik:
        return pd.DataFrame(), None, "Ticker not found in SEC's company list."

    mcap = get_market_cap(ticker)
    filings = list_form4_filings_for_ticker(cik, count=history_count)

    all_rows = []
    for f in filings:
        xml_url = get_form4_xml_url(f["index_url"])
        if not xml_url:
            continue
        try:
            xml_text = _get_with_retry(xml_url).text
        except Exception:
            continue
        tx_rows = fetch_and_parse_filing_from_text(xml_text, cik)
        enriched = enrich_rows(tx_rows, ticker, mcap, one_off_threshold)
        all_rows.extend(enriched)
        time.sleep(0.1)

    df = pd.DataFrame(all_rows)
    df = attach_price_performance(df, lookback_years)
    return df, mcap, None


def fetch_and_parse_filing_from_text(xml_text, cik):
    rows = []
    issuer_ticker = re.search(r"<issuerTradingSymbol>(.*?)</issuerTradingSymbol>", xml_text)
    issuer_name = re.search(r"<issuerName>(.*?)</issuerName>", xml_text)
    owner_name = re.search(r"<rptOwnerName>(.*?)</rptOwnerName>", xml_text)
    officer_title = re.search(r"<officerTitle>(.*?)</officerTitle>", xml_text)

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
            "issuer_cik": cik,
            "owner_name": owner_name.group(1) if owner_name else None,
            "officer_title": officer_title.group(1) if officer_title else None,
            "transaction_code": code.group(1) if code else None,
            "shares": float(shares.group(1)) if shares else np.nan,
            "price": float(price.group(1)) if price else np.nan,
            "acquired_disposed": ad_code.group(1) if ad_code else None,
            "transaction_date": tx_date.group(1) if tx_date else None,
            "shares_owned_after": float(shares_after.group(1)) if shares_after else np.nan,
        })
    return rows


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
st.title("📈 Insider Trading Feed — Small-Cap Auto-Discovery")
st.caption("Scan the entire market for a date range, a rolling recent window, or search one ticker directly.")

# --- Top search bar for single-ticker lookup (fixed alignment via matching labels) ---
search_col1, search_col2 = st.columns([4, 1])
with search_col1:
    ticker_search = st.text_input(
        "🔍 Search a single ticker (bypasses market scan)",
        placeholder="e.g. BBAI",
        label_visibility="visible",
        key="ticker_search_input",
    )
with search_col2:
    st.markdown(
        "<div style='height: 28px'></div>",
        unsafe_allow_html=True,
    )  # spacer to match the text_input's label height so the button lines up
    search_btn = st.button("Search Ticker", type="primary", use_container_width=True)

st.divider()

with st.sidebar:
    st.header("Market Scan Settings")
    scan_mode = st.radio("Scan mode", ["Date range", "Rolling recent window"], index=0)

    if scan_mode == "Date range":
        default_end = date.today() - timedelta(days=1)
        default_start = default_end - timedelta(days=7)
        date_range = st.date_input(
            "Pick a date range to retrieve filings for",
            value=(default_start, default_end),
            min_value=date(2001, 1, 1),
            max_value=default_end,
            help="Works for any past range, including years back — SEC's daily index is available historically. "
                 "Select a start and end date; wider ranges take longer to scan."
        )
    else:
        days_back = st.slider("Days of filings to scan", 1, 10, 3,
                               help="Rolling window counted backward from yesterday, skipping weekends.")

    cap_ceiling_b = st.slider("Market cap ceiling ($B)", 0.1, 10.0, 2.0, 0.1)
    one_off_m = st.slider("One-off trade threshold ($M)", 0.1, 10.0, 1.0, 0.1)
    ceo_only = st.checkbox("CEO transactions only", value=False)
    max_filings = st.slider("Max filings to fetch (rate-limit safety)", 50, 1000, 300, 50)
    lookback_years = st.slider("Price performance lookback (years)", 1, 5, 5)
    run_btn = st.button("Scan Market", type="primary")
    st.caption("Note: each filing is a live SEC request, so wider ranges/more filings = longer scan time.")

if "df" not in st.session_state:
    st.session_state.df = pd.DataFrame()
    st.session_state.scanned = 0
    st.session_state.mode_label = ""

if search_btn and ticker_search:
    with st.spinner(f"Pulling insider trading history for {ticker_search.upper()}..."):
        df, mcap, err = run_single_ticker_search(ticker_search, one_off_m * 1e6, lookback_years)
    if err:
        st.error(err)
    else:
        st.session_state.df = df
        st.session_state.scanned = len(df)
        mcap_str = f"${mcap:,.0f}" if mcap else "N/A"
        st.session_state.mode_label = f"Single-ticker search: {ticker_search.upper()} (Market cap: {mcap_str})"

if run_btn:
    if scan_mode == "Date range":
        if isinstance(date_range, tuple) and len(date_range) == 2:
            range_start, range_end = date_range
        else:
            range_start = range_end = date_range
        if range_start > range_end:
            st.error("Start date must be before end date.")
        else:
            with st.spinner(f"Retrieving all Form 4 filings from {range_start} to {range_end}..."):
                df, scanned, days_with_data = run_market_scan_for_date_range(
                    range_start, range_end, cap_ceiling_b * 1e9, one_off_m * 1e6,
                    ceo_only, max_filings, lookback_years
                )
            if days_with_data == 0:
                st.warning(f"No SEC filing index available for {range_start} to {range_end} — "
                           f"check that the range includes weekdays SEC has published data for.")
            st.session_state.df = df
            st.session_state.scanned = scanned
            st.session_state.mode_label = f"Market scan: {range_start} to {range_end} ({days_with_data} trading day(s) with data)"
    else:
        with st.spinner(f"Scanning last {days_back} day(s) of Form 4 filings market-wide..."):
            df, scanned = run_market_scan_recent(
                days_back, cap_ceiling_b * 1e9, one_off_m * 1e6, ceo_only, max_filings, lookback_years
            )
        st.session_state.df = df
        st.session_state.scanned = scanned
        st.session_state.mode_label = f"Market scan: last {days_back} trading day(s)"

df = st.session_state.df

if st.session_state.mode_label:
    st.info(f"**{st.session_state.mode_label}** | Filings inspected: {st.session_state.scanned}")

if df.empty:
    st.warning("Use the search bar above for a single ticker, or configure and run a market scan from the sidebar.")
    st.stop()

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Total Transactions", len(df))
col2.metric("Unique Companies", df["ticker"].nunique())
col3.metric("CEO Transactions", int(df["is_ceo"].sum()))
col4.metric("One-Off Trades (≥ threshold)", int(df["is_one_off"].sum()))
avg_perf = df["return_since_transaction_pct"].mean() if "return_since_transaction_pct" in df else None
col5.metric("Avg. Return Since Trade", f"{avg_perf:.1f}%" if pd.notna(avg_perf) else "N/A")

st.divider()

st.subheader("Insider Transactions Feed")
feed_cols = ["ticker", "issuer_name", "owner_name", "officer_title", "is_ceo",
             "transaction_code", "shares", "price", "trade_value_usd",
             "signal", "is_one_off", "market_cap", "allocation_pct_change",
             "transaction_date", "days_since_transaction", "return_since_transaction_pct"]
feed_cols = [c for c in feed_cols if c in df.columns]
feed_df = df[feed_cols].sort_values("trade_value_usd", ascending=False)
feed_df = feed_df.rename(columns={"return_since_transaction_pct": "% Return Since Transaction"})
st.dataframe(
    feed_df.style.format({"% Return Since Transaction": "{:.1f}%"}, na_rep="N/A"),
    use_container_width=True, height=420
)

st.divider()

col_a, col_b = st.columns(2)
with col_a:
    st.subheader("Signal Distribution")
    sig_counts = df["signal"].value_counts().reset_index()
    sig_counts.columns = ["signal", "count"]
    fig1 = px.pie(sig_counts, names="signal", values="count", hole=0.4)
    st.plotly_chart(fig1, use_container_width=True)

with col_b:
    st.subheader("One-Off Trades by Company")
    oneoff = df[df["is_one_off"]].groupby("ticker")["trade_value_usd"].max().reset_index()
    oneoff = oneoff.sort_values("trade_value_usd", ascending=False).head(15)
    if not oneoff.empty:
        fig2 = px.bar(oneoff, x="ticker", y="trade_value_usd", title="Largest Single Trades")
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No one-off trades above threshold found.")

st.subheader("Stock Performance Since Transaction, by Signal")
perf_df = df.dropna(subset=["return_since_transaction_pct"]) if "return_since_transaction_pct" in df else pd.DataFrame()
if not perf_df.empty:
    fig_perf = px.box(perf_df, x="signal", y="return_since_transaction_pct", color="signal",
                       points="all", hover_data=["ticker", "owner_name", "transaction_date"],
                       labels={"return_since_transaction_pct": "% Return Since Transaction"})
    st.plotly_chart(fig_perf, use_container_width=True)
else:
    st.info("No price performance data available yet.")

st.subheader("Forward Return Checkpoints (3M / 1Y / 2Y / 5Y)")
checkpoint_cols = ["ticker", "owner_name", "signal", "transaction_date",
                   "return_3m_pct", "return_1y_pct", "return_2y_pct", "return_5y_pct"]
checkpoint_cols = [c for c in checkpoint_cols if c in df.columns]
checkpoint_df = df[checkpoint_cols].dropna(
    subset=[c for c in checkpoint_cols if c.startswith("return_")], how="all"
) if len(checkpoint_cols) > 4 else pd.DataFrame()
if not checkpoint_df.empty:
    melted = checkpoint_df.melt(
        id_vars=["ticker", "owner_name", "signal", "transaction_date"],
        value_vars=[c for c in checkpoint_cols if c.startswith("return_")],
        var_name="horizon", value_name="return_pct"
    )
    melted["horizon"] = melted["horizon"].map({
        "return_3m_pct": "3M", "return_1y_pct": "1Y", "return_2y_pct": "2Y", "return_5y_pct": "5Y"
    })
    melted = melted.dropna(subset=["return_pct"])
    if not melted.empty:
        fig_checkpoints = px.box(melted, x="horizon", y="return_pct", color="signal",
                                  category_orders={"horizon": ["3M", "1Y", "2Y", "5Y"]})
        st.plotly_chart(fig_checkpoints, use_container_width=True)
    else:
        st.info("Not enough elapsed time yet for forward-return checkpoints.")
else:
    st.info("Not enough elapsed time yet for forward-return checkpoints.")

st.subheader("Allocation: Trade Size vs. Prior Holdings")
alloc_df = df.dropna(subset=["allocation_pct_change"]) if "allocation_pct_change" in df else pd.DataFrame()
if not alloc_df.empty:
    fig3 = px.scatter(alloc_df, x="trade_value_usd", y="allocation_pct_change",
                       color="signal", hover_data=["ticker", "owner_name", "officer_title"],
                       labels={"trade_value_usd": "Trade Value ($)", "allocation_pct_change": "% of Prior Holdings"})
    st.plotly_chart(fig3, use_container_width=True)

st.download_button("Download Full Feed CSV", df.to_csv(index=False),
                    file_name="insider_trading_feed.csv", mime="text/csv")
