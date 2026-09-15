# -*- coding: utf-8 -*-
"""说说卡片：QQ 空间风格的单条动态展示组件。"""
import hashlib
import os
import re

from PyQt6.QtCore import QObject, Qt, QThreadPool, QUrl, QRunnable, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtGui import QPixmap, QDesktopServices
from PyQt6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QPushButton, QVBoxLayout)

import util.ConfigUtil as Config
from gui.image_loader import (ImageLoader, PIC_DIR, emoji_to_html,
                              render_svg_icon)
from gui.logger import get_logger
from gui.styles import C_BORDER, C_TEXT_SUB, C_LINK

log = get_logger('moment')



EMOJI_RE = re.compile(r'\[em\](.*?)\[/em\]')


def collect_emoji_codes(text):
    return set(EMOJI_RE.findall(str(text)))


def make_circle(pixmap, size):
    """缩放并裁剪为圆形头像。"""
    if pixmap.isNull():
        return pixmap
    pm = pixmap.scaled(size, size,
                       Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                       Qt.TransformationMode.SmoothTransformation)
    from PyQt6.QtGui import QBitmap, QPainter
    mask = QBitmap(size, size)
    mask.fill(Qt.GlobalColor.color0)
    p = QPainter(mask)
    p.setBrush(Qt.GlobalColor.color1)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(0, 0, size, size)
    p.end()
    pm.setMask(mask)
    return pm


def split_name_body(content):
    """按 main.py 规则拆分 '昵称 ：正文'，返回 (昵称, 正文)，并去除两侧空白。"""
    parts = str(content).split('：')
    if len(parts) > 1:
        return parts[0].strip(), '：'.join(parts[1:]).strip()
    return '', str(content).strip()


class ClickableLabel(QLabel):
    """可点击文本标签（昵称，点击打开空间主页）。"""

    clicked = pyqtSignal()

    def __init__(self, text='', parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, ev):
        self.clicked.emit()
        super().mousePressEvent(ev)


class _PhotoLoadSignals(QObject):
    done = pyqtSignal(QImage)


