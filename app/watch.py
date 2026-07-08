# -*- coding: utf-8 -*-
"""
股价监控功能

支持设置上涨/下跌触发价，达到阈值时通过钉钉发送通知。
触发后标记为已触发，避免重复通知；可通过 reset 重置或删除后重新添加。

用法:
    python -m app watch add <name_or_code> <above_price> <below_price>
    python -m app watch list
    python -m app watch delete <id>
    python -m app watch reset <id>
    python -m app watch now    # 立即检查一次（测试用）
"""
import time
import datetime
import logging
import argparse

from sqlalchemy import select

from .qmt import connect, xtdata
from .db import Watch, Stock, SessionLocal, init_db
from .trade import resolve_code
from .repo import is_trading_day
from .notify.dingtalk import send as dingtalk_send

logger = logging.getLogger(__name__)


def is_trading_time(now=None):
    """判断当前是否在交易时段内（9:30-11:30, 13:00-15:00）"""
    if now is None:
        now = datetime.datetime.now()
    t = now.time()
    morning = datetime.time(9, 30) <= t <= datetime.time(11, 30)
    afternoon = datetime.time(13, 0) <= t <= datetime.time(15, 0)
    return morning or afternoon


# ============================================================
#  CRUD 操作
# ============================================================

def add_watch(name_or_code: str, above_price: float, below_price: float) -> Watch:
    """添加一条监控规则。

    above_price=0 或 below_price=0 表示不设置该方向阈值（转为 None）。
    """
    # 0 值视为未设置
    above = above_price if above_price and above_price > 0 else None
    below = below_price if below_price and below_price > 0 else None

    if above is None and below is None:
        raise ValueError('上涨触发价和下跌触发价不能同时为空（至少设置一个 >0 的值）')

    code = resolve_code(name_or_code)
    # 查询股票名称用于展示
    with SessionLocal() as s:
        stock = s.scalar(select(Stock).where(Stock.code == code))
        name = stock.name if stock else name_or_code

    init_db()
    watch = Watch(name=name, code=code, above_price=above, below_price=below)
    with SessionLocal() as s:
        s.add(watch)
        s.commit()
        s.refresh(watch)
    logger.info(f'已添加监控: {watch}')
    return watch


def list_watches() -> list[Watch]:
    """返回所有监控规则"""
    init_db()
    with SessionLocal() as s:
        return list(s.scalars(select(Watch).order_by(Watch.id)))


def delete_watch(watch_id: int) -> bool:
    """删除指定 id 的监控规则，返回是否删除成功"""
    init_db()
    with SessionLocal() as s:
        watch = s.get(Watch, watch_id)
        if watch is None:
            return False
        s.delete(watch)
        s.commit()
        logger.info(f'已删除监控: id={watch_id} {watch.name}({watch.code})')
        return True


def reset_watch(watch_id: int) -> bool:
    """重置指定规则的触发状态，使其可再次触发，返回是否重置成功"""
    init_db()
    with SessionLocal() as s:
        watch = s.get(Watch, watch_id)
        if watch is None:
            return False
        watch.above_triggered = False
        watch.below_triggered = False
        watch.above_triggered_at = None
        watch.below_triggered_at = None
        s.commit()
        logger.info(f'已重置监控: id={watch_id} {watch.name}({watch.code})')
        return True


# ============================================================
#  核心检查逻辑
# ============================================================

