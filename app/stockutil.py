# -*- coding: utf-8 -*-
"""
股票静态信息工具：代码后缀补全、名称→代码缓存构建、名称/代码解析。

这些函数只处理"证券标识符"层面的静态信息（代码归属哪个交易所、名称与代码的映射），
不涉及 QMT 连接或下单。名称→代码的映射缓存在 sqlite 的 stocks 表中，由
`build_name_cache` 从 xtdata 全市场成分股刷新；`resolve_code` 优先查该缓存。
"""
import sys
import logging

from sqlalchemy import delete, select

from .qmt import QmtClient
from .db import Stock, SessionLocal

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
    codes = QmtClient.get_stock_list_in_sector('沪深A股')
    logger.info(f'共 {len(codes)} 只股票，开始查询名称...')
    stocks = []
    for i, code in enumerate(codes):
        detail = QmtClient.get_instrument_detail(code)
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
