# -*- coding: utf-8 -*-
"""
QMT 连接模块（mini QMT 通道）

封装 xtquant 的 site-packages 路径设置、连接、订阅等操作。
其他脚本只需 `from qmt import connect, xtdata` 即可使用。

前置条件：
    1. mini QMT 终端已启动并登录
    2. config.toml 中填写本机的 account_id / mini_path / session_id
"""
import os
import sys

# ===== 读取 config.toml =====
_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.toml')
if not os.path.exists(_CONFIG_FILE):
    raise FileNotFoundError(f'配置文件不存在: {_CONFIG_FILE}，请参考 config.toml.example 创建')

try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # 需 pip install tomli（Python 3.6~3.10）
    except ImportError:
        raise ImportError('缺少 TOML 解析库，请运行: pip install tomli')

with open(_CONFIG_FILE, 'rb') as _f:
    _cfg = tomllib.load(_f)

ACCOUNT_ID = _cfg['account_id']
MINI_PATH = _cfg['mini_path']
SESSION_ID = _cfg['session_id']
# ==================

# xtquant 随 QMT 客户端安装，不在 .venv 中。将 QMT 的 site-packages 加入 sys.path
_QMT_SITE = os.path.join(os.path.dirname(MINI_PATH), 'bin.x64', 'Lib', 'site-packages')
if _QMT_SITE not in sys.path:
    sys.path.insert(0, _QMT_SITE)

from xtquant.xttrader import XtQuantTrader
from xtquant.xttype import StockAccount
from xtquant import xtconstant, xtdata


def connect():
    """连接 mini QMT 并订阅账户，返回 (xt_trader, acc)"""
    xt_trader = XtQuantTrader(MINI_PATH, SESSION_ID)
    xt_trader.start()
    ret = xt_trader.connect()
    if ret != 0:
        raise Exception(f'连接QMT失败, 返回码: {ret}')
    acc = StockAccount(ACCOUNT_ID)
    xt_trader.subscribe(acc)
    print(f'连接成功, 订阅账户: {ACCOUNT_ID}')
    return xt_trader, acc