def check_watches():
    """执行一次股价检查：获取所有未完全触发的监控股票行情，比对阈值并通知。

    单只股票异常不影响其他股票的检查。
    """
    init_db()
    # 查询未完全触发的规则（至少有一个方向尚未触发）
    with SessionLocal() as s:
        watches = list(s.scalars(
            select(Watch).where(
                (Watch.above_price.is_not(None) & (Watch.above_triggered == False)) |
                (Watch.below_price.is_not(None) & (Watch.below_triggered == False))
            )
        ))

    if not watches:
        logger.debug('没有需要检查的监控规则')
        return

    # 收集需要获取行情的代码（去重）
    codes = list({w.code for w in watches})
    logger.info(f'检查 {len(watches)} 条监控规则，涉及 {len(codes)} 只股票')

    # 订阅行情
    for code in codes:
        try:
            xtdata.subscribe_quote(code)
        except Exception as e:
            logger.warning(f'订阅 {code} 行情失败: {e}')
    time.sleep(0.3)  # 等待数据推送

    # 获取最新行情
    try:
        ticks = xtdata.get_full_tick(codes) or {}
    except Exception as e:
        logger.error(f'获取行情失败: {e}')
        return

    now = datetime.datetime.now()
    triggered_count = 0

    for w in watches:
        try:
            tick = ticks.get(w.code)
            if not tick:
                logger.warning(f'未获取到 {w.name}({w.code}) 的行情数据')
                continue
            price = tick.get('lastPrice')
            if price is None or price <= 0:
                logger.warning(f'{w.name}({w.code}) 价格异常: {price}')
                continue

            triggered = False
            messages = []

            # 检查上涨触发
            if (w.above_price is not None and not w.above_triggered
                    and price >= w.above_price):
                messages.append(
                    f'【涨破提醒】{w.name}({w.code})\n当前价格：{price:.2f}元，已涨破您设置的{w.above_price:.2f}元阈值'
                )
                w.above_triggered = True
                w.above_triggered_at = now
                triggered = True

            # 检查下跌触发
            if (w.below_price is not None and not w.below_triggered
                    and price <= w.below_price):
                messages.append(
                    f'【跌破提醒】{w.name}({w.code})\n当前价格：{price:.2f}元，已跌破您设置的{w.below_price:.2f}元阈值'
                )
                w.below_triggered = True
                w.below_triggered_at = now
                triggered = True

            if triggered:
                msg_parts = messages + [f'触发时间：{now.strftime("%Y-%m-%d %H:%M:%S")}']
                notify_msg = '\n'.join(msg_parts)
                logger.info(f'触发通知:\n{notify_msg}')
                try:
                    dingtalk_send(notify_msg)
                except Exception as e:
                    logger.error(f'发送钉钉通知失败: {e}')
                triggered_count += 1

                # 更新数据库状态
                with SessionLocal() as s:
                    s.merge(w)
                    s.commit()

        except Exception as e:
            logger.exception(f'检查 {w.name}({w.code}) 时发生异常: {e}')
            continue

    if triggered_count:
        logger.info(f'本轮检查共触发 {triggered_count} 条通知')


# ============================================================
#  定时任务入口
# ============================================================

def scheduled_watch():
    """APScheduler 定时任务入口：交易时段每分钟检查股价。"""
    if not is_trading_day():
        logger.debug('今日非交易日，跳过股价检查')
        return
    if not is_trading_time():
        logger.debug('当前非交易时段，跳过股价检查')
        return
    try:
        check_watches()
    except Exception:
        logger.exception('股价检查执行异常')


# ============================================================
#  CLI 入口
# ============================================================

def main(argv=None):
    """命令行入口: python -m app watch <subcommand>"""
    parser = argparse.ArgumentParser(prog='python -m app watch', description='股价监控管理')
    sub = parser.add_subparsers(dest='action')

    # add
    p_add = sub.add_parser('add', help='添加监控规则')
    p_add.add_argument('name_or_code', help='股票名称或6位代码')
    p_add.add_argument('above_price', type=float, help='上涨触发价（传0表示不设置）')
    p_add.add_argument('below_price', type=float, help='下跌触发价（传0表示不设置）')

    # list
    sub.add_parser('list', help='查看所有监控规则')

    # delete
    p_del = sub.add_parser('delete', help='删除监控规则')
    p_del.add_argument('id', type=int, help='监控规则ID')

    # reset
    p_reset = sub.add_parser('reset', help='重置触发状态')
    p_reset.add_argument('id', type=int, help='监控规则ID')

    # now (立即执行一次检查)
    sub.add_parser('now', help='立即执行一次股价检查（测试用）')

    args = parser.parse_args(argv)

    if args.action == 'add':
        w = add_watch(args.name_or_code, args.above_price, args.below_price)
        above_str = f'{w.above_price:.2f}' if w.above_price else '-'
        below_str = f'{w.below_price:.2f}' if w.below_price else '-'
        print(f'已添加监控: id={w.id} {w.name}({w.code}) 上涨阈值={above_str} 下跌阈值={below_str}')
    elif args.action == 'list':
        watches = list_watches()
        if not watches:
            print('暂无监控规则')
        else:
            print(f'{"ID":<4} {"股票名称":<10} {"代码":<12} {"上涨阈值":<10} {"下跌阈值":<10} {"上涨已触发":<10} {"下跌已触发":<10} {"创建时间":<20}')
            print('-' * 95)
            for w in watches:
                above_str = f'{w.above_price:.2f}' if w.above_price else '-'
                below_str = f'{w.below_price:.2f}' if w.below_price else '-'
                print(f'{w.id:<4} {w.name:<10} {w.code:<12} {above_str:<10} {below_str:<10} '
                      f'{"是" if w.above_triggered else "否":<10} '
                      f'{"是" if w.below_triggered else "否":<10} '
                      f'{w.created_at.strftime("%Y-%m-%d %H:%M"):<20}')
    elif args.action == 'delete':
        if delete_watch(args.id):
            print(f'已删除监控规则 id={args.id}')
        else:
            print(f'未找到 id={args.id} 的监控规则')
    elif args.action == 'reset':
        if reset_watch(args.id):
            print(f'已重置监控规则 id={args.id}')
        else:
            print(f'未找到 id={args.id} 的监控规则')
    elif args.action == 'now':
        # 立即执行需要先确保QMT连接
        connect()
        check_watches()
    else:
        parser.print_help()
