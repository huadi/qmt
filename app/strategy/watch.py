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

from ..qmt import QmtClient
from ..db import Watch, Stock, SessionLocal, init_db
from ..stockutil import resolve_code
from ..tradeutil import is_trading_day, is_trading_time
from ..notify import send as notify_send

logger = logging.getLogger(__name__)

# 调度参数：交易时段每分钟检查一次（周一到周五）。小时范围覆盖沪深两市交易时段，
# 由 __main__._serve 注册 cron 时引用，策略自带调度参数（与 repo.SCHEDULE_TIME 对齐）。
SCHEDULE_HOURS = '9-11,13-15'

# 已通过 QmtClient.subscribe_quote 订阅的代码集合，避免每分钟重复订阅。
# 每天换日时清空一次强制重订阅：既能跨天恢复行情推送（防止 QMT 中途重连后
# 本进程仍以为已订阅而哑掉），又控制了订阅 seq 的日内堆积。即使某天重连未
# 及时恢复，最多当天监控失效，次日自动恢复——对阈值提醒足够稳健。
_subscribed: set[str] = set()
_subscribed_date: datetime.date | None = None


# ============================================================
#  CRUD 操作
# ============================================================

def _normalize_threshold(value: float) -> float | None:
    """将 CLI 传入的阈值归一化：0/负数/None 视为未设置，返回 None；否则返回原值。"""
    return value if value is not None and value > 0 else None


def _reset_trigger_state(watch: Watch) -> None:
    """重置一条规则的触发状态（清空触发时间戳）。"""
    watch.above_triggered_at = None
    watch.below_triggered_at = None


def _validate_thresholds(below: float | None, above: float | None) -> None:
    """校验阈值合法性：不能同时为空，且下限必须 < 上限（若两者都设置）。"""
    if above is None and below is None:
        raise ValueError('上涨触发价和下跌触发价不能同时为空（至少设置一个 >0 的值）')
    if above is not None and below is not None and below >= above:
        raise ValueError(f'下跌阈值({below:.2f})必须小于上涨阈值({above:.2f})，参数顺序：先跌后涨')


def add_watch(name_or_code: str, below_price: float, above_price: float) -> Watch:
    """添加一条监控规则。

    below_price=0 或 above_price=0 表示不设置该方向阈值（转为 None）。
    参数顺序：先写下跌阈值（跌破提醒的低价），后写上涨阈值（涨破提醒的高价）。
    """
    below = _normalize_threshold(below_price)
    above = _normalize_threshold(above_price)
    _validate_thresholds(below, above)

    code = resolve_code(name_or_code)
    with SessionLocal() as s:
        stock = s.scalar(select(Stock).where(Stock.code == code))
        name = stock.name if stock else name_or_code
        watch = Watch(name=name, code=code, above_price=above, below_price=below)
        s.add(watch)
        s.commit()
        s.refresh(watch)
    logger.info(f'已添加监控: {watch}')
    return watch


def list_watches() -> list[Watch]:
    """返回所有监控规则"""
    with SessionLocal() as s:
        return list(s.scalars(select(Watch).order_by(Watch.id)))


def delete_watch(watch_id: int) -> bool:
    """删除指定 id 的监控规则，返回是否删除成功"""
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
    with SessionLocal() as s:
        watch = s.get(Watch, watch_id)
        if watch is None:
            return False
        _reset_trigger_state(watch)
        s.commit()
        logger.info(f'已重置监控: id={watch_id} {watch.name}({watch.code})')
        return True


