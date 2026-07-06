# AGENTS.md

Trading-automation scripts for QMT (迅投极速策略交易系统) on Chinese A-share markets, using the `xtquant` API. Two scripts — no package, no tests, no CI.

## Module structure

- **`qmt.py`** — shared connection module. Sets up `sys.path` to locate `xtquant` (which ships with the QMT client, not on PyPI), holds config (`ACCOUNT_ID`, `MINI_PATH`, `SESSION_ID`), and exposes `connect()`, `xtdata`, `xtconstant`. All standalone scripts import from here.
- **`trade.py`** — manual order placement CLI (`python trade.py buy 隆基绿能 12.40 800`). Imports `connect`, `xtdata`, `xtconstant` from `qmt`. Maintains a local `stock_names.json` cache for name→code resolution via `xtdata.get_stock_list_in_sector` / `xtdata.get_instrument_detail`.

## Encoding

- Both scripts are **UTF-8**.

## Dependencies & environment

- The execution runtime is the **QMT client's bundled Python**, not this repo's `.venv`. `xtquant` ships with the QMT installation and is **not on PyPI** — do not `pip install xtquant`. `qmt.py` adds the client's `bin.x64\Lib\site-packages` to `sys.path` automatically.
- The repo `.venv` (Python 3.10.11) only holds data-science deps for local syntax/import checks: `pandas 2.3.3`, `numpy 2.2.6`, `TA-Lib 0.7.0`. The QMT client bundles its own Python 3.10 with `xtquant` preinstalled under `bin.x64\Lib\site-packages`.
- No `requirements.txt` / `pyproject.toml` exists.

## No build / test / lint

There is no test suite, lint config, typecheck, or build step. Verify changes by careful reading and, for the standalone scripts only, importing them under the QMT-provided Python (or the repo `.venv` after `qmt.py` sets up the path).

## Machine-specific / sensitive values

- `qmt.py:25` (`ACCOUNT_ID`) and `qmt.py:26` (`MINI_PATH`) hardcode a real account ID and the mini QMT `userdata_mini` path. These are local to this machine — never commit real account IDs.

## Repo hygiene

A `.gitignore` exists covering Windows system files, IntelliJ/PyCharm state (`.idea/`), and Python artifacts (`.venv/`, `__pycache__/`, `stock_names.json`). `.idea/` was removed from the index via `git rm -r --cached`.
