# -*- coding: utf-8 -*-
"""后台工作线程：登录轮询、抓取导出。通过 Qt 信号与界面通信。"""
import time

from PyQt6.QtCore import QThread, pyqtSignal

from gui.logger import get_logger
import util.ConfigUtil as Config

log = get_logger('worker')


class QrLoginWorker(QThread):
    """二维码登录轮询线程。"""

    status_changed = pyqtSignal(str)      # wait/success/expired/cancel/error
    message = pyqtSignal(str)             # 提示文本

    def __init__(self, service, qrsig, parent=None):
        super().__init__(parent)
        self.service = service
        self.qrsig = qrsig
        self._running = True

    def stop(self):
        self._running = False

    def run(self):
        while self._running:
            status, data = self.service.poll_qr_login(self.qrsig)
            if status == 'wait':
                self.status_changed.emit('wait')
            elif status == 'expired':
                self.status_changed.emit('expired')
                self.message.emit('二维码已失效，请点击刷新')
                return
            elif status == 'cancel':
                self.status_changed.emit('cancel')
                return
            elif status == 'success':
                log.info('扫码登录成功')
                self.status_changed.emit('success')
                self.cookies = data
                return
            else:
                self.status_changed.emit('error')
                self.message.emit(f'登录轮询异常: {data}')
                return
            time.sleep(3)


class FetchWorker(QThread):
    """抓取并导出 QQ 空间数据的后台线程。"""

    progress = pyqtSignal(str, int)        # (说明文字, 百分比)
    finished_ok = pyqtSignal(object, object, object)   # (cookies, 结果目录, 统计)
    failed = pyqtSignal(str)

    def __init__(self, service, cookies, nickname, parent=None):
        super().__init__(parent)
        self.service = service
        self.cookies = cookies
        self.nickname = nickname

    def _emit(self, text, percent):
        self.progress.emit(text, percent)

    def run(self):
        t_start = time.time()
        log.info('开始抓取: 获取消息总数...')
        try:
            # 先校验登录态：QQ 登录会话可能已被挤出（如手机端登录），
            # 失效时立即给出明确提示，避免空等抓取失败
            self._emit('正在校验登录态...', 1)
            try:
                info = self.service.get_user_info(self.cookies)
                uin = self.service._clean_uin(self.cookies.get('uin'))
                if not (info or {}).get(uin):
                    raise RuntimeError('接口未返回本账号信息')
            except Exception as e:
                log.warning('登录态校验失败: %s', e)
                self.failed.emit('登录已过期或登录态无效，请点击「退出登录」后重新扫码登录。'
                                 f'\n（{e}）')
                return
            self._emit('正在获取消息总数...', 2)
            # 通道一：历史消息列表（支持断点续传 + 完成标识，见 services.fetch_history_messages）
            texts, all_friends = [], []
            try:
                texts, all_friends, count = self.service.fetch_history_messages(
                    self.cookies, None, self._emit)
            except Exception as e:
                log.error('获取历史消息失败: %s', e, exc_info=True)
                self._emit(f'获取历史消息失败: {e}', 5)
                texts, all_friends, count = [], [], 0
            log.info('历史消息总数=%s', count)
            if count > 0:
                self._emit(f'共 {count} 条历史消息，正在分批获取...', 8)
            else:
                self._emit('未获取到历史消息，尝试获取全部说说...', 8)

            # 通道二：全部可见说说（与 main.py 保持一致的合并策略：
            # 先剔除消息列表中与全部说说内容相同的条目，再以全部说说为准扩展）
            self._emit('获取全部未删除说说...', 50)
            try:
                moments = self.service.get_visible_moments(self.cookies, self._emit)
                if moments:
                    texts = [t for t in texts
                             if not any(Tools_is_any_mutual_exist(t[1], m[1]) for m in moments)]
                    texts.extend(moments)
            except Exception as e:
                log.error('获取全部说说失败: %s', e, exc_info=True)
                self._emit(f'获取全部说说失败: {e}', 50)

            # 通道三：mobile get_feeds 动态流（数据更全：评论/点赞/视频/转发原）。
            # 由 config.ini [fetch] feeds_channel 控制，失败不中断整体导出。
            if Config.feeds_channel:
                self._emit('获取动态流（增强通道）...', 58)
                try:
                    # 动态流条数无法预知，按历史消息/未删除说说的 2 倍预估作为
                    # 进度条分母，抓完自动封顶到 95%，导出阶段收尾到 100%
                    feeds_est = max(count, len(moments) if moments else 0, 50) * 2
                    feed_texts = self.service.fetch_all_feeds(
                        self.cookies, self._emit, feeds_est)
                    if feed_texts:
                        merged = texts
                        for ft in feed_texts:
                            if not any(Tools_is_any_mutual_exist(ft[1], t[1])
                                       for t in merged):
                                merged.append(ft)
                        texts = merged
                        log.info('合并动态流后总数=%s', len(texts))
                except Exception as e:
                    log.error('获取动态流失败（已跳过）: %s', e, exc_info=True)
                    self._emit(f'动态流获取失败（已跳过）: {e}', 58)

            log.info('合并后动态数=%s，好友数=%s', len(texts), len(all_friends))
            if not texts:
                self.failed.emit('未获取到任何数据。可能登录已过期或该账号无可见内容，'
                                 '请重新登录后重试。')
                return

            # 保证至少四列（通道三为五列，dump 会自动统一列宽）
            texts = [t if len(t) >= 4 else t + [""] * (4 - len(t)) for t in texts]
            self._emit('正在导出 Excel 与 HTML...', 98)
            result_path, stats = self.service.save_results(
                self.cookies, texts, all_friends, self.nickname, self._emit)
            self._emit('导出完成', 100)
            log.info('导出完成: %s，耗时 %.1fs', stats, time.time() - t_start)
            self.finished_ok.emit(self.cookies, result_path, stats)
        except Exception as e:
            log.error('抓取过程发生异常: %s', e, exc_info=True)
            self.failed.emit(f'抓取过程发生异常: {e}')


def Tools_is_any_mutual_exist(str1, str2):
    """内容等价判断（与 util.ToolsUtil 一致，避免 import 副作用）。"""
    import util.ToolsUtil as Tools
    return Tools.is_any_mutual_exist(str1, str2)