def update_watch(watch_id: int, below_price: float, above_price: float) -> Watch | None:
    """更新指定ID监控的阈值，同时自动重置触发状态，返回更新后的规则，不存在则返回None。

    below_price=0 或 above_price=0 表示关闭对应方向的监控（设为None）。
    参数顺序：先写下跌阈值（跌破提醒的低价），后写上涨阈值（涨破提醒的高价）。
    """
    below = _normalize_threshold(below_price)
    above = _normalize_threshold(above_price)
    _validate_thresholds(below, above)

    with SessionLocal() as s:
        watch = s.get(Watch, watch_id)
        if watch is None:
            return None
        watch.above_price = above
        watch.below_price = below
        _reset_trigger_state(watch)  # 阈值变更后重置触发状态
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
    """执行一次股价检查：获取所有监控股票行情，比对阈值并通知。

    流程：(1) 基于快照粗筛应触发的规则；(2) 在单个 DB 事务中基于最新状态 CAS 写入触发时间；
    (3) 提交成功后才发送钉钉通知，避免"已通知但未落库"导致重复提醒。
    单只股票行情异常不影响其他股票。
    """
    today = datetime.date.today()

    # 换日清空订阅集合：跨天强制重订阅，恢复因 QMT 重连丢失的行情推送
    global _subscribed_date
    if _subscribed_date != today:
        if _subscribed_date is not None:
            logger.info('换日，清空行情订阅集合，本轮将重新订阅 %d 只股票', len(_subscribed))
        _subscribed.clear()
        _subscribed_date = today

    with SessionLocal() as s:
        watches = list(s.scalars(
            select(Watch).where(
                Watch.above_price.is_not(None) | Watch.below_price.is_not(None)
            )
        ))

    if not watches:
        logger.debug('没有需要检查的监控规则')
        return

    codes = list({w.code for w in watches})
    logger.info(f'检查 {len(watches)} 条监控规则，涉及 {len(codes)} 只股票')

    # 仅订阅新出现的代码（已订阅的跳过），避免每分钟重复订阅
    new_codes = [c for c in codes if c not in _subscribed]
    for code in new_codes:
        try:
            QmtClient.subscribe_quote(code)
            _subscribed.add(code)
        except Exception as e:
            logger.warning(f'订阅 {code} 行情失败: {e}')
    if new_codes:
        time.sleep(0.3)  # 首次订阅后等待数据推送；已订阅的数据已在本地缓存，无需再等

    try:
        ticks = QmtClient.get_full_tick(codes) or {}
    except Exception as e:
        logger.error(f'获取行情失败: {e}')
        return

    now = datetime.datetime.now()

    # 阶段 1：基于快照粗筛本轮"可能触发"的规则（不写 DB）
    candidates: dict[int, dict] = {}  # watch_id -> info
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

            above_hit = (w.above_price is not None
                and (w.above_triggered_at is None or w.above_triggered_at.date() < today)
                and price >= w.above_price)
            below_hit = (w.below_price is not None
                and (w.below_triggered_at is None or w.below_triggered_at.date() < today)
                and price <= w.below_price)
            if not (above_hit or below_hit):
                continue

            # 按方向分别构造消息，阶段 2 根据 CAS 结果直接取用
            above_msg = (
                f'【涨破提醒】{w.name}({w.code})\n当前价格：{price:.2f}元，已涨破您设置的{w.above_price:.2f}元阈值'
                if above_hit else None
            )
            below_msg = (
                f'【跌破提醒】{w.name}({w.code})\n当前价格：{price:.2f}元，已跌破您设置的{w.below_price:.2f}元阈值'
                if below_hit else None
            )
            candidates[w.id] = {'above_msg': above_msg, 'below_msg': below_msg}
        except Exception as e:
            logger.exception(f'检查 {w.name}({w.code}) 行情时发生异常: {e}')
            continue

    if not candidates:
        return

    # 阶段 2：单个事务里，基于 DB 最新状态 CAS 写入触发时间（防并发重复通知 + 防 stale merge 覆盖 CLI 修改）
    triggered: list[tuple[str, str]] = []  # (name/code 标签, notify_msg)
    with SessionLocal() as s:
        for wid, info in candidates.items():
            w = s.get(Watch, wid)
            if w is None:
                continue
            msgs = []
            # 基于最新 DB 状态二次确认"今日尚未触发"，避免并发 reset/update/重复执行导致的重复通知
            if info['above_msg'] and w.above_price is not None and (
                w.above_triggered_at is None or w.above_triggered_at.date() < today
            ):
                w.above_triggered_at = now
                msgs.append(info['above_msg'])
            if info['below_msg'] and w.below_price is not None and (
                w.below_triggered_at is None or w.below_triggered_at.date() < today
            ):
                w.below_triggered_at = now
                msgs.append(info['below_msg'])
            if msgs:
                msgs.append(f'触发时间：{now.strftime("%Y-%m-%d %H:%M:%S")}')
                triggered.append((f'{w.name}({w.code})', '\n'.join(msgs)))
        s.commit()

    # 阶段 3：DB 提交成功后才真正发送通知（若 commit 抛异常，不会走到这里）
    for tag, msg in triggered:
        logger.info(f'触发通知 ({tag}):\n{msg}')
        try:
            notify_send(msg)
        except Exception as e:
            logger.error(f'发送通知失败 ({tag}): {e}')

    if triggered:
        logger.info(f'本轮检查共触发 {len(triggered)} 条通知')


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
#  CLI 工具函数
# ============================================================

def _fmt_threshold(v) -> str:
    return f'{v:.2f}' if v else '-'


