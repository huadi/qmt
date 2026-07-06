# -*- coding: utf-8 -*-
"""
QMT 连接模块

负责 xtquant 导入和连接管理。配置从 __init__ 获取，sys.path 由 __main__ 设置。
connect() 为单例：首次调用建立连接，后续调用返回同一实例，供 daemon 各任务共享。
仅在 daemon 退出时调用 disconnect() 断开。
"""
import logging
import random

from . import ACCOUNT_ID, MINI_PATH

from xtquant.xttrader import XtQuantTrader
from xtquant.xttype import StockAccount
from xtquant import xtconstant, xtdata

logger = logging.getLogger(__name__)

_trader = None


def connect():
    """连接 mini QMT，返回共享的 xt_trader 实例（单例）"""
    global _trader
    if _trader is not None:
        return _trader
    session_id = random.randint(100000, 999999)
    trader = XtQuantTrader(MINI_PATH, session_id)
    trader.start()
    ret = trader.connect()
    if ret != 0:
        raise Exception(f'连接QMT失败, 返回码: {ret}')
    acc = StockAccount(ACCOUNT_ID)
    trader.subscribe(acc)
    _trader = trader
    logger.info(f'连接成功, 订阅账户: {ACCOUNT_ID}')
    return _trader


def disconnect():
    """断开共享连接（仅在 daemon 退出时调用）"""
    global _trader
    if _trader is not None:
        _trader.stop()
        _trader = None
        logger.info('QMT 连接已断开')
