# -*- coding: utf-8 -*-
"""
手动下单脚本（mini QMT 通道）

用法:
    python -m app trade buy  隆基绿能 12.40 800     # 买入（名称自动反查代码）
    python -m app trade sell 隆基绿能 12.40 800     # 卖出
    python -m app trade buy  601012 12.40 800       # 也支持直接传6位代码

前置条件:
    1. mini QMT 终端已启动并登录
    2. config.toml 中填写本机的 account_id / mini_path / session_id
"""
import os
import sys
import time
import argparse
import logging

from .qmt import connect, xtdata, xtconstant
from .db import init_db, replace_all, find_code_by_name, search_codes_by_keyword

logger = logging.getLogger(__name__)


def append_suffix(code):
    """6位代码加交易所后缀"""
    if code.startswith(('60', '688', '51', '204')):
        return code + '.SH'
    elif code.startswith(('00', '300', '131')):
        return code + '.SZ'
    raise ValueError(f'无法识别的股票代码: {code}')


def build_name_cache():
    """扫描沪深A股，构建 名称→代码 缓存并写入 sqlite"""
    logger.info('正在获取沪深A股成分股列表...')
    codes = xtdata.get_stock_list_in_sector('沪深A股')
    logger.info(f'共 {len(codes)} 只股票，开始查询名称...')
    stocks = []
    for i, code in enumerate(codes):
        detail = xtdata.get_instrument_detail(code)
        if detail and detail.get('InstrumentName'):
            stocks.append((detail['InstrumentName'], code))
        if (i + 1) % 500 == 0:
            logger.info(f'已处理 {i + 1}/{len(codes)}')
    init_db()
    replace_all(stocks)
    logger.info(f'缓存已写入，共 {len(stocks)} 条记录')
    return len(stocks)


def resolve_code(name_or_code):
    """将股票名称或6位代码解析为带交易所后缀的代码"""
    code = name_or_code
    if len(code) == 6 and code.isdigit():
        return append_suffix(code)

    # 精确匹配
    result = find_code_by_name(code)
    if result:
        return result

    # 模糊匹配
    rows = search_codes_by_keyword(code)
    if len(rows) == 1:
        logger.info(f'模糊匹配: {name_or_code} -> {rows[0][0]}')
        return rows[0][1]
    elif len(rows) > 1:
        logger.warning(f'匹配到多个股票: {[r[0] for r in rows]}，请用更精确的名称')
        sys.exit(1)

    raise ValueError(f'未找到股票: {name_or_code}，请运行 `python -m app init` 初始化数据库')


def place_order(xt_trader, acc, code, direction, price, volume):
    """下单并打印委托结果"""
    order_type = xtconstant.STOCK_BUY if direction == 'buy' else xtconstant.STOCK_SELL
    action = '买入' if direction == 'buy' else '卖出'
    logger.info(f'{action} {code} 价格 {price} 数量 {volume}股 ...')
    order_id = xt_trader.order_stock(
        acc, code, order_type, volume, xtconstant.FIX_PRICE, price, '手动下单'
    )
    if order_id is not None and order_id >= 0:
        logger.info(f'委托提交成功, 订单号: {order_id}')
    else:
        logger.error(f'委托提交失败, 返回: {order_id}')
        sys.exit(1)

    time.sleep(1)
    orders = xt_trader.query_stock_orders(acc)
    if orders:
        for o in orders:
            if str(o.order_id) == str(order_id) or o.stock_code == code:
                logger.info(f'委托状态: {o.status_msg}, 已成交: {o.traded_volume}股')
                break


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m app trade', description='mini QMT 手动下单')
    parser.add_argument('direction', choices=['buy', 'sell'], help='买(buy)或卖(sell)')
    parser.add_argument('name_or_code', help='股票名称或6位代码')
    parser.add_argument('price', type=float, help='委托价格')
    parser.add_argument('volume', type=int, help='委托数量(股)')
    args = parser.parse_args(argv)

    code = resolve_code(args.name_or_code)

    if args.volume <= 0:
        logger.error('数量必须大于0')
        sys.exit(1)
    if args.volume % 100 != 0:
        logger.warning(f'数量{args.volume}不是100的整数倍, A股需按手(100股)交易')

    xt_trader, acc = connect()
    try:
        place_order(xt_trader, acc, code, args.direction, args.price, args.volume)
    finally:
        xt_trader.stop()
