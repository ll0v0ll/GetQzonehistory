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
import random
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
        # 无任何历史消息时二分下界溢出为 -1，统一归零语义
        return max(total, 0)

    def fetch_history_messages(self, cookies, count=None, progress=None):
        """通道一：历史消息列表（断点续传 + 完成标识 + 解析结果 JSON 存档）。

        接口按 start 偏移分页（每页 10 条），游标即偏移量：
        - 数据存档: data/fetch-all/<qq>/get_message_all.json
          {"texts": [...], "friends": [...], "meta": {count, completed, updated_at}}
        - 断点:     get_message_checkpoint.json {start, updated_at}
        - 已抓完（completed）：直接读 JSON，零请求（历史消息是静态快照，
          长期有效；如需强制刷新删除 get_message_all.json 与 checkpoint 即可）
        - 中断后 10 分钟内再次抓取：从断点偏移继续，新页解析结果增量合并回 JSON
        返回 (texts, friends, count)。
        """
        qq = self._clean_uin(cookies.get('uin'))
        workdir = os.path.join(Config.fetch_path, qq)
        cp_file = os.path.join(workdir, 'get_message_checkpoint.json')
        json_file = os.path.join(workdir, 'get_message_all.json')

        # ---- 读已存档数据 + checkpoint ----
        texts, friends, meta = [], [], {}
        try:
            if os.path.exists(json_file):
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                texts = data.get('texts') or []
                friends = data.get('friends') or []
                meta = data.get('meta') or {}
        except (OSError, ValueError, TypeError):
            texts, friends, meta = [], [], {}
        start, cp_mtime = 0, 0
        try:
            if os.path.exists(cp_file):
                cp_mtime = os.path.getmtime(cp_file)
                with open(cp_file, 'r', encoding='utf-8') as f:
                    start = int(json.load(f).get('start', 0) or 0)
        except (OSError, ValueError, TypeError):
            pass

        # ---- 已完成：直接返回 ----
        if meta.get('completed') and texts:
            if progress:
                progress(f'历史消息已完成（读取 {len(texts)} 条，跳过抓取）', 5)
            log.info('get_message 已完成缓存命中，读取 %d 条，跳过抓取', len(texts))
            return texts, friends, len(texts)

        # ---- 断点续传：10 分钟内从 start 继续 ----
        if start > 0 and time.time() - cp_mtime < 600:
            log.info('get_message 断点续传：从第 %d 条偏移继续（已存档 %d 条）',
                     start, len(texts))

        # ---- 探测总数（有存档则复用 count，避免重复二分探测） ----
        if count is None:
            count = meta.get('count') or self.get_message_count(cookies)
        log.info('历史消息总数=%s', count)
        if count <= 0:
            return [], [], 0

        # ---- 分页抓取：逐页解析并增量写回 JSON（中断不丢已抓部分） ----
        batch_count = int(count / 10) + 1
        start_page = min(start // 10, batch_count - 1)
        for i in range(start_page, batch_count):
            offset = i * 10
            try:
                resp = self.get_message(cookies, offset, 10)
                if resp is not None and hasattr(resp, 'content'):
                    bt, bf = self.parse_batch_messages((i, resp.content))
                    for t in bt:
                        if t[1] not in [x[1] for x in texts]:
                            texts.append(t)
                    for x in bf:
                        if x[1] not in [y[1] for y in friends]:
                            friends.append(x)
            except Exception as e:
                log.warning('get_message 分页失败（跳过，第 %d 页）: %s', i, e)
            if progress:
                done = min((i + 1) * 10, count)
                progress(f'获取历史消息 第 {i + 1}/{batch_count} 页（{done}/{count} 条）',
                         int(5 + (i + 1) * 10 / count * 45))
            # 增量写回 JSON（每页完成后已抓部分即可恢复）
            try:
                with open(json_file, 'w', encoding='utf-8') as f:
                    json.dump({'texts': texts, 'friends': friends,
                               'meta': {'count': count, 'completed': False,
                                        'updated_at': time.time()}},
                              f, ensure_ascii=False)
            except OSError:
                pass
            try:
                with open(cp_file, 'w', encoding='utf-8') as f:
                    json.dump({'start': offset + 10, 'updated_at': time.time()},
                              f, ensure_ascii=False)
            except OSError:
                pass
            # 限流：页间间隔由 config.ini [fetch] channel_interval_ms 统一控制
            time.sleep(Config.fetch_channel_interval_ms / 1000.0)

        # ---- 完成标识 ----
        try:
            with open(json_file, 'w', encoding='utf-8') as f:
                json.dump({'texts': texts, 'friends': friends,
                           'meta': {'count': count, 'completed': True,
                                    'updated_at': time.time()}},
                          f, ensure_ascii=False)
        except OSError:
            pass
        log.info('get_message 抓取完成（%d 页，%d 条），已标记 completed',
                 len(texts), count)
        return texts, friends, count

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
                progress("正在获取说说总条数...", 50)
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
        # 完成标识：抓取完成后写 checkpoint；再次抓取时缓存命中且标记完成
        # 则直接恢复，不发任何请求（需刷新可删除 moments 缓存与 checkpoint）
        mom_cp = os.path.join(workdir, 'get_moments_checkpoint.json')
        try:
            mom_done = False
            if os.path.exists(mom_cp):
                with open(mom_cp, 'r', encoding='utf-8') as f:
                    mom_done = bool(json.load(f).get('completed'))
        except (OSError, ValueError, TypeError):
            mom_done = False
        if moments and Tools.is_valid_json(moments) and mom_done:
            mom_count = len(json.loads(moments)['msglist'])
            if progress:
                progress(f'未删除说说已完成（{mom_count} 条，跳过抓取）', 50)
            log.info('get_visible_moments 已完成缓存命中，%d 条，跳过抓取', mom_count)
        elif not moments:
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
                    # 进度 50% -> 58% 区间（按条数实时推进），消息带页数
                    done = min((page + 1) * page_size, total)
                    progress(f"获取未删除说说 第 {page + 1}/{total_page} 页（{done}/{total} 条）",
                             int(50 + (page + 1) / total_page * 8))
                # 限流：页间间隔由 config.ini [fetch] channel_interval_ms 统一控制
                time.sleep(Config.fetch_channel_interval_ms / 1000.0)
            Tools.write_txt_file(workdir, moments_file,
                                 json.dumps({"msglist": all_data}, ensure_ascii=False, indent=2))
            # 完成标识：本次已完整抓取未删除说说，下次直接恢复不再请求
            try:
                with open(mom_cp, 'w', encoding='utf-8') as f:
                    json.dump({'completed': True, 'total': total,
                               'updated_at': time.time()}, f, ensure_ascii=False)
            except OSError:
                pass
            log.info('get_visible_moments 抓取完成（%d 条），已标记 completed', total)
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
                # 评论下的回复楼层（QQ 空间允许楼中楼）
                for r in c.get('replylist') or []:
                    try:
                        rtime = datetime.strptime(r.get('createTime2', ''), '%Y-%m-%d %H:%M:%S'
                                                  ).strftime('%Y年%m月%d日 %H:%M')
                    except (ValueError, TypeError):
                        rtime = r.get('createTime2', '')
                    rcontent = r.get('content', '')
                    rname = r.get('name', '')
                    ruin = r.get('uin', '')
                    # 回复对象（楼中楼指向谁）
                    if r.get('reply_uin'):
                        rname = f"{rname} 回复 {r.get('reply_name', '')}"
                    comments.append([rtime, rcontent, rname, ruin])
            if video_links:
                content = f"{content}\n[视频] {' '.join(video_links)}"
            # 来源客户端（如 iPhone 6 (4G)），信息不丢
            src = item.get('source_name') or ''
            if src:
                content = f"{content}\n来自：{src}"
            # 点赞数（appinfo.likeinfo.like_count），写入第 5 列（导出不影响前 4 列）
            try:
                like_count = int((item.get('appinfo') or {}).get('likeinfo', {}).get('like_count', 0))
            except (ValueError, TypeError):
                like_count = 0
            texts.append([create_time, f"{nickname} ：{content}", ",".join(pictures),
                          comments, like_count])
        return texts

    # ---------- 通道三：mobile get_feeds 动态流（参考 QzoneArchive） ----------
    FEEDS_URL = 'https://mobile.qzone.qq.com/get_feeds'
    _MOBILE_UA = ('Mozilla/5.0 (Linux; Android 13; 2211133C) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36')

    @staticmethod
    def parse_qzone_json(text):
        """容错解析 QQ 空间 JSON。

        服务端可能返回纯 JSON，也可能带 frameElement.callback(...)、
        _Callback(...)、try{}catch{} 等外层包裹，或尾部追加分号；
        逐个回退尝试，取最外层含 code/data 的对象。
        """
        normalized = (text or '').strip().lstrip('\ufeff').strip()
        if not normalized:
            return {'code': 0}
        try:
            return json.loads(normalized)
        except Exception:
            pass
        if 'frameElement.callback(' in normalized:
            start = normalized.find('{')
            end = normalized.rfind('}')
            if start != -1 and end > start:
                try:
                    return json.loads(normalized[start:end + 1])
                except Exception:
                    pass
        best = None
        for i in range(len(normalized) - 1, -1, -1):
            if normalized[i] != '}':
                continue
            for j in range(i - 1, -1, -1):
                if normalized[j] != '{':
                    continue
                try:
                    obj = json.loads(normalized[j:i + 1])
                except Exception:
                    continue
                if 'code' in obj or 'data' in obj:
                    return obj
                if best is None:
                    best = obj
                break
        if best is not None:
            return best
        raise ValueError('解析 QQ 空间响应失败')

    @staticmethod
    def _feed_obj(feed, *pointers):
        """按 JSON Pointer 路径依次取值，返回第一个命中的对象或 None。"""
        for pointer in pointers:
            node = feed
            ok = True
            for part in pointer.strip('/').split('/'):
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    ok = False
                    break
            if ok and node is not None:
                return node
        return None

    def _feed_str(self, feed, *pointers, default=''):
        node = self._feed_obj(feed, *pointers)
        if node is None:
            return default
        return str(node)

    @staticmethod
    def _pic_candidates(p):
        """单个图片节点的全部候选 URL（对齐 QzoneArchive picture_url_candidates）。

        真实动态流数据里 pic 节点一般没有顶层 /url，图片地址藏在：
          - photourl：对象 {尺寸key: {height,url}} 或多尺寸数组 -> 每个都有可用 url
          - busi_param：字符串化的 Python dict "{'-1': 'http://...'}" 或真对象 -> 原图直链
          - 少数接口版本才有顶层 /url、/url1、/url2
        返回去重、补全 https 的候选列表（首个为优先使用）。
        """
        cands = []
        if not isinstance(p, dict):
            return cands
        pu = p.get('photourl')
        if isinstance(pu, dict):
            for v in pu.values():
                if isinstance(v, dict) and isinstance(v.get('url'), str) \
                        and v['url'].strip():
                    cands.append(v['url'])
        elif isinstance(pu, list):
            for v in pu:
                if isinstance(v, dict) and isinstance(v.get('url'), str) \
                        and v['url'].strip():
                    cands.append(v['url'])
                elif isinstance(v, str) and v.strip():
                    cands.append(v)
        elif isinstance(pu, str) and pu.strip():
            cands.append(pu)
        for k in ('url', 'url1', 'url2'):
            v = p.get(k)
            if isinstance(v, str) and v.strip():
                cands.append(v)
        bp = p.get('busi_param')
        if isinstance(bp, dict):
            v = bp.get('-1')
            if isinstance(v, str) and v.strip():
                cands.append(v)
        elif isinstance(bp, str) and bp.strip().startswith('{'):
            try:
                import ast
                obj = ast.literal_eval(bp)
                if isinstance(obj, dict):
                    v = obj.get('-1')
                    if isinstance(v, str) and v.strip():
                        cands.append(v)
            except Exception:
                pass
        seen, out = set(), []
        for u in cands:
            u = str(u).strip()
            if not u:
                continue
            if u.startswith('//'):
                u = 'https:' + u
            if u in seen:
                continue
            seen.add(u)
            out.append(u)
        return out

    def _feed_int(self, feed, *pointers, default=0):
        node = self._feed_obj(feed, *pointers)
        try:
            return int(float(node))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _extract_comments(node, out, _depth=0):
        """宽松提取评论/回复楼层（兼容 cell_comment 的多种 JSON 形状）。"""
        if node is None or _depth > 6 or len(out) > 300:
            return
        if isinstance(node, list):
            for c in node:
                QzoneService._extract_comments(c, out, _depth + 1)
            return
        if not isinstance(node, dict):
            return
        # 评论节点：内容键 + 作者信息（昵称/uin 可能在顶层或 user 子键嵌套）
        if any(k in node for k in ('content', 'con', 'msg')):
            try:
                ctime = node.get('createTime2') or node.get('create_time2') or ''
                if not ctime and node.get('createTime'):
                    ctime = Tools.format_timestamp(node['createTime'])
                uinfo = node.get('user')
                if isinstance(uinfo, dict):
                    name = (node.get('name') or node.get('nickname')
                            or uinfo.get('nickname') or '')
                    uin = node.get('uin') or uinfo.get('uin') or ''
                else:
                    name = node.get('name') or node.get('nickname') or ''
                    uin = node.get('uin') or ''
                out.append([str(ctime), str(node.get('content') or node.get('con')
                                 or node.get('msg') or ''),
                            str(name), str(uin)])
            except Exception:
                pass
            return
        for key in ('commentlist', 'list', 'comments', 'replylist', 'replies',
                    'data', 'main_comment', 'maincomment'):
            if key in node:
                QzoneService._extract_comments(node[key], out, _depth + 1)
        # 未命中已知容器键时才宽松遍历子节点（避免重复提取）
        if not any(k in node for k in ('commentlist', 'list', 'comments',
                                       'replylist', 'replies', 'data',
                                       'main_comment', 'maincomment')):
            for key, value in node.items():
                if isinstance(value, (dict, list)):
                    QzoneService._extract_comments(value, out, _depth + 1)

    def parse_feed_item(self, feed):
        """单条动态流 -> dict：时间/正文/图片/视频/评论/点赞/作者/转发原/来源。

        同时识别动态流事件类型（参考 QzoneArchive 事件建模）：
          subid=217 -> 点赞事件（userinfo 为点赞者，original 为被赞动态）
          subid=2/311 -> 评论事件（summary 为评论内容，userinfo 为评论者）
          其余 -> 主动态（original 为动态本体）
        """
        subid = self._feed_int(feed, '/comm/subid', 0)
        is_like_event = subid == 217
        is_comment_event = subid in (2, 311)
        # 评论事件的内容在 /summary/summary（评论本身），不是 original 摘要
        if is_comment_event:
            content = self._feed_str(feed, '/summary/summary',
                                     '/original/cell_summary/summary')
        else:
            content = self._feed_str(feed, '/original/cell_summary/summary',
                                     '/summary/summary', '/title/title')
        item = {
            'feedkey': self._feed_str(feed, '/comm/feedskey', '/original/cell_comm/feedskey'),
            'cell_id': self._feed_str(feed, '/original/cell_id/cellid'),
            'time': self._feed_int(feed, '/comm/time', '/cell_comm/time'),
            'cell_time': self._feed_int(feed, '/original/cell_comm/time', '/comm/time'),
            'subid': subid,
            'content': content,
            'nickname': self._feed_str(feed, '/userinfo/user/nickname'),
            'uin': self._feed_str(feed, '/userinfo/user/uin'),
            'source_name': self._feed_str(feed, '/comm/from'),
            'fwd_uin': self._feed_str(feed, '/original/cell_userinfo/user/uin'),
            'fwd_name': self._feed_str(feed, '/original/cell_userinfo/user/nickname'),
            'fwd_content': self._feed_str(feed, '/original/cell_summary/summary'),
            # 留言板识别（参考 QzoneArchive）：appid=334 或 feedskey 以 334_ 开头
            'appid': self._feed_int(feed, '/original/cell_comm/appid', 0),
            'orig_key': self._feed_str(feed, '/original/cell_comm/feedskey'),
            'is_guestbook': False,
            'pics': [],
            'pics_all': [],
            'video_url': '',
            'video_cover': '',
            'comments': [],
            'comment_count': 0,
            'like_count': 0,
            'is_like_event': is_like_event,
            'is_comment_event': is_comment_event,
        }
        item['is_guestbook'] = (item['appid'] == 334
                                or str(item['orig_key'] or '').startswith('334_'))
        pics_node = self._feed_obj(feed, '/original/cell_pic/picdata/pic',
                                   '/original/cell_pic')
        if isinstance(pics_node, dict):
            pics_node = [pics_node]
        if isinstance(pics_node, list):
            item['pics_all'] = [self._pic_candidates(p) for p in pics_node
                                if isinstance(p, dict)]
            item['pics'] = [g[0] for g in item['pics_all'] if g]
        video = self._feed_obj(feed, '/original/cell_video')
        if isinstance(video, dict):
            item['video_url'] = self._feed_str(video, '/url', '/video', '/url1', default='')
            item['video_cover'] = self._feed_str(video, '/url1', '/pic',
                                                 '/coverurl/0/url', default='')
            if item['video_cover'] and item['video_cover'] not in item['pics']:
                item['pics'].append(item['video_cover'])
                item['pics_all'].append([item['video_cover']])
        item['comment_count'] = self._feed_int(feed, '/appinfo/commentinfo/comment_count')
        item['like_count'] = self._feed_int(feed, '/appinfo/likeinfo/like_count')
        self._extract_comments(self._feed_obj(feed, '/original/cell_comment'),
                               item['comments'])
        return item

    def fetch_feeds_page(self, cookies, refresh_type='1', attach_info=None, attempts=None):
        """获取一页动态流（指数退避重试）。返回 (feeds, next_attach, has_more)。"""
        if attempts is None:
            attempts = max(1, Config.fetch_max_attempts)
        qq = self._clean_uin(cookies.get('uin'))
        g_tk = self._bkn(cookies.get('p_skey'))
        headers = {
            'accept': 'application/json, text/javascript, */*; q=0.01',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'cache-control': 'no-cache',
            'pragma': 'no-cache',
            'origin': 'https://h5.qzone.qq.com',
            'referer': 'https://h5.qzone.qq.com/',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-site',
            'sec-ch-ua-mobile': '?1',
            'user-agent': self._MOBILE_UA,
        }
        params = {'g_tk': g_tk, 'res_type': '1', 'refresh_type': refresh_type,
                  'format': 'json'}
        if attach_info:
            params['res_attach'] = attach_info
        last_err = None
        permanent = False
        for attempt in range(1, attempts + 1):
            try:
                resp = self.session.get(self.FEEDS_URL, params=params, cookies=cookies,
                                        headers=headers, verify=False, timeout=(10, 30))
                if resp.status_code == 501 or resp.status_code == 429 or resp.status_code >= 500:
                    # 记录诊断（参考 QzoneArchive）：状态码 + 响应体前 800 字符，便于排查
                    log.warning('get_feeds 请求失败 HTTP %s（第 %d 次）响应体: %s',
                                resp.status_code, attempt,
                                resp.text[:800].replace('\n', ' '))
                    if resp.status_code == 501:
                        # 501：服务端拒绝该分页请求（游标到尽头或已过期），永久错误不重试
                        permanent = True
                        raise RuntimeError('HTTP 501')
                    raise RuntimeError(f'HTTP {resp.status_code}')
                data = self.parse_qzone_json(resp.text)
                code = data.get('code', 0)
                if code != 0:
                    message = str(data.get('message') or data.get('msg') or '未知错误')
                    if any(k in message for k in ('未登录', '登录失效', '权限', '封禁',
                                                  'p_skey', '禁止访问')):
                        permanent = True
                        raise RuntimeError(f'QQ 空间接口返回错误 {code}：{message}')
                    raise RuntimeError(f'QQ 空间接口返回错误 {code}：{message}')
                d = data.get('data') or {}
                feeds = d.get('vFeeds') or []
                attach = d.get('attachinfo') or ''
                has_more = bool(d.get('hasmore')) and bool(feeds) and bool(attach)
                return feeds, attach, has_more
            except Exception as e:
                last_err = e
                if permanent:
                    # 永久错误（501/登录失效等）立即失败，不做退避重试
                    raise RuntimeError(f'获取动态流失败：{e}') from e
                if attempt < attempts:
                    time.sleep(1.5 * (2 ** (attempt - 1)))
        raise RuntimeError(f'获取动态流失败：{last_err}')

    def fetch_all_feeds(self, cookies, progress=None, total_est=0, interval_ms=None):
        """全量抓取动态流（mobile get_feeds），参考 QzoneArchive 事件建模。

        增强点（取其精华）：
        1. 事件建模：subid=217 点赞事件 / 2、311 评论事件按 cell_id 聚合回填，
           主动态不再混入"XX 评论了 / XX 赞了"垃圾条目，点赞与评论更完整；
        2. raw JSON 全量存档：每页 vFeeds 原文落盘（data/fetch-all/<qq>/），
           以后解析升级无需重新抓取；
        3. 断点续传：checkpoint 记录游标，中断后从上次位置继续（10 分钟内有效），
           抓完自动清除；
        4. 滑动窗口总量限速：窗口秒数内最多抓 window_pages 页，超限等待重置。

        返回 texts（5 列：时间/内容/图片/评论/点赞数），与既有通道格式兼容。
        """
        if interval_ms is None:
            interval_ms = max(800, Config.fetch_interval_ms)
        qq = self._clean_uin(cookies.get('uin'))
        workdir = os.path.join(Config.fetch_path, qq)
        checkpoint_file = os.path.join(workdir, 'get_feeds_checkpoint.json')
        raw_dir = os.path.join(workdir, 'get_feeds_raw')
        try:
            os.makedirs(raw_dir, exist_ok=True)
        except OSError:
            pass

        # ---- 断点续传：读取有效 checkpoint，过期则重新开始 ----
        # 普通中断断点 10 分钟内有效；WAF 风控断点（waf_blocked>0）放宽到 24 小时，
        # 因为 WAF 按 IP/账号维度拦截，从头重翻（新会话）同样会被拦，
        # 正确做法是等风控解除后继续从断点翻页
        cursor = None
        start_page = 0
        cp_mtime = 0
        try:
            if os.path.exists(checkpoint_file):
                cp_mtime = os.path.getmtime(checkpoint_file)
                waf_n = 0
                try:
                    with open(checkpoint_file, 'r', encoding='utf-8') as f:
                        waf_n = int(json.load(f).get('waf_blocked', 0) or 0)
                except (OSError, ValueError, TypeError):
                    pass
                max_age = 86400 if waf_n > 0 else 600
                if time.time() - cp_mtime < max_age:
                    with open(checkpoint_file, 'r', encoding='utf-8') as f:
                        cp = json.load(f)
                    if cp.get('cursor'):
                        cursor = cp['cursor']
                        start_page = int(cp.get('pages', 0))
                        log.info('get_feeds 断点续传：从第 %d 页游标继续', start_page + 1)
        except (OSError, ValueError, TypeError):
            pass

        texts = []
        seen_keys = set()
        seen_cursors = set()   # 防死循环：同一游标重复翻页直接停止
        page = start_page
        # ---- 事件建模聚合区（cell_id -> 事件列表） ----
        events_comments = {}   # cell_id -> [[time, content, name, uin], ...]
        event_likers = {}      # cell_id -> {name, ...}
        liker_name = {}        # cell_id -> [name, ...]（保序）
        main_rows = {}         # cell_id -> texts 行索引（回填用）

        def _restore_event(it):
            """事件（点赞/评论）original 快照还原：互动过的动态内容不丢弃。

            参考 QzoneArchive save_original_dynamic：
            - 留言板（appid 334）：内容取 /summary/summary（留言本身）
            - self（original 作者=自己）：还原自己发过的动态（可能是已删除的说说）
            - other：还原互动过的他人动态
            cell_id 已存在的（main_rows）只回填事件，不重复还原。
            """
            if not it.get('cell_id'):
                return
            if it['cell_id'] in main_rows:
                return
            feed = it.get('_feed') or {}
            org = feed.get('original') or {}
            if not org:
                return
            if it.get('is_guestbook'):
                # 留言板：留言内容与留言者在外层 feed，original 是留言板本身
                content = self._feed_str(feed, '/summary/summary', '/title/title')
                author = it['nickname'] or it['uin'] or 'QQ用户'
                text = f'{author} 留言：{content}' if content else f'{author} 留言'
                src = '留言板'
                t = it['time']
            else:
                content = (it['fwd_content'] or '').strip()
                # 事件快照的摘要常带 "：" 前缀（回复格式），去掉便于阅读
                if content.startswith('：'):
                    content = content[1:].strip()
                if it['fwd_uin'] and it['fwd_uin'].lstrip('o') == \
                        str(cookies.get('uin', '')).lstrip('o'):
                    # 自己的动态（可能已删除）→ 归入说说
                    author = self._clean_uin(cookies.get('uin'))
                    text = f'{author} ：{content}' if content else author
                    src = '自己'
                else:
                    author = it['fwd_name'] or it['fwd_uin'] or 'QQ用户'
                    text = f'{author} ：{content}' if content else author
                    src = '他人'
                t = it['cell_time'] or it['time']
            pics = ','.join(it['pics'])
            if it['video_url']:
                text = f'{text}\n[视频] {it["video_url"]}'
            idx = len(texts)
            texts.append([Tools.format_timestamp(t), text, pics,
                          it['comments'], it['like_count'], src,
                          list(liker_name.get(it['cell_id'] or key, [])),
                          it.get('pics_all') or []])
            main_rows[it['cell_id']] = idx

        def _ingest(feed):
            """解析单条 feed 并入聚合区（主循环与 raw 恢复共用）。"""
            try:
                it = self.parse_feed_item(feed)
            except Exception:
                return
            it['_feed'] = feed
            key = it['feedkey'] or f'{it["time"]}:{it["content"][:40]}'
            if key in seen_keys:
                return
            seen_keys.add(key)
            # ---- 事件建模：点赞/评论事件挂到原动态，不单独成条 ----
            if it['is_like_event'] or it['is_comment_event']:
                # 先还原互动动态快照（original 有内容则新增，无则仅回填）
                _restore_event(it)
                if it['is_like_event']:
                    name = it['nickname'] or it['uin'] or 'QQ用户'
                    cid = it['cell_id'] or key
                    if cid not in event_likers:
                        event_likers[cid] = set()
                        liker_name[cid] = []
                    if name not in event_likers[cid]:
                        event_likers[cid].add(name)
                        liker_name[cid].append(name)
                    return
                if it['is_comment_event']:
                    cid = it['cell_id'] or key
                    events_comments.setdefault(cid, []).append(
                        [Tools.format_timestamp(it['time']),
                         it['content'],
                         it['nickname'] or 'QQ用户',
                         it['uin'] or ''])
                    return
            # ---- 主动态 ----
            content = it['content']
            title_name = it['nickname'] or 'QQ用户'
            text = f'{title_name} ：{content}' if content else title_name
            if it['source_name']:
                text = f'{text}\n来自：{it["source_name"]}'
            if it['video_url']:
                text = f'{text}\n[视频] {it["video_url"]}'
            if it['fwd_name']:
                text = f'{text}\n转发自：{it["fwd_name"]}'
            pics = ','.join(it['pics'])
            cid = it['cell_id'] or key
            idx = len(texts)
            texts.append([Tools.format_timestamp(it['time']), text, pics,
                          it['comments'], it['like_count'], '动态流',
                          list(liker_name.get(cid, [])),
                          it.get('pics_all') or []])
            main_rows[cid] = idx

        # ---- raw 恢复：有已落盘的 raw 文件就重建数据（无论是否续传） ----
        # 从头重翻（游标失效/WAF 多次拦截）时旧数据先恢复进聚合区并去重，
        # 新抓页面覆盖同名 raw 文件也不会丢数据
        try:
            raw_files = sorted(f for f in os.listdir(raw_dir)
                               if f.startswith('page_') and f.endswith('.json'))
            if raw_files:
                for rf in raw_files:
                    try:
                        with open(os.path.join(raw_dir, rf), 'r', encoding='utf-8') as f:
                            saved = json.load(f)
                    except (OSError, ValueError):
                        continue
                    # 兼容旧格式（纯数组）与新格式（{attachinfo, hasmore, feeds}）
                    feeds = saved.get('feeds') if isinstance(saved, dict) else saved
                    if not isinstance(feeds, list):
                        continue
                    for feed in feeds:
                        _ingest(feed)
                if cursor:
                    # 续传：从已有页数继续写新 raw
                    page = max(page, len(raw_files))
                else:
                    # 从头重翻：新数据覆盖旧 raw（旧数据已恢复去重）
                    page = 0
                log.info('get_feeds raw 恢复：%d 个 raw 文件，恢复 %d 条动态',
                         len(raw_files), len(texts))
        except OSError:
            pass

        window_started = time.time()
        window_pages = 0
        completed = False
        blocked = False   # True=被 WAF/服务端拦截中止（保留断点，下次续传）

        def _rate_limit_wait():
            """滑动窗口总量限速：窗口内页数超限则等待窗口重置。"""
            nonlocal window_started, window_pages
            limit = Config.fetch_window_pages
            window_sec = Config.fetch_window_seconds
            window_pages += 1
            if window_pages >= limit and window_sec > 0:
                elapsed = time.time() - window_started
                if elapsed < window_sec:
                    wait = window_sec - elapsed + 1
                    log.info('get_feeds 触发滑动窗口限速（%d 页/%ds），等待 %.0fs 后继续',
                             limit, window_sec, wait)
                    if progress:
                        progress(f'触发频率保护，等待 {int(wait)}s 后继续…',
                                 78 + min(int(wait / 60), 4))
                    time.sleep(wait)
                window_started = time.time()
                window_pages = 0

        def _save_checkpoint(waf_blocked=False):
            """保存断点（游标 + 进度 + WAF 拦截计数）。

            参考 QzoneArchive：checkpoint 记录失败位置，下次从该页续传；
            WAF 拦截时保留断点不删除，等风控解除后可继续。
            """
            nonlocal blocked
            try:
                cp = {'cursor': cursor, 'pages': page,
                      'fetched': len(texts), 'updated_at': time.time()}
                if waf_blocked:
                    blocked = True
                    prev = 0
                    try:
                        if os.path.exists(checkpoint_file):
                            with open(checkpoint_file, 'r', encoding='utf-8') as f:
                                prev = int(json.load(f).get('waf_blocked', 0) or 0)
                    except (OSError, ValueError, TypeError):
                        pass
                    cp['waf_blocked'] = prev + 1
                with open(checkpoint_file, 'w', encoding='utf-8') as f:
                    json.dump(cp, f, ensure_ascii=False)
            except OSError:
                pass

        while True:
            try:
                feeds, next_attach, has_more = self.fetch_feeds_page(
                    cookies, '1' if cursor is None else '2', cursor)
            except Exception as e:
                if 'HTTP 501' in str(e):
                    # 501：腾讯 WAF 风控拦截（响应体为 waf.tencent.com/501page.html）。
                    # 不是数据尽头！保留断点，风控解除后下次从该页续传；
                    # 连续多次被拦说明风控未解除，提示用户稍后再试。
                    _save_checkpoint(waf_blocked=True)
                    waf_n = 0
                    try:
                        with open(checkpoint_file, 'r', encoding='utf-8') as f:
                            waf_n = int(json.load(f).get('waf_blocked', 0) or 0)
                    except (OSError, ValueError, TypeError):
                        pass
                    if progress:
                        denom = max(total_est, len(texts), 50)
                        pct = 58 + min(int(len(texts) / denom * 37), 37)
                        if waf_n >= 3:
                            progress('动态流被 WAF 风控多次拦截，建议停止一段时间再试'
                                     '（断点已保存，可稍后继续）', pct)
                        else:
                            progress('动态流请求被 WAF 风控拦截（已保存断点，'
                                     '可稍后重新抓取继续）', pct)
                    log.warning('get_feeds 被腾讯 WAF 拦截（HTTP 501，第 %d 次，已抓 %d 页）：'
                                '断点已保留，风控解除后可继续；若持续拦截请稍后再试',
                                waf_n, page)
                else:
                    log.warning('get_feeds 分页失败（跳过继续，已抓 %d 页）：%s', page, e)
                    _save_checkpoint()
                break
            if not feeds:
                completed = True
                break
            page += 1
            # ---- raw JSON 全量存档（含游标元数据，便于后续恢复/二次分析） ----
            try:
                with open(os.path.join(raw_dir, f'page_{page:04d}.json'),
                          'w', encoding='utf-8') as f:
                    json.dump({'attachinfo': next_attach or '', 'hasmore': has_more,
                               'feeds': feeds}, f, ensure_ascii=False)
            except OSError:
                pass

            for feed in feeds:
                _ingest(feed)

            if progress:
                # 动态流总条数无法预知（接口只返回 hasmore + 游标，无总数），
                # 用调用方预估上限（total_est）平滑推进；已抓页数与条数实时精确
                denom = max(total_est, len(texts), 50)
                pct = 58 + min(int(len(texts) / denom * 37), 37)
                msg = f'获取动态流 第 {page} 页（已抓 {len(texts)} 条 / 预估 {denom} 条）'
                progress(msg, pct)
                # 终端日志：每 10 页报一次进度（避免数百页刷屏），翻页结束也报
                if page % 10 == 0 or not has_more or not next_attach:
                    log.info('get_feeds 进度: %s', msg)
            if not has_more or not next_attach:
                completed = True
                break
            if next_attach in seen_cursors:
                log.warning('get_feeds 游标重复（%s），停止翻页防止死循环', next_attach[:40])
                completed = True
                break
            seen_cursors.add(next_attach)
            cursor = next_attach
            _save_checkpoint()
            # ---- 页间限速 + 随机抖动 ----
            jitter = random.random() * (interval_ms / 4)
            time.sleep((interval_ms + jitter) / 1000.0)
            _rate_limit_wait()

        # ---- 事件回填：评论与点赞挂到对应主动态 ----
        for cid, idx in main_rows.items():
            row = texts[idx]
            extra = events_comments.get(cid)
            if extra:
                # 与自身 cell_comment 解析结果合并去重（按内容+昵称）
                own = row[3] if isinstance(row[3], list) else []
                seen_c = {(c[1], c[2]) for c in own if len(c) > 2}
                for c in extra:
                    if (c[1], c[2]) not in seen_c:
                        own.append(c)
                        seen_c.add((c[1], c[2]))
                row[3] = own
            likers = liker_name.get(cid)
            if likers:
                row[4] = max(int(row[4] or 0), len(likers))

        try:
            if completed and not blocked and os.path.exists(checkpoint_file):
                os.remove(checkpoint_file)
        except OSError:
            pass
        log.info('get_feeds 动态流完成：%s 页，%s 条'
                 '（事件回填：评论 %d 组 / 点赞 %d 组）%s',
                 page, len(texts), len(events_comments), len(event_likers),
                 '' if completed else '（被拦截中止，断点已保存，可续传）')
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
                    progress(f"下载图片 {done}/{len(tasks)} 张", 90 + int(done / len(tasks) * 8))
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
            if not data:
                return
            # 通道三会带来第 5 列（点赞数）/第 6 列（来源），与 4 列通道合并后行宽不一致：
            # 统一补齐到最大列宽；列名多于实际数据列时截断，避免 pandas 列数不匹配报错
            max_cols = max(len(r) for r in data)
            cols = list(columns)
            if max_cols > len(cols):
                if max_cols == 5 and len(cols) == 4:
                    cols.append('点赞数')
                else:
                    cols.extend(f'列{i}' for i in range(len(cols) + 1, max_cols + 1))
            else:
                cols = cols[:max_cols]
            rows = [list(r) + [''] * (max_cols - len(r)) for r in data]
            pd.DataFrame(rows, columns=cols).to_excel(
                user_save_path + uin + '_' + name + '.xlsx', index=False)
            stats[name] = len(data)

        if progress:
            progress("保存全部消息列表...", 88)
        # 候选图片地址组只在内存给 UI 做下载回退，不落 Excel：剥掉第 8 元素
        for r in texts:
            if len(r) > 7:
                del r[7:]
        # 通道三还原条目带第 6 列"来源"（动态流/自己/他人/留言板）
        dump('全部列表', texts, ['时间', '内容', '图片链接', '评论', '点赞数', '来源', '点赞者'])
        dump('好友列表', all_friends, ['昵称', 'QQ', '空间主页'])

        if progress:
            progress("下载说说图片...", 90)
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
            # 通道三还原条目：按第 6 列"来源"归类；老数据（无来源列）走内容关键词判断
            src = item[5] if len(item) > 5 else ''
            if src == '留言板':
                leave_message.append(item[:4])
            elif src == '自己':
                user_message.append(item[:4])
            elif src == '他人':
                other_message.append(item[:4])
            elif nickname in item_text:
                if '留言' in item_text:
                    leave_message.append(item[:-1])
                elif '转发' in item_text:
                    forward_message.append(item[:4])
                else:
                    user_message.append(item[:4])
            else:
                other_message.append(item[:4])

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
