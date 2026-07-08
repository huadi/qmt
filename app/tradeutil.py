# -*- coding: utf-8 -*-
"""
交易相关工具函数。

目前提供交易日/交易时段判断（is_trading_day / is_trading_time），
后续与"交易"相关的通用工具函数放这里。

所有时间均使用系统本地时间（datetime.now() / datetime.date.today()），
不再做任何时区换算。部署时确保服务器系统时区与交易所一致即可。
"""
import datetime
import logging

from .qmt import QmtClient

logger = logging.getLogger(__name__)


def is_trading_day(date: datetime.date | None = None) -> bool:
    """判断指定日期是否为交易日（通过 xtdata 交易日历，失败时回退到周末判断）。"""
    if date is None:
        date = datetime.date.today()
    date_str = date.strftime('%Y%m%d')
    try:
        dates = QmtClient.get_trading_dates('SH', date_str, date_str)
        if not dates:
            return False
        ts = dates[0]
        if ts > 1e12:  # 毫秒时间戳
            ts /= 1000
        return datetime.datetime.fromtimestamp(ts).date() == date
    except Exception as e:
        logger.warning(f'查询交易日历失败({e})，改用周末判断')
        return date.weekday() < 5


def is_trading_time(now: datetime.datetime | None = None) -> bool:
    """判断当前是否在交易时段内（9:30-11:30, 13:00-15:00，系统本地时间）。"""
    if now is None:
        now = datetime.datetime.now()
    t = now.time()
    morning = datetime.time(9, 30) <= t <= datetime.time(11, 30)
    afternoon = datetime.time(13, 0) <= t <= datetime.time(15, 0)
    return morning or afternoon