def _fmt_watch_brief(w: Watch) -> str:
    return f'id={w.id} {w.name}({w.code}) 下跌阈值={_fmt_threshold(w.below_price)} 上涨阈值={_fmt_threshold(w.above_price)}'


def _resolve_cli_target(identifier: str, action: str) -> Watch | list[Watch] | None:
    """CLI 辅助：解析标识符；若未找到/多匹配则打印提示并返回 None/list，由调用方处理。"""
    result = resolve_watch(identifier)
    if result is None:
        print(f'未找到匹配的监控规则：{identifier}')
        return None
    if isinstance(result, list):
        print(f'找到{len(result)}条匹配的监控规则，请使用ID指定要{action}的规则：')
        for w in result:
            print(f'  {_fmt_watch_brief(w)}')
        return result
    return result


# ============================================================
#  CLI 入口
# ============================================================

def main(argv=None):
    """命令行入口: python -m app watch <subcommand>"""
    parser = argparse.ArgumentParser(prog='python -m app watch', description='股价监控管理')
    sub = parser.add_subparsers(dest='action')

    p_add = sub.add_parser('add', help='添加监控规则')
    p_add.add_argument('name_or_code', help='股票名称或6位代码')
    p_add.add_argument('below_price', type=float, help='下跌触发价（跌破该价格提醒，传0表示不设置）')
    p_add.add_argument('above_price', type=float, help='上涨触发价（涨破该价格提醒，传0表示不设置）')

    sub.add_parser('list', help='查看所有监控规则')

    p_del = sub.add_parser('delete', help='删除监控规则')
    p_del.add_argument('id', type=str, help='监控规则ID、股票名称或6位代码（同股票有多条规则时请传ID）')

    p_reset = sub.add_parser('reset', help='重置触发状态')
    p_reset.add_argument('id', type=str, help='监控规则ID、股票名称或6位代码（同股票有多条规则时请传ID）')

    p_update = sub.add_parser('update', help='更新监控阈值（自动重置触发状态）')
    p_update.add_argument('id', type=str, help='监控规则ID、股票名称或6位代码（同股票有多条规则时请传ID）')
    p_update.add_argument('below_price', type=float, help='新的下跌触发价（跌破该价格提醒，传0表示关闭该方向监控）')
    p_update.add_argument('above_price', type=float, help='新的上涨触发价（涨破该价格提醒，传0表示关闭该方向监控）')

    sub.add_parser('now', help='立即执行一次股价检查（测试用）')

    args = parser.parse_args(argv)

    # 统一初始化数据库表结构，避免各函数重复调用
    init_db()

    if args.action == 'add':
        w = add_watch(args.name_or_code, args.below_price, args.above_price)
        print(f'已添加监控: {_fmt_watch_brief(w)}')
    elif args.action == 'list':
        watches = list_watches()
        if not watches:
            print('暂无监控规则')
        else:
            today = datetime.date.today()
            print(f'{"ID":<4} {"股票名称":<10} {"代码":<12} {"下跌阈值":<10} {"上涨阈值":<10} {"今日跌破已通知":<14} {"今日涨破已通知":<14} {"创建时间":<20}')
            print('-' * 105)
            for w in watches:
                below_today = w.below_triggered_at is not None and w.below_triggered_at.date() == today
                above_today = w.above_triggered_at is not None and w.above_triggered_at.date() == today
                print(f'{w.id:<4} {w.name:<10} {w.code:<12} {_fmt_threshold(w.below_price):<10} {_fmt_threshold(w.above_price):<10} '
                      f'{"是" if below_today else "否":<14} '
                      f'{"是" if above_today else "否":<14} '
                      f'{w.created_at.strftime("%Y-%m-%d %H:%M"):<20}')
    elif args.action == 'delete':
        result = _resolve_cli_target(args.id, '删除')
        if isinstance(result, Watch):
            if delete_watch(result.id):
                print(f'已删除监控规则 id={result.id} {result.name}({result.code})')
    elif args.action == 'reset':
        result = _resolve_cli_target(args.id, '重置')
        if isinstance(result, Watch):
            if reset_watch(result.id):
                print(f'已重置监控规则 id={result.id} {result.name}({result.code})')
    elif args.action == 'update':
        result = _resolve_cli_target(args.id, '更新')
        if isinstance(result, Watch):
            try:
                w = update_watch(result.id, args.below_price, args.above_price)
            except ValueError as e:
                print(f'更新失败: {e}')
                return
            print(f'已更新监控: {_fmt_watch_brief(w)}，触发状态已自动重置')
    elif args.action == 'now':
        QmtClient.connect()
        check_watches()
    else:
        parser.print_help()
