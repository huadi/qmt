import logging
import requests

from .. import config

logger = logging.getLogger(__name__)

# 复用 TCP 连接（HTTP keep-alive）
_session = requests.Session()
_TIMEOUT = (3, 10)  # (connect, read) 秒
_webhook = (
    f'https://oapi.dingtalk.com/robot/send?access_token={config.dingtalk_token}'
    if config.dingtalk_token else None
)


def send(msg):
    if _webhook is None:
        logger.debug('未配置 dingtalk_token，跳过钉钉通知')
        return
    logger.info('发送钉钉通知: %s', msg)
    try:
        resp = _session.post(
            _webhook,
            json={'msgtype': 'text', 'text': {'content': msg}},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.error('钉钉网络请求失败: %s', e)
        return
    if resp.status_code == 200 and resp.json().get('errcode') == 0:
        logger.info('钉钉通知发送成功')
    else:
        logger.error('钉钉通知发送失败: HTTP %s, 响应: %s', resp.status_code, resp.text)