class _PhotoLoadTask(QRunnable):
    """后台加载大图原图并缩放到屏幕可容纳尺寸。

    信号对象由 PhotoDialog 持有，本任务 autoDelete 时不会波及。
    """

    def __init__(self, path, max_size, signals):
        super().__init__()
        self.path = path
        self.max_size = max_size
        self.signals = signals

    def run(self):
        try:
            img = QImage(self.path)
            if not img.isNull() and max(img.width(), img.height()) > self.max_size:
                img = img.scaled(self.max_size, self.max_size,
                                 Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
            self.signals.done.emit(img)
        except Exception as e:
            log.warning('大图加载失败 %s: %s', self.path, e)


def resolve_image_path(link):
    """把 pic/ 相对路径或 http URL 解析为可加载的本地路径。"""
    link = str(link).strip()
    if link.startswith(('pic/', 'pic\\')):
        return os.path.join(Config.PROJECT_ROOT, link.replace('\\', '/'))
    if link.startswith('http'):
        h = hashlib.md5(link.encode('utf-8')).hexdigest()[:16]
        return os.path.join(PIC_DIR, f'{h}.jpg')
    return link


class PhotoDialog(QDialog):
    """大图查看对话框（后台加载，避免阻塞界面）。"""

    def __init__(self, link, parent=None):
        super().__init__(parent)
        self.setWindowTitle('查看图片')
        self._path = resolve_image_path(link)
        screen = self.screen() or self
        geo = screen.availableGeometry()
        self._max_size = int(min(geo.width(), geo.height()) * 0.8)
        self.resize(int(geo.width() * 0.5), int(geo.height() * 0.5))

        self.label = QLabel(self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setText('加载中…')
        self.label.setStyleSheet('color:#9FB2C8; background:#F7F9FC;')
        lay = QVBoxLayout(self)
        lay.addWidget(self.label)
        lay.setContentsMargins(8, 8, 8, 8)

        # 信号对象由对话框持有，避免任务 autoDelete 后跨线程信号访问已删对象
        self._photo_sig = _PhotoLoadSignals(self)
        self._photo_sig.done.connect(self._on_loaded)
        self._photo_sig.done.connect(self._photo_sig.deleteLater)
        task = _PhotoLoadTask(self._path, self._max_size, self._photo_sig)
        QThreadPool.globalInstance().start(task)

    def _on_loaded(self, img):
        if img.isNull():
            ph = Config.resource_path('img_placeholder.png')
            if os.path.exists(ph):
                self.label.setPixmap(QPixmap(ph))
            else:
                self.label.setText('图片加载失败或已失效')
            return
        pm = QPixmap.fromImage(img)
        self.label.setPixmap(pm)
        self.resize(min(pm.width(), self._max_size) + 20,
                    min(pm.height(), self._max_size) + 20)


class ClickableImage(QLabel):
    """可点击的图片格子。"""

    clicked = pyqtSignal(str)  # link

    # 原尺寸显示的上限保护（防止超大图撑爆卡片布局）
    # 原尺寸显示的上限保护：不超过内容区可用宽度/高度（物理容纳上限，
    # 超出部分按比例缩至可用区域，这是任何看图器的标准行为）
    FIT_MAX_W = 520
    FIT_MAX_H = 720

    def __init__(self, size=140, parent=None, fit_original=False):
        super().__init__(parent)
        self.fit_original = fit_original
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            f'background:#EEF2F7; border:1px solid {C_BORDER}; border-radius:6px;'
            'color:#B8C4D4; font-size:12px;')
        self.setText('加载中…')
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pm = None
        self._link = ''
        self.candidates = []   # 图片候选 URL（首个为主，失败逐个回退，QzoneArchive 思路）
        self.candidate_index = 0
        self.current_key = ''  # 最近一次请求的 URL hash（用于失败识别）

    def set_link(self, link):
        self._link = link

    def set_candidates(self, cands):
        """设置候选 URL 列表（已去重/补全 https）。"""
        self.candidates = [str(c) for c in cands if str(c).strip()]
        self.candidate_index = 0

    def next_candidate(self):
        """返回下一个候选 URL（补全 https），全部用完返回 None。"""
        while self.candidate_index < len(self.candidates):
            u = self.candidates[self.candidate_index]
            self.candidate_index += 1
            if u.startswith('//'):
                u = 'https:' + u
            return u
        return None

    def set_pix(self, pm: QPixmap):
        self._pm = pm
        if pm.isNull():
            self._show_placeholder()
            return
        if self.fit_original:
            # 单图：只有超过内容区可用上限的图才等比例缩小（任何看图器的标准行为），
            # 小图完全按原图显示，不做任何二次处理
            w, h = pm.width(), pm.height()
            if w > self.FIT_MAX_W or h > self.FIT_MAX_H:
                ratio = min(self.FIT_MAX_W / w, self.FIT_MAX_H / h)
                w, h = max(1, int(w * ratio)), max(1, int(h * ratio))
                self.setFixedSize(w, h)
                self.setPixmap(pm.scaled(w, h,
                                         Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))
            else:
                self.setFixedSize(w, h)
                self.setPixmap(pm)
        else:
            # 多图缩略网格：小图按原图显示（不放大），只有超过格子的图才缩到格子内
            tw, th = self.width() - 4, self.height() - 4
            if pm.width() <= tw and pm.height() <= th:
                self.setPixmap(pm)
            else:
                self.setPixmap(pm.scaled(tw, th,
                                         Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))
        self.setText('')

    def _show_placeholder(self):
        """图片缺失/失效时展示默认占位图，不留空白格。"""
        ph = Config.resource_path('img_placeholder.png')
        if os.path.exists(ph):
            p = QPixmap(ph)
            if not p.isNull():
                self.setPixmap(p.scaled(self.width() - 4, self.height() - 4,
                                        Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation))
                self.setText('')
                return
        self.setText('图片失效')

    def mousePressEvent(self, ev):
        if self._pm and not self._pm.isNull():
            self.clicked.emit(self._link)
        super().mousePressEvent(ev)


class MomentWidget(QFrame):
    """单条说说卡片。"""

    def __init__(self, time_str, content, img_links, comments,
                 avatar_uin, nickname, parent=None, likers=None,
                 like_count=0, pics_all=None):
        super().__init__(parent)
        self.setObjectName('MomentCard')
        self.time_str = time_str
        self.content = content
        self.img_links = img_links or []
        self.comments = comments or []
        self.avatar_uin = avatar_uin
        self.nickname = nickname
        self.likers = list(likers or [])
        self.like_count = int(like_count or 0)
        self.pics_all = list(pics_all or [])  # 每组图片的候选 URL（下载失败逐个回退）
        self._emoji_codes = set()
        self._grid_cells = []      # (link, cell) 列表
        self._comment_labels = []  # 评论富文本标签
        self._comment_rows = []    # 每条评论的全部控件（折叠时统一隐藏）
        self._comment_avatars = {}  # uin -> 评论头像 QLabel
        self._comments_expanded = False
        self._comments_box = None
        self._toggle_btn = None
        self._build_ui()
        ImageLoader.instance().loaded.connect(self._on_image_loaded)
        self._load_all()

    # ---------- UI ----------
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(12)
        head.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.avatar = QLabel(self)
        self.avatar.setFixedSize(56, 56)
        self.avatar.setStyleSheet(
            f'background:#DDE6F0; border:1px solid {C_BORDER}; border-radius:28px;'
            'color:#9FB2C8; font-size:20px;')
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar.setText('QQ')
        head.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignTop)

        body = QVBoxLayout()
        body.setSpacing(6)

        info = QHBoxLayout()
        info.setSpacing(10)
        self.name_lb = ClickableLabel(self.nickname, self)
        self.name_lb.setObjectName('MomName')
        self.name_lb.clicked.connect(self._open_home)
        self.time_lb = QLabel(self.time_str, self)
        self.time_lb.setObjectName('MomTime')
        info.addWidget(self.name_lb)
        info.addWidget(self.time_lb)
        info.addStretch(1)
        body.addLayout(info)

        self.content_lb = QLabel(self)
        self.content_lb.setWordWrap(True)
        self.content_lb.setTextFormat(Qt.TextFormat.RichText)
        self.content_lb.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(self.content_lb)

        # 来源客户端（来自：xxx）以灰色小字独立展示，不混入正文
        self.src_lb = QLabel(self)
        self.src_lb.setStyleSheet(f'color:{C_TEXT_SUB}; font-size:11px;')
        self.src_lb.setWordWrap(False)
        self.src_lb.hide()
        body.addWidget(self.src_lb)

        self._build_images(body)
        self._build_comments(body)
        self._build_actions(body)
        head.addLayout(body, 1)
        outer.addLayout(head)

    def _build_images(self, body):
        if not self.img_links:
            return
        self.grid = QGridLayout()
        self.grid.setSpacing(6)
        n = len(self.img_links)
        cols = 1 if n == 1 else (2 if n <= 4 else 3)
        # 单图按原尺寸显示（fit_original），多图用缩略网格
        size = 320 if n == 1 else (180 if n == 2 else 140)
        for i, link in enumerate(self.img_links):
            cell = ClickableImage(size, self, fit_original=(n == 1))
            cell.set_link(link)
            cands = [link]
            if i < len(self.pics_all) and isinstance(self.pics_all[i], (list, tuple)):
                group = [str(x) for x in self.pics_all[i] if x]
                if group:
                    cands = group
            cell.set_candidates(cands)
            cell.clicked.connect(self._show_photo)
            self.grid.addWidget(cell, i // cols, i % cols)
            self._grid_cells.append((link, cell))
        body.addLayout(self.grid)

    def _build_comments(self, body):
        if not self.comments:
            return
        box = QFrame(self)
        box.setObjectName('CommentBox')
        self._comments_box = box
        lay = QVBoxLayout(box)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(6)
        for i, c in enumerate(self.comments):
            row = QHBoxLayout()
            row.setSpacing(8)
            row.setAlignment(Qt.AlignmentFlag.AlignTop)
            try:
                ctime, ccontent, cnick, cuin = c
            except (TypeError, ValueError):
                continue
            av = QLabel(box)
            av.setFixedSize(28, 28)
            av.setAlignment(Qt.AlignmentFlag.AlignCenter)
            av.setStyleSheet(
                f'background:#E3EAF2; border:1px solid {C_BORDER};'
                'border-radius:14px; color:#94A7BC; font-size:12px;')
            av.setText('Q')
            cuin = str(cuin or '').strip()
            if cuin:
                self._comment_avatars[cuin] = av
                ImageLoader.instance().avatar(cuin)
            row.addWidget(av, 0, Qt.AlignmentFlag.AlignTop)

            col = QVBoxLayout()
            col.setSpacing(1)
            head = QHBoxLayout()
            head.setSpacing(6)
            name = QLabel(str(cnick or 'QQ用户'), box)
            name.setObjectName('CommentText')
            name.setStyleSheet(f'color:{C_LINK}; font-weight:bold;')
            t = QLabel(str(ctime), box)
            t.setStyleSheet(f'color:{C_TEXT_SUB}; font-size:11px;')
            head.addWidget(name)
            head.addStretch(1)
            head.addWidget(t)
            col.addLayout(head)
            msg = QLabel(box)
            msg.setWordWrap(True)
            msg.setTextFormat(Qt.TextFormat.RichText)
            msg.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            msg.setText(emoji_to_html(str(ccontent), 16))
            self._comment_labels.append(msg)
            self._emoji_codes |= collect_emoji_codes(ccontent)
            col.addWidget(msg)
            row.addLayout(col, 1)
            self._comment_rows.append([av, name, t, msg])
            lay.addLayout(row)
        if len(self.comments) > 3:
            self._toggle_btn = QPushButton(f'查看全部 {len(self.comments)} 条评论', box)
            self._toggle_btn.setObjectName('MoreBtn')
            self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._toggle_btn.clicked.connect(self._toggle_comments)
            lay.addWidget(self._toggle_btn)
            self._apply_comment_fold()
        body.addWidget(box)

    def _apply_comment_fold(self):
        """评论 >3 条时折叠/展开：前 3 条可见，其余隐藏。"""
        expanded = self._comments_expanded
        for i, widgets in enumerate(self._comment_rows):
            show = expanded or i < 3
            for w in widgets:
                w.setVisible(show)
        if self._toggle_btn:
            if expanded:
                self._toggle_btn.setText('收起评论')
            else:
                self._toggle_btn.setText(f'查看全部 {len(self.comments)} 条评论')

    def _toggle_comments(self):
        self._comments_expanded = not self._comments_expanded
        self._apply_comment_fold()

    def _build_actions(self, body):
        """互动信息条：赞数 / 评论数 / 图片数（替代无效的演示操作按钮）。"""
        stats = [('heart', self.like_count, '赞'),
                 ('comments', len(self.comments), '评论'),
                 ('images', len(self.img_links), '图片')]
        row = QHBoxLayout()
        row.setSpacing(6)
        shown = 0
        for icon, count, label in stats:
            if count <= 0:
                continue
            if shown:
                dot = QLabel('·', self)
                dot.setStyleSheet(f'color:{C_TEXT_SUB}; font-size:12px;')
                row.addWidget(dot)
            pm = render_svg_icon(icon, C_TEXT_SUB, 13)
            if pm is not None:
                ic = QLabel(self)
                ic.setPixmap(pm)
                row.addWidget(ic)
                lb = QLabel(str(count), self)
                lb.setStyleSheet(f'color:{C_TEXT_SUB}; font-size:12px;')
                row.addWidget(lb)
            else:
                lb = QLabel(f'{label} {count}', self)
                lb.setStyleSheet(f'color:{C_TEXT_SUB}; font-size:12px;')
                row.addWidget(lb)
            if label == '赞' and self.likers:
                names = '、'.join(self.likers[:20])
                if len(self.likers) > 20:
                    names += f' 等 {len(self.likers)} 人'
                lb.setToolTip(f'{names} 赞了')
            shown += 1
        if shown:
            row.addStretch(1)
            body.addLayout(row)

    # ---------- 数据加载 ----------
    @staticmethod
    def _split_source(body_text):
        """把正文中 '来自：xxx' 的行拆出来，返回 (主正文, 来源文本)。"""
        if not body_text:
            return body_text, ''
        src, keep = '', []
        for line in str(body_text).split('\n'):
            if line.startswith('来自：'):
                src = line
            else:
                keep.append(line)
        return '\n'.join(keep).strip(), src

    def _load_all(self):
        ImageLoader.instance().avatar(self.avatar_uin)
        name, body_text = split_name_body(self.content)
        body_text, src_text = self._split_source(body_text if body_text else self.content)
        self.src_lb.setText(src_text)
        self.src_lb.setVisible(bool(src_text))
        self._emoji_codes |= collect_emoji_codes(self.content)
        self.content_lb.setText(emoji_to_html(body_text, 20))
        if not name:
            name = self.nickname
        for link, cell in self._grid_cells:
            self._load_pic(link, cell)

    def _refresh_rich_texts(self):
        """表情下载完成后刷新富文本正文与评论（防重入：表情就绪回调不再触发新请求）。"""
        if getattr(self, '_refreshing', False):
            return
        self._refreshing = True
        try:
            name, body_text = split_name_body(self.content)
            body_text, src_text = self._split_source(body_text if body_text else self.content)
            self.src_lb.setText(src_text)
            self.src_lb.setVisible(bool(src_text))
            self.content_lb.setText(emoji_to_html(body_text, 20))
            i = 0
            for c in self.comments:
                try:
                    _t, ccontent, _n, _u = c
                except (TypeError, ValueError):
                    continue
                if i < len(self._comment_labels):
                    self._comment_labels[i].setText(emoji_to_html(str(ccontent), 16))
                i += 1
        finally:
            self._refreshing = False

    def _load_pic(self, link, cell):
        link = str(link).strip()
        if not link:
            cell.setText('')
            return
        if link.startswith(('pic/', 'pic\\')):
            path = os.path.join(Config.PROJECT_ROOT, link.replace('\\', '/'))
            if os.path.exists(path):
                # 本地图片加载缩略上限 1024：保证显示清晰的同时控制内存
                ImageLoader.instance().load_local(f'local:{link}', path, 1024)
                return
            cell.set_pix(QPixmap())
            return
        if link.startswith('http') or link.startswith('//'):
            if link.startswith('//'):
                link = 'https:' + link  # 协议相对链接补全
            cell.current_key = hashlib.md5(link.encode('utf-8')).hexdigest()[:16]
            ImageLoader.instance().picture(link)
            return
        cell.setText('')

    def _on_image_loaded(self, key, pixmap):
        if key.startswith('avatar:'):
            uin = key.split(':', 1)[1]
            if uin == self.avatar_uin:
                if not pixmap.isNull():
                    self.avatar.setPixmap(make_circle(pixmap, 56))
                return
            # 评论者头像
            av = self._comment_avatars.get(uin)
            if av is not None and not pixmap.isNull():
                av.setPixmap(make_circle(pixmap, 28))
            return
        if key.startswith('emoji:'):
            code = key.split(':', 1)[1]
            if code in self._emoji_codes:
                self._refresh_rich_texts()
            return
        if key.startswith('local:'):
            link = key.split(':', 1)[1]
            for lk, cell in self._grid_cells:
                if lk == link:
                    cell.set_pix(pixmap)
                    break
            return
        if key.startswith('pic:'):
            # 找到对应格子（兼容协议相对链接的两种写法）；下载失败时
            # 逐个尝试候选地址（QzoneArchive load_archived_image 思路）
            h = key.split(':', 1)[1]
            for link, cell in self._grid_cells:
                hit = (hashlib.md5(link.encode('utf-8')).hexdigest()[:16] == h) or \
                      (link.startswith('//') and hashlib.md5(
                          ('https:' + link).encode('utf-8')).hexdigest()[:16] == h) or \
                      (cell.current_key == h)
                if hit:
                    if pixmap.isNull():
                        nxt = cell.next_candidate()
                        if nxt:
                            cell.current_key = hashlib.md5(
                                nxt.encode('utf-8')).hexdigest()[:16]
                            ImageLoader.instance().picture(nxt)
                        else:
                            cell.set_pix(pixmap)  # 全部候选失败 -> 默认占位图
                    else:
                        cell.set_pix(pixmap)
                    break

    # ---------- 交互 ----------
    def _open_home(self):
        if self.avatar_uin:
            QDesktopServices.openUrl(
                QUrl(f'https://user.qzone.qq.com/{self.avatar_uin}/main'))

    def _show_photo(self, link):
        dlg = PhotoDialog(link, self)
        dlg.exec()
