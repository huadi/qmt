# -*- coding: utf-8 -*-
"""
股价监控功能

支持设置上涨/下跌触发价，达到阈值时通过钉钉发送通知。
每个阈值方向**每日最多通知1次**，次日自动恢复通知，无需手动重置；也可通过 reset 手动清空当日触发状态。

用法:
    python -m app watch add <name_or_code> <below_price> <above_price>
    python -m app watch list
    python -m app watch update <id/name/code> <new_below_price> <new_above_price>  # 更新阈值，自动重置触发状态
    python -m app watch delete <id/name/code>
    python -m app watch reset <id/name/code>
    python -m app watch now    # 立即检查一次（测试用）
    * 参数顺序：先写下跌阈值（跌破提醒的低价，传0表示不设置），后写上涨阈值（涨破提醒的高价，传0表示不设置）
    * update/delete/reset 均支持传入ID、股票名称或6位代码，同一只股票有多条规则时请传ID指定
    * 示例：监控深信服跌破90/涨破130通知 → python -m app watch add 深信服 90 130
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

def add_watch(name_or_code: str, below_price: float, above_price: float) -> Watch:
    """添加一条监控规则。

    below_price=0 或 above_price=0 表示不设置该方向阈值（转为 None）。
    参数顺序：先写下跌阈值（跌破提醒的低价），后写上涨阈值（涨破提醒的高价）。
    """
    # 0 值视为未设置
    below = below_price if below_price and below_price > 0 else None
    above = above_price if above_price and above_price > 0 else None

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


def update_watch(watch_id: int, below_price: float, above_price: float) -> Watch | None:
    """更新指定ID监控的阈值，同时自动重置触发状态，返回更新后的规则，不存在则返回None。

    below_price=0 或 above_price=0 表示关闭对应方向的监控（设为None）。
    参数顺序：先写下跌阈值（跌破提醒的低价），后写上涨阈值（涨破提醒的高价）。
    """
    # 0 值视为关闭该方向
    below = below_price if below_price and below_price > 0 else None
    above = above_price if above_price and above_price > 0 else None

    if above is None and below is None:
        raise ValueError('上涨触发价和下跌触发价不能同时为空（至少设置一个 >0 的值）')

    init_db()
    with SessionLocal() as s:
        watch = s.get(Watch, watch_id)
        if watch is None:
            return None
        # 更新阈值
        watch.above_price = above
        watch.below_price = below
        # 阈值变更后重置触发状态
        watch.above_triggered = False
        watch.below_triggered = False
        watch.above_triggered_at = None
        watch.below_triggered_at = None
        s.commit()
        s.refresh(watch)
    logger.info(f'已更新监控: {watch}')
    return watch


def resolve_watch(identifier: str) -> Watch | list[Watch] | None:
    """根据标识符查找监控规则，支持三种匹配方式：
    1. 数字ID：优先按ID精确匹配，匹配不到则尝试匹配股票代码/名称
    2. 股票名称
    3. 6位股票代码

    返回值：
    - None: 未找到任何匹配规则
    - Watch: 唯一匹配的单条规则
    - list[Watch]: 匹配到多条规则（同一只股票设置了多个监控时出现）
    """
    init_db()
    # 1. 优先尝试按数字ID匹配
    watch_id = None
    if identifier.isdigit():
        try:
            watch_id = int(identifier)
        except ValueError:
            pass
    if watch_id is not None:
        with SessionLocal() as s:
            w = s.get(Watch, watch_id)
            if w is not None:
                return w

    # 2. 按股票名称/代码匹配
    try:
        code = resolve_code(identifier)
    except ValueError:
        # 既不是有效ID，也不是有效股票名/代码
        return None
    with SessionLocal() as s:
        watches = list(s.scalars(select(Watch).where(Watch.code == code)))
        if not watches:
            return None
        if len(watches) == 1:
            return watches[0]
        return watches


# ============================================================
#  核心检查逻辑
# ============================================================

def check_watches():
    """执行一次股价检查：获取所有未完全触发的监控股票行情，比对阈值并通知。

    单只股票异常不影响其他股票的检查。
    """
    init_db()
    today = datetime.date.today()
    # 查询所有设置了至少一个阈值的监控规则（每日自动重置，无需排除已触发的）
    with SessionLocal() as s:
        watches = list(s.scalars(
            select(Watch).where(
                Watch.above_price.is_not(None) | Watch.below_price.is_not(None)
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

            # 检查上涨触发：每日最多1次（未触发过 或 最后触发不是今天）
            above_can_trigger = (w.above_price is not None
                and (w.above_triggered_at is None or w.above_triggered_at.date() < today))
            if above_can_trigger and price >= w.above_price:
                messages.append(
                    f'【涨破提醒】{w.name}({w.code})\n当前价格：{price:.2f}元，已涨破您设置的{w.above_price:.2f}元阈值'
                )
                w.above_triggered = True
                w.above_triggered_at = now
                triggered = True

            # 检查下跌触发：每日最多1次（未触发过 或 最后触发不是今天）
            below_can_trigger = (w.below_price is not None
                and (w.below_triggered_at is None or w.below_triggered_at.date() < today))
            if below_can_trigger and price <= w.below_price:
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
    p_add.add_argument('below_price', type=float, help='下跌触发价（跌破该价格提醒，传0表示不设置）')
    p_add.add_argument('above_price', type=float, help='上涨触发价（涨破该价格提醒，传0表示不设置）')

    # list
    sub.add_parser('list', help='查看所有监控规则')

    # delete
    p_del = sub.add_parser('delete', help='删除监控规则')
    p_del.add_argument('id', type=str, help='监控规则ID、股票名称或6位代码（同股票有多条规则时请传ID）')

    # reset
    p_reset = sub.add_parser('reset', help='重置触发状态')
    p_reset.add_argument('id', type=str, help='监控规则ID、股票名称或6位代码（同股票有多条规则时请传ID）')

    # update
    p_update = sub.add_parser('update', help='更新监控阈值（自动重置触发状态）')
    p_update.add_argument('id', type=str, help='监控规则ID、股票名称或6位代码（同股票有多条规则时请传ID）')
    p_update.add_argument('below_price', type=float, help='新的下跌触发价（跌破该价格提醒，传0表示关闭该方向监控）')
    p_update.add_argument('above_price', type=float, help='新的上涨触发价（涨破该价格提醒，传0表示关闭该方向监控）')

    # now (立即执行一次检查)
    sub.add_parser('now', help='立即执行一次股价检查（测试用）')

    args = parser.parse_args(argv)

    if args.action == 'add':
        w = add_watch(args.name_or_code, args.below_price, args.above_price)
        below_str = f'{w.below_price:.2f}' if w.below_price else '-'
        above_str = f'{w.above_price:.2f}' if w.above_price else '-'
        print(f'已添加监控: id={w.id} {w.name}({w.code}) 下跌阈值={below_str} 上涨阈值={above_str}')
    elif args.action == 'list':
        watches = list_watches()
        if not watches:
            print('暂无监控规则')
        else:
            today = datetime.date.today()
            print(f'{"ID":<4} {"股票名称":<10} {"代码":<12} {"下跌阈值":<10} {"上涨阈值":<10} {"今日跌破已通知":<14} {"今日涨破已通知":<14} {"创建时间":<20}')
            print('-' * 105)
            for w in watches:
                below_str = f'{w.below_price:.2f}' if w.below_price else '-'
                above_str = f'{w.above_price:.2f}' if w.above_price else '-'
                below_today = w.below_triggered_at is not None and w.below_triggered_at.date() == today
                above_today = w.above_triggered_at is not None and w.above_triggered_at.date() == today
                print(f'{w.id:<4} {w.name:<10} {w.code:<12} {below_str:<10} {above_str:<10} '
                      f'{"是" if below_today else "否":<14} '
                      f'{"是" if above_today else "否":<14} '
                      f'{w.created_at.strftime("%Y-%m-%d %H:%M"):<20}')
    elif args.action == 'delete':
        result = resolve_watch(args.id)
        if result is None:
            print(f'未找到匹配的监控规则：{args.id}')
        elif isinstance(result, list):
            print(f'找到{len(result)}条匹配的监控规则，请使用ID指定要删除的规则：')
            for w in result:
                below_str = f'{w.below_price:.2f}' if w.below_price else '-'
                above_str = f'{w.above_price:.2f}' if w.above_price else '-'
                print(f'  id={w.id} {w.name}({w.code}) 下跌阈值={below_str} 上涨阈值={above_str}')
        else:
            if delete_watch(result.id):
                print(f'已删除监控规则 id={result.id} {result.name}({result.code})')
    elif args.action == 'reset':
        result = resolve_watch(args.id)
        if result is None:
            print(f'未找到匹配的监控规则：{args.id}')
        elif isinstance(result, list):
            print(f'找到{len(result)}条匹配的监控规则，请使用ID指定要重置的规则：')
            for w in result:
                below_str = f'{w.below_price:.2f}' if w.below_price else '-'
                above_str = f'{w.above_price:.2f}' if w.above_price else '-'
                print(f'  id={w.id} {w.name}({w.code}) 下跌阈值={below_str} 上涨阈值={above_str}')
        else:
            if reset_watch(result.id):
                print(f'已重置监控规则 id={result.id} {result.name}({result.code})')
    elif args.action == 'update':
        result = resolve_watch(args.id)
        if result is None:
            print(f'未找到匹配的监控规则：{args.id}')
            return
        elif isinstance(result, list):
            print(f'找到{len(result)}条匹配的监控规则，请使用ID指定要更新的规则：')
            for w in result:
                below_str = f'{w.below_price:.2f}' if w.below_price else '-'
                above_str = f'{w.above_price:.2f}' if w.above_price else '-'
                print(f'  id={w.id} {w.name}({w.code}) 下跌阈值={below_str} 上涨阈值={above_str}')
            return
        try:
            w = update_watch(result.id, args.below_price, args.above_price)
        except ValueError as e:
            print(f'更新失败: {e}')
            return
        below_str = f'{w.below_price:.2f}' if w.below_price else '-'
        above_str = f'{w.above_price:.2f}' if w.above_price else '-'
        print(f'已更新监控: id={w.id} {w.name}({w.code}) 下跌阈值={below_str} 上涨阈值={above_str}，触发状态已自动重置')
    elif args.action == 'now':
        # 立即执行需要先确保QMT连接
        connect()
        check_watches()
    else:
        parser.print_help()
