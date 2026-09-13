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


def _resolve(rel):
    """相对路径基于项目根解析；绝对路径原样保留。"""
    rel = str(rel).strip()
    if os.path.isabs(rel):
        return os.path.normpath(rel)
    return os.path.normpath(os.path.join(PROJECT_ROOT, rel.replace('/', os.sep)))


def _load_config():
    cfg = configparser.ConfigParser()
    if os.path.exists(CONFIG_FILE):
        try:
            cfg.read(CONFIG_FILE, encoding='utf-8')
        except Exception:
            cfg = configparser.ConfigParser()
    if not cfg.has_section('paths'):
        cfg['paths'] = _DEFAULTS
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                cfg.write(f)
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
