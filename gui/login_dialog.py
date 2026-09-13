# -*- coding: utf-8 -*-
"""登录对话框：已保存用户选择 + 二维码扫码登录（仿 QQ 空间登录窗）。"""
import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
                             QStackedWidget, QVBoxLayout, QWidget)

from gui.image_loader import ImageLoader
from gui.services import QR_IMG_PATH
from gui.workers import QrLoginWorker

import util.ConfigUtil as Config

QR_SIZE = 280


class _ScaledLabel(QLabel):
    """按可用区域等比例缩放显示图片（不裁剪、不拉伸变形）。"""

    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        self._src = pixmap if pixmap else QPixmap()
        self._last = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(10, 10)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._render()

    def _render(self):
        if self._src.isNull() or self.width() <= 0 or self.height() <= 0:
            return
        if (self.width(), self.height()) == self._last:
            return
        self._last = (self.width(), self.height())
        self.setPixmap(self._src.scaled(self.size(),
                                        Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation))


class LoginDialog(QDialog):
    """返回 result_cookies（dict）或 None。"""

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.result_cookies = None
        self._qrsig = None
        self._qr_worker = None
        self.setWindowTitle('登录 QQ 空间')
        self.setModal(True)
        self.resize(420, 640)
        self._build_ui()
        self._show_user_list()

    # ---------- UI ----------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card = QFrame(self)
        card.setObjectName('LoginCard')
        lay = QVBoxLayout(card)
        lay.setContentsMargins(30, 26, 30, 26)
        lay.setSpacing(14)

        title = QLabel('QQ 空间', card)
        title.setObjectName('LoginTitle')
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        sub = QLabel('登录后即可获取你的历史说说数据', card)
        sub.setObjectName('LoginHint')
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(sub)

        self.stack = QStackedWidget(card)
        self.stack.addWidget(self._build_user_page())
        self.stack.addWidget(self._build_qr_page())
        lay.addWidget(self.stack)
        root.addWidget(card)

    def _build_user_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(10)
        hint = QLabel('选择已登录的账号，或扫码登录新账号', page)
        hint.setObjectName('LoginHint')
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(hint)
        self.users_box = QVBoxLayout()
        self.users_box.setSpacing(8)
        lay.addLayout(self.users_box)
        self.no_user_lb = QLabel('暂无已登录账号，请扫码登录', page)
        self.no_user_lb.setObjectName('LoginHint')
        self.no_user_lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.no_user_lb)

        # 打赏图：铺满账号列表下方剩余页面空间（等比例缩放，不重叠）
        lay.addWidget(self._build_sponsor_card(page), 1)

        # 作者 / 联系方式独立信息卡
        lay.addWidget(self._build_about_card(page))

        qr_btn = QPushButton('扫码登录新账号', page)
        qr_btn.setObjectName('PrimaryBtn')
        qr_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        qr_btn.clicked.connect(self._to_qr)
        lay.addWidget(qr_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        return page

    def _build_qr_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(12)

        box = QFrame(page)
        box.setObjectName('QrBox')
        blay = QVBoxLayout(box)
        blay.setContentsMargins(10, 10, 10, 10)
        self.qr_lb = QLabel(box)
        self.qr_lb.setFixedSize(QR_SIZE, QR_SIZE)
        self.qr_lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_lb.setText('加载二维码中…')
        self.qr_lb.setStyleSheet('color:#9FB2C8; background:#F7F9FC; border-radius:8px;')
        blay.addWidget(self.qr_lb)
        lay.addWidget(box, 0, Qt.AlignmentFlag.AlignHCenter)

        self.qr_status = QLabel('请使用手机 QQ 扫码登录', page)
        self.qr_status.setObjectName('LoginHint')
        self.qr_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.qr_status)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        refresh_btn = QPushButton('刷新二维码', page)
        refresh_btn.setObjectName('GhostBtn')
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.clicked.connect(self._refresh_qr)
        back_btn = QPushButton('返回账号列表', page)
        back_btn.setObjectName('FlatBtn')
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.clicked.connect(self._back_to_users)
        btn_row.addStretch(1)
        btn_row.addWidget(back_btn)
        btn_row.addWidget(refresh_btn)
        lay.addLayout(btn_row)
        return page

    def _build_sponsor_card(self, parent):
        """打赏图：铺满账号列表下方剩余页面空间（等比例缩放，不裁剪），
        文案固定在图片下方。"""
        box = QWidget(parent)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        sp = _ScaledLabel(QPixmap(), box)
        sp_path = Config.resource_path('sponsor.jpg')
        if os.path.exists(sp_path):
            pm = QPixmap(sp_path)
            if not pm.isNull():
                sp._src = pm
        lay.addWidget(sp, 1)

        tip = QLabel('如果这个工具帮到了你，欢迎打赏支持（自愿）。', box)
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tip.setWordWrap(True)
        tip.setStyleSheet('color:#999999; font-size:12px; line-height:1.5;')
        lay.addWidget(tip)
        return box

    def _build_about_card(self, parent):
        """作者 / 联系方式信息卡（独立于打赏内容）。"""
        about = QFrame(parent)
        about.setObjectName('LoginAbout')
        alay = QHBoxLayout(about)
        alay.setContentsMargins(14, 8, 14, 8)
        alay.setSpacing(8)
        alay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        logo_lb = QLabel(about)
        logo_lb.setFixedSize(28, 28)
        logo_lb.setScaledContents(True)
        logo_path = Config.resource_path('logo.png')
        if os.path.exists(logo_path):
            logo_lb.setPixmap(QPixmap(logo_path))
        else:
            logo_lb.setText('GZH')
            logo_lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            logo_lb.setStyleSheet(
                'background:#49A1F9; color:#fff; border-radius:14px; font-weight:bold;')
        alay.addWidget(logo_lb)

        author = QLabel('作者 · ll0v0ll', about)
        author.setStyleSheet('font-size:12px; font-weight:bold; color:#333333;')
        alay.addWidget(author)

        links = QLabel(
            '<a href="https://github.com/ll0v0ll" style="color:#49A1F9;'
            'text-decoration:none;">GitHub 主页 ↗</a>'
            ' · <a href="mailto:chaiyzh@126.com" style="color:#49A1F9;'
            'text-decoration:none;">chaiyzh@126.com</a>', about)
        links.setOpenExternalLinks(True)
        links.setTextFormat(Qt.TextFormat.RichText)
        links.setStyleSheet('font-size:11px;')
        alay.addWidget(links)
        return about

    # ---------- 用户列表 ----------
    def _show_user_list(self):
        users = self.service.load_saved_users()
        while self.users_box.count():
            item = self.users_box.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        if not users:
            self.no_user_lb.show()
        else:
            self.no_user_lb.hide()
            for qq in users:
                btn = QPushButton(f'QQ 号：{qq}', self)
                btn.setObjectName('UserBtn')
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setMinimumHeight(44)
                btn.clicked.connect(lambda _, u=qq: self._pick_user(u))
                self.users_box.addWidget(btn)
        self.stack.setCurrentIndex(0)

    def _pick_user(self, qq):
        try:
            cookies = self.service.load_cookie(qq)
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, '读取失败', f'读取登录信息失败：{e}')
            return
        self.result_cookies = cookies
        self.accept()

    # ---------- 扫码 ----------
    def _to_qr(self):
        self.stack.setCurrentIndex(1)
        self._refresh_qr()

    def _back_to_users(self):
        if self._qr_worker:
            self._qr_worker.stop()
            self._qr_worker = None
        self._show_user_list()

    def _refresh_qr(self):
        if self._qr_worker:
            self._qr_worker.stop()
            self._qr_worker = None
        self.qr_status.setText('正在获取二维码…')
        try:
            self._qrsig = self.service.fetch_qr_code()
        except Exception as e:
            self.qr_status.setText(f'获取二维码失败：{e}')
            return
        pm = QPixmap(QR_IMG_PATH)
        if pm.isNull():
            self.qr_status.setText('二维码图片加载失败，请点击刷新重试')
            return
        self.qr_lb.setPixmap(pm.scaled(QR_SIZE, QR_SIZE,
                                       Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation))
        self.qr_status.setText('请使用手机 QQ 扫码登录')

        self._qr_worker = QrLoginWorker(self.service, self._qrsig, self)
        self._qr_worker.status_changed.connect(self._on_qr_status)
        self._qr_worker.message.connect(self.qr_status.setText)
        self._qr_worker.finished.connect(self._on_worker_finished)
        self._qr_worker.start()

    def _on_qr_status(self, status):
        if status == 'wait':
            self.qr_status.setText('等待扫码…')
        elif status == 'expired':
            self.qr_status.setText('二维码已失效，请点击刷新')
        elif status == 'cancel':
            self.qr_status.setText('已取消登录')

    def _on_worker_finished(self):
        worker = self.sender()
        cookies = getattr(worker, 'cookies', None)
        self._qr_worker = None
        if cookies:
            self.result_cookies = cookies
            self.accept()

    def closeEvent(self, ev):
        if self._qr_worker:
            self._qr_worker.stop()
        super().closeEvent(ev)
