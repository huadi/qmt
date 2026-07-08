# AGENTS.md

Trading-automation scripts for QMT (迅投极速策略交易系统) on Chinese A-share markets, using the `xtquant` API. One package (`app/`) — no tests, no CI.

## Module structure

- **`app/__init__.py`** — package initialization. Reads `config.toml` and exposes a single immutable `config` object (`Config` dataclass: `account_id`, `mini_path`, `dingtalk_token`). All runtime config is accessed via `from . import config`; no flat module-level config variables exist anywhere else.
- **`app/__main__.py`** — entry point for `python -m app`. Adds QMT's `bin.x64\Lib\site-packages` to `sys.path` and dispatches subcommands: `init-db`, `trade`, `watch`, `serve`.
- **`app/tradeutil.py`** — trade-related utility functions. Currently `is_trading_day` (queries trading calendar via `QmtClient.get_trading_dates`, falls back to weekday check) and `is_trading_time` (9:30-11:30 / 13:00-15:00, system local time). Future trade-related helpers go here.
- **`app/qmt.py`** — QMT API wrapper. Encapsulates the `QmtClient` class, centralizing every call into QMT (`xt_trader` + `xtdata`): connection management (`connect`/`disconnect`/`get_account`), market-data (`subscribe_quote`/`get_full_tick`/`get_trading_dates`/`download_history_data`/`get_market_data_ex`/`get_stock_list_in_sector`/`get_instrument_detail`), and order/asset (`place_order`/`check_order`/`query_stock_asset`/`query_stock_orders`). The `xt_trader`/`StockAccount` are process-level singletons owned by the class. All other modules call QMT through `QmtClient` classmethods — never hold an `xt_trader` instance or call `xtdata` directly. `xtquant` is imported only here; `xtconstant` constants may still be imported from this module (`from .qmt import xtconstant`).
- **`app/db.py`** — database access module (SQLAlchemy 2.x ORM). Defines the module-level `engine` / `SessionLocal` bound to `data/sqlite.db`, the `Stock` (name→code cache) and `Watch` (price-alert rules) models, and `init_db()` (calls `Base.metadata.create_all`). Callers use `with SessionLocal() as s:` directly.
- **`app/stockutil.py`** — instrument static-info utilities. Provides `append_suffix` (6-digit code → suffixed code, covers A股/科创板/创业板301/ETF/LOF/可转债/逆回购), `build_name_cache` (refreshes the name→code cache from xtdata into the `stocks` table), and `resolve_code` (name or 6-digit code → suffixed code, via the cache). No QMT connection or order logic — those live in `QmtClient`.
- **`app/__main__.py`** (trade subcommand) — the `trade buy/sell` CLI is inlined here: parses args, calls `resolve_code` → `QmtClient.place_order` → `QmtClient.check_order`. No separate `trade.py` module.
- **`app/strategy/`** — strategy package. Active (apscheduler-driven) and future callback-driven (QMT quote/order-push) strategies live here. Each active strategy module declares its own scheduling params and exposes a `scheduled_*()` entry (trading-day/time guard + `try/except` around the business function) plus a `run_*()`/`check_*()` business function.
  - **`app/strategy/repo.py`** — treasury bond reverse repo (国债逆回购). At `SCHEDULE_TIME` (14:58) each trading day, compares the 1-day repo yield of Shanghai (`204001.SH`) vs Shenzhen (`131810.SZ`), sells the higher-rate market using all available cash at counterparty-best price. Entry: `scheduled_repo()`. Business logic: `run_repo()`. (No CLI — use `serve` or call `run_repo()` directly to test.)
  - **`app/strategy/watch.py`** — price-monitoring feature with DingTalk notification. Declares `SCHEDULE_HOURS` (cron hour range). Provides CRUD (`add_watch`/`list_watches`/`delete_watch`/`reset_watch`/`update_watch`/`resolve_watch`), the per-minute `check_watches()` (commits trigger timestamps before sending notifications to prevent duplicate alerts), and the scheduler entry `scheduled_watch()`. CLI: `watch add/list/update/delete/reset/now`.
- **`app/notify/`** — notification facade. `notify.send(msg)` in `__init__.py` fans out to all configured channels; `notify/dingtalk.py` is the current DingTalk implementation (uses keep-alive Session, connect/read timeout, no-op when token is unset).

## Run

```
python -m app init-db                       # 初始化 db（建表 + 刷新股票名称缓存）
python -m app trade buy  隆基绿能 12.40 800
python -m app trade sell 隆基绿能 12.40 800
python -m app watch add 隆基绿能 10.0 15.0  # 添加股价监控（先跌后涨顺序）
python -m app watch list
python -m app watch update 深信服 90 130
python -m app watch delete 深信服
python -m app watch reset 深信服
python -m app watch now                     # 立即检查一次（测试）
python -m app serve                         # daemon：常驻运行，定时任务 + 未来回调注册点
```

## Dependencies & environment

- This project runs on its own Python (the repo `.venv`), separate from QMT's bundled Python. All code is standard Python — only `xtquant` is borrowed from the QMT client.
- `xtquant` ships with the QMT installation and is **not on PyPI** — do not `pip install xtquant`. `app/__main__.py` adds the client's `bin.x64\Lib\site-packages` to `sys.path` automatically so `xtquant` can be imported.
- The repo `.venv` holds third-party deps: `SQLAlchemy`, `APScheduler`, `requests`, `tomli` (on Python < 3.11). `pandas`/`numpy`/`TA-Lib` are transitive deps kept in `requirements.txt` from `pip freeze` but not imported directly by app code.
- `requirements.txt` is UTF-8 encoded (LF line endings).
- Runtime deps are pinned in `requirements.txt` (via `pip freeze`).

## About xtQuant
The home page of xtQuant's documents is: https://dict.thinktrader.net/nativeApi/start_now.html?id=I3DJ97
