<div align="center">

# ◈ TokenScope

[English](README.md) | **简体中文**

### 多个模型，多台机器，一眼看清。

轻量、本地优先的 **CC-Switch Token 用量与费用看板**<br>
面向 Codex、Claude Code 和多机使用场景。

**Python 驱动 · 无需前端构建 · MIT 开源**

[体验动态演示](https://crear12.github.io/tokenscope/) · [快速开始](#快速开始) · [统计口径与局限](#统计口径与局限)

</div>

[![TokenScope：使用模拟数据展示每日 Token 用量、费用和月度领先模型](assets/demo-preview.png)](https://crear12.github.io/tokenscope/)

*截图和在线演示均使用虚构数据。点击截图即可体验动态、可交互的看板。*

---

## 把分散的用量，放进同一张看板

不用逐台打开统计页面，就能查看各模型的 Token 用量、每日记录费用，以及每个月用量最多的模型。

| 功能 | 可以做什么 |
| :--- | :--- |
| **自适应时间粒度的用量与费用** | 用不同颜色的柱状图比较模型用量，用折线查看预估美元费用。单日按小时显示；较短日期范围根据图表宽度使用 1–12 小时时段，较长范围按日显示。会话热图使用相同时段。 |
| **月度领先模型** | 在图中直接查看当月 Token 最多的模型、用量占比及该模型当月费用。 |
| **日期与模型筛选** | 缩小日期范围，选择一个或多个模型。 |
| **会话热力图** | 纵轴为会话标题、横轴为日期，以自适应 jet 色阶显示 Token 用量。 |
| **会话详情** | 点击标题，展开 Token 构成、请求数、费用，以及按日期和模型划分的明细。 |
| **本地与多机汇总** | 读取本机统计，或通过你自己的私有 SSH 配置读取远程机器。 |
| **桌面版下载** | 从 GitHub Releases 下载 Windows x64、Linux x64 或 macOS Apple 芯片/Intel 独立版本。 |
| **可调刷新间隔** | 在网页中设置 5 秒至 10 分钟的刷新间隔，并按时钟边界执行。 |
| **静态报告导出** | 生成 PNG/SVG 图表、CSV 表格以及 JSON/Markdown 汇总。 |

实时网页仅需 **Python 标准库**，不需要 Node、数据库服务器或额外云账号。
一条命令启动，Ctrl+C 退出；不会安装常驻系统服务。运行期间仍需要保持 Python 进程开启。

## 先体验，再连接自己的数据

网页顶部的 **语言 / Language** 可一键切换中文、英文，并记住选择。
切换不会改变日期和模型筛选；模型名、来源名称、会话标题及美元费用保持原样。
可直接分享[中文版演示](https://crear12.github.io/tokenscope/?lang=zh-CN)；英文链接使用 `?lang=en`。

[在线演示](https://crear12.github.io/tokenscope/)包含 **90 天、三个虚构模型、两个虚构数据源**。
支持图表动画、重播、日期与模型筛选，并尊重系统的减少动态效果偏好。
演示不会读取你的配置、数据库、会话文件或 SSH 设置。

也可以在项目目录本地运行演示：

```sh
python -m http.server 8877 --bind 127.0.0.1 --directory docs
```

打开 `http://localhost:8877`。运行 `python build_demo.py` 可重新生成演示。
生成器只读取看板源码资源，不读取真实用量；模拟数据由确定性规则生成。
动画在演示网页中运行，GitHub README 本身不执行 JavaScript。

## 快速开始

### 桌面版应用

从 [GitHub Releases](https://github.com/Crear12/tokenscope/releases) 下载适合系统的最新版本：

- **macOS：** Apple 芯片下载 **TokenScope-macOS-arm64**，Intel 下载
  **TokenScope-macOS-x86_64**。解压后将 `TokenScope.app` 移入“应用程序”并打开。
  可用 **Machine settings…** 编辑私有配置；配置和缓存均保存在
  `~/Library/Application Support/TokenScope/`。
- **Windows x64：** 解压后双击 `launch-tokenscope.bat`。控制台会显示采集状态；
  在控制台按 Ctrl+C 停止。配置保存在 `%APPDATA%\TokenScope\config.ini`，
  缓存位于 `%LOCALAPPDATA%\TokenScope\output\`。
- **Linux x64：** 解压 `.tar.gz` 后在终端运行 `./launch-tokenscope.sh`，
  先进入解压得到的 `TokenScope-Linux-x86_64` 文件夹。按 Ctrl+C 停止。
  配置和缓存使用 XDG 目录，默认分别为
  `~/.config/tokenscope/` 和 `~/.cache/tokenscope/`。二进制在 Ubuntu 22.04 上构建，
  需要兼容的 glibc。

macOS 应用和 Windows 可执行文件均未签名。macOS 首次打开可能显示警告；
确认信任后，可在 Finder 中按住 Control 并点击应用，再选择“打开”。
Windows SmartScreen 也可能对未签名程序发出警告。macOS Developer ID 签名/公证
需要加入 Apple 付费开发者计划。公开仓库的 GitHub Actions 构建无需付费 GitHub 计划。

应用默认监听局域网，和 `python app.py` 一样没有登录验证或 TLS。仅在可信网络中使用；
局域网其他设备可以查看看板并修改共享刷新间隔。

Codex 原生日志 TPS 估算默认开启。在单会话区域取消勾选
**包含 Codex 原生日志 TPS 估算**即可关闭显示，选择保存在本浏览器。
如需停止某台机器的日志计时扫描，在私有 INI 的对应 `[source:...]`
中设置 `codex_native_tps = false`，重启并刷新。
估算使用日志中用户或工具输入至生成输出的时间，不使用整个会话耗时；
排除已完成的工具执行和轮次间空闲，但可能包含客户端调度和首 Token 等待。
仅连接会话、用量时间戳（秒）及输入、缓存、输出 Token 数完全一致且唯一的记录。
缺少边界或存在歧义时保持不可用。带 **≈** 的速率包含估算，非服务器精确计时。
开关不会改变 Token、费用或请求数；旧快照需要刷新。

需要 Python 3.9+，以及已有的 CC-Switch 数据库。

```sh
git clone https://github.com/Crear12/tokenscope.git
cd tokenscope
cp config.example.ini config.ini
python app.py
```

Windows 用户将 `cp` 换成 `copy`，或手动复制配置文件。

打开 `http://localhost:8765`。局域网内的其他设备也可以通过运行机器的 IP 和端口访问。
Tailscale 网络内的设备可使用 `http://YOUR_TAILSCALE_IPV4:8765`（主机的 `100.x.x.x` 地址）；
需确保 tailnet 访问策略允许 TCP 8765。无需公网端口转发，也无需 Tailscale Funnel。
Ctrl+C 会停止前台服务器和采集工作进程。

如需仅供本机访问：

```sh
python app.py --host 127.0.0.1 --port 8765 --interval 300 --config config.ini
```

**默认监听 `0.0.0.0`，仅适用于可信局域网或 tailnet：应用没有身份认证，也没有 TLS。**
局域网访问者可以查看用量并修改共享刷新间隔。不要直接暴露到公网。

启动后立即采集，随后按本机时钟边界刷新。网页允许设置 5–600 秒；300 秒对应每小时
的 :00、:05、:10 等时刻。正在采集时会跳过新的触发点，不叠加采集任务。
关闭浏览器不会停止采集，Ctrl+C 才会停止进程。日期和模型筛选仅影响当前浏览器。
月度领先模型及费用随筛选重新计算，支持不完整月份及并列情况。

最新成功结果会原子覆盖 `output/web.json`，失败时保留上一次结果，临时导出会被清理。
修改配置后请重启。每次刷新读取 CC-Switch 保留的全部统计，**不是增量采集**。
需要新导入的会话统计时，请先让 CC-Switch 完成同步。

## 会话用量与详情

勾选 **Show per-session usage** 显示热力图和表格，取消勾选可隐藏它们，不改变筛选条件。
表格可按 Token、预估费用或最近活动排序；初始显示 50 条，可继续展开。

会话按「机器 + 应用 + 会话身份」分组，**标题相同不会合并**。
先应用日期、模型筛选，再汇总会话。因此跨多天的会话显示的是选中日期内的用量，
不是整个生命周期用量。同一会话切换模型时仍归属于该会话。

点击表格或热力图中的标题，可查看：

- 新增输入、缓存读取、缓存写入、输出和总 Token；
- 请求数与记录的预估费用；
- 按日期和按模型划分的明细。

热力图包含全部匹配的会话，可滚动浏览。jet 线性色阶根据筛选后有记录单元格的最小、最大
用量自动调整，缺少记录的单元格留空。悬停或用键盘聚焦可以查看准确数值。

标题读取自本地保存的 Codex 名称或 Claude Code 标题。支持 Claude 内联标题事件、
每个会话的 `custom-title.json`，以及 Claude / Claude-3p 桌面元数据。
当 CC-Switch 使用请求级会话 ID 时，通过 `request_id = session:<message.id>`
精确关联 Claude 日志中的会话 ID。只使用唯一匹配，不按时间猜测；无法匹配或存在歧义时
保持原样。映射不改变 Token 与费用统计，不导出消息正文。

找不到保存的标题时显示 **Title unavailable**，不会自动生成摘要或使用消息内容代替。
可选的 `codex_home`、`claude_projects` 数据源设置用于指定元数据位置。
会话 ID 在导出前经过 SHA-256 哈希；哈希是化名标识，**不代表匿名**。

**会话标题可能包含敏感信息。** 标题会进入本地输出，且局域网访问者可以看到。
不要发布真实生成结果。公开演示仅使用虚构标题和模拟统计。
没有可用会话 ID 的请求和历史汇总无法归入具体会话，但仍计入每日总量，并显示缺失明细的覆盖情况。
旧缓存需要成功刷新一次后才包含新字段。静态导出也包含 `session_daily_usage.csv`。

## 可选：添加远程机器

在私有 `config.ini` 中添加数据源，每个数据源使用唯一显示名称，并设置：

- `transport = ssh`；
- `host`：你自己的 SSH 目标；
- `python`：远程 Python 解释器路径；
- `database`：远程 CC-Switch 数据库路径。

需要 OpenSSH 密钥或代理认证，以及支持 SQLite 的远程 Python。
仓库不附带真实远程地址或 SSH 别名。采集器在远程内存中执行只读脚本，不安装软件、
不复制整份数据库。请将所有连接信息保留在私有配置中。

## 静态图表与汇总

```sh
python -m pip install -r requirements.txt
python update.py
python update.py --hosts local
python -m unittest discover -s . -p 'test_*.py'
```

只有静态图表需要 Matplotlib。单数据源输出到 `output/local/`，多数据源输出到
`output/overall/`，固定文件名会被覆盖，不累积历史快照。
输出包括 Token/费用 PNG、SVG、机器对比图、每日及每小时 CSV、诊断用请求 TPS CSV，以及 JSON/Markdown 汇总。
看板不绘制 TPS。所有配置的数据源必须采集成功，否则会报告失败，不将不完整结果呈现为完整汇总。

## 统计口径与局限

- 总 Token = 新增输入 + 缓存读取 + 缓存写入 + 输出。缓存处理遵循 CC-Switch 输入字段语义。
  当前数据结构没有独立推理 Token 字段，不额外估算或叠加推理 Token。
- 费用是 CC-Switch 记录的美元估算，不是账单，也不包含订阅费。零费用可能意味着缺少定价，不能解释为免费。
- 所有机器有时间戳的请求均按仪表板主机的本地日期与小时统计。只有日期的历史汇总保留来源机器的本地日期；选中单日时显示在“未知小时”栏，无法精确重新划分时区。
- 导入会话和代理请求按应用、模型、Token 数及 600 秒时间窗口去重；跨机相同请求 ID 只计一次，
  数值冲突会报错。历史汇总缺少原始 ID，跨机仍可能重叠。该去重规则与标题的精确 ID 关联是不同步骤。
- Provider 元数据可从 Codex 会话首行读取，不导出会话正文；缺失元数据会明确标记未知。
- Claude Desktop 网关记录归入 Claude Code，但不代表覆盖所有 Desktop 对话。
- 诊断 TPS 使用输出 Token 除以记录的请求耗时，必要时使用延迟字段；缺少有效耗时或输出的请求没有 TPS 样本。
- 公开版本保留原始模型名称，不附带私人模型合并规则。统计准确性受上游记录质量和覆盖范围限制。

## 隐私与发布

可以提交源码、测试、说明文档、依赖列表、示例配置和公开模拟演示。
**不要提交真实配置、数据库、生成结果、私人用量截图、SSH 文件、凭据或会话日志。**

数据库读取采用字段白名单，不导出 Provider 设置、凭据或提示词。
标题解析会读取日志中的标识和保存的标题；汇总结果仍会暴露用量、来源名称及会话标题，不是匿名数据。

`.gitignore` 只是防误提交措施，不是敏感信息扫描器。每次推送前仍需检查暂存文件。
对外发布应使用独立仓库，不要带上父项目或私有 Git 历史。`docs/` 仅用于模拟演示及公开资源。

## 项目结构

```text
app.py              前台网页服务与刷新调度
collect.py          只读 CC-Switch 采集与会话关联
update.py           统计口径、汇总与静态图表
web.*               实时网页与演示共用的界面
session_usage.js    会话汇总、热力图与详情计算
config.example.ini  仅含本地默认值的公开配置模板
build_demo.py       确定性模拟数据演示生成器
docs/               GitHub Pages 静态演示
test_*.py           统计、HTTP 控制与演示测试
```

## 许可与致谢

[MIT](LICENSE) · Copyright © 2026 Crear12。

本项目是 [CC-Switch](https://github.com/farion1231/cc-switch) 的独立配套工具，
不是官方账单系统，也不是关联产品。采集依赖受支持的 CC-Switch SQLite 结构，上游结构变化时可能需要更新。
