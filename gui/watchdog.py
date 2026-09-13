# -*- coding: utf-8 -*-
"""看门狗：程序无响应（主线程卡死）时自动记录各线程调用栈，用于定位卡死原因。

注意：不用 faulthandler.dump_traceback_later —— 它在 PyQt6 环境触发 dump 时
会直接崩溃（access violation）。这里用纯 Python 后台线程 + sys._current_frames()
定期采样所有线程的 Python 栈，安全无副作用。

用法：程序启动时调用 enable_watchdog()。
卡死时，resource/logs/thread_dump.log 会周期性记录主线程阻塞位置。
"""
import os
import sys
import threading
import time
import traceback
from datetime import datetime

import util.ConfigUtil as Config

DUMP_FILE = os.path.join(Config.log_path, 'thread_dump.log')

_stop = threading.Event()


def _sampler(interval, out):
    while not _stop.wait(interval):
        try:
            frames = list(sys._current_frames().items())
        except Exception:
            continue
        try:
            with open(out, 'a', encoding='utf-8') as f:
                f.write('\n' + '=' * 60 + '\n')
                f.write(f'线程栈采样 {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
                for tid, frame in frames:
                    f.write(f'\n--- 线程 {tid} ---\n')
                    traceback.print_stack(frame, file=f)
        except Exception:
            pass


def enable_watchdog(interval=20):
    """每 interval 秒把全部线程栈追加到 resource/logs/thread_dump.log。"""
    try:
        os.makedirs(os.path.dirname(DUMP_FILE), exist_ok=True)
        t = threading.Thread(target=_sampler, args=(interval, DUMP_FILE),
                             name='watchdog', daemon=True)
        t.start()
    except Exception:
        return None
    return DUMP_FILE
