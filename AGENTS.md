# AGENTS.md

Trading-automation scripts for QMT (迅投极速策略交易系统) on Chinese A-share markets, using the `xtquant` API. One package (`app/`) — no tests, no CI.

## Module structure

- **`app/__init__.py`** — package initialization. Reads `config.toml` and exposes a single immutable `config` object (`Config` dataclass: `account_id`, `mini_path`, `dingtalk_token`). All runtime config is accessed via `from . import config`; no flat module-level config variables exist anywhere else.
- **`app/__main__.py`** — entry point for `python -m app`. Adds QMT's `bin.x64\Lib\site-packages` to `sys.path` and dispatches subcommands (e.g. `python -m app trade buy 隆基绿能 12.40 800`).
- **`app/qmt.py`** — QMT connection module. Imports config from `app` (`from . import config`), imports `xtquant` (`XtQuantTrader`, `StockAccount`, `xtconstant`, `xtdata`), and defines `connect()`. Other modules import from here via `from .qmt import connect, xtdata, xtconstant`. Does not manage `sys.path`.
- **`app/db.py`** — database access module (SQLAlchemy 2.x ORM). Defines the module-level `engine` / `SessionLocal` (sessionmaker) bound to `data/sqlite.db`, the `Stock` model (`id`, `name`, `code`), and `init_db()` (calls `Base.metadata.create_all`). Callers use `with SessionLocal() as s:` directly — no raw SQL, no procedural wrapper functions.
- **`app/trade.py`** — manual order placement logic. Imports `connect`, `xtdata`, `xtconstant` from `app.qmt` and `Stock`, `SessionLocal`, `init_db` from `app.db`. Maintains a local SQLite cache (`data/sqlite.db`) for name→code resolution via `xtdata.get_stock_list_in_sector` / `xtdata.get_instrument_detail`. Exposes `main(argv)` for `__main__` to call; not run directly.
- **`app/repo.py`** — treasury bond reverse repo (国债逆回购) scheduled task. Imports `connect`, `xtdata` from `app.qmt` and `place_order` from `app.trade`. At 14:58 on each trading day, compares the 1-day repo yield of Shanghai (`204001.SH`) vs Shenzhen (`131810.SZ`), sells the higher-rate market using all available cash (1张=100元, 10张/1000元 minimum) at bid-1 price. `--now` executes immediately. Exposes `main(argv)` for `__main__` to call; not run directly.

## Run

```
python -m app init                          # 初始化 db（建表 + 刷新股票名称）
python -m app trade buy  隆基绿能 12.40 800
python -m app trade sell 隆基绿能 12.40 800
python -m app repo                          # 国债逆回购定时任务（14:58 全额资金自动下单）
python -m app repo --now                    # 立即执行逆回购
```

## Dependencies & environment

- This project runs on its own Python (the repo `.venv`), separate from QMT's bundled Python. All code is standard Python — only `xtquant` is borrowed from the QMT client.
- `xtquant` ships with the QMT installation and is **not on PyPI** — do not `pip install xtquant`. `app/__main__.py` adds the client's `bin.x64\Lib\site-packages` to `sys.path` automatically so `xtquant` can be imported.
- The repo `.venv` holds third-party deps: `pandas`, `numpy`, `TA-Lib`, `tomli`, `SQLAlchemy`, `APScheduler`, `requests`. These are the project's real runtime dependencies, not just for syntax checks.
- Runtime deps are pinned in `requirements.txt` (via `pip freeze`).

## About xtQuant
The home page of xtQuant's documents is: https://dict.thinktrader.net/nativeApi/start_now.html?id=I3DJ97
