# AGENTS.md

Trading-automation scripts for QMT (迅投极速策略交易系统) on Chinese A-share markets, using the `xtquant` API. One package (`app/`) — no tests, no CI.

## Module structure

- **`app/__init__.py`** — package initialization. Reads `config.toml` and exposes `ACCOUNT_ID`, `MINI_PATH`, `SESSION_ID` as package-level variables.
- **`app/__main__.py`** — entry point for `python -m app`. Adds QMT's `bin.x64\Lib\site-packages` to `sys.path` and dispatches subcommands (e.g. `python -m app trade buy 隆基绿能 12.40 800`).
- **`app/qmt.py`** — QMT connection module. Imports config from `__init__`, imports `xtquant` (`XtQuantTrader`, `StockAccount`, `xtconstant`, `xtdata`), and defines `connect()`. Other modules import from here via `from .qmt import connect, xtdata, xtconstant`. Does not manage `sys.path`.
- **`app/db.py`** — database access module. Manages the SQLite connection (`data/sqlite.db`) and all `stocks` table operations (`get_conn`, `init_db`, `upsert_stock`, `replace_all`, `find_code_by_name`, `search_codes_by_keyword`).
- **`app/trade.py`** — manual order placement logic. Imports `connect`, `xtdata`, `xtconstant` from `app.qmt` and db functions from `app.db`. Maintains a local SQLite cache (`data/sqlite.db`) for name→code resolution via `xtdata.get_stock_list_in_sector` / `xtdata.get_instrument_detail`. Exposes `main(argv)` for `__main__` to call; not run directly.
- **`app/repo.py`** — treasury bond reverse repo (国债逆回购) scheduled task. Imports `connect`, `xtdata`, `xtconstant` from `app.qmt`. At 14:58 on each trading day, compares the 1-day repo yield of Shanghai (`204001.SH`) vs Shenzhen (`131810.SZ`), sells the higher-rate market using the account's remaining cash. Self-schedules (waits until 14:58) by default; `--now` executes immediately. Exposes `main(argv)` for `__main__` to call; not run directly.

## Run

```
python -m app init                          # 初始化 db（建表 + 刷新股票名称）
python -m app trade buy  隆基绿能 12.40 800
python -m app trade sell 隆基绿能 12.40 800
python -m app repo                          # 国债逆回购定时任务（14:58 自动下单）
python -m app repo --now                    # 立即执行逆回购
```

## Dependencies & environment

- This project runs on its own Python (the repo `.venv`), separate from QMT's bundled Python. All code is standard Python — only `xtquant` is borrowed from the QMT client.
- `xtquant` ships with the QMT installation and is **not on PyPI** — do not `pip install xtquant`. `app/__main__.py` adds the client's `bin.x64\Lib\site-packages` to `sys.path` automatically so `xtquant` can be imported.
- The repo `.venv` holds third-party deps: `pandas`, `numpy`, `TA-Lib`, `tomli`. These are the project's real runtime dependencies, not just for syntax checks.
- No `requirements.txt` / `pyproject.toml` exists.
