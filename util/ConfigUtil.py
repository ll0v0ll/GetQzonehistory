import configparser
import os
import sys


def get_resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


config = configparser.ConfigParser()
config.read(get_resource_path('resource/config/config.ini'), encoding='utf-8')

temp_path = config.get('File', 'temp')
user_path = config.get('File', 'user')
result_path = config.get('File', 'result')


def save_user(cookies):
    with open(user_path + cookies.get('uin'), 'w') as f:
        f.write(str(cookies))


def init_flooder():
    if not os.path.exists(temp_path):
        os.makedirs(temp_path)
        print(f"Created directory: {temp_path}")

    if not os.path.exists(user_path):
        os.makedirs(user_path)
        print(f"Created directory: {user_path}")

    if not os.path.exists(result_path):
        os.makedirs(result_path)
        print(f"Created directory: {result_path}")


def read_files_in_folder():
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
        content = file.read()

    return content
