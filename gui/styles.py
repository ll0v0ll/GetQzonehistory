# -*- coding: utf-8 -*-
"""QQ 空间风格全局样式（QSS 主题）。"""

# 主色调
C_NAV_TOP = '#4FA9FF'      # 导航栏渐变上
C_NAV_BOTTOM = '#2E7FE0'   # 导航栏渐变下
C_MAIN_BG = '#F2F5F9'      # 主背景
C_CARD = '#FFFFFF'         # 卡片背景
C_TEXT = '#333333'         # 主文字
C_TEXT_SUB = '#999999'     # 次要文字
C_LINK = '#2E7FE0'         # 链接蓝
C_BORDER = '#E5E9F0'       # 边框
C_BTN_HOVER = '#EAF3FF'    # 按钮悬浮
C_BTN_PRESS = '#D6E8FF'    # 按钮按下
C_BADGE = '#FF8F00'        # 徽章橙（LV 等级）

GLOBAL_QSS = f"""
* {{
    font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif;
    font-size: 13px;
    color: {C_TEXT};
}}
QMainWindow, QDialog {{
    background: {C_MAIN_BG};
}}
QPushButton {{
    background: #FFFFFF;
    border: 1px solid #D8DFE8;
    border-radius: 6px;
    padding: 5px 16px;
    color: #333333;
}}
QPushButton:hover {{
    background: #EFF5FF;
    border-color: #7FB0EE;
    color: #2E7FE0;
}}
QPushButton:pressed {{ background: #DCEBFF; }}
QPushButton:disabled {{
    background: #F0F2F5;
    color: #B0BCCB;
    border-color: #E5E9F0;
}}
QToolTip {{
    background: #FFFFFF;
    color: {C_TEXT};
    border: 1px solid {C_BORDER};
    padding: 4px 8px;
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #C9D4E3;
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: #A9BBD4; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
}}
QScrollBar::handle:horizontal {{ background: #C9D4E3; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
"""

# 顶部导航栏
NAV_BAR_QSS = f"""
QWidget#NavBar {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 {C_NAV_TOP}, stop:1 {C_NAV_BOTTOM});
}}
QLabel#NavTitle {{
    color: #FFFFFF;
    font-size: 20px;
    font-weight: bold;
}}
QLabel#NavSub {{
    color: #DCEBFF;
    font-size: 11px;
}}
QPushButton#NavBtn {{
    color: #FFFFFF;
    background: transparent;
    border: none;
    padding: 6px 16px;
    border-radius: 14px;
    font-size: 14px;
}}
QPushButton#NavBtn:hover {{ background: rgba(255,255,255,0.18); }}
QPushButton#NavBtn:checked {{ background: rgba(255,255,255,0.30); font-weight: bold; }}
QLabel#NavUser {{
    color: #FFFFFF;
    font-size: 13px;
}}
QPushButton#NavLogout {{
    color: #FFFFFF;
    background: rgba(255,255,255,0.15);
    border: 1px solid rgba(255,255,255,0.5);
    border-radius: 12px;
    padding: 3px 14px;
}}
QPushButton#NavLogout:hover {{ background: rgba(255,255,255,0.30); }}
"""

# 白色卡片容器
CARD_QSS = f"""
QFrame#Card {{
    background: {C_CARD};
    border: 1px solid {C_BORDER};
    border-radius: 10px;
}}
"""

# 左侧栏
SIDEBAR_QSS = f"""
QFrame#SidebarCard {{
    background: {C_CARD};
    border: 1px solid {C_BORDER};
    border-radius: 10px;
}}
QLabel#SideTitle {{
    font-size: 14px;
    font-weight: bold;
    color: {C_TEXT};
}}
QPushButton#MenuBtn {{
    text-align: left;
    padding: 8px 14px;
    border: none;
    border-radius: 6px;
    background: transparent;
    color: {C_TEXT};
    font-size: 13px;
}}
QPushButton#MenuBtn:hover {{ background: {C_BTN_HOVER}; color: {C_LINK}; }}
QPushButton#MenuBtn:checked {{
    background: {C_BTN_HOVER};
    color: {C_LINK};
    font-weight: bold;
}}
QLabel#LvBadge {{
    background: {C_BADGE};
    color: #FFFFFF;
    border-radius: 8px;
    padding: 1px 8px;
    font-size: 11px;
    font-weight: bold;
}}
"""

# 说说卡片
MOMENT_QSS = f"""
QFrame#MomentCard {{
    background: {C_CARD};
    border: 1px solid {C_BORDER};
    border-radius: 10px;
}}
QLabel#MomName {{
    color: {C_LINK};
    font-size: 14px;
    font-weight: bold;
}}
QLabel#MomTime {{
    color: {C_TEXT_SUB};
    font-size: 12px;
}}
QFrame#CommentBox {{
    background: #F5F7FA;
    border-radius: 6px;
}}
QLabel#CommentText {{
    color: {C_TEXT};
    font-size: 12px;
}}
QPushButton#ActBtn {{
    border: none;
    background: transparent;
    color: {C_TEXT_SUB};
    padding: 4px 10px;
    border-radius: 12px;
    font-size: 12px;
}}
QPushButton#ActBtn:hover {{ background: {C_BTN_HOVER}; color: {C_LINK}; }}
"""

