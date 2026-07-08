# -*- coding: utf-8 -*-
"""
国债逆回购自动下单（业务逻辑 + 定时 job 函数）

每个交易日的 14:58，用全部可用资金下单 1 天期国债逆回购，
比较沪市(204001)与深市(131810)利率，选择利率更高的市场，以买1价委托卖出。

用法:
    python -m app serve             # daemon 模式：启动调度器，每个交易日 14:58 全额资金自动下单
    python -m app repo --now        # 立即执行一次逆回购（测试/手动触发）

定时调度由 `python -m app serve` 统一管理（见 __main__.py），本模块只提供
job 函数 scheduled_repo() 和业务函数 run_repo()，不再自建调度器。

前置条件:
    1. mini QMT 终端已启动并登录
    2. config.toml 已配置 account_id / mini_path
"""
import time
import datetime
import logging
import argparse

from . import config
from .qmt import connect, xtdata, xtconstant
from .trade import place_order
from xtquant.xttype import StockAccount

logger = logging.getLogger(__name__)

# 1天期国债逆回购代码
REPO_SH = '204001.SH'   # 沪市 GC001
REPO_SZ = '131810.SZ'   # 深市 R-001
REPO_CODES = [REPO_SH, REPO_SZ]

# 国债逆回购通用参数（沪深两市2024年5月新规后统一）
# 申报单位为"张"，1张=100元面值，最小10张（1000元）起投，按10张整数倍申报
REPO_FACE = 100      # 每张面值（元）
REPO_LOT = 10        # 申报步长（张）
REPO_MIN = 10        # 最小申报数量（张）
SCHEDULE_TIME = (14, 58)  # 定时执行时刻


# ============================================================
#  交易日判断
# ============================================================

def is_trading_day(date=None):
    """判断指定日期是否为交易日（通过 xtdata 交易日历）"""
    if date is None:
        date = datetime.date.today()
    date_str = date.strftime('%Y%m%d')
    try:
        dates = xtdata.get_trading_dates('SH', date_str, date_str)
        if not dates:
            return False
        ts = dates[0]
        if ts > 1e12:  # 毫秒时间戳
            ts /= 1000
        return datetime.datetime.fromtimestamp(ts).date() == date
    except Exception as e:
        logger.warning(f'查询交易日历失败({e})，改用周末判断')
        return date.weekday() < 5


# ============================================================
#  利率查询与市场选择
# ============================================================

def get_repo_rates():
    """获取两只逆回购的最新利率（年化收益率，即最新成交价）"""
    # 订阅行情以确保能获取实时数据
    for code in REPO_CODES:
        try:
            xtdata.subscribe_quote(code)
        except Exception:
            pass
    time.sleep(0.5)

    rates = {}
    # 方式1: get_full_tick
    try:
        ticks = xtdata.get_full_tick(REPO_CODES)
        for code in REPO_CODES:
            tick = (ticks or {}).get(code)
            if tick:
                price = tick.get('lastPrice')
                if price and price > 0:
                    rates[code] = price
    except Exception as e:
        logger.warning(f'get_full_tick 获取利率失败: {e}')

    # 方式2: get_market_data_ex（补充缺失的）
    for code in REPO_CODES:
        if code in rates:
            continue
        try:
            xtdata.download_history_data(code, 'tick')
            result = xtdata.get_market_data_ex([], [code], period='tick', count=1)
            data = result[0] if isinstance(result, tuple) else result
            df = data.get(code)
            if df is not None and len(df) > 0:
                for col in ('last', 'lastPrice', 'close'):
                    if col in df.columns:
                        val = float(df[col].iloc[-1])
                        if val > 0:
                            rates[code] = val
                        break
        except Exception as e:
            logger.warning(f'获取 {code} 利率失败: {e}')
    return rates


