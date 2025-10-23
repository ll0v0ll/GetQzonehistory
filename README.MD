
2025-10-22修改之后亲测可以正常工作

自己运行时遇到了问题，所以用cursor改写了更鲁棒的策略

## 修复内容

### 编码问题修复 (2025-10-22)

**问题描述：**
在使用过程中遇到编码错误：
```
获取QQ空间互动消息发生异常: 'charmap' codec can't decode byte 0x81 in position 4180: character maps to <undefined>
```

**修复内容：**
1. **main.py** - 修复了消息获取时的编码问题
   - 实现了多编码尝试机制（UTF-8、GBK、GB2312、GB18030、Big5）
   - 保留了chardet自动检测功能
   - 添加了完善的错误处理机制

2. **util/RequestUtil.py** - 修复了用户信息获取时的编码问题
   - 同样实现了多编码尝试机制
   - 针对QQ空间API响应优化了编码策略
   - 添加了错误处理和警告信息



### SSL连接问题修复 (2025-10-22)

**问题描述：**
在使用过程中遇到SSL连接错误：
```
SSLError(SSLEOFError(8, '[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol (_ssl.c:1028)'))
```

**修复内容：**
1. **util/RequestUtil.py** - 修复了SSL连接问题
   - 添加了自定义SSL上下文配置
   - 实现了自动重试机制（最多重试3次）
   - 配置了更宽松的SSL验证模式
   - 添加了完整的异常处理机制

2. **main.py** - 优化了图片下载的SSL配置
   - 为图片下载请求添加了SSL配置
   - 增加了异常处理和重试机制
   - 设置了适当的超时时间



### 并发性能优化 (2025-10-22)


**优化内容：**
1. **并发消息处理**
   - 使用 `ThreadPoolExecutor` 实现多线程并发处理
   - 最大并发数限制为8个线程，避免过载
   - 实现了线程安全的数据合并机制

2. **并发图片下载**
   - 图片下载使用独立的线程池（5个并发线程）

3. **等待时间优化**
   - 将原来的3秒等待时间减少到0.5秒
   - 请求间隔从2秒减少到0.2秒
   - 优化了批次处理逻辑

4. **进度跟踪优化**
   - 使用 `tqdm` 显示详细的进度信息
   - 分别显示数据获取和处理的进度
   - 添加了图片下载进度显示







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
│   ├── result/              # 导出结果的目录，格式为“你的qq.xlsx”
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

