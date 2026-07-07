# -*- coding: utf-8 -*-
"""
国债逆回购自动下单（业务逻辑 + 定时 job 函数）

每个交易日的 9:32，固定下单 10 张 1 天期国债逆回购，
比较沪市(204001)与深市(131810)利率，选择利率更高的市场，以买1价委托卖出。

用法:
    python -m app serve             # daemon 模式：启动调度器，每个交易日 9:32 自动下单
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

from .qmt import connect, xtdata
from .trade import place_order

logger = logging.getLogger(__name__)

# 1天期国债逆回购代码
REPO_SH = '204001.SH'   # 沪市 GC001
REPO_SZ = '131810.SZ'   # 深市 R-001
REPO_CODES = [REPO_SH, REPO_SZ]

REPO_VOLUME = 10          # 固定委托数量（张）
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
#  买1价查询
# ============================================================

def get_bid_price(code):
    """获取指定逆回购的买1价（用于下单）"""
    try:
        ticks = xtdata.get_full_tick([code])
        tick = (ticks or {}).get(code)
        if tick:
            bids = tick.get('bidPrice')
            if bids and len(bids) > 0 and bids[0] > 0:
                return bids[0]
    except Exception as e:
        logger.warning(f'获取 {code} 买1价失败: {e}')
    return None


# ============================================================
#  主流程
# ============================================================

def run_repo():
    """执行一次国债逆回购：比较利率 -> 选市场 -> 取买1价 -> 固定10张下单

    使用共享的 QMT 连接（单例），不负责连接的建立与断开。
    """
    xt_trader = connect()
    rates = get_repo_rates()
    code, rate = select_market(rates)
    bid = get_bid_price(code)
    if not bid:
        logger.warning(f'未获取到 {code} 买1价，跳过下单')
        return
    place_order(xt_trader, code, 'sell', bid, REPO_VOLUME, remark='qmt auto', unit='张')


def scheduled_repo():
    """APScheduler 定时任务入口：交易日 9:32 执行逆回购"""
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
