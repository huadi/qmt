# -*- coding: utf-8 -*-
"""
app 包入口，支持 `python -m app` 运行。

负责运行时初始化（sys.path）和子命令派发。

用法:
    python -m app init-db                     # 初始化数据库（刷新股票名称到数据库）
    python -m app trade buy  隆基绿能 12.40 800
    python -m app trade sell 隆基绿能 12.40 800
    python -m app serve                        # daemon：常驻运行，定时任务 + 回调
    python -m app watch add 隆基绿能 10.0 15.0  # 添加股价监控（跌破10或涨破15通知，先跌后涨顺序）
    python -m app watch list                   # 查看所有监控规则
    python -m app watch update 深信服 90 130    # 更新深信服监控阈值为跌破90/涨破130，支持传ID/股票名/代码
    python -m app watch delete 300454          # 删除深信服的监控，支持传ID/股票名/代码
    python -m app watch reset 深信服            # 重置深信服的触发状态，支持传ID/股票名/代码
    python -m app watch now                    # 立即执行一次股价检查（测试）
"""
import os
import sys
import argparse
import logging
import threading
import time
from logging.handlers import RotatingFileHandler

from . import config

_QMT_SITE = os.path.join(os.path.dirname(config.mini_path), 'bin.x64', 'Lib', 'site-packages')
if _QMT_SITE not in sys.path:
    sys.path.insert(0, _QMT_SITE)

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

_LOG_FORMAT = '%(asctime)s [%(name)s] %(levelname)s: %(message)s'


def _setup_scheduler_logging() -> None:
    """配置 apscheduler 日志输出到 logs/scheduler.log（轮转），不传播到 root logger。"""
    os.makedirs('logs', exist_ok=True)
    scheduler_logger = logging.getLogger('apscheduler')
    scheduler_logger.setLevel(logging.INFO)
    scheduler_logger.propagate = False
    # 防止重复挂载 handler（如 _serve 被多次调用时）
    if not scheduler_logger.handlers:
        handler = RotatingFileHandler(
            'logs/scheduler.log',
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding='utf-8',
        )
        handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        scheduler_logger.addHandler(handler)


def main():
    parser = argparse.ArgumentParser(prog='python -m app', description='QMT 交易工具')
    sub = parser.add_subparsers(dest='command')

    # init-db 子命令
    sub.add_parser('init-db', help='初始化数据库（刷新股票名称缓存）')

    # trade 子命令
    p_trade = sub.add_parser('trade', help='手动下单')
    p_trade.add_argument('direction', choices=['buy', 'sell'], help='买(buy)或卖(sell)')
    p_trade.add_argument('name_or_code', help='股票名称或6位代码')
    p_trade.add_argument('price', type=float, help='委托价格')
    p_trade.add_argument('volume', type=int, help='委托数量(股)')

    # watch 子命令（股价监控管理）
    p_watch = sub.add_parser('watch', help='股价监控管理')
    p_watch.add_argument('args', nargs=argparse.REMAINDER, help='子命令参数: add/list/delete/reset/now')

    # serve 子命令（daemon 模式）
    sub.add_parser('serve', help='启动常驻服务（定时任务 + 回调）')

    args = parser.parse_args()

    if args.command == 'init-db':
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
    elif args.command == 'watch':
        from .watch import main as watch_main
        watch_main(args.args)
    elif args.command == 'serve':
        _serve()
    else:
        parser.print_help()


def _serve():
    """daemon 模式：建立共享连接 + 启动后台调度器 + 阻塞主线程（24h 常驻）"""
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    from .db import init_db
    from .qmt import connect, disconnect, get_account
    from .repo import scheduled_repo, SCHEDULE_TIME
    from .watch import scheduled_watch

    # 0. 统一初始化数据库表结构
    init_db()

    _setup_scheduler_logging()

    # 1. 建立共享 QMT 连接（单例，供所有定时任务与回调复用）
    xt_trader = connect()
    acc = get_account()

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
    # 股价监控：周一到周五 9-11点、13-15点 每分钟执行，非交易时段在函数内跳过
    scheduler.add_job(
        scheduled_watch,
        CronTrigger(day_of_week='mon-fri', hour='9-11,13-15', minute='*', second='0'),
        id='price_watch',
        misfire_grace_time=30,
        coalesce=True,
    )
    scheduler.start()
    logger.info('定时任务已启动，每个交易日 %02d:%02d 自动执行逆回购', *SCHEDULE_TIME)
    logger.info('股价监控已启动，交易时段（9:30-11:30/13:00-15:00）每分钟检查一次价格，每个阈值每日最多通知1次')
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
