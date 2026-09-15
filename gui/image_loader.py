# -*- coding: utf-8 -*-
"""异步图片加载器：头像 / 说说图片 / QQ 表情。

- 远程图片：后台线程用 requests 下载（与抓取同一网络栈），最多 MAX_CONCURRENT 路并发，磁盘缓存
- 本地图片：QThreadPool 后台线程用 QImage 加载并缩略，避免主线程卡顿
- 统一通过 loaded 信号回传 (key, QPixmap)

注意：不使用 QNetworkAccessManager —— 实测在部分 Windows 网络环境下，
QNAM 的下载会阻塞 Qt 主线程事件循环（UI 卡死无响应）。requests 线程化后
主线程只做 QPixmap 本地加载，彻底规避该问题。
"""
import hashlib
import os
import re
import time

import requests
from PyQt6.QtCore import (QObject, QRunnable, QThreadPool,
                          pyqtSignal, pyqtSlot, Qt as _Qt)
from PyQt6.QtGui import QImage, QPixmap

import util.ConfigUtil as Config
from gui.logger import get_logger

log = get_logger('image')

Qt_AspectRatioMode_KeepAspectRatio = _Qt.AspectRatioMode.KeepAspectRatio
Qt_TransformationMode_SmoothTransformation = _Qt.TransformationMode.SmoothTransformation


def render_svg_icon(name, color, size=36):
    """渲染 resource/icons/<name>.svg 为指定颜色的 pixmap（Font Awesome，开源免费）。

    图标缺失时返回 None，调用方负责回退文本，保证界面不出现空白。
    """
    try:
        path = Config.resource_path(os.path.join('icons', f'{name}.svg'))
        if not os.path.exists(path):
            return None
        svg = open(path, encoding='utf-8').read()
        if 'fill=' not in svg:
            svg = svg.replace('<path ', f'<path fill="{color}" ')
        else:
            svg = re.sub(r'fill="[^"]*"', f'fill="{color}"', svg)
        from PyQt6.QtCore import QByteArray
        from PyQt6.QtGui import QPainter
        from PyQt6.QtSvg import QSvgRenderer
        renderer = QSvgRenderer(QByteArray(svg.encode('utf-8')))
        img = QImage(size, size, QImage.Format.Format_ARGB32)
        img.fill(_Qt.GlobalColor.transparent)
        p = QPainter(img)
        renderer.render(p)
        p.end()
        pm = QPixmap.fromImage(img)
        return pm if not pm.isNull() else None
    except Exception:
        log.debug('SVG 图标渲染失败: %s', name, exc_info=True)
        return None

TEMP_ROOT = Config.temp_path
AVATAR_DIR = os.path.join(TEMP_ROOT, 'avatars')
EMOJI_DIR = os.path.join(TEMP_ROOT, 'em')
PIC_DIR = os.path.join(TEMP_ROOT, 'pics')
CACHE_DIRS = (AVATAR_DIR, EMOJI_DIR, PIC_DIR)

EMOJI_URL = 'http://qzonestyle.gtimg.cn/qzone/em/{code}.gif'

MAX_CONCURRENT = 4          # 远程下载最大并发数（限流：避免对服务器造成压力）
LOCAL_THUMB_SIZE = 640      # 本地图片缩略尺寸上限

_DL_HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'),
    'Referer': 'https://user.qzone.qq.com/',
    'Accept': 'image/avif,image/webp,image/png,image/jpeg,image/*,*/*;q=0.8',
}


def _ensure_dirs():
    for d in CACHE_DIRS:
        os.makedirs(d, exist_ok=True)


class _LocalLoadSignals(QObject):
    done = pyqtSignal(str, QImage)


class _LocalImageTask(QRunnable):
    """后台加载本地图片（QImage 线程安全），完成后发信号。

    信号对象由外部（ImageLoader）持有，本任务 autoDelete 时不会波及。
    """

    def __init__(self, key, path, max_size, signals):
        super().__init__()
        self.key = key
        self.path = path
        self.max_size = max_size
        self.signals = signals

    def run(self):
        try:
            img = QImage(self.path)
            if img.isNull():
                log.warning('本地图片加载失败: %s', self.path)
                return
            if self.max_size and max(img.width(), img.height()) > self.max_size:
                img = img.scaled(self.max_size, self.max_size,
                                 Qt_AspectRatioMode_KeepAspectRatio,
                                 Qt_TransformationMode_SmoothTransformation)
            self.signals.done.emit(self.key, img)
        except Exception as e:
            log.warning('本地图片加载异常 %s: %s', self.path, e)


class _RemoteSignals(QObject):
    done = pyqtSignal(str, str, bool)   # (key, save_to, ok)


def _valid_image_bytes(data):
    """魔数校验下载内容是否为真实图片（对齐 QzoneArchive archived_image_extension）。

    网页服务器可能返回 200 + HTML 错误页 / 验证页，必须拒绝，否则破图被缓存。
    """
    if not data or len(data) < 12:
        return False
    if data.startswith(b'\xff\xd8\xff'):
        return True                            # jpg
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return True                            # png
    if data.startswith((b'GIF87a', b'GIF89a')):
        return True                            # gif
    if data.startswith(b'BM'):
        return True                            # bmp
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        return True                            # webp
    if data[4:8] == b'ftyp' and data[8:12] in (b'avif', b'avis', b'mif1', b'heic', b'heix'):
        return True                            # avif/heic
    return False


