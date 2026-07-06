# -*- coding: utf-8 -*-
"""
数据库访问模块

统一管理 SQLite 的连接和 stocks 表操作。
"""
import os
import sqlite3

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DB_DIR = os.path.join(_ROOT, 'data')
_DB_FILE = os.path.join(_DB_DIR, 'sqlite.db')

_CREATE_TABLE = '''
    CREATE TABLE IF NOT EXISTS stocks (
        name  TEXT PRIMARY KEY,
        code  TEXT NOT NULL
    )
'''


def get_conn():
    """获取数据库连接"""
    os.makedirs(_DB_DIR, exist_ok=True)
    return sqlite3.connect(_DB_FILE)


def init_db():
    """初始化表结构"""
    conn = get_conn()
    conn.execute(_CREATE_TABLE)
    conn.commit()
    conn.close()


def upsert_stock(name, code):
    """插入或更新一条股票记录"""
    conn = get_conn()
    conn.execute('INSERT OR REPLACE INTO stocks (name, code) VALUES (?, ?)', (name, code))
    conn.commit()
    conn.close()


def replace_all(stocks):
    """清表后批量写入，stocks 为 [(name, code), ...] 列表"""
    conn = get_conn()
    conn.execute('DELETE FROM stocks')
    conn.executemany('INSERT OR REPLACE INTO stocks (name, code) VALUES (?, ?)', stocks)
    conn.commit()
    conn.close()


def find_code_by_name(name):
    """精确匹配名称，返回 code 或 None"""
    conn = get_conn()
    row = conn.execute('SELECT code FROM stocks WHERE name = ?', (name,)).fetchone()
    conn.close()
    return row[0] if row else None


def search_codes_by_keyword(keyword):
    """模糊匹配名称，返回 [(name, code), ...] 列表"""
    conn = get_conn()
    rows = conn.execute('SELECT name, code FROM stocks WHERE name LIKE ?',
                        (f'%{keyword}%',)).fetchall()
    conn.close()
    return rows
