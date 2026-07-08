# -*- coding: utf-8 -*-
"""
数据库访问模块

使用 SQLAlchemy 2.x ORM 统一管理 SQLite 连接与 stocks 表操作。
"""
import os
import datetime
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DB_DIR = os.path.join(_ROOT, 'data')
_DB_FILE = os.path.join(_DB_DIR, 'sqlite.db')

os.makedirs(_DB_DIR, exist_ok=True)

engine = create_engine(f'sqlite:///{_DB_FILE}', echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Stock(Base):
    __tablename__ = 'stocks'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(unique=True, nullable=False)
    code: Mapped[str] = mapped_column(nullable=False)

    def __repr__(self):
        return f'<Stock(name={self.name!r}, code={self.code!r})>'


class Watch(Base):
    """股价监控规则"""
    __tablename__ = 'watches'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(nullable=False)
    code: Mapped[str] = mapped_column(nullable=False, index=True)
    above_price: Mapped[Optional[float]] = mapped_column(comment='上涨触发价，None表示不监控')
    below_price: Mapped[Optional[float]] = mapped_column(comment='下跌触发价，None表示不监控')
    above_triggered: Mapped[bool] = mapped_column(default=False, comment='上涨阈值是否已触发')
    below_triggered: Mapped[bool] = mapped_column(default=False, comment='下跌阈值是否已触发')
    above_triggered_at: Mapped[Optional[datetime.datetime]] = mapped_column(comment='上涨触发时间')
    below_triggered_at: Mapped[Optional[datetime.datetime]] = mapped_column(comment='下跌触发时间')
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.now)

    def __repr__(self):
        return f'<Watch(id={self.id}, name={self.name!r}, code={self.code!r}, above={self.above_price}, below={self.below_price})>'


def init_db():
    """初始化表结构（CREATE TABLE IF NOT EXISTS）"""
    Base.metadata.create_all(engine)