def select_market(rates):
    """比较利率，返回 (代码, 利率)"""
    sh = rates.get(REPO_SH)
    sz = rates.get(REPO_SZ)
    if sh is None and sz is None:
        raise Exception('无法获取逆回购利率')
    if sh is None:
        logger.info(f'沪市利率缺失，选择深市 {REPO_SZ} 利率 {sz}')
        return REPO_SZ, sz
    if sz is None:
        logger.info(f'深市利率缺失，选择沪市 {REPO_SH} 利率 {sh}')
        return REPO_SH, sh
    if sh >= sz:
        logger.info(f'沪市 {REPO_SH} 利率 {sh} >= 深市 {REPO_SZ} 利率 {sz}，选择沪市')
        return REPO_SH, sh
    logger.info(f'深市 {REPO_SZ} 利率 {sz} > 沪市 {REPO_SH} 利率 {sh}，选择深市')
    return REPO_SZ, sz


# ============================================================
#  资金查询与数量计算
# ============================================================

def get_available_cash(xt_trader):
    """查询账户可用资金（元）"""
    acc = StockAccount(config.account_id)
    asset = xt_trader.query_stock_asset(acc)
    if asset is None:
        raise Exception('查询账户资产失败')
    cash = getattr(asset, 'cash', None)
    if cash is None:
        raise Exception('查询账户可用资金字段失败(asset.cash is None)')
    cash = float(cash)
    if cash <= 0:
        raise Exception(f'账户可用资金异常: {cash:.2f}元（可能QMT资产数据未同步）')
    return cash


def calc_repo_volume(cash):
    """根据可用资金计算委托数量（张），向下取整到申报步长单位（10张）。

    使用整数分计算避免浮点数精度问题：1张=100元面值，沪深两市统一10张起投、
    按10张整数倍申报。
    """
    cash_fen = int(round(cash * 100))
    face_fen = REPO_FACE * 100
    max_shares = cash_fen // face_fen
    volume = (max_shares // REPO_LOT) * REPO_LOT
    return volume if volume >= REPO_MIN else 0


# ============================================================
#  主流程
# ============================================================

def run_repo():
    """执行一次国债逆回购：比较利率 -> 选市场 -> 查可用资金 -> 以买1价全额下单

    使用共享的 QMT 连接（单例），不负责连接的建立与断开。
    使用 xtconstant.BUY1_PRICE 直接以买1价委托，无需提前查询行情，避免下单时点价差。
    """
    xt_trader = connect()
    rates = get_repo_rates()
    code, rate = select_market(rates)
    cash = get_available_cash(xt_trader)
    volume = calc_repo_volume(cash)
    if volume <= 0:
        logger.warning(f'可用资金 {cash:.2f} 元不足下单 {code} 最小单位（10张/1000元），跳过')
        return
    logger.info(f'可用资金 {cash:.2f} 元，委托 {code} 数量 {volume} 张（{volume * REPO_FACE} 元面值），买1价卖出')
    # 卖逆回购对手方是买方，直接用 BUY1_PRICE（买1价）委托，无需传价格
    place_order(xt_trader, code, 'sell', 0, volume, remark='qmt auto', unit='张',
                price_type=xtconstant.BUY1_PRICE)


def scheduled_repo():
    """APScheduler 定时任务入口：交易日 14:58 执行逆回购"""
    if not is_trading_day():
        logger.info('今日非交易日，跳过')
        return
    try:
        run_repo()
    except Exception:
        logger.exception('逆回购执行异常')


def main(argv=None):
    """命令行入口：立即执行一次逆回购（用于测试/手动触发）。

    定时调度已移至 `python -m app serve` 统一管理，本函数不再自建调度器。
    """
    parser = argparse.ArgumentParser(prog='python -m app repo', description='国债逆回购（立即执行一次）')
    parser.add_argument('--now', action='store_true', help='立即下单（默认行为，保留兼容旧用法）')
    args = parser.parse_args(argv)
    run_repo()