def _is_qq_missing_placeholder(data):
    """QQ 缺失图片占位图识别（对齐 QzoneArchive is_qq_missing_image_placeholder）。

    注意：识别到的占位图**原样保存显示**——用户要求"服务器返回的失效占位图
    就原样显示"，不当作下载失败处理（失效与否由服务器决定，本地不做二次判断）。
    """
    if not data or len(data) < 10:
        return False
    w = int.from_bytes(data[6:8], 'little')
    h = int.from_bytes(data[8:10], 'little')
    return ((len(data) in (2038, 2687) and data.startswith(b'GIF89a')
             and w == 340 and h == 320)
            or (len(data) == 1643 and data.startswith(b'GIF87a')
                and w == 99 and h == 99)
            or (len(data) == 1547 and data.startswith(b'GIF87a')
                and w == 98 and h == 98))


class _RemoteDownloadTask(QRunnable):
    """后台线程下载远程图片到本地文件（纯 requests，无 Qt 网络栈）。

    信号对象由 ImageLoader 持有，本任务 autoDelete 时不会波及。
    """

    def __init__(self, key, url, save_to, signals):
        super().__init__()
        self.key = key
        self.url = url
        self.save_to = save_to
        self.signals = signals

    def run(self):
        try:
            # 超大超时 + 失败重试 1 次：老图片/CDN 慢，瞬时网络抖动不应判死
            last = None
            for attempt in (1, 2):
                try:
                    r = requests.get(self.url, headers=_DL_HEADERS,
                                     verify=False, timeout=(8, 30))
                    if r.status_code == 200 and r.content:
                        if not _valid_image_bytes(r.content):
                            # 200 但内容不是图片（HTML 错误页/风控页等）
                            log.warning('下载内容非图片（HTTP %s, %d bytes），拒绝缓存: %s',
                                        r.status_code, len(r.content), self.url[:120])
                            self.signals.done.emit(self.key, self.save_to, False)
                            return
                        try:
                            with open(self.save_to, 'wb') as f:
                                f.write(r.content)
                        except Exception as e:
                            log.warning('保存远程图片失败 %s: %s', self.save_to, e)
                        self.signals.done.emit(self.key, self.save_to, True)
                        return
                    # 非 200（404/403/50x 等）：重试一次后仍失败再放弃
                    last = f'HTTP {r.status_code}'
                except Exception as e:
                    last = str(e)
                if attempt == 1:
                    log.warning('远程下载第 1 次失败（%s），重试: %s', last, self.url[:120])
                    time.sleep(0.6)
            log.warning('远程下载失败 %s: %s', self.url[:120], last)
        except Exception as e:
            log.warning('远程下载失败 %s: %s', self.url, e)
        self.signals.done.emit(self.key, self.save_to, False)