# 发布框
POSTER_QSS = f"""
QFrame#PosterCard {{
    background: {C_CARD};
    border: 1px solid {C_BORDER};
    border-radius: 10px;
}}
QTextEdit#PosterInput {{
    border: none;
    background: #F7F9FC;
    border-radius: 8px;
    padding: 10px;
    font-size: 14px;
}}
QTextEdit#PosterInput:focus {{ border: 1px solid {C_LINK}; background: #FFFFFF; }}
QPushButton#PostBtn {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 {C_NAV_TOP}, stop:1 {C_NAV_BOTTOM});
    color: #FFFFFF;
    border: none;
    border-radius: 16px;
    padding: 6px 26px;
    font-size: 14px;
    font-weight: bold;
}}
QPushButton#PostBtn:hover {{ background: #3F96F2; }}
QPushButton#PostBtn:pressed {{ background: #2A72C9; }}
QPushButton#PostBtn:disabled {{ background: #A9C8EF; }}
"""

# 分类标签
TAB_QSS = f"""
QPushButton#TabBtn {{
    border: none;
    background: transparent;
    padding: 8px 18px;
    font-size: 14px;
    color: {C_TEXT_SUB};
    border-bottom: 3px solid transparent;
}}
QPushButton#TabBtn:hover {{ color: {C_LINK}; }}
QPushButton#TabBtn:checked {{
    color: {C_LINK};
    font-weight: bold;
    border-bottom: 3px solid {C_LINK};
}}
"""

# 按钮（通用）
BTN_QSS = f"""
QPushButton#PrimaryBtn {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 {C_NAV_TOP}, stop:1 {C_NAV_BOTTOM});
    color: #FFFFFF;
    border: none;
    border-radius: 16px;
    padding: 6px 22px;
    font-size: 13px;
}}
QPushButton#PrimaryBtn:hover {{ background: #3F96F2; }}
QPushButton#PrimaryBtn:pressed {{ background: #2A72C9; }}
QPushButton#GhostBtn {{
    color: {C_LINK};
    background: #FFFFFF;
    border: 1px solid {C_LINK};
    border-radius: 16px;
    padding: 5px 20px;
    font-size: 13px;
}}
QPushButton#GhostBtn:hover {{ background: {C_BTN_HOVER}; }}
QPushButton#FlatBtn {{
    border: none;
    background: transparent;
    color: {C_LINK};
    padding: 4px 8px;
    font-size: 13px;
}}
QPushButton#FlatBtn:hover {{ color: #1B5FB8; text-decoration: underline; }}
"""

# 进度条
PROGRESS_QSS = f"""
QProgressBar {{
    border: none;
    background: #E3EAF3;
    border-radius: 4px;
    height: 8px;
    text-align: center;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 {C_NAV_TOP}, stop:1 {C_NAV_BOTTOM});
    border-radius: 4px;
}}
"""

# 登录对话框
LOGIN_QSS = f"""
QFrame#LoginCard {{
    background: {C_CARD};
    border-radius: 14px;
}}
QLabel#LoginTitle {{
    font-size: 18px;
    font-weight: bold;
    color: {C_TEXT};
}}
QLabel#LoginHint {{
    color: {C_TEXT_SUB};
    font-size: 12px;
}}
QPushButton#UserBtn {{
    border: 1px solid {C_BORDER};
    background: #FFFFFF;
    border-radius: 8px;
    padding: 10px 14px;
    text-align: left;
    font-size: 14px;
}}
QPushButton#UserBtn:hover {{ border-color: {C_LINK}; background: {C_BTN_HOVER}; }}
QPushButton#UserBtn:checked {{ border-color: {C_LINK}; background: {C_BTN_HOVER}; }}
QFrame#QrBox {{
    background: #FFFFFF;
    border: 1px solid {C_BORDER};
    border-radius: 10px;
}}
QFrame#LoginAbout {{
    background: #F7F9FC;
    border: 1px solid {C_BORDER};
    border-radius: 10px;
}}
"""

STATUS_QSS = f"""
QWidget#StatusBar {{
    background: #FFFFFF;
    border-top: 1px solid {C_BORDER};
}}
QLabel#StatusText {{ color: {C_TEXT_SUB}; font-size: 12px; }}
"""


def build_all_qss():
    return (GLOBAL_QSS + NAV_BAR_QSS + CARD_QSS + SIDEBAR_QSS + MOMENT_QSS
            + POSTER_QSS + TAB_QSS + BTN_QSS + PROGRESS_QSS + LOGIN_QSS + STATUS_QSS)
