# -*- coding: utf-8 -*-
"""
app 包入口，支持 `python -m app` 运行。

负责运行时初始化（sys.path）和子命令派发。

用法:
    python -m app init                        # 初始化 db（刷新股票名称到数据库）
    python -m app trade buy  隆基绿能 12.40 800
    python -m app trade sell 隆基绿能 12.40 800
    python -m app serve                        # daemon：常驻运行，定时任务 + 回调
    python -m app repo --now                   # 立即执行一次逆回购（测试/手动）
"""
import os
import sys
import argparse
import logging

from . import config

_QMT_SITE = os.path.join(os.path.dirname(config.mini_path), 'bin.x64', 'Lib', 'site-packages')
if _QMT_SITE not in sys.path:
    sys.path.insert(0, _QMT_SITE)

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


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

    # repo 子命令（立即执行一次，用于测试/手动触发）
    p_repo = sub.add_parser('repo', help='国债逆回购（立即执行一次）')
    p_repo.add_argument('--now', action='store_true', help='立即下单（默认行为，保留兼容）')

    # serve 子命令（daemon 模式）
    sub.add_parser('serve', help='启动常驻服务（定时任务 + 回调）')

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
    elif args.command == 'repo':
        from .repo import main as repo_main
        repo_main(['--now'] if args.now else [])
    elif args.command == 'serve':
        _serve()
    else:
        parser.print_help()


def _serve():
    """daemon 模式：建立共享连接 + 启动后台调度器 + 阻塞主线程（24h 常驻）"""
    import threading
    import time
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from xtquant.xttype import StockAccount

    from .qmt import connect, disconnect
    from .repo import scheduled_repo, SCHEDULE_TIME

    # 1. 建立共享 QMT 连接（单例，供所有定时任务与回调复用）
    xt_trader = connect()
    acc = StockAccount(config.account_id)

    # 查询并打印账户资金（连接刚建立，少量重试等待数据同步）
    asset = None
    for _ in range(3):
        asset = xt_trader.query_stock_asset(acc)
        if asset is not None:
            break
        time.sleep(0.5)
    if asset is not None:
        logger.info(
            '账户 %s 资金: 总资产 %.2f, 可用 %.2f, 冻结 %.2f, 持仓市值 %.2f',
            config.account_id, asset.total_asset, asset.cash,
            asset.frozen_cash, asset.market_value,
        )
    else:
        logger.warning('查询账户 %s 资金失败', config.account_id)

    # 2. 启动后台调度器，注册定时任务
    scheduler = BackgroundScheduler(timezone='Asia/Shanghai')
    scheduler.add_job(
        scheduled_repo,
        CronTrigger(day_of_week='mon-fri', hour=SCHEDULE_TIME[0], minute=SCHEDULE_TIME[1]),
        id='repo_daily',
        misfire_grace_time=120,
    )
    scheduler.start()
    logger.info('定时任务已启动，每个交易日 %02d:%02d 自动执行逆回购', *SCHEDULE_TIME)
    logger.info('daemon 运行中，按 Ctrl+C 退出')

    # 3. 阻塞主线程；未来在此注册 QMT 回调（行情订阅、订单回报、通知推送）
    #    xt_trader.register_callback(...) / xtdata.subscribe_quote(...)
    #
    #    注意：threading.Event().wait() 不带超时时在 Windows 上无法被
    #    Ctrl+C 打断，必须给一个超时值（哪怕很大），让主线程周期性返回
    #    控制权给解释器，信号处理器才能抛出 KeyboardInterrupt。
    stop_event = threading.Event()
    try:
        while not stop_event.wait(1):
            pass
    except (KeyboardInterrupt, SystemExit):
        logger.info('正在停止 daemon...')
    finally:
        scheduler.shutdown(wait=False)
        disconnect()
        logger.info('已退出')


if __name__ == '__main__':
    main()
