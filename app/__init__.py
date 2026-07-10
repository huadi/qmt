# -*- coding: utf-8 -*-
"""
app 包初始化模块

读取项目根目录的 config.toml，解析为不可变的 config 对象暴露给各模块。
运行时配置请从本包导入：`from . import config`。
"""
import os
from dataclasses import dataclass

try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # 需 pip install tomli（Python 3.6~3.10）
    except ImportError:
        raise ImportError('缺少 TOML 解析库，请运行: pip install tomli')


@dataclass(frozen=True)
class Config:
    """运行时配置（只读）"""
    account_id: str
    mini_path: str
    dingtalk_token: str = ''
    # 模拟盘（可选）：未配置时 SimQmtClient.connect() 会抛 RuntimeError
    sim_account_id: str = ''
    sim_mini_path: str = ''


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_FILE = os.path.join(_ROOT, 'config.toml')
if not os.path.exists(_CONFIG_FILE):
    raise FileNotFoundError(f'配置文件不存在: {_CONFIG_FILE}，请参考 config.toml.example 创建')

with open(_CONFIG_FILE, 'rb') as _f:
    _cfg = tomllib.load(_f)

_qmt = _cfg['qmt']
_sim = _qmt.get('sim', {})
_notify = _cfg.get('notify', {})

config = Config(
    account_id=_qmt['account_id'],
    mini_path=_qmt['mini_path'],
    dingtalk_token=_notify.get('dingtalk_token', ''),
    sim_account_id=_sim.get('account_id', ''),
    sim_mini_path=_sim.get('mini_path', ''),
)
