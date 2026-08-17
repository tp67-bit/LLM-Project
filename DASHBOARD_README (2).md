# Insider Trading Feed Dashboard — Demo Guide

## Two new capabilities added

### 1. Pick an exact date (including years back)
The sidebar now has a **"Scan mode"** toggle:
- **Specific date** — use the date picker to jump to any single day, including years in the past (SEC's daily index archive goes back to 2001). Selecting a date and clicking "Scan Market" retrieves every Form 4 filed market-wide on that exact day, filtered to your market cap ceiling.
- **Rolling recent window** — the original behavior: counts backward N business days from yesterday (weekends skipped automatically).

Tested live: pulling August 15, 2024 (two years ago) correctly returned real filings from that exact date, filtered under $2B market cap.

### 2. Ticker search bar
A search bar now sits at the top of the dashboard, above the sidebar controls:
- Type any ticker (e.g. `BBAI`) and click **"Search Ticker"**.
- This bypasses the market-wide scan entirely and pulls that one company's full insider trading history directly from SEC (last ~40 Form 4 filings), enriched with the same CEO flag, signal, allocation %, and stock-performance-since-transaction columns.
- Useful for verifying/deep-diving a specific name that showed up in a market scan, or preparing a hero example for your demo ahead of time.

## How date-based scanning works internally
SEC publishes a daily index file per date (`form.YYYYMMDD.idx`) listing every Form 4 filed that day. The app fetches that file directly for whatever date you pick — no difference in mechanics whether it's yesterday or 2024. If a date has no index (weekend, market holiday, or a future/not-yet-published date), the app tells you clearly and suggests picking a nearby weekday.

## Setup (one-time)
```bash
pip install streamlit plotly requests pandas numpy yfinance
```

## Run it
```bash
streamlit run dashboard.py
```
Opens at `http://localhost:8501`.

## Using it
1. **For a single company**: type its ticker in the top search bar, click "Search Ticker."
2. **For a specific historical date**: sidebar → Scan mode → "Specific date" → pick the date → set market cap ceiling → click "Scan Market."
3. **For a rolling recent window**: sidebar → Scan mode → "Rolling recent window" → set days back → click "Scan Market."
4. Adjust market cap ceiling, one-off threshold, CEO-only filter, and price performance lookback (up to 5 years) the same way in all modes.

## What's on the dashboard (unchanged from before)
- Live feed table with CEO flag, signal, trade value, market cap, allocation %, and % return since transaction.
- KPI row: total transactions, unique companies, CEO transactions, one-off trades, avg. return since trade.
- Signal distribution pie chart, largest one-off trades bar chart.
- Stock performance since transaction (by signal) box plot — the core research chart.
- Forward return checkpoints (3M/1Y/2Y/5Y) box plot.
- Allocation scatter plot (trade size vs. % of prior holdings).
- CSV download of the full result set.

## Demo Script Suggestion (3-5 min)
1. Open with the search bar — type a ticker you know has interesting insider activity (e.g. one you found in a prior scan) to show the single-company deep-dive instantly.
2. Switch to "Specific date" mode, pick a date from ~1-2 years ago, run the scan — show that the tool works retroactively, not just live.
3. Point to the "% Return Since Transaction" column on a couple of rows to show the outcome-tracking feature.
4. Show the "Stock Performance by Signal" box plot as your core finding.
5. Mention the CSV download as your backup data source.

## Performance Notes for Live Demo
- Single-ticker search is fast (~40 filings max, typically 10-20 seconds).
- A full market scan (whether a specific date or rolling window) fetches every matching filing live — expect 30-90 seconds depending on "max filings" setting.
- Results are cached for 30 minutes per exact combination of settings, so re-running the same date/tickers during Q&A is instant.
- Recommended: pre-run your date scan and a couple of ticker searches 5-10 minutes before presenting so the cache is warm.

## Note on File Persistence
This sandbox resets between sessions, so if a download link ever breaks, just ask and the files will be regenerated.
