# -*- coding: utf-8 -*-
"""运行时日志：输出到控制台与 resource/logs/gui.log（滚动），并捕获未处理异常。"""
import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler

import util.ConfigUtil as Config

LOG_DIR = Config.log_path
LOG_FILE = os.path.join(LOG_DIR, 'gui.log')
_configured = False

_FORMAT = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
_DATEFMT = '%Y-%m-%d %H:%M:%S'


def setup_logging(log_file=None, console=True, level=logging.INFO):
    """初始化日志。重复调用幂等。返回日志文件路径。"""
    global _configured
    path = log_file or LOG_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)

    root = logging.getLogger()
    if _configured:
        return path
    root.setLevel(level)
    root.handlers.clear()

    fmt = logging.Formatter(_FORMAT, datefmt=_DATEFMT)
    try:
        fh = RotatingFileHandler(path, maxBytes=2 * 1024 * 1024, backupCount=5,
                                 encoding='utf-8')
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception as e:
        print(f'[logger] 无法创建日志文件 {path}: {e}', file=sys.stderr)

    if console:
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        root.addHandler(ch)

    _configured = True
    return path


def install_excepthooks():
    """把未捕获异常（主线程 + 工作线程）写入日志。"""
    log = logging.getLogger('unhandled')

    def main_hook(exc_type, exc_value, exc_tb):
        log.critical('未捕获异常（主线程）', exc_info=(exc_type, exc_value, exc_tb))
        # 保留默认行为，让 traceback 也打印到控制台
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    def thread_hook(args):
        log.critical('未捕获异常（线程 %s）: %s',
                     getattr(args.thread, 'name', '?'), args.exc_value,
                     exc_info=(args.exc_type, args.exc_value, args.exc_tb))

    sys.excepthook = main_hook
    threading.excepthook = thread_hook


def get_logger(name):
    return logging.getLogger(name)
