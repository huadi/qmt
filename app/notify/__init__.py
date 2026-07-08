# -*- coding: utf-8 -*-
"""通知模块 facade：从各渠道读取配置，统一通过 notify(msg) 分发。"""
import logging

from .dingtalk import send as _dingtalk_send

logger = logging.getLogger(__name__)


def send(msg: str) -> None:
    """发送通知到所有已配置的渠道。未配置任何渠道时记录 debug 日志。"""
    _dingtalk_send(msg)
