# -*- coding: utf-8 -*-
"""针对 fix/export-tttt-and-dead-image-links 分支的回归测试。

仅使用标准库，可直接运行：
    python tests/test_fix_regression.py
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from util.ToolsUtil import process_old_html, extract_string_between

FAILURES = []


def check(name, cond):
    print(('PASS' if cond else 'FAIL') + '  ' + name)
    if not cond:
        FAILURES.append(name)


def test_process_old_html_no_more_tttt():
    """核心回归：\\t 转义不再变成字面量 't' 残留"""
    # r"\t" 在源码里就是「反斜杠 + t」两个字符，模拟 QQ 原始消息里的转义制表符
    raw = ("html:'<li class=\"f-single f-s-s\">"
           "\\t\\t\\t\\t<div class=info-detail>逃跑的月亮☆\\t2025年6月10日 16:42</div>"
           "\\n不过是最初.<\\/a>',opuin:123}")
    out = process_old_html(raw)
    check('无连续 t 残留', 'tt' not in out)
    check('无裸反斜杠+t序列', '\\t' not in out)
    check('昵称完整保留', '逃跑的月亮☆' in out)
    check('正文完整保留', '不过是最初.' in out)
    check('日期完整保留', '2025年6月10日 16:42' in out)
    check('起始位置正确提取', out.startswith('<li'))
    check('结束边界正确截断', out.endswith('</a>'))
    print('     -> 清洗结果: ' + repr(out))


def test_extract_string_between():
    check('常规提取', extract_string_between('AxxBxxC', 'A', 'B') == 'xx')
    check('start 缺失返回空串', extract_string_between('AxxB', 'Z', 'x') == '')
    check('end 缺失取到末尾', extract_string_between('AxxBxxC', 'A', 'Z') == 'xxBxxC')
    check('end 从 start 之后查找', extract_string_between('AABAA', 'AA', 'AA') == 'B')


def test_comment_time_format():
    """评论时间 createTime2 的英文格式可被按预期解析为中文格式"""
    parsed = datetime.strptime('2022-09-29 20:50:53', '%Y-%m-%d %H:%M:%S')
    check('英文时间解析', parsed.year == 2022 and parsed.month == 9)
    check('中文格式化', parsed.strftime('%Y年%m月%d日 %H:%M') == '2022年09月29日 20:50')


if __name__ == '__main__':
    print('=== toolsutil 回归测试 ===')
    test_process_old_html_no_more_tttt()
    test_extract_string_between()
    test_comment_time_format()
    if FAILURES:
        print('\n共 %d 项失败' % len(FAILURES))
        sys.exit(1)
    print('\n全部通过')