class ImageLoader(QObject):
    """全局图片加载器（懒加载单例，通过 instance() 获取）。"""

    _instance = None

    loaded = pyqtSignal(str, QPixmap)  # (cache_key, pixmap)

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        super().__init__()
        _ensure_dirs()
        self._remote_sig = _RemoteSignals(self)
        self._remote_sig.done.connect(self._on_remote_done)
        self._queue = []              # 等待下载队列 (key, url, save_to)
        self._queue_keys = set()      # 队列中 key（去重）
        self._downloading = set()     # 正在后台下载的 key
        self._failed = set()          # 下载失败的 key（会话内不再重试，避免失败-重试死循环）
        self._remote_active = 0       # 当前在飞下载数（限流）
        self._pixmap_cache = {}
        self._local_pending = set()   # 正在后台加载的本地 key
        self._threadpool = QThreadPool.globalInstance()

    # ---------- 对外接口 ----------
    def get(self, key, url, save_to):
        """按 key 请求远程图片；磁盘有缓存直接发 loaded，否则入队限流下载。"""
        if key in self._failed:
            # 已确认失败：不再重复请求；仍通知前端显示默认占位图
            self.loaded.emit(key, QPixmap())
            return
        if key in self._pixmap_cache:
            self.loaded.emit(key, self._pixmap_cache[key])
            return
        if os.path.exists(save_to):
            self._emit_from_file(key, save_to)
            return
        if key in self._downloading or key in self._queue_keys:
            return
        self._queue.append((key, url, save_to))
        self._queue_keys.add(key)
        self._pump()

    def load_local(self, key, path, max_size=LOCAL_THUMB_SIZE):
        """后台异步加载本地图片并生成缩略图，完成后发 loaded。"""
        if key in self._local_pending or key in self._pixmap_cache:
            return
        if not os.path.exists(path):
            log.warning('本地图片不存在: %s', path)
            return
        self._local_pending.add(key)
        signals = _LocalLoadSignals(self)   # 信号对象由 ImageLoader 持有
        signals.done.connect(self._on_local_done)
        signals.done.connect(signals.deleteLater)  # 任务完成后自毁
        task = _LocalImageTask(key, path, max_size, signals)
        self._threadpool.start(task)

    def avatar(self, uin):
        """QQ 头像。"""
        url = f'https://q.qlogo.cn/headimg_dl?dst_uin={uin}&spec=640&img_type=jpg'
        save = os.path.join(AVATAR_DIR, f'{uin}.jpg')
        self.get(f'avatar:{uin}', url, save)

    def emoji(self, code):
        """QQ 表情。"""
        save = os.path.join(EMOJI_DIR, f'{code}.gif')
        self.get(f'emoji:{code}', EMOJI_URL.format(code=code), save)

    def picture(self, url):
        """远程说说图片。"""
        h = hashlib.md5(url.encode('utf-8')).hexdigest()[:16]
        save = os.path.join(PIC_DIR, f'{h}.jpg')
        self.get(f'pic:{h}', url, save)

    def is_failed(self, key):
        """该 key 是否已确认下载失败（用于渲染层避免重复请求）。"""
        return key in self._failed

    def shutdown(self):
        """退出前清理：清空等待队列（在飞任务自然结束，结果被忽略）。"""
        self._queue.clear()
        self._queue_keys.clear()

    # ---------- 内部：远程下载 ----------
    def _pump(self):
        """从队列调度下载，控制并发数。"""
        while self._remote_active < MAX_CONCURRENT and self._queue:
            key, url, save = self._queue.pop(0)
            self._queue_keys.discard(key)
            self._downloading.add(key)
            self._remote_active += 1
            self._threadpool.start(
                _RemoteDownloadTask(key, url, save, self._remote_sig))

    @pyqtSlot(str, str, bool)
    def _on_remote_done(self, key, save_to, ok):
        self._downloading.discard(key)
        self._remote_active = max(0, self._remote_active - 1)
        if ok and os.path.exists(save_to):
            self._emit_from_file(key, save_to)
        else:
            # 下载失败：记录后发出空 pixmap，前端据此显示默认占位图
            self._failed.add(key)
            self.loaded.emit(key, QPixmap())
        self._pump()

    def _emit_from_file(self, key, path):
        """从磁盘缓存读取并发出（仅处理小图，大头像/表情/缩略图场景）。"""
        try:
            img = QImage(path)
            if img.isNull():
                self._failed.add(key)  # 损坏缓存：会话内不再重试
                return
            pm = QPixmap.fromImage(img)
            self._pixmap_cache[key] = pm
            self.loaded.emit(key, pm)
        except Exception as e:
            log.warning('读取缓存失败 %s: %s', path, e)

    # ---------- 内部：本地图片 ----------
    @pyqtSlot(str, QImage)
    def _on_local_done(self, key, img):
        self._local_pending.discard(key)
        pm = QPixmap.fromImage(img)
        if not pm.isNull():
            self._pixmap_cache[key] = pm
            self.loaded.emit(key, pm)


# ---------- 文本 → HTML 富文本 ----------
_EM_RE = re.compile(r'\[em\](.*?)\[/em\]')


_VALID_IMG_CACHE = set()   # 已验证可安全渲染的文件（避免每次渲染重复解码）


def _valid_image(path):
    """确认图片文件可被 Qt 完整解码（防截断/损坏文件渲染时崩溃）。

    仅检查文件头魔数不够：6 字节 GIF89a 头能通过魔数检查，但 QLabel
    渲染时 Qt 解析器会因截断内容崩溃（access violation）。必须实际解码。
    """
    if path in _VALID_IMG_CACHE:
        return True
    try:
        img = QImage(path)
        if img.isNull():
            return False
        _VALID_IMG_CACHE.add(path)
        return True
    except Exception:
        return False


def emoji_to_html(text, size=20):
    """把 [em]code[/em] 转换为指向本地缓存表情的 <img>。

    表情文件尚未下载（或已损坏）时只发起请求、以 [em]code[/em] 文本占位：
    避免生成指向不存在/损坏文件的破图，也避免 get() -> loaded 信号
    自触发 _refresh_rich_texts 造成无限递归（RecursionError 卡死）。
    不设置 width/height：表情按原始尺寸显示，不做额外缩放。
    """
    from html import escape

    def _one(m):
        code = m.group(1).strip()
        if not code:
            return ''
        path = os.path.join(EMOJI_DIR, f'{code}.gif')
        if not (os.path.exists(path) and _valid_image(path)):
            # 仅当该表情尚未确认失败才请求下载；失败后保持文本占位，避免
            # 失败 -> 空信号 -> 刷新 -> 再请求 的无限循环
            loader = ImageLoader.instance()
            if not loader.is_failed(f'emoji:{code}'):
                loader.emoji(code)
            return f'[em]{code}[/em]'
        return (f'<img src="{path.replace(chr(92), "/")}" '
                f'align="absmiddle" style="vertical-align:middle;"/>')

    return _EM_RE.sub(_one, escape(text))
