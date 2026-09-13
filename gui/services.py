# -*- coding: utf-8 -*-
"""核心服务层：登录、数据抓取、解析与导出。

独立于 Qt 实现，可在 QThread 中安全调用；
通过 progress_callback 回调上报进度，供 GUI 刷新。
注意：不复用 util.RequestUtil / util.LoginUtil 的模块级副作用（import 即触发登录），
     而是在此处重新封装等价的请求逻辑。
"""
import json
import math
import os
import re
import ssl
import time
import urllib3
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import pandas as pd
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import util.ConfigUtil as Config
import util.ToolsUtil as Tools
from gui.logger import get_logger

log = get_logger('service')

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

QQ_APPID = '549000912'
QR_IMG_PATH = os.path.join(Config.temp_path, 'QR.png')


def make_session():
    """带重试与宽松 SSL 的会话。

    注意：不要在这里调用 ssl.create_default_context() —— 它会加载系统证书库，
    在部分 Windows 环境（证书路径异常/网络驱动器）耗时极长甚至卡死；
    且本工具请求全部 verify=False，无需加载任何系统证书。
    """
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1,
                  status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["HEAD", "GET", "PUT", "DELETE", "OPTIONS", "TRACE", "POST"])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


class QzoneService:
    """QQ 空间数据服务。"""

    def __init__(self):
        Config.init_flooder()
        self.session = make_session()
        self.headers = {
            'authority': 'user.qzone.qq.com',
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'user-agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                           '(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'),
        }

    # ---------- 登录 ----------
    def load_saved_users(self):
        """返回 resource/user 下已保存的 QQ 号列表。"""
        path = Config.user_path
        if not os.path.exists(path):
            return []
        users = set()
        for f in os.listdir(path):
            if f.isdigit():
                users.add(f)
            elif f.startswith('o') and f[1:].isdigit():
                users.add(f[1:])   # 兼容旧版带 o 前缀的 cookie 文件
        return sorted(users)

    def load_cookie(self, qq):
        """按 QQ 号读取已保存 cookie（兼容 o 前缀旧文件）。"""
        qq = str(qq).strip()
        for name in (qq, 'o' + qq):
            path = os.path.join(Config.user_path, name)
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    return eval(f.read())
        raise FileNotFoundError('未找到账号 %s 的登录信息' % qq)

    def save_cookie(self, cookies):
        """保存登录 cookie，文件名统一用纯数字 QQ 号。"""
        qq = self._clean_uin(cookies.get('uin'))
        with open(os.path.join(Config.user_path, qq), 'w', encoding='utf-8') as f:
            f.write(str(cookies))
        log.info('登录信息已保存: QQ=%s', qq)

    @staticmethod
    def _bkn(p_skey):
        t, n, o = 5381, 0, len(p_skey)
        while n < o:
            t += (t << 5) + ord(p_skey[n])
            n += 1
        return t & 2147483647

    @staticmethod
    def _ptqrtoken(qrsig):
        n, i, e = len(qrsig), 0, 0
        while n > i:
            e += (e << 5) + ord(qrsig[i])
            i += 1
        return 2147483647 & e

    @staticmethod
    def _clean_uin(uin):
        return re.sub(r'o0*', '', str(uin))

    def fetch_qr_code(self):
        """获取登录二维码，写入 resource/temp/QR.png，返回 qrsig。"""
        url = ('https://ssl.ptlogin2.qq.com/ptqrshow?appid=549000912&e=2&l=M&s=3&d=72&v=4'
               '&t=0.8692955245720428&daid=5&pt_3rd_aid=0')
        r = self.session.get(url, verify=False, timeout=(10, 30))
        qrsig = requests.utils.dict_from_cookiejar(r.cookies).get('qrsig')
        os.makedirs(os.path.dirname(QR_IMG_PATH), exist_ok=True)
        with open(QR_IMG_PATH, 'wb') as f:
            f.write(r.content)
        return qrsig

    def poll_qr_login(self, qrsig):
        """轮询扫码结果，返回 (状态, cookies 或 None)。

        状态: 'wait' 等待 / 'success' 登录成功 / 'expired' 二维码失效 / 'cancel' 用户取消
        """
        ptqrtoken = self._ptqrtoken(qrsig)
        url = ('https://ssl.ptlogin2.qq.com/ptqrlogin?u1=https%3A%2F%2Fqzs.qq.com%2Fqzone%2Fv5%2F'
               'loginsucc.html%3Fpara%3Dizone&ptqrtoken=' + str(ptqrtoken) +
               '&ptredirect=0&h=1&t=1&g=1&from_ui=1&ptlang=2052&action=0-0-' + str(time.time()) +
               '&js_ver=20032614&js_type=1&login_sig=&pt_uistyle=40&aid=549000912&daid=5&')
        try:
            r = self.session.get(url, cookies={'qrsig': qrsig}, verify=False, timeout=(10, 30))
        except Exception as e:
            return 'error', str(e)

        text = r.text
        if '二维码未失效' in text or '二维码认证中' in text:
            return 'wait', None
        if '二维码已失效' in text:
            return 'expired', None
        if '登录成功' in text:
            cookies = requests.utils.dict_from_cookiejar(r.cookies)
            uin = cookies.get('uin')
            sigx = re.findall(r'ptsigx=(.*?)&', text)[0]
            url = ('https://ptlogin2.qzone.qq.com/check_sig?pttype=1&uin=' + uin +
                   '&service=ptqrlogin&nodirect=0&ptsigx=' + sigx +
                   '&s_url=https%3A%2F%2Fqzs.qq.com%2Fqzone%2Fv5%2Floginsucc.html%3Fpara%3Dizone'
                   '&f_url=&ptlang=2052&ptredirect=100&aid=549000912&daid=5&j_later=0'
                   '&low_login_hour=0&regmaster=0&pt_login_type=3&pt_aid=0&pt_aaid=16'
                   '&pt_light=0&pt_3rd_aid=0')
            try:
                r = self.session.get(url, cookies=cookies, allow_redirects=False, verify=False, timeout=(10, 30))
                target_cookies = requests.utils.dict_from_cookiejar(r.cookies)
                self.save_cookie(target_cookies)
                log.info('扫码登录成功，uin=%s', target_cookies.get('uin'))
                return 'success', target_cookies
            except Exception as e:
                return 'error', str(e)
        return 'cancel', None

    # ---------- 用户信息 ----------
    def get_user_info(self, cookies):
        """获取昵称等用户信息。返回 dict {qq: [..]}。"""
        uin = self._clean_uin(cookies.get('uin'))
        g_tk = self._bkn(cookies.get('p_skey'))
        url = 'https://r.qzone.qq.com/fcg-bin/cgi_get_portrait.fcg?g_tk=' + str(g_tk) + '&uins=' + uin
        r = self.session.get(url, headers=self.headers, cookies=cookies, verify=False, timeout=(10, 30))
        for enc in ('gbk', 'gb2312', 'gb18030', 'utf-8', 'big5'):
            try:
                info = r.content.decode(enc)
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
        else:
            info = r.content.decode('gbk', errors='replace')
        info = info.strip().lstrip('portraitCallBack(').rstrip(');')
        return json.loads(info)

    # ---------- 通道一：历史消息列表 ----------
    def get_message(self, cookies, start, count):
        uin = self._clean_uin(cookies.get('uin'))
        g_tk = self._bkn(cookies.get('p_skey'))
        params = {
            'uin': uin, 'begin_time': '0', 'end_time': '0', 'getappnotification': '1',
            'getnotifi': '1', 'has_get_key': '0', 'offset': start, 'set': '0', 'count': count,
            'useutf8': '1', 'outputhtmlfeed': '1', 'scope': '1', 'format': 'jsonp',
            'g_tk': [g_tk, g_tk],
        }
        url = ('https://user.qzone.qq.com/proxy/domain/ic2.qzone.qq.com/cgi-bin/'
               'feeds/feeds2_html_pav_all')
        try:
            return self.session.get(url, params=params, cookies=cookies, headers=self.headers,
                                    timeout=(10, 30), verify=False)
        except Exception:
            return None

    def get_message_count(self, cookies):
        """二分法探测历史消息总条数。"""
        lower, upper = 0, 10000000
        total = upper // 2
        while lower <= upper:
            resp = self.get_message(cookies, total, 100)
            if resp is None or not hasattr(resp, 'text'):
                break
            if 'f-single' in resp.text:
                lower = total + 1
            else:
                upper = total - 1
            total = (lower + upper) // 2
        return total

    @staticmethod
    def _decode_bytes(content_bytes):
        """多编码解码响应字节。"""
        message = None
        encodings = ['utf-8', 'gbk', 'gb2312', 'gb18030', 'big5']
        if message is None:
            for enc in encodings:
                try:
                    message = content_bytes.decode(enc)
                    break
                except (UnicodeDecodeError, UnicodeError):
                    continue
        if message is None:
            message = content_bytes.decode('utf-8', errors='replace')
        return message

    def parse_batch_messages(self, batch_data):
        """解析单个批次消息，返回 (texts, friends)。

        图片：一条说说可能有多张图，全部收集（逗号分隔）；
        视频：收集视频封面图，并在正文后追加 [视频] 标记。
        """
        batch_texts, batch_friends = [], []
        try:
            i, content_bytes = batch_data
            message = self._decode_bytes(content_bytes)
            html = Tools.process_old_html(message)
            if 'f-single' not in html:
                return batch_texts, batch_friends
            soup = BeautifulSoup(html, 'html.parser')
            for element in soup.find_all('li', class_=re.compile(r'f-single')):
                put_time = text = img = None
                friend_el = element.find('a', class_='f-name q_namecard')
                if friend_el is not None:
                    friend_name = friend_el.get_text()
                    friend_link = friend_el.get('href')
                    friend_qq = ''
                    link_attr = friend_el.get('link') or ''
                    if link_attr.startswith('http') or '=' in link_attr:
                        friend_qq = link_attr.split('=')[-1]
                    elif len(link_attr) > 9:
                        friend_qq = link_attr[9:]
                    batch_friends.append([friend_name, friend_qq, friend_link])
                time_el = element.find('div', class_='info-detail')
                text_el = element.find('p', class_='txt-box-title ellipsis-one')
                if time_el is not None and text_el is not None:
                    put_time = time_el.get_text().replace('\xa0', ' ')
                    text = text_el.get_text().replace('\xa0', ' ')
                    # 多图：所有 img-item 中的图片
                    imgs = []
                    for a in element.find_all('a', class_='img-item'):
                        im = a.find('img')
                        src = (im or {}).get('src') if im is not None else None
                        if src:
                            imgs.append(src)
                    # 补充：正文附近所有 QQ 图片（qpic 域名，含视频封面），去重保序
                    for im in element.find_all('img'):
                        s = im.get('src') or im.get('data-src') or ''
                        if 'qpic.cn' in s or 'gtimg.cn' in s:
                            imgs.append(s)
                    # 视频：封面图入列表，正文追加标记
                    has_video = False
                    for v in element.find_all(['video', 'audio']):
                        if v.get('src') or v.get('data-src'):
                            has_video = True
                            break
                    if not has_video:
                        for a in element.find_all('a', class_=re.compile('video', re.I)):
                            if a.find('img') or a.get('href'):
                                has_video = True
                                break
                    img = ','.join(dict.fromkeys(imgs)) if imgs else ''
                    if has_video:
                        text = text + ' [视频]'
                    batch_texts.append([put_time, text, img, ""])
        except Exception:
            pass
        return batch_texts, batch_friends

    # ---------- 通道二：全部可见说说 ----------
    def get_visible_moments(self, cookies, progress=None):
        """获取所有可见未删除说说，返回 texts（格式同 main.py）。"""
        qq = self._clean_uin(cookies.get('uin'))
        workdir = os.path.join(Config.fetch_path, qq)
        user_info_file = 'user_qzone_info.json'
        moments_file = 'qzone_moments_all.json'

        user_qzone_info = Tools.read_txt_file(workdir, user_info_file)
        if not user_qzone_info:
            if progress:
                progress("正在获取说说总条数...", 0)
            user_qzone_info = self._get_user_qzone_info(cookies, 1)
            Tools.write_txt_file(workdir, user_info_file, user_qzone_info)
            user_qzone_info = Tools.read_txt_file(workdir, user_info_file)
        if not Tools.is_valid_json(user_qzone_info):
            return None
        total = json.loads(user_qzone_info)['total']
        log.info('未删除说说总数=%s', total)
        if total == 0:
            return None

        moments = Tools.read_txt_file(workdir, moments_file)
        if not moments:
            page_size = 30
            total_page = math.ceil(total / page_size)
            all_data = []
            for page in range(total_page):
                pos = page * page_size
                try:
                    resp = self._get_user_qzone_info(cookies, page_size, pos)
                    page_data = json.loads(resp)['msglist']
                    if page_data:
                        all_data.extend(page_data)
                except Exception:
                    continue
                if progress:
                    progress(f"获取未删除说说 {min((page + 1) * page_size, total)}/{total} 条",
                             int((page + 1) / total_page * 60))
                # 限流：爬虫对官方服务器有压力，页间至少间隔 0.8s
                time.sleep(0.8)
            Tools.write_txt_file(workdir, moments_file,
                                 json.dumps({"msglist": all_data}, ensure_ascii=False, indent=2))
            moments = Tools.read_txt_file(workdir, moments_file)
        if not Tools.is_valid_json(moments):
            return None

        texts = []
        for item in json.loads(moments)['msglist']:
            content = item.get('content') or ""
            # conlist 兜底：content 为空时从 conlist 拼正文（纯文本/表情）
            if not str(content).strip() and item.get('conlist'):
                parts = []
                for c in item['conlist']:
                    if c.get('con'):
                        parts.append(str(c['con']))
                    elif c.get('emotion'):
                        parts.append('[em]%s[/em]' % c['emotion'])
                content = ''.join(parts)
            nickname = item.get('name') or ""
            create_time = Tools.format_timestamp(item.get('created_time', 0))
            pictures = []
            for pic in item.get('pic', []) or []:
                u = pic.get('url') or pic.get('url1') or pic.get('url2')
                if u:
                    pictures.append(u)
            video_links = []
            for vid in item.get('video', []) or []:
                cover = vid.get('url1') or vid.get('pic') or ''
                if cover:
                    pictures.append(cover)
                vurl = vid.get('url') or vid.get('video') or ''
                if vurl:
                    video_links.append(vurl)
            comments = []
            for c in item.get('commentlist', []) or []:
                try:
                    ctime = datetime.strptime(c.get('createTime2', ''), '%Y-%m-%d %H:%M:%S'
                                              ).strftime('%Y年%m月%d日 %H:%M')
                except (ValueError, TypeError):
                    ctime = c.get('createTime2', '')
                comments.append([ctime, c.get('content', ''), c.get('name', ''), c.get('uin', '')])
            if video_links:
                content = f"{content}\n[视频] {' '.join(video_links)}"
            # 来源客户端（如 iPhone 6 (4G)），信息不丢
            src = item.get('source_name') or ''
            if src:
                content = f"{content}\n来自：{src}"
            texts.append([create_time, f"{nickname} ：{content}", ",".join(pictures), comments])
        return texts

    def _get_user_qzone_info(self, cookies, page_size, offset=0):
        qq = self._clean_uin(cookies.get('uin'))
        g_tk = self._bkn(cookies.get('p_skey'))
        headers = {
            'accept': '*/*',
            'accept-language': 'en-US,en;q=0.9',
            'cookie': (f"uin={cookies.get('p_uin')};skey={cookies.get('skey')};"
                       f"p_uin={cookies.get('p_uin')};pt4_token={cookies.get('pt4_token')};"
                       f"p_skey={cookies.get('p_skey')}"),
            'referer': f'https://user.qzone.qq.com/{qq}/main',
            'user-agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                           '(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'),
        }
        params = {
            'uin': qq, 'ftype': '0', 'sort': '0', 'pos': offset, 'num': page_size,
            'replynum': '100', 'g_tk': g_tk, 'callback': '_preloadCallback',
            'code_version': '1', 'format': 'jsonp', 'need_private_comment': '1',
        }
        resp = self.session.get('https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/'
                                'emotion_cgi_msglist_v6', headers=headers, params=params,
                                verify=False, timeout=(10, 30))
        raw = re.sub(r'^_preloadCallback\((.*)\);?$', r'\1', resp.text, flags=re.S)
        data = json.loads(raw)
        if data.get('code') != 0:
            raise RuntimeError(f"获取说说失败: {data.get('message')}")
        return json.dumps(data, ensure_ascii=False, indent=2)

    # ---------- 图片下载 ----------
    @staticmethod
    def _hires(url):
        url = str(url).replace('/m&ek=1&kp=1', '/s&ek=1&kp=1')
        return str(url).replace(r'!/m/', '!/s/')

    def download_images(self, texts, pic_save_path, progress=None):
        """并发下载说说图片，返回 {远程链接: 本地文件名}。"""
        tasks = []
        for item in texts:
            item_text = item[1]
            links = str(item[2]).split(',')
            upgraded = []
            for link in links:
                if link and 'http' in link:
                    link = self._hires(link)
                    tasks.append((link, item_text, pic_save_path))
                upgraded.append(link)
            item[2] = ','.join(upgraded)
        if not tasks:
            return {}
        os.makedirs(pic_save_path, exist_ok=True)
        done = 0
        results = {}
        # 限流：图片下载并发控制在 3 路，避免对服务器造成压力
        with ThreadPoolExecutor(max_workers=3) as ex:
            futures = {ex.submit(self._download_one, t): t for t in tasks}
            for fut in as_completed(futures):
                link = futures[fut][0]
                try:
                    name = fut.result()
                except Exception:
                    name = None
                if name:
                    results[link] = name
                done += 1
                if progress and done % 5 == 0:
                    progress(f"下载图片 {done}/{len(tasks)} 张", 75 + int(done / len(tasks) * 20))
        return results

    def _download_one(self, args):
        link, item_text, pic_save_path = args
        if not link or 'http' not in link:
            return None
        try:
            pic_name = re.sub(r'\[em\].*?\[/em\]|[^\w\s]|[\\/:*?"<>|\r\n]+', '_',
                              item_text).replace(' ', '') + '.jpg'
            if len(pic_name) > 40:
                pic_name = pic_name[:40] + '.jpg'
            resp = self.session.get(link, verify=False, timeout=(10, 30))
            if resp.status_code == 200:
                if os.path.exists(pic_save_path + pic_name):
                    pic_name = pic_name.split('.')[0] + '_' + str(int(time.time())) + '.jpg'
                with open(pic_save_path + pic_name, 'wb') as f:
                    f.write(resp.content)
                return pic_name
        except Exception:
            return None
        return None

    # ---------- 导出 ----------
    @staticmethod
    def _safe_strptime(date_str):
        date_str = str(date_str).strip()
        for fmt in ("%Y年%m月%d日 %H:%M:%S", "%Y年%m月%d日 %H:%M",
                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        return datetime.min

    def save_results(self, cookies, texts, all_friends, nickname, progress=None):
        """分类、写 Excel、渲染 HTML、下载图片。返回 (结果目录, 统计 dict)。"""
        uin = self._clean_uin(cookies.get('uin'))
        user_save_path = os.path.join(Config.result_path, uin, '')
        pic_save_path = os.path.join(user_save_path, 'pic', '')
        os.makedirs(pic_save_path, exist_ok=True)

        stats = {'全部': len(texts)}

        def dump(name, data, columns):
            if data:
                pd.DataFrame(data, columns=columns).to_excel(
                    user_save_path + uin + '_' + name + '.xlsx', index=False)
                stats[name] = len(data)

        if progress:
            progress("保存全部消息列表...", 60)
        dump('全部列表', texts, ['时间', '内容', '图片链接', '评论'])
        dump('好友列表', all_friends, ['昵称', 'QQ', '空间主页'])

        if progress:
            progress("下载说说图片...", 70)
        local_map = self.download_images(texts, pic_save_path, progress)
        for item in texts:
            links = str(item[2]).split(',')
            new_links = []
            for link in links:
                name = local_map.get(link)
                new_links.append('pic/' + name if name else link)
            item[2] = ','.join(new_links)

        leave_message, forward_message, user_message, other_message = [], [], [], []
        for item in texts:
            item_text = item[1]
            if nickname in item_text:
                if '留言' in item_text:
                    leave_message.append(item[:-1])
                elif '转发' in item_text:
                    forward_message.append(item)
                else:
                    user_message.append(item)
            else:
                other_message.append(item[:-1])

        dump('说说列表', user_message, ['时间', '内容', '图片链接', '评论'])
        dump('转发列表', forward_message, ['时间', '内容', '图片链接', '评论'])
        dump('留言列表', leave_message, ['时间', '内容', '图片链接'])
        dump('其他列表', other_message, ['时间', '内容', '图片链接'])

        try:
            self.render_html(user_save_path + uin + '_说说列表.xlsx',
                             user_save_path + uin + '_转发列表.xlsx', cookies, uin)
        except Exception as e:
            print(f"渲染 HTML 失败: {e}")

        stats['图片'] = len([f for f in os.listdir(pic_save_path)
                             if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif'))]) \
            if os.path.exists(pic_save_path) else 0
        log.info('导出统计: %s', stats)
        return user_save_path, stats

    def render_html(self, shuoshuo_path, zhuanfa_path, cookies, uin):
        avatar_url = f"https://q.qlogo.cn/headimg_dl?dst_uin={uin}&spec=640&img_type=jpg"
        html_template, post_template, comment_template = Tools.get_html_template()
        post_html = ""

        def parse_sheet(path):
            if not os.path.exists(path):
                return []
            df = pd.read_excel(path)
            return df[['时间', '内容', '图片链接', '评论']].values.tolist()

        all_data = parse_sheet(shuoshuo_path) + parse_sheet(zhuanfa_path)
        all_data.sort(key=lambda x: self._safe_strptime(x[0]), reverse=True)
        for entry in all_data:
            try:
                time_str, content, img_urls, comments = entry
                img_url_lst = str(img_urls).split(',')
                content_lst = str(content).split('：')
                if len(content_lst) == 1:
                    continue
                nickname = re.sub(r'\[em\](.*?)\[/em\]', Tools.replace_em_to_img, content_lst[0])
                message = re.sub(r'\[em\](.*?)\[/em\]', Tools.replace_em_to_img, content_lst[1])
                image_html = '<div class="image">'
                for img_url in img_url_lst:
                    if img_url and img_url.startswith('http'):
                        image_html += f'<img src="{img_url}" alt="图片">\n'
                image_html += '</div>'
                comment_html = ''
                if str(comments) != 'nan' and comments:
                    try:
                        for c in eval(comments):
                            ctime, ccontent, cnick, cuin = c
                            cnick = re.sub(r'\[em\](.*?)\[/em\]', Tools.replace_em_to_img, str(cnick))
                            ccontent = re.sub(r'\[em\](.*?)\[/em\]', Tools.replace_em_to_img, str(ccontent))
                            cavatar = f"https://q.qlogo.cn/headimg_dl?dst_uin={cuin}&spec=640&img_type=jpg"
                            comment_html += comment_template.format(avatar_url=cavatar, nickname=cnick,
                                                                    time=ctime, message=ccontent)
                    except Exception:
                        pass
                post_html += post_template.format(avatar_url=avatar_url, nickname=nickname,
                                                  time=time_str, message=message,
                                                  image=image_html, comments=comment_html)
            except Exception:
                continue
        final_html = html_template.format(posts=post_html)
        output = os.path.join(Config.result_path, uin, uin + '_说说网页版.html')
        with open(output, 'w', encoding='utf-8') as f:
            f.write(final_html)
