
# GetQzonehistory(获取qq发布的历史说说)

2026-9-13 新增 PyQt 图形界面 修复若干Bug

2026-4-25 修复若干问题

2025-10-22修改之后亲测可以正常工作 编码问题修复、SSL连接问题修复、并发性能优化

自己运行时遇到了问题，所以用cursor改写了更鲁棒的策略


# 爬虫来源与使用须知（重要事情说三遍！！！）

> **爬虫逻辑来源**：本项目爬虫部分逻辑来自 [LibraHp/GetQzonehistory](https://github.com/LibraHp/GetQzonehistory)，在此致谢原作者的思路与实现。

## 请合理使用，不要给服务器添负担

爬虫本质上是对官方服务器发起大量请求，**请务必遵守以下约定**：

1. **本项目已内置限流**
2. **避免高峰期爬取**：请勿在 QQ 空间使用高峰时段批量抓取数据，无异于对服务器发起攻击。
3. **风险自担**：因滥用导致账号受限、IP 被限制等后果，由使用者自行承担。

**倡导**：不要频繁请求 —— 我们获取的是自己的历史数据，不是给任何工作人员增添负担。

<details><summary><font color="#FF0000" size="5">免责声明【必读】</font></summary>

本工具仅供学习和技术研究使用，不得用于任何商业或非法行为，否则后果自负。

本工具的作者不对本工具的安全性、完整性、可靠性、有效性、正确性或适用性做任何明示或暗示的保证，也不对本工具的使用或滥用造成的任何直接或间接的损失、责任、索赔、要求或诉讼承担任何责任。

本工具的作者保留随时修改、更新、删除或终止本工具的权利，无需事先通知或承担任何义务。

本工具的使用者应遵守相关法律法规，尊重QQ的版权和隐私，不得侵犯QQ或其他第三方的合法权益，不得从事任何违法或不道德的行为。

本工具的使用者在下载、安装、运行或使用本工具时，即表示已阅读并同意本免责声明。如有异议，请立即停止使用本工具，并删除所有相关文件。

</details>


---

## 抓取通道与限速配置

程序默认使用 **三条通道** 合并获取数据（按内容自动去重）：

1. **历史消息列表**（feeds2_html_pav_all）：早期全部历史消息
2. **全部可见说说**（emotion_cgi_msglist_v6）：未删除说说，含图片/视频/评论/来源客户端
3. **动态流增强通道**（mobile get_feeds，参考 [QzoneArchive](https://github.com/ehre/QzoneArchive)）：动态流 JSON，补充评论楼中楼回复、点赞数、转发原文信息

动态流通道借鉴了 QzoneArchive 的 **事件建模**



### 使用建议

1. **避免高峰期爬取**：请勿在 QQ 空间使用高峰时段批量抓取数据；
2. **间隔抓取**：同一账号每次全量抓取之间建议间隔数小时或隔天，避免长时间连续请求；
3. 抓取的是**自己的历史数据**，请勿用于任何商业或非法用途。



501 是腾讯 WAF 防火墙拦截

---


# 快速开始

> **直接下载运行（推荐）**
>
> 前往 [Releases](https://github.com/ll0v0ll/GetQzonehistory/releases) 页面下载对应系统的可执行文件，无需安装Python环境，双击即可运行。
>
> | 系统 | 文件 |
> |------|------|
> | Windows | `GetQzonehistory_win.exe` |
> | macOS | `GetQzonehistory_mac` |
> | Linux | `GetQzonehistory_linux` |
>
> Mac/Linux 用户需要先赋予执行权限：`chmod +x GetQzonehistory_mac`


或构建虚拟环境后

```bash
# 安装依赖（含 PyQt6）
pip install -r requirements.txt

# 启动图形界面
python main_gui.py
```

---



## 运行日志（debug 用）

GUI 运行时会把关键事件（登录、抓取进度、导出统计、异常堆栈）写入日志文件：
- 路径：`data/logs/gui.log`
- 内容：主线程与后台线程的未捕获异常也会自动记录，方便排查"卡死/闪退"类问题

遇到问题时，请把 `data/logs/gui.log`（以及卡死时的 `data/logs/thread_dump.log`）一并提供我帮助快速定位。

---

## 作者 / Contact Me

- GitHub: [ll0v0ll](https://github.com/ll0v0ll)
- 邮箱: [chaiyzh@126.com](mailto:chaiyzh@126.com)

有问题欢迎通过 GitHub Issues 或邮件联系。

---

## 打赏支持

如果这个工具帮到了你，欢迎请作者喝杯咖啡 ☕ 你的支持是持续维护的动力～

![打赏二维码](resource/sponsor.jpg)

> 打赏纯属自愿，无论是否打赏，本项目都免费开放使用。


# 工作原理与局限性

## 工作原理

本项目通过以下两种互补的方式获取QQ空间历史记录：

### 1. 扫码登录获取Cookie
- 调用QQ登录二维码接口，用户扫码后获取关键Cookie信息
- 基于Cookie计算鉴权参数

### 2. 数据获取（多通道策略）

**通道一：历史消息列表API**
- 调用 `feeds2_html_pav_all` 接口获取QQ空间互动消息列表
- 返回HTML格式数据，包含有互动记录的说说、留言、转发等内容
- 使用BeautifulSoup解析HTML提取关键信息

**通道二：未删除说说API**

- 调用 `emotion_cgi_msglist_v6` 接口获取当前账号所有可见的未删除说说
- 返回JSON格式数据，包含说说内容、图片、视频、评论等

**通道三：动态流增强API**

- 调用 `mobile get_feeds` 接口获取动态流 JSON（参考 QzoneArchive）
- 补充评论楼中楼回复、点赞数、视频、转发原文信息，支持断点续传与滑动窗口限速

### 3. 数据合并与导出
- 将两种方式获取的数据合并、去重
- 分类导出为Excel、HTML、图片等多种格式

## 局限性

| 局限性 | 说明 |
|--------|------|
| 互动记录依赖 | 历史消息列表只能获取有互动（评论、点赞等）记录的说说，无互动的说说无法通过此方式获取 |
| 仅自己可见内容 | 无法获取"仅自己可见"的说说（除非在消息列表中有互动记录） |
| 已删除内容 | 已删除的说说只能通过历史消息列表恢复（前提是有互动记录） |
| 时间范围 | 历史消息列表可能有时间范围限制，无法获取过于久远的数据 |

---

# 以下是作者原始内容

# GetQzonehistory(获取qq发布的历史说说)

已经打包好的单文件可以直接执行，在release页面。


<details><summary><font color="#FF0000" size="5">免责声明【必读】</font></summary>

本工具仅供学习和技术研究使用，不得用于任何商业或非法行为，否则后果自负。

本工具的作者不对本工具的安全性、完整性、可靠性、有效性、正确性或适用性做任何明示或暗示的保证，也不对本工具的使用或滥用造成的任何直接或间接的损失、责任、索赔、要求或诉讼承担任何责任。

本工具的作者保留随时修改、更新、删除或终止本工具的权利，无需事先通知或承担任何义务。

本工具的使用者应遵守相关法律法规，尊重QQ的版权和隐私，不得侵犯QQ或其他第三方的合法权益，不得从事任何违法或不道德的行为。

本工具的使用者在下载、安装、运行或使用本工具时，即表示已阅读并同意本免责声明。如有异议，请立即停止使用本工具，并删除所有相关文件。

</details>


该项目通过获取QQ空间的历史消息列表来获取该账号下发布的所有说说（当然消息列表中没有的就获取不到，例如一些仅自己可见的说说）[B站有详细食用教程](https://space.bilibili.com/1117414477)

主要实现还是通过模拟登录QQ空间来获取历史消息列表，然后进行数据分析，最后将爬取的说说存放到/resource/result目录下

由于对python编程还不是很熟悉，所以代码有很多疏漏，可以通过自己的想法来完善代码
## 目录结构

```text
project/
├── resource/                # 资源目录
│   ├── config/              # 配置目录，文件保存位置配置
│   │   └── config.ini
│   ├── result/              # 导出结果的目录，格式为"你的qq.xlsx"
│   │   ├── ...
│   │   └── ...
│   ├── temp/                # 缓存目录
│   │   ├── ...
│   │   └── ...
│   ├── user/                # 用户信息
│   │   ├── ...
│   │   └── ...
├── util/                    # 单元工具目录
│   ├── ConfigUtil.py        # 读取配置
│   ├── GetAllMomentsUtil.py # 获取未删除的所有说说
│   ├── LoginUtil.py         # 登录相关
│   ├── RequestUtil.py       # 请求数据相关
│   └── ToolsUtil.py         # 工具
├── main.py                  # 主程序入口
├── fetch_all_message.py     # 主程序入口
├── README.md                # 项目说明文件
├── requirements.txt         # 依赖项列表
└── LICENSE                  # 许可证文件
```

## 安装

#### 使用虚拟环境（推荐）
```bash
# 克隆储存库
git clone https://github.com/LibraHp/GetQzonehistory.git
# 打开目录
cd GetQzonehistory
# 创建名为 myenv 的虚拟环境
python -m venv myenv
# 激活虚拟环境。在终端或命令提示符中运行以下命令：
# 对于 Windows：
.\myenv\Scripts\activate
# 对于 macOS/Linux：
source myenv/bin/activate
# 安装依赖
pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
# 运行脚本
python main.py
```
#### 使用本机环境（不推荐）
```bash
# 克隆储存库
git clone https://github.com/LibraHp/GetQzonehistory.git
# 打开目录
cd GetQzonehistory
# 安装依赖
pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
# 运行脚本
python main.py
```


## 参考

登录方法参考自
[python-QQ空间扫码登录](https://blog.csdn.net/m0_50153253/article/details/113780595)


![Star History Chart](https://api.star-history.com/svg?repos=ll0v0ll%2Fgetqzonehistory&type=Date)

---
