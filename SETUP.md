# AlphaAbsolute — Setup Guide

## Step 1: Install Python Packages

```bash
# Core dependencies
pip install "notebooklm-py[browser]" mcp anthropic

# set-mcp for Thai financial data
pip install set-mcp
# OR use uvx (recommended):
# uvx set-mcp

# TradingView MCP — follow instructions at:
# https://github.com/tradesdontlie/tradingview-mcp
```

## Step 2: NotebookLM Login (one-time)

```bash
python -m notebooklm login
```
This opens a browser for Google login. Complete login once — session is saved.
Auth stored at: `C:\Users\Pizza\.notebooklm\storage_state.json`

## Step 3: Create NotebookLM Notebooks

Go to https://notebooklm.google.com and create these 5 notebooks:
1. `AlphaAbsolute — PULSE framework`
2. `AlphaAbsolute — Megatrend Themes`
3. `AlphaAbsolute — Investment Lessons`
4. `AlphaAbsolute — Thai Market Intelligence`
5. `AlphaAbsolute — Global Macro Database`

Then upload initial sources (see `memory/notebooklm_index.md` for priority list).

## Step 4: Set Environment Variables

Add to Windows System Environment Variables (or create `.env` file):
```
FRED_API_KEY=your_fred_api_key_here
ANTHROPIC_API_KEY=your_anthropic_api_key_here
```

Get FRED API key free at: https://fred.stlouisfed.org/docs/api/api_key.html

## Step 5: Configure Claude Code

Open Claude Code settings and add MCP servers from `.claude/settings.json`

Or in Claude Code desktop: Settings → MCP Servers → Add from file → select `.claude/settings.json`

## Step 6: Test Everything

Test set-mcp:
```
Analyse balance sheet of BH in Thai stock market
```

Test NotebookLM MCP:
```
Query PULSE framework notebook: what is the VCP entry criteria?
```

Test full daily pipeline:
```
run daily brief
```

## Step 7: Populate NotebookLM (Priority Order)

1. Upload all `skills/*.md` files to `AlphaAbsolute — PULSE framework`
2. Copy AlphaPULSE research from `C:\Users\Pizza\OneDrive\Desktop\PizzaClaude\output\` → Thai Market Intelligence
3. Upload any Minervini/Wyckoff/Weinstein reference PDFs → PULSE framework
4. Upload FOMC minutes → Global Macro Database

## File Structure

```
AlphaAbsolute/
├── CLAUDE.md              ← Master config (auto-loaded by Claude Code)
├── SETUP.md               ← This file
├── .claude/
│   └── settings.json      ← MCP server config
├── skills/                ← 20 agent skill files (loaded per task)
├── scripts/
│   └── notebooklm_mcp.py  ← NotebookLM MCP server
├── data/
│   ├── portfolio.json     ← Current holdings
│   ├── trade_log.json     ← All historical trades
│   └── event_calendar.json← Rolling 4-week event calendar
├── output/                ← All generated reports (daily, weekly, PPT)
├── memory/
│   ├── notebooklm_index.md← Index of all NbLM sources
│   ├── investment_lessons.md← Running lessons log
│   └── framework_updates.md← Rule change history
└── templates/             ← PPT/report templates
```

