# -*- coding: utf-8 -*-
"""
QMT API 封装层。

集中所有对 QMT（xt_trader + xtdata）的调用：连接管理、行情订阅/查询、下单/查资产。
其余模块通过 `QmtClient` 的类方法调用，不再直接持有 xt_trader 或调用 xtdata。

xtquant 的导入统一在本模块完成；xtconstant 常量仍可从本模块导入（`from .qmt import xtconstant`）。
配置从 __init__ 获取，sys.path 由 __main__ 设置。
connect() 为单例：首次调用建立连接，后续调用返回同一实例，供 daemon 各任务共享。
仅在 daemon 退出时调用 disconnect() 断开。
"""
import logging
import random

from . import config

from xtquant.xttrader import XtQuantTrader
from xtquant.xttype import StockAccount
from xtquant import xtconstant, xtdata

logger = logging.getLogger(__name__)


class QmtClient:
    """QMT API 统一封装（classmethod + 模块级单例）。

    连接/账户是进程级单例（`_trader`/`_account`），daemon 启动时调一次 `connect()`，
    策略函数直接调各 `@classmethod`，不传递 trader 实例。
    """

    _trader = None
    _account = None

    # ============================================================
    #  连接管理
    # ============================================================

    @classmethod
    def connect(cls):
        """连接 mini QMT，建立共享的 xt_trader + StockAccount 单例。"""
        if cls._trader is not None:
            return cls._trader
        session_id = random.randint(100000, 999999)
        trader = XtQuantTrader(config.mini_path, session_id)
        trader.start()
        ret = trader.connect()
        if ret != 0:
            raise Exception(f'连接QMT失败, 返回码: {ret}')
        acc = StockAccount(config.account_id)
        trader.subscribe(acc)
        cls._trader = trader
        cls._account = acc
        logger.info(f'连接成功, 订阅账户: {config.account_id}')
        return cls._trader

    @classmethod
    def disconnect(cls):
        """断开共享连接（仅在 daemon 退出时调用）"""
        if cls._trader is not None:
            cls._trader.stop()
            cls._trader = None
            cls._account = None
            logger.info('QMT 连接已断开')

    @classmethod
    def get_trader(cls):
        """返回共享的 xt_trader 单例；若尚未 connect() 则先建立连接。"""
        if cls._trader is None:
            cls.connect()
        return cls._trader

    @classmethod
    def get_account(cls):
        """返回共享的 StockAccount 单例；若尚未 connect() 则先建立连接。"""
        if cls._account is None:
            cls.connect()
        return cls._account

    # ============================================================
    #  行情查询（xtdata）
    # ============================================================

    @classmethod
    def subscribe_quote(cls, code, period='tick'):
        """订阅单只股票行情推送。"""
        return xtdata.subscribe_quote(code, period)

    @classmethod
    def get_full_tick(cls, code_list):
        """获取股票最新 tick 快照。"""
        return xtdata.get_full_tick(code_list)

    @classmethod
    def get_trading_dates(cls, market, start_time, end_time):
        """查询交易日历。"""
        return xtdata.get_trading_dates(market, start_time, end_time)

    @classmethod
    def download_history_data(cls, code, period):
        """下载历史行情数据。"""
        return xtdata.download_history_data(code, period)

    @classmethod
    def get_market_data_ex(cls, field_list, code_list, period='', count=-1):
        """获取历史行情数据。"""
        return xtdata.get_market_data_ex(field_list, code_list, period=period, count=count)

    @classmethod
    def get_stock_list_in_sector(cls, sector_name):
        """获取板块成分股代码列表。"""
        return xtdata.get_stock_list_in_sector(sector_name)

    @classmethod
    def get_instrument_detail(cls, code):
        """获取合约详情。"""
        return xtdata.get_instrument_detail(code)

    # ============================================================
    #  下单 / 查询（xt_trader）
    # ============================================================

    @classmethod
    def place_order(cls, code, direction, price, volume, remark='手动下单', price_type=None):
        """提交委托，返回订单号；失败抛 RuntimeError。

        price_type: 价格类型，默认限价单（xtconstant.FIX_PRICE）。
        可选：xtconstant.BUY1_PRICE（买1价）/xtconstant.LATEST_PRICE（最新价）等，
        使用市价类型时 price 参数传 0 即可。
        """
        acc = cls.get_account()
        order_type = xtconstant.STOCK_BUY if direction == 'buy' else xtconstant.STOCK_SELL
        action = '买入' if direction == 'buy' else '卖出'
        unit = cls._unit_label(code)
        if price_type is None:
            price_type = xtconstant.FIX_PRICE
        price_type_name = cls._price_type_name(price_type)
        price_str = f'{price}' if price_type == xtconstant.FIX_PRICE else price_type_name
        logger.info(f'{action} {code} 价格 {price_str} 数量 {volume}{unit} ...')
        order_id = cls._trader.order_stock(
            acc, code, order_type, volume, price_type, price, order_remark=remark
        )
        if order_id is not None and order_id >= 0:
            logger.info(f'委托提交成功, 订单号: {order_id}')
        else:
            logger.error(f'委托提交失败, 返回: {order_id}')
            raise RuntimeError(f'order_stock 返回 {order_id}')
        return order_id

    @classmethod
    def check_order(cls, order_id, code):
        """回查委托状态并打印成交情况（按 order_id 精确匹配，避免把同股票旧单误当作新单）。"""
        acc = cls.get_account()

        orders = cls._trader.query_stock_orders(acc)
        if not orders:
            logger.warning(f'未查到任何委托记录, 请在客户端确认订单号 {order_id}（QMT 同步可能有延迟）')
            return
        for o in orders:
            if str(o.order_id) == str(order_id):
                logger.info(
                    '委托状态: %s, 已成交: %s%s',
                    o.status_msg, o.traded_volume, cls._unit_label(code),
                )
                return
        logger.warning(f'未找到订单号 {order_id}（{code}），QMT 同步可能有延迟，请在客户端确认')

    @classmethod
    def query_stock_asset(cls):
        """查询账户资产。"""
        acc = cls.get_account()
        return cls._trader.query_stock_asset(acc)

    @classmethod
    def query_stock_orders(cls):
        """查询今日委托。"""
        acc = cls.get_account()
        return cls._trader.query_stock_orders(acc)

    # ============================================================
    #  内部辅助
    # ============================================================

    @staticmethod
    def _unit_label(code: str) -> str:
        """根据代码返回委托数量单位：逆回购为"张"，其余为"股"。"""
        return '张' if code.startswith(('204', '1318')) else '股'

    @staticmethod
    def _price_type_name(price_type) -> str:
        """价格类型 → 中文名称。"""
        return {
            xtconstant.FIX_PRICE: '限价',
            xtconstant.LATEST_PRICE: '最新价',
            xtconstant.MARKET_PEER_PRICE_FIRST: '对手方最优',
            xtconstant.MARKET_MINE_PRICE_FIRST: '本方最优',
        }.get(price_type, f'type{price_type}')
