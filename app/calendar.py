# -*- coding: utf-8 -*-
"""
交易日历工具（统一时区 + 交易时段判断）。

中国股市始终使用 UTC+8（无夏令时）。APScheduler 配置 timezone='Asia/Shanghai'，
这里所有 now()/today() 都基于 UTC+8 计算，避免部署到非 CST 时区服务器时
cron 触发时间与业务时间判断不一致。

注意：返回的 datetime 是 **naive** 的（不带 tzinfo），与 SQLite/现有列的
datetime 列保持兼容，避免"naive vs aware"比较报错。
"""
import datetime
import logging

from .qmt import xtdata

logger = logging.getLogger(__name__)

# UTC+8 固定偏移（上海时间，无夏令时）
_SH_DELTA = datetime.timedelta(hours=8)
_UTC = datetime.timezone.utc


def now_sh() -> datetime.datetime:
    """返回当前上海时间作为 naive datetime（UTC+8，不带 tzinfo，兼容 SQLite datetime 列）。"""
    utc_now = datetime.datetime.now(_UTC)
    sh_now = utc_now + _SH_DELTA
    return sh_now.replace(tzinfo=None)


def today_sh() -> datetime.date:
    """返回当前上海日期。"""
    return now_sh().date()


def is_trading_day(date: datetime.date | None = None) -> bool:
    """判断指定日期是否为交易日（通过 xtdata 交易日历，失败时回退到周末判断）。"""
    if date is None:
        date = today_sh()
    date_str = date.strftime('%Y%m%d')
    try:
        dates = xtdata.get_trading_dates('SH', date_str, date_str)
        if not dates:
            return False
        ts = dates[0]
        if ts > 1e12:  # 毫秒时间戳
            ts /= 1000
        # xtdata 时间戳为 UTC 秒/毫秒，转为上海日期后比较
        sh_date = (datetime.datetime.fromtimestamp(ts, tz=_UTC) + _SH_DELTA).date()
        return sh_date == date
    except Exception as e:
        logger.warning(f'查询交易日历失败({e})，改用周末判断')
        return date.weekday() < 5


def is_trading_time(now: datetime.datetime | None = None) -> bool:
    """判断当前是否在交易时段内（9:30-11:30, 13:00-15:00，上海时间）。

    传入的 naive datetime 视为上海时间；传入 aware datetime 会先转为上海时间。
    """
    if now is None:
        now = now_sh()
    elif now.tzinfo is not None:
        now = now.astimezone(_UTC) + _SH_DELTA
        now = now.replace(tzinfo=None)
    t = now.time()
    morning = datetime.time(9, 30) <= t <= datetime.time(11, 30)
    afternoon = datetime.time(13, 0) <= t <= datetime.time(15, 0)
    return morning or afternoon
