# -*- coding: utf-8 -*-
"""配置管理：读取项目根 config.ini，路径统一基于项目根解析（绝对路径）。

- 支持在 config.ini 的 [paths] 段覆盖默认路径（相对路径按项目根解析）
- 项目根下没有 config.ini 时自动生成一份默认模板
- 路径不再依赖命令行工作目录，从任意位置启动都能正确读写
"""
import configparser
import os
import sys


def _base_dir():
    """可写数据基础目录：PyInstaller 打包后为 exe 所在目录（用户可写），
    开发环境为项目根目录。"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(rel):
    """静态资源路径：PyInstaller onefile 打包后位于 _MEIPASS/resource
    （随 exe 解压的只读资源），开发环境为项目根/resource。"""
    if getattr(sys, 'frozen', False):
        base = getattr(sys, '_MEIPASS', _base_dir())
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.normpath(os.path.join(base, 'resource', rel.replace('/', os.sep)))


# 项目根目录（根据本文件位置推导，与启动时的工作目录无关）
PROJECT_ROOT = _base_dir()
CONFIG_FILE = os.path.join(PROJECT_ROOT, 'config.ini')

_DEFAULTS = {
    'temp_path': './data/temp/',
    'user_path': './data/user/',
    'result_path': './data/result/',
    'log_path': './data/logs/',
    'fetch_path': './data/fetch-all/',
}

_FETCH_DEFAULTS = {
    'interval_ms': '2000',      # 抓取页间最小间隔（毫秒），爬虫对服务器有压力，勿小于 800
    'channel_interval_ms': '800',  # 通道一/二（历史消息、未删除说说）页间间隔（毫秒）
    'feeds_channel': '1',       # 是否启用 mobile get_feeds 动态流通道（数据更全）
    'max_attempts': '6',        # 单页失败最大重试次数
    'window_pages': '300',      # 滑动窗口内最多抓取页数（总量限速，参考 QzoneArchive）
    'window_seconds': '600',    # 滑动窗口时长（秒），超限后等待窗口重置再继续
}


def _resolve(rel):
    """相对路径基于项目根解析；绝对路径原样保留。"""
    rel = str(rel).strip()
    if os.path.isabs(rel):
        return os.path.normpath(rel)
    return os.path.normpath(os.path.join(PROJECT_ROOT, rel.replace('/', os.sep)))


_DEFAULT_CONFIG = """\
; QQ 空间历史数据获取 - 配置文件
; 相对路径均基于项目根目录解析（不依赖启动时的工作目录）。
; 修改后重启程序生效。
; 本文件仅首次运行时自动生成，之后程序只读取、绝不改写，请放心持久化自定义配置。

[paths]
; 临时文件（二维码、图片缓存等）
temp_path = ./data/temp/

; 用户登录数据（cookie，敏感，勿提交到 git）
user_path = ./data/user/

; 抓取结果导出目录（按 QQ 号分子目录）
result_path = ./data/result/

; 运行日志目录（gui.log / thread_dump.log）
log_path = ./data/logs/

; 全部说说（未删除）抓取缓存
fetch_path = ./data/fetch-all/

[fetch]
; 抓取页间最小间隔（毫秒），爬虫对服务器有压力，勿小于 800
interval_ms = 2000

; 通道一/二（历史消息、未删除说说）页间间隔（毫秒），默认 800
channel_interval_ms = 800

; 是否启用 mobile get_feeds 动态流通道（数据更全：评论/点赞/视频/转发）
feeds_channel = 1

; 单页失败最大重试次数
max_attempts = 6

; 滑动窗口总量限速：窗口时长（秒）内最多抓取 window_pages 页
; 超限后自动等待窗口重置，进一步降低被风控的概率（参考 QzoneArchive）
window_pages = 300
window_seconds = 600
"""


def _load_config():
    """读取配置；config.ini 不存在时生成一份默认模板，存在时只读不写。"""
    cfg = configparser.ConfigParser()
    if os.path.exists(CONFIG_FILE):
        try:
            cfg.read(CONFIG_FILE, encoding='utf-8')
        except Exception:
            cfg = configparser.ConfigParser()
    else:
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                f.write(_DEFAULT_CONFIG)
        except Exception:
            pass
        try:
            cfg.read(CONFIG_FILE, encoding='utf-8')
        except Exception:
            pass
    return cfg


_cfg = _load_config()


def _path(key):
    return _resolve(_cfg.get('paths', key, fallback=_DEFAULTS[key]))


temp_path = _path('temp_path')
user_path = _path('user_path')
result_path = _path('result_path')
log_path = _path('log_path')
fetch_path = _path('fetch_path')


# ---------- 抓取行为配置（[fetch] 段） ----------
def _fetch_int(key, default):
    try:
        return int(_cfg.get('fetch', key, fallback=str(default)))
    except (ValueError, TypeError):
        return default


fetch_interval_ms = _fetch_int('interval_ms', 2000)
fetch_channel_interval_ms = max(400, min(10000, _fetch_int('channel_interval_ms', 800)))
feeds_channel = _fetch_int('feeds_channel', 1) == 1
fetch_max_attempts = max(1, min(10, _fetch_int('max_attempts', 6)))
fetch_window_pages = max(1, min(10000, _fetch_int('window_pages', 300)))
fetch_window_seconds = max(60, min(86400, _fetch_int('window_seconds', 600)))


def save_user(cookies):
    """保存登录 cookie（控制台版使用）。"""
    with open(os.path.join(user_path, cookies.get('uin')), 'w') as f:
        f.write(str(cookies))


def init_flooder():
    """初始化临时 / 用户 / 结果目录。"""
    for p in (temp_path, user_path, result_path):
        if not os.path.exists(p):
            os.makedirs(p)
            print(f"Created directory: {p}")


def read_files_in_folder():
    """读取已保存用户列表并交互选择（控制台版使用）。"""
    if not os.path.exists(user_path):
        return None
    files = os.listdir(user_path)
    if not files:
        return None
    print("已登录用户列表:")
    for i, file in enumerate(files):
        print(f"{i + 1}. {file}")

    while True:
        try:
            choice = int(input("请选择要登录的用户序号，重新登录输入0: "))
            if 1 <= choice <= len(files):
                break
            elif choice == 0:
                return None
            else:
                print("无效的选择，请重新输入。")
        except ValueError:
            print("无效的选择，请重新输入。")

    selected_file = files[choice - 1]
    file_path = os.path.join(user_path, selected_file)
    with open(file_path, 'r') as file:
        return file.read()
