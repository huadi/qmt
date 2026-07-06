# -*- coding: utf-8 -*-
"""
手动下单脚本（mini QMT 通道）

用法:
    python trade.py buy  隆基绿能 12.40 800     # 买入（名称自动反查代码）
    python trade.py sell 隆基绿能 12.40 800     # 卖出
    python trade.py buy  601012 12.40 800       # 也支持直接传6位代码
    python trade.py --update-cache              # 更新股票名称缓存

前置条件:
    1. mini QMT 终端已启动并登录
    2. qmt.py 中 ACCOUNT_ID / MINI_PATH 改为本机真实值
"""
import os
import sys
import json
import time
import argparse

from qmt import connect, xtdata, xtconstant

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock_names.json')


def append_suffix(code):
    """6位代码加交易所后缀"""
    if code.startswith(('60', '688', '51', '204')):
        return code + '.SH'
    elif code.startswith(('00', '300', '131')):
        return code + '.SZ'
    raise ValueError(f'无法识别的股票代码: {code}')


def build_name_cache():
    """扫描沪深A股，构建 名称→代码 缓存并写入文件"""
    print('正在获取沪深A股成分股列表...')
    codes = xtdata.get_stock_list_in_sector('沪深A股')
    print(f'共 {len(codes)} 只股票，开始查询名称...')
    name_map = {}
    for i, code in enumerate(codes):
        detail = xtdata.get_instrument_detail(code)
        if detail and detail.get('InstrumentName'):
            name_map[detail['InstrumentName']] = code
        if (i + 1) % 500 == 0:
            print(f'  已处理 {i + 1}/{len(codes)}')
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(name_map, f, ensure_ascii=False, indent=2)
    print(f'缓存已保存到 {CACHE_FILE}，共 {len(name_map)} 条记录')
    return name_map


def load_name_cache():
    """读取本地名称缓存，不存在返回None"""
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


def resolve_code(name_or_code, name_map):
    """将股票名称或6位代码解析为带交易所后缀的代码"""
    code = name_or_code
    if len(code) == 6 and code.isdigit():
        return append_suffix(code)
    if name_map and code in name_map:
        return name_map[code]
    if name_map:
        matches = [k for k in name_map if code in k]
        if len(matches) == 1:
            print(f'模糊匹配: {name_or_code} -> {matches[0]}')
            return name_map[matches[0]]
        elif len(matches) > 1:
            print(f'匹配到多个股票: {matches}，请用更精确的名称')
            sys.exit(1)
    raise ValueError(f'未找到股票: {name_or_code}，请运行 `python trade.py --update-cache` 更新缓存')


def place_order(xt_trader, acc, code, direction, price, volume):
    """下单并打印委托结果"""
    order_type = xtconstant.STOCK_BUY if direction == 'buy' else xtconstant.STOCK_SELL
    action = '买入' if direction == 'buy' else '卖出'
    print(f'{action} {code} 价格 {price} 数量 {volume}股 ...')
    order_id = xt_trader.order_stock(
        acc, code, order_type, volume, xtconstant.FIX_PRICE, price, '手动下单'
    )
    if order_id is not None and order_id >= 0:
        print(f'委托提交成功, 订单号: {order_id}')
    else:
        print(f'委托提交失败, 返回: {order_id}')
        sys.exit(1)

    time.sleep(1)
    orders = xt_trader.query_stock_orders(acc)
    if orders:
        for o in orders:
            if str(o.order_id) == str(order_id) or o.stock_code == code:
                print(f'委托状态: {o.status_msg}, 已成交: {o.traded_volume}股')
                break


def main():
    parser = argparse.ArgumentParser(description='mini QMT 手动下单')
    parser.add_argument('--update-cache', action='store_true', help='更新股票名称缓存')
    parser.add_argument('direction', nargs='?', choices=['buy', 'sell'], help='买(buy)或卖(sell)')
    parser.add_argument('name_or_code', nargs='?', help='股票名称或6位代码')
    parser.add_argument('price', nargs='?', type=float, help='委托价格')
    parser.add_argument('volume', nargs='?', type=int, help='委托数量(股)')
    args = parser.parse_args()

    if args.update_cache:
        build_name_cache()
        return

    if not args.direction:
        parser.error('请指定 buy/sell，或使用 --update-cache 更新缓存')

    name_map = load_name_cache()
    if name_map is None:
        print('名称缓存不存在，首次运行，正在自动构建...')
        name_map = build_name_cache()

    code = resolve_code(args.name_or_code, name_map)

    if args.volume <= 0:
        print('数量必须大于0')
        sys.exit(1)
    if args.volume % 100 != 0:
        print(f'警告: 数量{args.volume}不是100的整数倍, A股需按手(100股)交易')

    xt_trader, acc = connect()
    try:
        place_order(xt_trader, acc, code, args.direction, args.price, args.volume)
    finally:
        xt_trader.stop()


if __name__ == '__main__':
    main()
