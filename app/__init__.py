# -*- coding: utf-8 -*-
"""
app 包初始化模块

负责系统级初始化：读取 config.toml，暴露 ACCOUNT_ID / MINI_PATH / SESSION_ID。
"""
import os
import random

try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # 需 pip install tomli（Python 3.6~3.10）
    except ImportError:
        raise ImportError('缺少 TOML 解析库，请运行: pip install tomli')

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_FILE = os.path.join(_ROOT, 'config.toml')
if not os.path.exists(_CONFIG_FILE):
    raise FileNotFoundError(f'配置文件不存在: {_CONFIG_FILE}，请参考 config.toml.example 创建')

with open(_CONFIG_FILE, 'rb') as _f:
    _cfg = tomllib.load(_f)

_qmt = _cfg['qmt']

ACCOUNT_ID = _qmt['account_id']
MINI_PATH = _qmt['mini_path']
SESSION_ID = _qmt.get('session_id') or random.randint(100000, 999999)
