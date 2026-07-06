# -*- coding: utf-8 -*-
"""
QMT 连接模块

负责 xtquant 导入和连接。配置从 __init__ 获取，sys.path 由 __main__ 设置。
"""
import logging

from . import ACCOUNT_ID, MINI_PATH, SESSION_ID

from xtquant.xttrader import XtQuantTrader
from xtquant.xttype import StockAccount
from xtquant import xtconstant, xtdata

logger = logging.getLogger(__name__)


def connect():
    """连接 mini QMT，返回 xt_trader"""
    xt_trader = XtQuantTrader(MINI_PATH, SESSION_ID)
    xt_trader.start()
    ret = xt_trader.connect()
    if ret != 0:
        raise Exception(f'连接QMT失败, 返回码: {ret}')
    acc = StockAccount(ACCOUNT_ID)
    xt_trader.subscribe(acc)
    logger.info(f'连接成功, 订阅账户: {ACCOUNT_ID}')
    return xt_trader
