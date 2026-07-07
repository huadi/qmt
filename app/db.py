# -*- coding: utf-8 -*-
"""
数据库访问模块

使用 SQLAlchemy 2.x ORM 统一管理 SQLite 连接与 stocks 表操作。
"""
import os

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


def init_db():
    """初始化表结构（CREATE TABLE IF NOT EXISTS）"""
    Base.metadata.create_all(engine)
