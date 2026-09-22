# -*- coding: utf-8 -*-
"""QQ 空间历史数据获取 - PyQt 桌面前端入口。

用法：
    python main_gui.py

日志：data/logs/gui.log（滚动保留 5 份，含未捕获异常）
"""
import sys

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox

from gui.image_loader import ImageLoader
from gui.logger import get_logger, install_excepthooks, setup_logging
from gui.login_dialog import LoginDialog
from gui.main_window import MainWindow
from gui.services import QzoneService
from gui.styles import build_all_qss
from gui.watchdog import enable_watchdog

log = get_logger('app')

EXIT_RELOGIN = 100  # 退出码：触发重新登录


class UserInfoWorker(QThread):
    """后台获取账号昵称，避免阻塞主界面。"""

    got = pyqtSignal(str)

    def __init__(self, service, cookies, parent=None):
        super().__init__(parent)
        self.service = service
        self.cookies = cookies

    def run(self):
        nickname = ''
        try:
            uin = self.service._clean_uin(self.cookies.get('uin'))
            info = self.service.get_user_info(self.cookies)
            arr = (info or {}).get(uin) or []
            if arr:
                if len(arr) > 6 and str(arr[6]).strip():
                    nickname = str(arr[6]).strip()
                elif len(arr) > 1 and str(arr[1]).strip():
                    nickname = str(arr[1]).strip()
        except Exception as e:
            log.warning('获取用户昵称失败: %s', e)
        self.got.emit(nickname)


def main():
    log_file = setup_logging()
    install_excepthooks()
    dump_file = enable_watchdog(20)
    log.info('=' * 50)
    log.info('QQ 空间历史数据获取 GUI 启动，日志文件: %s', log_file)
    if dump_file:
        log.info('看门狗已启用，线程栈将定期写入: %s', dump_file)

    app = QApplication(sys.argv)
    app.setApplicationName('QQ空间历史数据获取')
    app.setStyleSheet(build_all_qss())

    service = QzoneService()
    workers = []
    while True:
        dlg = LoginDialog(service)
        if dlg.exec() != LoginDialog.DialogCode.Accepted or dlg.result_cookies is None:
            log.info('未选择登录，退出程序')
            return 0
        cookies = dlg.result_cookies
        uin = service._clean_uin(cookies.get('uin'))
        if not uin or uin == 'None':
            log.warning('登录信息中未解析出 QQ 号')
            QMessageBox.warning(None, '登录失败', '未能从登录信息中解析出 QQ 号，请重新登录。')
            continue
        log.info('登录成功: QQ=%s', uin)

        nickname = f'QQ{uin}'
        win = MainWindow(service, cookies, uin, nickname)
        win.show()

        # 后台获取真实昵称，完成后刷新界面并重新分类
        worker = UserInfoWorker(service, cookies)
        worker.got.connect(win.update_nickname)
        worker.finished.connect(worker.deleteLater)
        workers.append(worker)
        worker.start()

        code = app.exec()
        ImageLoader.instance().shutdown()
        log.info('事件循环退出，code=%s', code)
        if code != EXIT_RELOGIN:
            return code
        # 退出登录：销毁旧窗口与后台线程，避免窗口残留 / 信号误连
        win.close()
        win.deleteLater()
        app.processEvents()

        def _alive(w):
            try:
                return w.isRunning()
            except RuntimeError:
                return False  # 线程对象已被 deleteLater 销毁

        workers = [w for w in workers if _alive(w)]
        # 退出登录 → 重新登录
    return 0


if __name__ == '__main__':
    sys.exit(main())
