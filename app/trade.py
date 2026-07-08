# -*- coding: utf-8 -*-
"""
手动下单脚本（mini QMT 通道）

用法:
    python -m app trade buy  隆基绿能 12.40 800     # 买入（名称自动反查代码）
    python -m app trade sell 隆基绿能 12.40 800     # 卖出
    python -m app trade buy  601012 12.40 800       # 也支持直接传6位代码

前置条件:
    1. mini QMT 终端已启动并登录
    2. config.toml 中填写本机的 account_id / mini_path
"""
import sys
import time
import argparse
import logging

from sqlalchemy import delete, select

from .qmt import connect, xtdata, xtconstant, get_account
from .db import Stock, SessionLocal, init_db

logger = logging.getLogger(__name__)


def append_suffix(code):
    """6位代码加交易所后缀。

    使用"长前缀优先 + 首位兜底"策略，覆盖沪深 A 股/科创板/创业板(含注册制301)/ETF/LOF/逆回购/可转债。
    """
    # 沪市特判：逆回购(204xxx) / 可转债(110/111/113/115xxx)
    if code.startswith(('204', '11')):
        return code + '.SH'
    # 深市特判：逆回购(1318xx) / 可转债(123/127/128xxx) / B股(200xxx)
    if code.startswith(('1318', '12', '200')):
        return code + '.SZ'
    first = code[0]
    if first in ('5', '6', '9'):
        return code + '.SH'  # 沪市：5=基金/ETF, 6=A股/科创板, 9=B股
    if first in ('0', '3', '1'):
        return code + '.SZ'  # 深市：0=主板, 3=创业板(300/301), 1=基金/LOF/其他
    raise ValueError(f'无法识别的股票代码: {code}（请检查代码是否正确，或使用股票名称）')


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
    with SessionLocal() as s:
        s.execute(delete(Stock))
        s.add_all([Stock(name=name, code=code) for name, code in stocks])
        s.commit()
    logger.info(f'缓存已写入，共 {len(stocks)} 条记录')
    return len(stocks)


def resolve_code(name_or_code):
    """将股票名称或6位代码解析为带交易所后缀的代码"""
    code = name_or_code
    if len(code) == 6 and code.isdigit():
        return append_suffix(code)

    # 精确匹配
    with SessionLocal() as s:
        result = s.scalar(select(Stock.code).where(Stock.name == code))
    if result:
        return result

    # 模糊匹配
    with SessionLocal() as s:
        rows = s.execute(
            select(Stock.name, Stock.code).where(Stock.name.like(f'%{code}%'))
        ).all()
    if len(rows) == 1:
        logger.info(f'模糊匹配: {name_or_code} -> {rows[0][0]}')
        return rows[0][1]
    elif len(rows) > 1:
        logger.warning(f'匹配到多个股票: {[r[0] for r in rows]}，请用更精确的名称')
        sys.exit(1)

    raise ValueError(f'未找到股票: {name_or_code}，请运行 `python -m app init-db` 初始化数据库')


def _unit_label(code: str) -> str:
    """根据代码返回委托数量单位：逆回购为"张"，其余为"股"。"""
    return '张' if code.startswith(('204', '1318')) else '股'


# 价格类型 → 中文名称（延迟查表，避免模块加载时 xtconstant 还未就绪）
def _price_type_name(price_type) -> str:
    return {
        xtconstant.FIX_PRICE: '限价',
        xtconstant.LATEST_PRICE: '最新价',
        xtconstant.MARKET_PEER_PRICE_FIRST: '对手方最优',
        xtconstant.MARKET_MINE_PRICE_FIRST: '本方最优',
    }.get(price_type, f'type{price_type}')


def place_order(xt_trader, code, direction, price, volume, remark='手动下单', price_type=None):
    """提交委托，返回订单号；失败抛 RuntimeError

    price_type: 价格类型，默认限价单（xtconstant.FIX_PRICE）。
    可选：xtconstant.BUY1_PRICE（买1价）/xtconstant.LATEST_PRICE（最新价）等，
    使用市价类型时 price 参数传 0 即可。
    """
    acc = get_account()
    order_type = xtconstant.STOCK_BUY if direction == 'buy' else xtconstant.STOCK_SELL
    action = '买入' if direction == 'buy' else '卖出'
    unit = _unit_label(code)
    if price_type is None:
        price_type = xtconstant.FIX_PRICE
    price_type_name = _price_type_name(price_type)
    price_str = f'{price}' if price_type == xtconstant.FIX_PRICE else price_type_name
    logger.info(f'{action} {code} 价格 {price_str} 数量 {volume}{unit} ...')
    order_id = xt_trader.order_stock(
        acc, code, order_type, volume, price_type, price, order_remark=remark
    )
    if order_id is not None and order_id >= 0:
        logger.info(f'委托提交成功, 订单号: {order_id}')
    else:
        logger.error(f'委托提交失败, 返回: {order_id}')
        raise RuntimeError(f'order_stock 返回 {order_id}')
    return order_id


def check_order(xt_trader, order_id, code):
    """回查委托状态并打印成交情况（按 order_id 精确匹配，避免把同股票旧单误当作新单）。"""
    acc = get_account()

    orders = xt_trader.query_stock_orders(acc)
    if not orders:
        logger.warning(f'未查到任何委托记录, 请在客户端确认订单号 {order_id}（QMT 同步可能有延迟）')
        return
    for o in orders:
        if str(o.order_id) == str(order_id):
            logger.info(
                '委托状态: %s, 已成交: %s%s',
                o.status_msg, o.traded_volume, _unit_label(code),
            )
            return
    logger.warning(f'未找到订单号 {order_id}（{code}），QMT 同步可能有延迟，请在客户端确认')


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m app trade', description='mini QMT 手动下单')
    parser.add_argument('direction', choices=['buy', 'sell'], help='买(buy)或卖(sell)')
    parser.add_argument('name_or_code', help='股票名称或6位代码')
    parser.add_argument('price', type=float, help='委托价格')
    parser.add_argument('volume', type=int, help='委托数量(股)')
    args = parser.parse_args(argv)

    init_db()  # 确保表结构存在，避免首次使用报表不存在
    code = resolve_code(args.name_or_code)

    if args.volume <= 0:
        logger.error('数量必须大于0')
        sys.exit(1)
    if args.volume % 100 != 0:
        logger.warning(f'数量{args.volume}不是100的整数倍, A股需按手(100股)交易')

    xt_trader = connect()
    try:
        order_id = place_order(xt_trader, code, args.direction, args.price, args.volume)
        time.sleep(1)
        check_order(xt_trader, order_id, code)
    except RuntimeError:
        sys.exit(1)
