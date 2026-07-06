# -*- coding: utf-8 -*-
"""
app 包入口，支持 `python -m app` 运行。

负责运行时初始化（sys.path）和子命令派发。

用法:
    python -m app init                        # 初始化 db（刷新股票名称到数据库）
    python -m app trade buy  隆基绿能 12.40 800
    python -m app trade sell 隆基绿能 12.40 800
"""
import os
import sys
import argparse
import logging

from . import MINI_PATH

_QMT_SITE = os.path.join(os.path.dirname(MINI_PATH), 'bin.x64', 'Lib', 'site-packages')
if _QMT_SITE not in sys.path:
    sys.path.insert(0, _QMT_SITE)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')


def main():
    parser = argparse.ArgumentParser(prog='python -m app', description='QMT 交易工具')
    sub = parser.add_subparsers(dest='command')

    # init 子命令
    sub.add_parser('init', help='初始化数据库（刷新股票名称）')

    # trade 子命令
    p_trade = sub.add_parser('trade', help='手动下单')
    p_trade.add_argument('direction', choices=['buy', 'sell'], help='买(buy)或卖(sell)')
    p_trade.add_argument('name_or_code', help='股票名称或6位代码')
    p_trade.add_argument('price', type=float, help='委托价格')
    p_trade.add_argument('volume', type=int, help='委托数量(股)')

    args = parser.parse_args()

    if args.command == 'init':
        from .trade import build_name_cache
        from .db import init_db
        init_db()
        build_name_cache()
    elif args.command == 'trade':
        from .trade import main as trade_main
        trade_main([
            args.direction,
            args.name_or_code,
            str(args.price),
            str(args.volume),
        ])
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
