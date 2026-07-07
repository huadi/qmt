import logging
import requests

from .. import config

logger = logging.getLogger(__name__)

webhook = f'https://oapi.dingtalk.com/robot/send?access_token={config.dingtalk_token}'


def send(msg):
    logger.info('发送钉钉通知: %s', msg)
    resp = requests.post(webhook, json={'msgtype': 'text', 'text': {'content': msg}})
    if resp.status_code == 200 and resp.json().get('errcode') == 0:
        logger.info('钉钉通知发送成功')
    else:
        logger.error('钉钉通知发送失败: HTTP %s, 响应: %s', resp.status_code, resp.text)