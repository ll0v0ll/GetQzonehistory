# -*- coding: utf-8 -*-
"""主窗口：仿 QQ 空间布局（顶部导航 + 左侧资料栏 + 中央动态流）。

动态流采用分批渲染（每次 RENDER_BATCH 条，滚动到底自动加载），
避免一次性渲染上千条说说导致界面卡死。
"""
import os
import platform
import re
import subprocess
import time

import pandas as pd
from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QHeaderView,
                             QLabel, QMainWindow, QMessageBox, QProgressBar,
                             QPushButton, QScrollArea, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

import util.ConfigUtil as Config
from gui.image_loader import ImageLoader, render_svg_icon
from gui.logger import get_logger
from gui.moment_widget import MomentWidget, make_circle
from gui.workers import FetchWorker

log = get_logger('main')

TABS = ['全部', '说说', '转发', '留言', '其他']
RENDER_BATCH = 20  # 每次滚动加载条数



# 作者联系方式（contact me）
AUTHOR = {
    'name': 'll0v0ll',
    'github': 'https://github.com/ll0v0ll',
    'email': 'chaiyzh@126.com',
}


class MainWindow(QMainWindow):

    def __init__(self, service, cookies, uin, nickname, parent=None):
        super().__init__(parent)
        self.service = service
        self.cookies = cookies
        self.uin = uin
        self.nickname = nickname
        self.texts = []
        self.tab_data = {t: [] for t in TABS}
        self._fetch_worker = None
        self._result_path = None
        self._stats = {}
        # 分批渲染状态
        self._pending_moments = []
        self._rendered = 0
        self._current_tab = '全部'
        self._render_batch = RENDER_BATCH
        self._shown = False      # 窗口是否已显示（显示前布局高度无效，禁止自动补载）

        self.setWindowTitle('QQ 空间 - 历史数据获取')
        self.resize(1120, 975)
        self.setMinimumSize(900, 560)
        self._fit_screen()
        self._build_ui()
        self._after_login()
        t0 = time.time()
        self.load_local_data()
        log.info('加载本地数据完成，耗时 %.2fs，共 %d 条', time.time() - t0, len(self.texts))
        self.apply_tab('全部')

    def _fit_screen(self):
        """按屏幕可用区域自适应窗口尺寸，避免小屏/高 DPI 下底部被裁。"""
        try:
            from PyQt6.QtGui import QGuiApplication
            screen = QGuiApplication.primaryScreen()
            if screen is None:
                return
            geo = screen.availableGeometry()
            w = min(1120, geo.width() - 40)
            h = min(975, geo.height() - 80)
            if w >= 900 and h >= 560:
                self.resize(w, h)
        except Exception:
            pass

    def showEvent(self, ev):
        super().showEvent(ev)
        self._shown = True
        # 显示后布局高度有效，若内容不足视口则自动补载
        QTimer.singleShot(50, self._fill_viewport)

    # ================= UI 构建 =================
    def _build_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_navbar())

        body = QHBoxLayout()
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(14)
        body.addWidget(self._build_sidebar())
        body.addWidget(self._build_content(), 1)
        root.addLayout(body, 1)

        root.addWidget(self._build_statusbar())

    # ----- 顶部导航 -----
    def _build_navbar(self):
        nav = QFrame(self)
        nav.setObjectName('NavBar')
        nav.setFixedHeight(58)
        lay = QHBoxLayout(nav)
        lay.setContentsMargins(18, 0, 18, 0)
        lay.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        logo_path = Config.resource_path('logo.png')
        if os.path.exists(logo_path):
            logo_lb = QLabel(nav)
            logo_lb.setFixedSize(24, 24)
            logo_lb.setScaledContents(True)
            logo_lb.setPixmap(QPixmap(logo_path))
            title_row.addWidget(logo_lb)
        title = QLabel('QQ 空间', nav)
        title.setObjectName('NavTitle')
        title_row.addWidget(title)
        lay.addLayout(title_row)
        sub = QLabel('GetQzonehistory · 历史数据获取', nav)
        sub.setObjectName('NavSub')
        lay.addWidget(sub)
        lay.addStretch(1)

        self.nav_avatar = QLabel(nav)
        self.nav_avatar.setFixedSize(28, 28)
        self.nav_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.nav_avatar.setStyleSheet(
            'background:rgba(255,255,255,0.3); border-radius:14px; color:#fff;')
        self.nav_avatar.setText('QQ')
        self.nav_user = QLabel(self.nickname, nav)
        self.nav_user.setObjectName('NavUser')
        logout = QPushButton('退出登录', nav)
        logout.setObjectName('NavLogout')
        logout.setCursor(Qt.CursorShape.PointingHandCursor)
        logout.clicked.connect(self._logout)
        lay.addWidget(self.nav_avatar)
        lay.addWidget(self.nav_user)
        lay.addSpacing(6)
        lay.addWidget(logout)
        return nav

    # ----- 左侧栏 -----
    def _build_sidebar(self):
        # 侧栏整体放入滚动区：窗口较矮时内容可滚动查看，避免文字重叠/裁剪
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(238)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            'QScrollArea{background:transparent; border:none;} '
            'QScrollArea > QWidget > QWidget{background:transparent;}')
        side = QFrame(scroll)
        side.setObjectName('SidebarCard')
        side.setFixedWidth(232)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(14, 16, 14, 16)
        lay.setSpacing(10)

        # 个人资料卡
        info = QFrame(side)
        info.setObjectName('Card')
        ilay = QVBoxLayout(info)
        ilay.setContentsMargins(14, 16, 14, 14)
        ilay.setSpacing(8)

        self.big_avatar = QLabel(info)
        self.big_avatar.setFixedSize(84, 84)
        self.big_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.big_avatar.setStyleSheet(
            'background:#DDE6F0; border:2px solid #FFFFFF; border-radius:42px;'
            'color:#9FB2C8; font-size:28px;')
        self.big_avatar.setText('QQ')
        ilay.addWidget(self.big_avatar, 0, Qt.AlignmentFlag.AlignHCenter)

        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        self.side_name = QLabel(self.nickname, info)
        self.side_name.setObjectName('SideTitle')
        self.side_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lv = QLabel('LV', info)
        lv.setObjectName('LvBadge')
        name_row.addStretch(1)
        name_row.addWidget(self.side_name)
        name_row.addWidget(lv)
        name_row.addStretch(1)
        ilay.addLayout(name_row)

        self.side_qq = QLabel(f'QQ号：{self.uin}', info)
        self.side_qq.setStyleSheet('color:#999999; font-size:12px;')
        self.side_qq.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ilay.addWidget(self.side_qq)
        lay.addWidget(info)

        # 菜单
        menu = QFrame(side)
        menu.setObjectName('Card')
        mlay = QVBoxLayout(menu)
        mlay.setContentsMargins(8, 10, 8, 10)
        mlay.setSpacing(4)
        self._menu_group = QButtonGroup(self)
        self._menu_group.setExclusive(True)
        for i, (name, tab) in enumerate((('我的说说', '全部'), ('全部动态', '全部'),
                                         ('数据统计', '统计'))):
            b = QPushButton(name, menu)
            b.setObjectName('MenuBtn')
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _, t=tab: self.apply_tab(t))
            self._menu_group.addButton(b, i)
            mlay.addWidget(b)
        self._menu_group.button(0).setChecked(True)
        lay.addWidget(menu)

        # 统计卡
        self.stats_card = QFrame(side)
        self.stats_card.setObjectName('Card')
        slay = QVBoxLayout(self.stats_card)
        slay.setContentsMargins(14, 12, 14, 12)
        slay.setSpacing(6)
        t = QLabel('数据统计', self.stats_card)
        t.setObjectName('SideTitle')
        slay.addWidget(t)
        self.stats_lb = QLabel('暂无数据', self.stats_card)
        self.stats_lb.setWordWrap(True)
        self.stats_lb.setStyleSheet('color:#666666; font-size:12px; line-height:1.6;')
        slay.addWidget(self.stats_lb)
        lay.addWidget(self.stats_card)

        # 作者信息卡固定在侧栏下方固定位置（stretch 之前），避免窗口较矮时被裁剪
        lay.addWidget(self._build_about_card(side))
        lay.addStretch(1)

        fetch_btn = QPushButton('开始获取数据', side)
        fetch_btn.setObjectName('PrimaryBtn')
        fetch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fetch_btn.clicked.connect(self.start_fetch)
        lay.addWidget(fetch_btn)

        open_btn = QPushButton('打开结果目录', side)
        open_btn.setObjectName('GhostBtn')
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.clicked.connect(self.open_result_dir)
        lay.addWidget(open_btn)
        scroll.setWidget(side)
        return scroll

    def _build_about_card(self, parent):
        about = QFrame(parent)
        about.setObjectName('Card')
        alay = QVBoxLayout(about)
        alay.setContentsMargins(12, 10, 12, 10)
        alay.setSpacing(5)

        head = QHBoxLayout()
        head.setSpacing(8)
        logo_path = Config.resource_path('logo.png')
        logo_lb = QLabel(about)
        logo_lb.setFixedSize(36, 36)
        logo_lb.setScaledContents(True)
        if os.path.exists(logo_path):
            logo_lb.setPixmap(QPixmap(logo_path))
        else:
            logo_lb.setText('GZH')
            logo_lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            logo_lb.setStyleSheet(
                'background:#49A1F9; color:#fff; border-radius:18px; font-weight:bold;')
        head.addWidget(logo_lb)

        tcol = QVBoxLayout()
        tcol.setSpacing(2)
        proj = QLabel('GetQzonehistory', about)
        proj.setObjectName('SideTitle')
        author = QLabel(f'作者 · {AUTHOR["name"]}', about)
        author.setStyleSheet('color:#999999; font-size:11px;')
        tcol.addWidget(proj)
        tcol.addWidget(author)
        head.addLayout(tcol, 1)
        alay.addLayout(head)

        gh = QLabel(f'<a href="{AUTHOR["github"]}" style="color:#49A1F9;'
                    'text-decoration:none;">GitHub 主页 ↗</a>', about)
        gh.setOpenExternalLinks(True)
        gh.setTextFormat(Qt.TextFormat.RichText)
        gh.setStyleSheet('font-size:12px;')
        gh.setCursor(Qt.CursorShape.PointingHandCursor)
        alay.addWidget(gh)

        mail = QLabel(f'<a href="mailto:{AUTHOR["email"]}" style="color:#49A1F9;'
                      f'text-decoration:none;">{AUTHOR["email"]}</a>', about)
        mail.setOpenExternalLinks(True)
        mail.setTextFormat(Qt.TextFormat.RichText)
        mail.setStyleSheet('font-size:12px;')
        mail.setCursor(Qt.CursorShape.PointingHandCursor)
        mail.setToolTip(f'发送邮件至 {AUTHOR["email"]}')
        alay.addWidget(mail)
        alay.addSpacing(2)
        return about

    # ----- 中央内容区 -----
    def _build_content(self):
        content = QWidget(self)
        lay = QVBoxLayout(content)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        # 分类 tab
        tabs = QHBoxLayout()
        tabs.setSpacing(4)
        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)
        for i, name in enumerate(TABS):
            b = QPushButton(name, content)
            b.setObjectName('TabBtn')
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _, n=name: self.apply_tab(n))
            self._tab_group.addButton(b, i)
            tabs.addWidget(b)
        tabs.addStretch(1)
        self.tab_count_lb = QLabel('', content)
        self.tab_count_lb.setStyleSheet('color:#999999; font-size:12px;')
        tabs.addWidget(self.tab_count_lb)
        lay.addLayout(tabs)

        # 内容滚动区（滚动到底部时加载下一批）
        self.scroll = QScrollArea(content)
        self.scroll.setWidgetResizable(True)
        self.flow_container = QWidget(self.scroll)
        self.flow_layout = QVBoxLayout(self.flow_container)
        self.flow_layout.setContentsMargins(2, 4, 2, 8)
        self.flow_layout.setSpacing(12)
        self.flow_layout.addStretch(1)
        self.scroll.setWidget(self.flow_container)
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)
        lay.addWidget(self.scroll, 1)
        return content

    # ----- 底部状态栏 -----
    def _build_statusbar(self):
        bar = QFrame(self)
        bar.setObjectName('StatusBar')
        bar.setFixedHeight(34)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(12)
        self.status_text = QLabel('就绪', bar)
        self.status_text.setObjectName('StatusText')
        lay.addWidget(self.status_text)
        lay.addStretch(1)
        self.progress = QProgressBar(bar)
        self.progress.setFixedWidth(240)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.hide()
        lay.addWidget(self.progress)
        return bar

    # ================= 数据 =================
    def _after_login(self):
        """登录后：设置默认头像、后台加载真实头像、设置路径。"""
        self._apply_default_avatar()
        ImageLoader.instance().avatar(self.uin)
        ImageLoader.instance().loaded.connect(self._on_nav_avatar)
        self._result_path = os.path.join(Config.result_path, self.uin)

    def _apply_default_avatar(self):
        """真实头像加载完成前，先使用默认管理员头像。"""
        path = Config.resource_path('defaultAdmin.jpg')
        if not os.path.exists(path):
            return
        pm = QPixmap(path)
        if pm.isNull():
            return
        self.nav_avatar.setPixmap(make_circle(pm, 28))
        self.big_avatar.setPixmap(make_circle(pm, 84))

    def _on_nav_avatar(self, key, pixmap):
        if key == f'avatar:{self.uin}' and not pixmap.isNull():
            small = make_circle(pixmap, 28)
            self.nav_avatar.setPixmap(small)
            big = make_circle(pixmap, 84)
            self.big_avatar.setPixmap(big)

    def load_local_data(self):
        """从本地导出目录读取已有数据。"""
        base = self._result_path
        full_xlsx = os.path.join(base, f'{self.uin}_全部列表.xlsx')
        if os.path.exists(full_xlsx):
            try:
                df = pd.read_excel(full_xlsx)
                self.texts = df[['时间', '内容', '图片链接', '评论']].values.tolist()
                log.info('已读取本地数据 %d 条: %s', len(self.texts), full_xlsx)
            except Exception as e:
                log.error('读取本地数据失败: %s', e, exc_info=True)
        self._classify()
        self._update_stats()

    def _classify(self):
        """按 main.py 规则分类。"""
        self.tab_data = {t: [] for t in TABS}
        for item in self.texts:
            item_text = str(item[1])
            if self.nickname in item_text:
                if '留言' in item_text:
                    self.tab_data['留言'].append(item[:-1])
                elif '转发' in item_text:
                    self.tab_data['转发'].append(item)
                else:
                    self.tab_data['说说'].append(item)
            else:
                self.tab_data['其他'].append(item[:-1])
        self.tab_data['全部'] = self.texts

    def _update_stats(self):
        pic_path = os.path.join(self._result_path or '', 'pic')
        pic_count = 0
        if os.path.exists(pic_path):
            pic_count = len([f for f in os.listdir(pic_path)
                             if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif'))])
        lines = [
            f'全部动态：{len(self.tab_data["全部"])}',
            f'我的说说：{len(self.tab_data["说说"])}',
            f'转发：{len(self.tab_data["转发"])}',
            f'留言：{len(self.tab_data["留言"])}',
            f'其他：{len(self.tab_data["其他"])}',
            f'图片：{pic_count}',
        ]
        self.stats_lb.setText('\n'.join(lines))

    # ================= 渲染 =================
    def apply_tab(self, name):
        self._current_tab = name
        if name == '统计':
            self._render_stats_page()
            self._set_menu_checked('统计')
            # 统计页不属于顶部 tab：取消顶部全部高亮，避免残留错乱
            self._tab_group.setExclusive(False)
            for i in range(len(TABS)):
                self._tab_group.button(i).setChecked(False)
            self._tab_group.setExclusive(True)
            return
        self._set_menu_checked(name if name in ('全部',) else '我的说说')
        # 同步 tab 按钮状态
        if name in TABS:
            self._tab_group.button(TABS.index(name)).setChecked(True)
        self.tab_count_lb.setText(f'共 {len(self.tab_data.get(name, []))} 条')
        self._render_moments(self.tab_data.get(name, []))

    def _set_menu_checked(self, tab):
        mapping = {'全部': 0, '我的说说': 0, '统计': 2}
        idx = mapping.get(tab)
        if idx is not None:
            self._menu_group.button(idx).setChecked(True)

    @staticmethod
    def _wipe_layout(lay):
        """递归销毁 layout 内所有子项（widget / 子 layout / spacer）。

        统计页用 insertLayout 插入的 QHBoxLayout 项，若只 takeAt 不销毁，
        其中 widget 仍挂在布局上继续渲染，反复切换导航会叠加出重复内容。
        """
        if lay is None:
            return
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)      # 立即脱离可见树（不依赖事件循环）
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                self._wipe_layout(sub)
                sub.setParent(None)
                sub.deleteLater()

    def _clear_flow(self):
        while self.flow_layout.count() > 1:  # 保留底部 stretch
            item = self.flow_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                self._wipe_layout(sub)
                sub.setParent(None)
                sub.deleteLater()
        self._pending_moments = []
        self._rendered = 0

    def _render_moments(self, data):
        self._clear_flow()
        if not data:
            self._render_empty_card()
            return
        self._pending_moments = list(data)
        self._load_more()

    def _render_empty_card(self):
        """无数据时展示默认内容卡片（默认头像 + 欢迎语）。"""
        card = QFrame(self)
        card.setObjectName('MomentCard')
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(12)
        av = QLabel(card)
        av.setFixedSize(56, 56)
        av.setAlignment(Qt.AlignmentFlag.AlignCenter)
        av.setStyleSheet('background:#DDE6F0; border-radius:28px;')
        avatar_path = Config.resource_path('defaultAdmin.jpg')
        if os.path.exists(avatar_path):
            pm = QPixmap(avatar_path)
            if not pm.isNull():
                av.setPixmap(make_circle(pm, 56))
        head.addWidget(av, 0, Qt.AlignmentFlag.AlignTop)

        body = QVBoxLayout()
        body.setSpacing(6)
        name = QLabel('小Q助手', card)
        name.setObjectName('MomName')
        body.addWidget(name)
        from datetime import datetime
        t = QLabel(datetime.now().strftime('%Y年%m月%d日 %H:%M'), card)
        t.setObjectName('MomTime')
        body.addWidget(t)
        content = QLabel(
            '这里空空如也～ 点击左侧「开始获取数据」拉取你的历史动态。\n\n'
            '获取完成后，你的历史说说、图片与评论都会展示在这里。\n'
            '（以上为默认内容，获取数据后自动替换）', card)
        content.setWordWrap(True)
        content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        content.setStyleSheet('font-size:14px; color:#333333; line-height:1.6;')
        body.addWidget(content)
        head.addLayout(body, 1)
        lay.addLayout(head)
        self.flow_layout.insertWidget(0, card)

    def _load_more(self):
        """渲染下一批说说卡片。"""
        start = self._rendered
        end = min(start + self._render_batch, len(self._pending_moments))
        for i in range(start, end):
            card = self._make_moment_card(self._pending_moments[i])
            if card is not None:
                # 插入到底部 stretch 之前
                self.flow_layout.insertWidget(self.flow_layout.count() - 1, card)
        self._rendered = end
        # 提示条
        tip = QLabel('', self)
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tip.setStyleSheet('color:#B0BCCB; font-size:12px; padding:8px;')
        if self._rendered < len(self._pending_moments):
            tip.setText(f'已加载 {self._rendered}/{len(self._pending_moments)} 条，'
                        '滚动加载更多…')
        else:
            tip.setText(f'已全部加载（共 {self._rendered} 条）')
        self.flow_layout.insertWidget(self.flow_layout.count() - 1, tip)
        log.debug('分批渲染: %d-%d / %d', start, end, len(self._pending_moments))
        # 若当前内容高度不足以填满视口（例如卡片较矮），继续加载下一批
        QTimer.singleShot(0, self._fill_viewport)

    def _fill_viewport(self):
        if not self._shown:
            return
        if not self._pending_moments:
            return
        if self._rendered >= len(self._pending_moments):
            return
        # 内容高度不足以填满视口时，继续加载下一批
        if self.flow_container.height() < self.scroll.viewport().height():
            self._load_more()

    def _make_moment_card(self, item):
        try:
            time_str = str(item[0])
            content = str(item[1])
            img_links = [x for x in str(item[2]).split(',') if x.strip()] \
                if len(item) > 2 else []
            comments = self._parse_comments(item[3]) if len(item) > 3 else []
            like_count = 0
            likers = []
            if len(item) > 4:
                try:
                    like_count = int(item[4] or 0)
                except (TypeError, ValueError):
                    like_count = 0
            if len(item) > 6 and isinstance(item[6], (list, tuple)):
                likers = [str(x) for x in item[6] if x]
            pics_all = []
            if len(item) > 7 and isinstance(item[7], (list, tuple)):
                pics_all = item[7]
        except Exception as e:
            log.warning('说说数据解析失败: %s', e)
            return None
        return MomentWidget(time_str, content, img_links, comments,
                            self.uin, self.nickname, self,
                            likers=likers, like_count=like_count,
                            pics_all=pics_all)

    def _on_scroll(self, value):
        """滚动到底部附近时加载下一批。"""
        if not self._pending_moments:
            return
        if self._rendered >= len(self._pending_moments):
            return
        bar = self.scroll.verticalScrollBar()
        if bar.maximum() == 0 or value >= bar.maximum() - 400:
            self._load_more()
    @staticmethod
    def _parse_comments(v):
        if isinstance(v, str) and v.strip():
            try:
                return eval(v)
            except Exception:
                return []
        if isinstance(v, list):
            return v
        return []

    # ================= 仪表盘（数据统计页） =================
    @staticmethod
    def _dir_size(path):
        total = 0
        if os.path.exists(path):
            for root, _, files in os.walk(path):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
        return total

    @staticmethod
    def _fmt_size(size):
        if size < 1024 * 1024:
            return f'{size / 1024:.1f} KB'
        return f'{size / 1024 / 1024:.1f} MB'

    def _count_local_images(self):
        pic_path = os.path.join(self._result_path or '', 'pic')
        if not os.path.exists(pic_path):
            return 0
        return len([f for f in os.listdir(pic_path)
                    if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif'))])

    def _render_svg_icon(self, name, color, size=36):
        """渲染 resource/icons/<name>.svg 为指定颜色的 pixmap（Font Awesome，开源免费）。

        图标缺失时回退为 emoji 字符，保证界面不出现空白。
        """
        return render_svg_icon(name, color, size)

    def _stat_card(self, label, value, hint, color, icon):
        card = QFrame(self)
        card.setObjectName('StatCard')
        lay = QHBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)
        ic = QLabel(card)
        ic.setFixedSize(36, 36)
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ic.setStyleSheet(f'background:{color}22; border-radius:8px;')
        pm = self._render_svg_icon(icon, color)
        if pm is not None:
            ic.setPixmap(pm.scaled(20, 20, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation))
        else:
            ic.setText(icon)
            ic.setStyleSheet(f'font-size:16px; background:{color}22; color:{color};'
                             ' border-radius:8px;')
        lay.addWidget(ic)
        col = QVBoxLayout()
        col.setSpacing(1)
        lb = QLabel(label, card)
        lb.setObjectName('StatCardLabel')
        val = QLabel(value, card)
        val.setObjectName('StatCardValue')
        val.setStyleSheet(f'color:{color};')
        ht = QLabel(hint, card)
        ht.setObjectName('StatCardHint')
        col.addWidget(lb)
        col.addWidget(val)
        col.addWidget(ht)
        lay.addLayout(col, 1)
        return card

    def _render_stats_page(self):
        self._clear_flow()
        self.tab_count_lb.setText('')
        texts = self.tab_data.get('全部', []) or []

        # 统计口径：内存动态 + 本地已下载图片 + 目录占用
        pic_count = self._count_local_images()
        comment_total = sum(len(t[3]) for t in texts
                            if len(t) > 3 and isinstance(t[3], list))
        like_total = sum(int(t[4]) for t in texts
                         if len(t) > 4 and str(t[4]).isdigit())
        used = self._dir_size(self._result_path) + self._dir_size(Config.fetch_path)

        # ---- hero 回忆区（参考 QzoneArchive 仪表盘 hero-panel） ----
        hero = QFrame(self)
        hero.setObjectName('HeroPanel')
        hlay = QHBoxLayout(hero)
        hlay.setContentsMargins(22, 18, 22, 18)
        hlay.setSpacing(16)

        hcol = QVBoxLayout()
        hcol.setSpacing(4)
        kicker = QLabel(f'时光档案馆 · QQ {self.uin}', hero)
        kicker.setObjectName('HeroKicker')
        hcol.addWidget(kicker)
        if texts:
            title = QLabel(f'欢迎回来，{self.nickname} —— 这里珍藏着你的 {len(texts)} 条空间记忆',
                           hero)
        else:
            title = QLabel('把珍贵的空间记忆，安全保存在本地', hero)
        title.setObjectName('HeroTitle')
        title.setWordWrap(True)
        hcol.addWidget(title)
        sub = QLabel(f'本地已保存 {len(texts)} 条动态 · {pic_count} 张照片'
                     f' · {comment_total} 条评论 · {like_total} 个赞', hero)
        sub.setObjectName('HeroSub')
        hcol.addWidget(sub)
        hlay.addLayout(hcol, 1)

        # 右侧年份大字装饰（最早年份 → 今年，营造时光感）
        years = sorted({str(t[0])[:4] for t in texts
                        if len(t) > 0 and str(t[0])[:4].isdigit()})
        if years:
            year_lb = QLabel(years[0], hero)
            year_lb.setObjectName('HeroYears')
            hlay.addWidget(year_lb, 0, Qt.AlignmentFlag.AlignVCenter)
        self.flow_layout.insertWidget(0, hero)

        # 统计卡（参考 QzoneArchive 仪表盘）
        cards = QHBoxLayout()
        cards.setSpacing(12)
        for label, value, hint, color, icon in (
            ('动态', str(len(texts)), '本地归档动态总数', '#49A1F9', 'file-lines'),
            ('图片', str(pic_count), '已下载说说图片', '#7C5CFC', 'images'),
            ('评论', str(comment_total), '含楼中楼回复', '#22B07D', 'comments'),
            ('本地占用', self._fmt_size(used), '导出 + 抓取缓存', '#F5A623', 'database'),
        ):
            cards.addWidget(self._stat_card(label, value, hint, color, icon))
        self.flow_layout.insertLayout(1, cards)

        # ---- 互动排行 + 数据分布（参考 QzoneArchive 仪表盘，无数据则整卡不渲染） ----
        stats_grid = QHBoxLayout()
        stats_grid.setSpacing(12)

        from collections import Counter
        commenters = Counter()
        for t in texts:
            if len(t) > 3 and isinstance(t[3], list):
                for c in t[3]:
                    if len(c) > 2 and c[2]:
                        commenters[c[2]] += 1
        if commenters:
            # 左：评论互动排行
            rank_card = QFrame(self)
            rank_card.setObjectName('MomentCard')
            rlay = QVBoxLayout(rank_card)
            rlay.setContentsMargins(16, 12, 16, 12)
            rlay.setSpacing(8)
            rhead = QHBoxLayout()
            rt = QLabel('评论互动排行', rank_card)
            rt.setObjectName('SectionTitle')
            rk = QLabel('INTERACTION', rank_card)
            rk.setObjectName('SectionKicker')
            rhead.addWidget(rt)
            rhead.addStretch(1)
            rhead.addWidget(rk, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
            rlay.addLayout(rhead)
            mx = max(commenters.values())
            for name, cnt in commenters.most_common(8):
                row = QHBoxLayout()
                row.setSpacing(8)
                nm = QLabel(str(name), rank_card)
                nm.setStyleSheet('color:#333333; font-size:12px; font-weight:600;')
                nm.setFixedWidth(88)
                nm.setToolTip(str(name))
                bar_bg = QFrame(rank_card)
                bar_bg.setStyleSheet('background:#EEF2F7; border-radius:4px;')
                bar_bg.setFixedHeight(8)
                bb = QHBoxLayout(bar_bg)
                bb.setContentsMargins(0, 0, 0, 0)
                bb.setSpacing(0)
                fill = QFrame(bar_bg)
                fill.setStyleSheet('background:#49A1F9; border-radius:4px;')
                fill.setFixedWidth(max(8, int(88 * cnt / mx)))
                bb.addWidget(fill)
                bb.addStretch(1)
                ct = QLabel(str(cnt), rank_card)
                ct.setStyleSheet('color:#999999; font-size:11px;')
                ct.setFixedWidth(26)
                row.addWidget(nm)
                row.addWidget(bar_bg, 1)
                row.addWidget(ct)
                rlay.addLayout(row)
            stats_grid.addWidget(rank_card, 1)

        # 右：动态分布（按年 + 来源构成，无年份数据则整卡不渲染）
        years = Counter()
        srcs = Counter()
        for t in texts:
            y = str(t[0])[:4]
            if y.isdigit():
                years[y] += 1
            srcs[t[5] if len(t) > 5 and t[5] else '历史'] += 1
        if years:
            dist_card = QFrame(self)
            dist_card.setObjectName('MomentCard')
            dlay = QVBoxLayout(dist_card)
            dlay.setContentsMargins(16, 12, 16, 12)
            dlay.setSpacing(8)
            dhead = QHBoxLayout()
            dt = QLabel('数据分布', dist_card)
            dt.setObjectName('SectionTitle')
            dk = QLabel('DISTRIBUTION', dist_card)
            dk.setObjectName('SectionKicker')
            dhead.addWidget(dt)
            dhead.addStretch(1)
            dhead.addWidget(dk, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
            dlay.addLayout(dhead)
            ymax = max(years.values())
            for y in sorted(years, reverse=True):
                row = QHBoxLayout()
                row.setSpacing(8)
                yl = QLabel(y, dist_card)
                yl.setStyleSheet('color:#555555; font-size:11px;')
                yl.setFixedWidth(42)
                bar_bg = QFrame(dist_card)
                bar_bg.setStyleSheet('background:#EEF2F7; border-radius:4px;')
                bar_bg.setFixedHeight(8)
                bb = QHBoxLayout(bar_bg)
                bb.setContentsMargins(0, 0, 0, 0)
                bb.setSpacing(0)
                fill = QFrame(bar_bg)
                fill.setStyleSheet('background:#22B07D; border-radius:4px;')
                fill.setFixedWidth(max(6, int(88 * years[y] / ymax)))
                bb.addWidget(fill)
                bb.addStretch(1)
                yc = QLabel(str(years[y]), dist_card)
                yc.setStyleSheet('color:#999999; font-size:11px;')
                yc.setFixedWidth(26)
                row.addWidget(yl)
                row.addWidget(bar_bg, 1)
                row.addWidget(yc)
                dlay.addLayout(row)
            src_line = '　'.join(f'{k} {v}' for k, v in srcs.most_common(5))
            sl = QLabel(src_line, dist_card)
            sl.setStyleSheet('color:#B0BCCB; font-size:11px;')
            sl.setWordWrap(True)
            dlay.addWidget(sl)
            stats_grid.addWidget(dist_card, 1)

        if stats_grid.count() > 0:
            self.flow_layout.insertLayout(2, stats_grid)

        # 最近动态（无数据则整卡不渲染）
        recent = sorted(texts, key=lambda x: str(x[0]), reverse=True)[:5]
        if recent:
            recent_card = QFrame(self)
            recent_card.setObjectName('MomentCard')
            rlay = QVBoxLayout(recent_card)
            rlay.setContentsMargins(16, 12, 16, 12)
            rlay.setSpacing(8)
            rhead = QHBoxLayout()
            k = QLabel('RECENT', recent_card)
            k.setObjectName('SectionKicker')
            t = QLabel('最近动态', recent_card)
            t.setObjectName('SectionTitle')
            rhead.addWidget(t)
            rhead.addWidget(k, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
            rlay.addLayout(rhead)
            for item in recent:
                time_str = str(item[0])
                body = str(item[1]) if len(item) > 1 else ''
                body = re.sub(r'\[em\].*?\[/em\]', '[表情]', body)
                row = QLabel(f'<b>{time_str}</b>　{body[:60]}', recent_card)
                row.setTextFormat(Qt.TextFormat.RichText)
                row.setStyleSheet('color:#555555; font-size:12px;')
                row.setWordWrap(True)
                rlay.addWidget(row)
            self.flow_layout.insertWidget(3, recent_card)

        # 已导出文件清单
        base = self._result_path
        files = []
        if base and os.path.exists(base):
            for f in sorted(os.listdir(base)):
                if os.path.isfile(os.path.join(base, f)):
                    files.append(f)
        file_card = QFrame(self)
        file_card.setObjectName('MomentCard')
        flay = QVBoxLayout(file_card)
        flay.setContentsMargins(16, 12, 16, 12)
        flay.setSpacing(6)
        ft = QLabel('已导出文件', file_card)
        ft.setObjectName('SectionTitle')
        flay.addWidget(ft)
        body = '\n'.join(files) if files else '（尚未导出，请先点击「开始获取数据」）'
        fl = QLabel(body, file_card)
        fl.setWordWrap(True)
        fl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        fl.setStyleSheet('color:#666666; font-size:12px; line-height:1.6;')
        flay.addWidget(fl)
        self.flow_layout.insertWidget(4, file_card)

    # ================= 昵称（后台获取后更新） =================
    def update_nickname(self, nickname):
        """登录后异步获取到真实昵称时刷新界面并重新分类。"""
        nickname = (nickname or '').strip()
        if not nickname or nickname == self.nickname:
            return
        log.info('更新昵称: %s -> %s', self.nickname, nickname)
        self.nickname = nickname
        self.nav_user.setText(nickname)
        self.side_name.setText(nickname)
        self.setWindowTitle(f'QQ 空间 - {nickname} 的历史数据')
        self._classify()
        self._update_stats()
        self.apply_tab(self._current_tab)

    # ================= 交互 =================
    def start_fetch(self):
        if self._fetch_worker and self._fetch_worker.isRunning():
            QMessageBox.information(self, '提示', '正在获取数据中，请稍候…')
            return
        log.info('开始抓取数据')
        self.progress.setValue(0)
        self.progress.show()
        self.status_text.setText('正在获取数据…')
        self._fetch_worker = FetchWorker(self.service, self.cookies, self.nickname, self)
        self._fetch_worker.progress.connect(self._on_progress)
        self._fetch_worker.finished_ok.connect(self._on_fetch_done)
        self._fetch_worker.failed.connect(self._on_fetch_failed)
        self._fetch_worker.start()

    def _on_progress(self, text, percent):
        self.status_text.setText(text)
        self.progress.setValue(max(0, min(100, percent)))

    def _on_fetch_done(self, cookies, result_path, stats):
        self.progress.hide()
        self.status_text.setText(f'获取完成：共 {stats.get("全部", 0)} 条动态，'
                                 f'已保存至 {result_path}')
        log.info('抓取完成: %s', stats)
        self._result_path = result_path
        self.load_local_data()
        self.apply_tab('全部')
        QMessageBox.information(self, '获取完成',
                                f'数据获取完成！\n\n共获取 {stats.get("全部", 0)} 条动态，'
                                f'其中说说 {stats.get("说说", 0)} 条、转发 {stats.get("转发", 0)} 条、'
                                f'留言 {stats.get("留言", 0)} 条、其他 {stats.get("其他", 0)} 条，'
                                f'图片 {stats.get("图片", 0)} 张。\n\n'
                                f'已导出 Excel / 网页版 / 图片到：\n{result_path}')

    def _on_fetch_failed(self, msg):
        self.progress.hide()
        self.status_text.setText('获取失败')
        log.error('抓取失败: %s', msg)
        QMessageBox.warning(self, '获取失败', msg)

    def open_result_dir(self):
        path = self._result_path
        if not path or not os.path.exists(path):
            QMessageBox.information(self, '提示', '结果目录尚未生成，请先开始获取数据')
            return
        if platform.system() == 'Windows':
            os.startfile(path)  # noqa
        elif platform.system() == 'Darwin':
            subprocess.Popen(['open', path])
        else:
            subprocess.Popen(['xdg-open', path])

    def _logout(self):
        ret = QMessageBox.question(self, '退出登录', '确定要退出当前账号吗？')
        if ret == QMessageBox.StandardButton.Yes:
            from PyQt6.QtWidgets import QApplication
            QApplication.instance().exit(100)  # 特殊退出码触发重新登录
