# Insider Trading Feed Dashboard — Demo Guide

## What changed in this update

### 1. Fixed "Search Ticker" button alignment
The button now sits properly in line with the ticker search box — a spacer div
was added above the button to match the height of the text input's label, so
they're vertically aligned instead of the button sitting lower than the box.

### 2. Date range picker (instead of single date)
The "Specific date" mode is now **"Date range"** — you pick a start and end
date (defaults to the last 7 days), and the scan pulls Form 4 filings across
every weekday in that window, not just one day.

- Works for ranges anywhere in SEC's history (back to 2001), including
  multi-year-old windows.
- Tested live: a 4-day range from August 12–15, 2024 correctly pulled real
  filings across all 4 trading days and merged them into one result set.
- The status banner after a scan now shows how many trading days in your
  range actually had data (e.g. "4 trading day(s) with data"), so you know
  if weekends/holidays reduced your effective window.

## How to use the date range picker
1. Sidebar → Scan mode → **"Date range"**.
2. Click the date field — a calendar picker lets you select both a start and end date in one click-through.
3. Wider ranges mean more filings to fetch, so use the "Max filings to fetch" slider to keep scan time reasonable if you pick a long window.
4. Click **"Scan Market"**.

## Setup (one-time)
```bash
pip install streamlit plotly requests pandas numpy yfinance
```

## Run it
```bash
streamlit run dashboard.py
```
Opens at `http://localhost:8501`.

## Full feature set (recap)
- **Top search bar**: type a ticker, click "Search Ticker" (now properly aligned) to pull that company's full insider trading history directly.
- **Date range scan**: pick any start/end date, including years back, to retrieve every Form 4 filed market-wide in that window.
- **Rolling recent window**: alternative mode — scans the last N business days from yesterday.
- **Market cap ceiling, one-off threshold, CEO-only filter, price performance lookback (up to 5 years)**: all adjustable via sidebar sliders regardless of mode.
- **Feed table**: every transaction with CEO flag, signal, trade value, market cap, allocation %, and % return since transaction.
- **Charts**: signal distribution pie, largest one-off trades bar chart, stock performance by signal box plot, forward return checkpoints (3M/1Y/2Y/5Y), and allocation scatter plot.
- **CSV download** of the full result set.

## Performance Notes for Live Demo
- A date range scan's speed depends on both the number of days in range and total filings found — each filing is a live SEC request.
- For a demo, keep ranges to 3-7 days and "max filings" around 200-300 for a ~30-60 second scan.
- Results are cached for 30 minutes per exact combination of settings, so re-running the same range during Q&A is instant.
- Recommended: pre-run your date range and a ticker search or two 5-10 minutes before presenting so the cache is warm.

## Note on File Persistence
This sandbox resets between sessions, so if a download link ever breaks, just ask and the files will be regenerated.
