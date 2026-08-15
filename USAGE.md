# MilRadio 使用说明（v1.3.0）

> 军事无线电信号接收与元数据分析系统 —— 通过全球公开的 KiwiSDR 网络接收 HF 信号，
> 自动静噪录音、频谱分析、调制识别，并把全部元数据落进 SQLite。
>
> 本文是**当前版本的完整操作手册**。版本历史见 [CHANGELOG.md](CHANGELOG.md)，
> 信号链缺陷的完整审计过程见 [reports/milradio-audit.html](reports/milradio-audit.html)。

**本版本（v1.3.0）改了什么，对使用者意味着什么** —— 如果你用过老版本，
先看 [§13 从旧版本升级](#13-从旧版本升级)，那里列了三处读数缺陷的修复
和**旧数据不能和新数据混着比**的原因。

---

## 目录

1. [系统组成](#1-系统组成)
2. [安装](#2-安装)
3. [五分钟上手](#3-五分钟上手)
4. [配置文件](#4-配置文件)
5. [命令行完整参考](#5-命令行完整参考)
6. [Web 实时监听界面](#6-web-实时监听界面)
7. [读懂分析读数](#7-读懂分析读数)
8. [录音清理](#8-录音清理)
9. [数据存储与查询](#9-数据存储与查询)
10. [HTTP API 与 WebSocket](#10-http-api-与-websocket)
11. [公开日志下载与对比](#11-公开日志下载与对比)
12. [辅助脚本与测试](#12-辅助脚本与测试)
13. [从旧版本升级](#13-从旧版本升级)
14. [常见问题](#14-常见问题)

---

## 1. 系统组成

```
KiwiSDR 公开节点  ──WebSocket──>  kiwi_client.py   解析 SND 帧 → 音频样本 + S-meter
                                       │
                                       ▼
                                  squelch.py       RMS 静噪状态机（带 pre-roll 和尾部延迟）
                                       │ 开→录音，关→收尾
                                       ▼
                                  recorder.py      写 WAV（超长自动分段）
                                       │
                                       ▼
                                  analyzer.py      带内 SNR / 占用带宽 / 包络 / 音调 → 调制类型 + 置信度
                                       │
                                       ▼
                                  db.py            SQLite：sessions / signals / analysis / nodes
                                       │
                        ┌──────────────┴──────────────┐
                        ▼                             ▼
                  web_server.py                  reporter/           HTML 报告 + 10 张图表
                  实时页面 + REST API
```

| 模块 | 职责 |
|------|------|
| `src/kiwi_client.py` | KiwiSDR WebSocket 协议、SND 帧解析、S-meter、丢帧统计 |
| `src/modes.py` | **解调滤波器与音频通带的唯一真值表**（收发两端共用，见 §13） |
| `src/squelch.py` | RMS 静噪状态机、pre-roll 环形缓冲 |
| `src/recorder.py` | WAV 录制、自动分段回调 |
| `src/analyzer.py` | 频域/时域分析、调制分类、实时频谱、频谱图 |
| `src/db.py` | SQLite 持久化、自动补列迁移、统计查询 |
| `src/receiver.py` | 命令行的扫描与持续监听编排 |
| `src/web_server.py` | aiohttp 服务、REST API、WebSocket 推送、在线静噪标定、自动重连 |
| `src/node_manager.py` | 节点连通性探测与择优 |
| `src/reporter/` | HTML 报告与图表（`theme.py` / `charts.py` / `reporter.py`） |

---

## 2. 安装

### 环境要求

- **Python 3.9+**（用到 `zoneinfo`、`asyncio.get_running_loop()` 等，推荐 3.10+）
- 能访问公网（KiwiSDR 节点是 `ws://host:8073`，走 **HTTP/WebSocket 明文端口**，
  很多企业网/校园网会拦）

### 安装依赖

```bash
git clone https://github.com/reinhardt6678-sudo/Radio.git
cd Radio

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

依赖清单（`requirements.txt`）：

| 包 | 用途 |
|----|------|
| `websockets>=12.0` | KiwiSDR WebSocket 客户端 |
| `numpy>=1.24` / `scipy>=1.10` | FFT、频谱图、信号处理 |
| `matplotlib>=3.7` | 报告图表 |
| `pyyaml>=6.0` | 配置与频率库 |
| `aiohttp>=3.9` | Web 服务器 |
| `requests>=2.31` / `beautifulsoup4>=4.12` | 公开日志抓取 |

跑测试还需要 `pytest`（不在 requirements 里）：`pip install pytest`。

### 目录会自动创建

`data/recordings/`、`data/radio_monitor.db`、`reports/` 都在首次运行时自动建，
不用手工创建。`data/` 整个目录在 `.gitignore` 里，录音和数据库不会进版本库。

### Windows 说明

`main.py` 已经处理了两件事，不用手工干预：

- 控制台 GBK 编码无法输出 Unicode → 启动时把 stdout/stderr 包成 UTF-8；
- `ProactorEventLoop` 与 aiohttp/websockets 的兼容问题 → 自动切到 `WindowsSelectorEventLoopPolicy`。

---

## 3. 五分钟上手

```bash
# ① 看哪些 KiwiSDR 节点现在能连
python main.py nodes

# ② 开 Web 界面（推荐的入口，实时频谱 + 录音回放 + 在线调静噪都在这）
python main.py web
#    浏览器打开 http://localhost:8888

# —— 或者走命令行 ——

# ③ 监听 HFGCS 日间主频
python main.py monitor -f 11175

# ④ 分析一段录音
python main.py analyze data/recordings/20260601_034521_11175.0kHz_KPH_California.wav

# ⑤ 生成 HTML 报告
python main.py report
```

**先听哪个频率？** 白天 **11175 kHz**、全天 **8992 kHz** 是 HFGCS 最活跃的，
夜间用 **4724 kHz**。这三个都是 USB。

**第一次监听的正确姿势**：默认的静噪是**自适应**的（阈值 = 实测底噪 +6 dB），
开着不用管，跑两秒测出底噪就开始判信号。想用固定阈值就让它跑 3-5 分钟，
左侧 Squelch 面板统计出**实测底噪**之后点"按底噪设定"。
静噪阈值取决于对面节点的 AGC，猜是猜不准的（详见 §6.4）。

---

## 4. 配置文件

### 4.1 `config.yaml`

#### `nodes` —— KiwiSDR 节点列表

```yaml
nodes:
  - host: "kphsdr.com"       # 节点地址
    port: 8073               # 端口（通常 8073，个别是 8074/8075）
    name: "KPH California"   # 显示名
    location: "California, USA"
    lat: 38.10               # 纬度（报告里的地图用）
    lon: -122.95             # 经度
```

去 <http://rx.kiwisdr.com> 或 <http://rx.linkfanel.net> 找公开节点。
仓库里的 `fetch_nodes.py` 可以直接从 linkfanel 拉一份**当前在线且有空闲通道**的列表。

> 选节点的经验：接收 HFGCS 优先选北美/欧洲的节点；节点通道有限，
> 高峰期常常满员，多配几个备用（Web 监听会在连续失败 3 次后自动切换，见 §6.6）。

#### `receiver` —— 接收参数

| 字段 | 默认 | 说明 |
|------|------|------|
| `max_concurrent` | 2 | 并发连接上限，每个连接占 KiwiSDR 一个通道 |
| `listen_duration` | 0 | 每频率监听时长（秒），0 = 无限 |
| `scan_dwell_time` | 30 | 扫描模式每频率停留秒数 |
| `sample_rate` | 12000 | 音频采样率，KiwiSDR 固定 12 kHz |
| `bandwidth` | 6000 | 音频带宽 Hz |

#### `squelch` —— 静噪（VOX）

```yaml
squelch:
  mode: adaptive          # adaptive = 底噪 +N dB（默认）；absolute = 固定阈值

  # --- adaptive 模式 ---
  open_margin_db: 6.0     # 开启阈值高于实测底噪多少 dB（6 dB = 底噪 × 2）
  close_margin_db: 3.0    # 关闭阈值高于实测底噪多少 dB（3 dB = 底噪 × 1.41）
  min_open_threshold: 0.005   # 开启阈值绝对下限，防止把数字静音当信号
  floor_window_seconds: 600   # 底噪统计窗口（秒）
  floor_percentile: 10        # 底噪取窗口内第几百分位

  # --- absolute 模式 ---
  open_threshold: 0.10    # 开启阈值 (RMS, 0-1)：低于它认为是底噪，不录
  close_threshold: 0.085  # 关闭阈值：必须 < open，形成滞后防止频繁开关

  tail_time: 3.0          # 信号消失后继续录几秒，防止截断尾音
  max_open_seconds: 300   # 静噪最长连续打开时间，到点强制收尾（0 = 不限）
  window_size: 1024       # RMS 分析窗口（采样点）
```

> 节点开着 AGC（`SET agc=1`），底噪会被自动放大到一个差不多恒定的电平，
> 同一个信号的绝对 RMS 在不同节点/不同时段能差好几倍。所以默认用 `adaptive`：
> 阈值跟着实测底噪走，换节点换频率都不用重调。
>
> 要用 `absolute` 就**不要照抄这个数**，先量一次底噪再定值（§6.4 或 `diagnose_rms.py`）。
> **阈值只要低于底噪，静噪打开后就再也关不掉**，信号数会一直停在 0 ——
> 现在这种情况会在日志和 Web 界面上明确告警，并按 `max_open_seconds` 强制分段。

> `max_open_seconds` 有两个作用：一段几十分钟的连续通联按这个长度分段入库
> （和 `recording.max_duration` 对齐，一段录音对应一条信号记录）；
> 阈值配错时也不会一整天憋着不出任何记录。

#### `recording` —— 录制

| 字段 | 默认 | 说明 |
|------|------|------|
| `output_dir` | `data/recordings` | WAV 输出目录（Web 回放接口只允许访问这个目录内的文件） |
| `max_duration` | 300 | 单段最长秒数，超过自动分段 |
| `bit_depth` | 16 | WAV 位深（16 或 32） |
| `pre_roll` | 2.0 | 保留信号开始**之前**的秒数，避免掐头 |

#### `analysis` —— 分析参数（v1.3.0 新增了 3 个）

```yaml
analysis:
  fft_size: 4096
  window_type: "hann"           # hann / hamming / blackman
  bandwidth_threshold_db: 20    # 旧口径带宽阈值，仅用于和历史数据对照
  noise_percentile: 20          # 带内噪声基底取第几百分位          【v1.3.0 新增】
  noise_snr_threshold_db: 3.0   # 低于此带内 SNR 直接判 NOISE       【v1.3.0 新增】
  min_confidence: 0.35          # 低于此置信度输出 UNKNOWN 而非硬猜  【v1.3.0 新增】
```

- **`noise_percentile`** 越低，噪声基底越不容易被"占满通带的强信号"抬高。
  信号密集的频率上可以调到 10-15。
- **`min_confidence`** 调高 → 更多 `UNKNOWN`，但输出的标签更可信；
  调低 → 标签更多，但会开始出现硬猜。**不要调到 0**，那就退回旧版本
  "按字典顺序输出第一个"的行为了。

#### `report` —— 报告

| 字段 | 默认 | 说明 |
|------|------|------|
| `output_dir` | `reports` | HTML 与 PNG 输出目录 |
| `recent_days` | 7 | 报告覆盖的最近天数 |
| `chart_dpi` | 150 | 图表 DPI |
| `chart_theme` | `dark` | `dark` / `light` |

### 4.2 `frequencies.yaml`

按网络分组，目前内置 5 组：`hfgcs`、`nato`、`military_air`、`digital`、`reference`。

```yaml
my_frequencies:                     # 组名（= scan --network 的匹配对象）
  description: "我的自定义监听频率"
  frequencies:
    - freq: 7850.0                  # kHz
      mode: "USB"                   # USB / LSB / AM / CW / CWN / NFM
      description: "某个感兴趣的频率"
      active_hours: "全天"          # 仅作说明，程序不据此过滤
      priority: high                # high / medium / low
```

- `monitor` 不带 `-f` 时，**默认只监听 `priority: high` 的频率**。
- `scan --network` 做的是**子串匹配**（`--network hf` 也能命中 `hfgcs`）。
- `mode` 决定接收机的解调滤波器**和**分析通带 —— 这两个从 v1.3.0 起
  共用 `src/modes.py` 里的同一张表，不会再各写各的。

---

## 5. 命令行完整参考

### 全局参数

```bash
python main.py [-v] [-c CONFIG] <子命令> [子命令参数]
```

| 参数 | 说明 |
|------|------|
| `-v, --verbose` | DEBUG 级日志（帧解析、握手细节都会打出来） |
| `-c, --config` | 指定配置文件，默认 `config.yaml` |

### 5.1 `nodes` —— 节点连通性检查

```bash
python main.py nodes
python main.py nodes --timeout 20      # 网络慢时加长超时（默认 10s）
```

```
==============================================================================
 状态  名称                   地址                              延迟  位置
------------------------------------------------------------------------------
 +   KPH California       kphsdr.com:8073                   312ms California, USA
 -   SK3W Sweden          kiwisdr.sk3w.se:8073                N/A Sweden
==============================================================================
总计: 1/8 节点可用
```

结果会写进 `nodes` 表（含平均延迟、累计连接/失败次数），Web 界面的节点下拉框直接用它。

### 5.2 `scan` —— 频率扫描

在每个频率停留一段时间，统计有没有活动，用来快速筛"今天哪个频率有戏"。

```bash
python main.py scan                        # 扫描 frequencies.yaml 里所有频率
python main.py scan --network hfgcs        # 只扫 HFGCS
python main.py scan --priority high        # 只扫高优先级
python main.py scan --dwell 10             # 每频率停 10 秒（默认取 config 的 30）
python main.py scan --node "KPH"           # 指定节点（按名字子串匹配）
python main.py scan --freq-file my.yaml    # 换一个频率库
```

### 5.3 `monitor` —— 持续监听（核心）

```bash
# 基本
python main.py monitor -f 11175            # 单频率
python main.py monitor -f 11175 8992 4724  # 多频率轮询
python main.py monitor                     # 不给 -f 则监听所有 high 优先级频率

# 模式与节点
python main.py monitor -f 5000 -m AM       # USB / LSB / AM / CW
python main.py monitor -f 11175 --node "KPH California"

# 时长
python main.py monitor -f 11175 --duration 300   # 300 秒后停，0/省略 = 无限

# 调试
python main.py -v monitor -f 11175
```

`-f` 给的频率如果不在 `frequencies.yaml` 里，会自动建一个临时目标
（模式取 `-m`，默认 USB）。

**监听时发生了什么：**

1. 连接 KiwiSDR 节点，按 `mode` 设置解调滤波器（`src/modes.py` 的 `DEMOD_FILTERS`）
2. 逐帧解析 SND：`tag(3) + flags(1) + seq(4, 小端) + smeter(2, 大端)` + 音频样本
3. 静噪检测器算每块 RMS：超过开启阈值 → 开录（带 2 秒 pre-roll）。
   `mode: adaptive` 时阈值 = 实测底噪 +6 dB，底噪还没测出来的头两秒不判信号
4. RMS 掉到关闭阈值以下，再等 `tail_time` 秒 → 收尾，写 WAV。
   连续打开超过 `max_open_seconds` 会强制收尾分段；断线/停止监听时
   正在录的那段也会正常收尾入库，不会只剩一个没有记录的 WAV
5. 对整段音频做频域+时域分析，按解调模式取通带算 SNR 与调制类型
6. `signals` + `analysis` 两张表各写一行
7. `Ctrl+C` 停止（Windows 上 `Ctrl+Break` 同样有效）

### 5.4 `analyze` —— 分析录音文件

```bash
python main.py analyze data/recordings/xxx.wav
python main.py analyze data/recordings/xxx.wav -m AM   # 录音时不是 USB 就要指定
```

`-m` 可选 `USB / LSB / AM / CW / CWN`，**默认 USB**。它决定分析通带，
进而决定带内 SNR 和调制判定 —— 用错模式会让 SNR 偏低、调制判定失准。

输出：

```
==================================================
  [RESULT] 信号分析结果
==================================================
  文件: 20260601_034521_11175.0kHz_KPH_California.wav
  时长: 12.50s
  采样率: 12000 Hz
  样本数: 150000
--------------------------------------------------
  [TIME-DOMAIN] 时域分析:
     RMS 能量: 0.034521
     峰值幅度: 0.287654
     峰均比: 18.4 dB
     总能量: 178.7654
--------------------------------------------------
  [FREQ-DOMAIN] 频域分析 (通带 300-3000 Hz, 模式 USB):
     峰值频率: 1200.0 Hz
     频谱质心: 1180.4 Hz
     占用带宽: 1620.0 Hz (旧口径 -20dB: 2698.0 Hz)
     带内 SNR: 22.4 dB (噪声基底 -71.2 dB)
     频谱平坦度: 0.089000
--------------------------------------------------
  [FEATURES] 判别特征:
     包络音节率: 4.2 Hz (深度 0.61)
     键控率: 4.2 Hz
     音调数: 6 (间距 320 Hz, 纯度 0.29)
--------------------------------------------------
  [MODULATION] 估计调制类型: USB_VOICE (上边带语音)
     置信度: 0.75
     各类别得分: VOICE=1.00, PSK=0.41, CW=0.00, CARRIER=0.00, FSK=0.00
==================================================

  [PEAKS] 频谱主要峰值:
     1. 1200.0 Hz (-42.3 dB)
     ...
```

每一项怎么读见 [§7](#7-读懂分析读数)。

### 5.5 `report` —— 生成 HTML 报告

```bash
python main.py report
```

在 `reports/` 下生成 `report_YYYYMMDD_HHMM.html`，覆盖最近
`report.recent_days` 天（默认 7），包含：

- 频率活跃度排名、24 小时活动热力图、信号强度分布、信号时间线散点
- 调制类型分布、SNR/带宽散点、时长分布、网络分布、综合仪表盘
- 频率统计表与最近信号列表

图表是同目录的 `chart_*.png`，HTML 用相对路径引用 —— **别单独挪走 PNG**，
否则已生成的报告会变成坏图（`.gitignore` 里专门留了说明）。

### 5.6 `web` —— 实时监听界面

```bash
python main.py web                     # 默认 0.0.0.0:8888
python main.py web --port 9090
python main.py web --host 127.0.0.1    # 只监听本机
python main.py web --freq-file my.yaml
```

> **`--host` 默认是 `0.0.0.0`**，也就是同一局域网内任何人都能打开这个页面并
> 控制你的监听、回放你的录音。服务端没有鉴权。放在不可信网络里请显式加
> `--host 127.0.0.1`，或用反向代理挡一层。

详见 [§6](#6-web-实时监听界面)。

### 5.7 `clean` —— 清理垃圾录音

```bash
python main.py clean                   # 预览（默认，不删任何东西）
python main.py clean --delete          # 实际删除
python main.py clean --delete --clean-db   # 同时删数据库里对应的信号记录
python main.py clean --min-duration 3 --min-snr 8
python main.py clean -m AM             # 录音是 AM 的话要指定，决定分析通带
```

详见 [§8](#8-录音清理)。

---

## 6. Web 实时监听界面

```bash
python main.py web
# → http://localhost:8888
```

### 6.1 页面布局

| 区域 | 内容 |
|------|------|
| **左侧 Control Panel** | 频率快捷键（按网络分组）· 监听参数（频率/模式/节点）· **Squelch 在线调整** · 节点检查与状态 |
| **中央** | 五块仪表（Frequency / S-Meter / 带内 SNR / RMS Level / Signals）· **频谱 + 瀑布图** · 实时 RMS 曲线 |
| **右侧** | 三个标签页：**实时**（信号日志 + 会话统计）· **录音**（浏览与回放）· **统计** |

### 6.2 操作流程

1. 点 **Check Node Availability** 探测节点（结果同时写进数据库）
2. 左侧选频率 —— 快捷按钮按网络分组，默认只列 `priority: high` 的，勾"全部"展开其余；
   **最近 30 天有过信号的频率会被高亮并标出条数**（悬停看详情）；
   也可以直接在输入框敲任意 100-30000 kHz
3. 选模式（USB/LSB/AM/CW）和节点（`Auto` = 自动挑可用的）
4. **Start Monitoring**
5. 中央实时刷新频谱、瀑布图、RMS 曲线；仪表给出真实 S-meter 与带内 SNR
6. 检测到信号 → 右侧"实时"标签自动追加一条，可以立刻 ▶ 回放 / ▤ 看频谱图
7. **Stop Monitoring** 停止

### 6.3 频谱与瀑布图

后端每积够 2048 个样本（约 170 ms）算一列 128 格频谱推给前端，显示上限 4000 Hz。
用的是 `analyzer.live_spectrum()`（**分段平均**，不是单次 FFT —— 单次 FFT 会让
实时 SNR 虚高）。

上半是频谱曲线，下半是向下滚动的瀑布图（最新在最上面），蓝色阴影标出当前模式的
解调通带（USB 是 300-3000 Hz）。右上角实时显示峰值频率。

> 瀑布图是这个页面信息密度最高的控件：
> **稳定的水平亮线** = 未调制载波；**规律的短横** = CW 键控；
> **占满通带的连续色块** = 语音或数据；**从上到下扫过的斜线** = 扫频干扰。

### 6.4 静噪在线标定（不用停下监听）

左侧 **Squelch** 区：

- **自适应**复选框 —— 勾上就是 `mode: adaptive`，阈值 = 实测底噪 +6 dB / +3 dB，
  跟着底噪自己走；取消勾选才用下面两个滑块的固定值
- 两个滑块直接改开启/关闭阈值，点**应用**后**立刻作用到正在跑的检测器**，
  不用停下重来（后端 `POST /api/squelch` 直接改 `SquelchDetector` 实例）
- **实测底噪 (RMS)** —— 检测器持续统计 RMS 的第 10 百分位，窗口 10 分钟。
  静噪打开期间也照常统计（低百分位本来就不会被间歇性的信号抬起来），
  刚启动时样本不够会显示 `采样中...`
- **建议阈值 (底噪 +6/+3 dB)** —— 就是 `底噪 × 2` 和 `底噪 × 1.41`
- **生效阈值** —— 当前真正在用的开/关阈值（自适应模式下它会随底噪变）
- **静噪状态** —— `开 (录制中)` / `关`
- **按底噪设定** —— 一键切到固定模式并把阈值设成上面的建议值

> ⚠ 如果关闭阈值低于实测底噪，面板顶部会出现黄色告警：这种配置下静噪
> 打开后永远关不掉，**一整天都不会产生任何信号记录**（信号只在静噪关闭
> 的那一刻才落库）。看到告警就点"按底噪设定"或勾上"自适应"。

约束（服务端会校验并返回 400）：阈值必须在 0-1 之间，`close < open`（否则没有滞后），
`tail_time` 在 0-60 秒，`mode` 只能是 `absolute` / `adaptive`。

RMS 曲线上同时画三条线：**噪声基底线**、**静噪开启线**、**静噪关闭线**。
三条线摆在一起就能一眼看出阈值卡得对不对 —— 开启线应该在噪声基底上方约 6 dB。

> 这是对历史上 `0.65 → 0.15 → 0.10` 三次手调阈值的一次性了结。
> 那三次调的其实都是节点的 AGC 差异，不是"正确的阈值"。

### 6.5 录音浏览与回放

"录音"标签页从数据库读信号 + 分析记录，每条显示：
时长、带内 SNR、调制类型 + 置信度、占用带宽、S-meter、文件大小。

- **▶** 浏览器内直接播放（`GET /api/recordings/{id}/audio`）
- **▤** 展开这段录音的**频谱图缩略图** + 包络波形
  （`GET /api/recordings/{id}/spectrogram`，动态曝光按这段录音自身的
  25/99.7 百分位定标，弱信号也看得清）
- 顶部可按时间范围（24h / 7天 / 30天 / 全部）、最低 SNR（6/12/20 dB）筛选，
  或勾"只看有录音"

> 回放接口会把数据库里的路径 `resolve()` 后校验是否落在配置的录音目录内，
> 目录外的路径一律 404 并记警告；列表接口也不会把服务器绝对路径吐给页面。

### 6.6 自动重连与节点切换

Web 监听内置了断线恢复，不需要人守着：

- 断开后**指数退避**重连：5s → 10s → 20s → 40s → 60s 封顶
- 同一节点**连续失败 3 次**自动换一个可用节点继续监听
- 重连、切换节点、等待秒数都会通过 WebSocket 推到页面上
- 会话统计里能看到 **Reconnects** 和 **丢帧** 计数

丢帧数来自 `kiwi_client` 按 SND 帧 seq 跳变的统计 —— 数字持续上涨说明这个节点
或你的网络在丢包，换个节点通常就好了。

### 6.7 统计标签页

- **频率活跃度** —— 各频率信号数与总时长
- **调制类型分布** —— 每类的数量与**平均置信度**（平均置信度低说明这批判定不太可信）
- **带内 SNR 分布** —— 分档直方图
- **24 小时活动热条** —— 哪个 UTC 时段最活跃

---

## 7. 读懂分析读数

### 7.1 核心指标

| 指标 | 字段 | 怎么读 |
|------|------|--------|
| **带内 SNR** | `snr_db` | 只在解调通带内估算：噪声基底取带内功率的低分位数，`SNR = 扣除底噪的信号功率 / 底噪总功率`。清晰语音 **> 20 dB**，可辨认的弱信号 6-15 dB，**纯底噪是负值** |
| **噪声基底** | `noise_floor_db` | 通带内第 `noise_percentile` 百分位的功率。它是 SNR 的分母，也是判断"这个节点安不安静"的依据 |
| **占用带宽** | `bandwidth_hz` | 扣除底噪后包含 **90% 能量**的频率跨度。纯音几十 Hz，CW 百来 Hz，语音 1-2.5 kHz |
| **旧口径带宽** | `bandwidth_20db_hz` | 峰值 -20 dB 的跨度，**只为和历史数据对照保留**，在固定通带下几乎没有区分度 |
| **峰均比** | `crest_factor_db` | 语音 8-22 dB；恒包络数据信号偏低；脉冲干扰 > 15 dB 且很短 |
| **频谱平坦度** | `spectral_flatness` | 越接近 1 越像白噪声 |
| **包络音节率 / 深度** | `envelope_rate_hz` / `envelope_depth` | 语音音节率集中在 **2-8 Hz** 且深度明显；FSK/PSK 是恒包络（深度极低）；莫尔斯键控深度接近 100% |
| **键控率** | `keying_rate_hz` | CW 的通断速率 |
| **音调数 / 间距 / 纯度** | `tone_count` / `tone_spacing_hz` / `tone_purity` | 2-4 个稳定间距的音调 → 像 FSK；一个高纯度音调 → 像未调载波；数出一大把 → 是连续谱 |
| **置信度** | `modulation_confidence` | 同时看绝对得分和领先第二名的幅度。**低于 `min_confidence`（默认 0.35）输出 `UNKNOWN`** |

### 7.2 调制类型

| 标签 | 含义 | 主要证据 |
|------|------|----------|
| `USB_VOICE` / `LSB_VOICE` / `AM_VOICE` | 语音 | 2-8 Hz 音节包络 + 明显起伏 + 占据大半话音带。**边带类型由接收机的解调模式决定，不从音频里猜** |
| `CW` | 莫尔斯 | 极窄 + 深度键控包络 |
| `CARRIER` | 未调制载波 | 极窄 + 恒定 + 能量集中在单个高纯度音调 |
| `FSK` | 移频键控 | 少数几个稳定间距的音调 + 恒包络 |
| `PSK` | 相移键控 / 数据 | 恒包络 + 连续谱 + 没有音节率 |
| `NOISE` | 只有底噪 | 带内 SNR < `noise_snr_threshold_db`（默认 3 dB），此时其余特征都没有意义 |
| `UNKNOWN` | 证据不足 | 置信度低于 `min_confidence`。**这是一个正常输出，不是错误** |

分类器除了加权打分还有一层**否决条件**：比如一个 20 Hz 宽的信号，
无论其它特征多好看都不可能是宽带数据波形；FSK 是恒包络的，包络深度大就否掉。
这一层是旧版本没有的，也是"什么都能并列第一"的根因（见 §13）。

### 7.3 S-meter

`s_meter_dbm` 是 KiwiSDR 报告的接收电平，换算式 `0.1 × (raw & 0x0FFF) − 127`。
Web 页面同时显示 dBm、S 级和电平条。

典型值：S9 ≈ -73 dBm；HF 底噪常在 -110 ~ -95 dBm；强 EAM 播发能到 -70 dBm 上下。

> **v1.3.0 之前这一列是坏的**（记的是帧计数器的高位字节），见 §13。

---

## 8. 录音清理

静噪会被短促噪声爆发、脉冲干扰（"滴"一声）、底噪波动触发，跑一夜下来
垃圾录音可能占一半以上。`clean` 就是用来批量清掉它们的。

### 8.1 判定标准

对每个 WAV 做完整的频域+时域分析，满足**任一**条件即判为垃圾：

| 条件 | 默认阈值 | 说明 |
|------|----------|------|
| 时长极短 | < 2.0 s | 噪声的短促触发 |
| SNR 极低 | < 5.0 dB | 几乎没有有效信号 |
| 频谱平坦 | > 0.5 | 接近白噪声 |
| 调制类型 | `NOISE` | 分析器综合判定 |
| 脉冲干扰 | 时长 < 3 s 且 峰均比 > 15 dB | "滴"一声那种 |
| 近乎静音 | RMS < 0.005 | 录下来了但基本是空的 |

### 8.2 用法

```bash
# 第一步：先预览（默认就是预览，不会删任何东西）
python main.py clean

# 第二步：确认分类合理后再删
python main.py clean --delete

# 连带清掉数据库里对应的信号记录
python main.py clean --delete --clean-db

# 调阈值
python main.py clean --min-duration 3 --min-snr 8

# 录音不是 USB 的话要指定模式（决定分析通带）
python main.py clean -m AM

# 看每个文件的详细判定过程
python main.py -v clean
```

独立脚本 `clean_recordings.py` 暴露了更多参数：

```bash
python clean_recordings.py --help

python clean_recordings.py \
    --recordings-dir /path/to/recordings \
    --min-duration 3 --min-snr 8 --max-flatness 0.4 \
    --impulse-max-dur 3.0 --impulse-min-crest 15.0 \
    --mode USB --delete
```

### 8.3 输出

```
[CLEAN] 录音清理 (👁 预览模式)
  录音目录: data/recordings
  解调模式: USB
  最短时长: 2.0s | 最低 SNR: 5.0 dB

[SCAN] 正在分析 342 个录音文件...

======================================================================
  录音清理分析报告
======================================================================
  总文件数:     342
  有效录音:     156 (45.2 MB)
  垃圾录音:     186 (12.8 MB)
  垃圾占比:     54.4%
----------------------------------------------------------------------
  垃圾录音原因分布:
    时长极短 (噪声触发): 120 个
    信噪比极低 (纯噪声): 98 个
    脉冲干扰 (如滴一声): 34 个
    分析器判定为 NOISE: 45 个
======================================================================

[TIP] 预览模式，加 --delete 参数实际删除:
       python main.py clean --delete
```

> 删除不可撤销。**永远先跑一次不带 `--delete` 的**，看看分类结果合不合理
> 再动手。阈值设得太激进会把弱但真实的信号一起删掉。

---

## 9. 数据存储与查询

### 9.1 目录结构

```
Radio/
├── data/                            <-- 整个目录在 .gitignore 里
│   ├── recordings/                  <-- 录音 WAV
│   │   └── 20260601_034521_11175.0kHz_KPH_California.wav
│   ├── radio_monitor.db             <-- SQLite（永久保存，每次运行追加）
│   └── public_logs/                 <-- fetch_public_logs.py 下载的公开日志
│       ├── eam_watch/
│       ├── shortwave_archive/
│       └── comparison/
└── reports/
    ├── report_20260601_0400.html
    ├── chart_*.png
    └── milradio-audit.html          <-- 信号链审计报告
```

录音文件名格式：`YYYYMMDD_HHMMSS_<频率>kHz_<节点名>.wav`

### 9.2 数据库表

| 表 | 内容 | 关键字段 |
|----|------|----------|
| `sessions` | 每次监听会话 | `start_time`, `end_time`, `node_host`, `node_name`, `frequencies`, `status` |
| `signals` | 每个检测到的信号 | `timestamp`, `frequency_khz`, `mode`, `duration_seconds`, `peak_rms`, `avg_rms`, `s_meter_dbm`, `recording_path` |
| `analysis` | 信号的频谱分析 | `snr_db`, `bandwidth_hz`, `estimated_modulation`, `spectral_centroid_hz`, `spectral_flatness`, `crest_factor_db`, `fft_peak_magnitudes` |
| `nodes` | 节点状态历史 | `host`, `port`, `is_available`, `avg_latency_ms`, `total_connections`, `total_failures` |

**v1.3.0 给 `analysis` 加了 6 列**（老数据库自动补列，不丢历史数据）：

| 列 | 含义 |
|----|------|
| `modulation_confidence` | 调制判定置信度 0-1 |
| `demod_mode` | 分析时用的解调模式（决定通带） |
| `noise_floor_db` | 带内噪声基底 |
| `envelope_rate_hz` | 包络音节率 |
| `envelope_depth` | 包络调制深度 |
| `tone_count` | 检出的音调数 |

索引：`signals(timestamp)`、`signals(frequency_khz)`、`signals(session_id)`、
`analysis(signal_id)`。

> **所有数据永久保存。** 每次运行都追加到同一个库，历史不会被覆盖。

### 9.3 直接查 SQLite

```python
import sqlite3

conn = sqlite3.connect("data/radio_monitor.db")
conn.row_factory = sqlite3.Row

# 最近 10 个信号
for row in conn.execute("""
    SELECT timestamp, frequency_khz, mode, duration_seconds, peak_rms, s_meter_dbm, node_name
    FROM signals ORDER BY timestamp DESC LIMIT 10
"""):
    print(f"{row['timestamp'][:19]} | {row['frequency_khz']:>8.1f} kHz | "
          f"{row['duration_seconds']:.1f}s | {row['s_meter_dbm']:.0f} dBm | {row['node_name']}")

# 只看高置信度的判定（v1.3.0 之后才有意义）
for row in conn.execute("""
    SELECT s.timestamp, s.frequency_khz, a.estimated_modulation,
           a.modulation_confidence, a.snr_db, a.bandwidth_hz
    FROM analysis a JOIN signals s ON s.id = a.signal_id
    WHERE a.modulation_confidence >= 0.6 AND a.snr_db > 10
    ORDER BY a.snr_db DESC LIMIT 20
"""):
    print(f"{row['timestamp'][:19]} {row['frequency_khz']:>8.1f} kHz "
          f"{row['estimated_modulation']:<12} conf={row['modulation_confidence']:.2f} "
          f"SNR={row['snr_db']:.1f} dB BW={row['bandwidth_hz']:.0f} Hz")

conn.close()
```

现成脚本：`example_query.py`（查询示例）、`example_batch_analyze.py`（批量分析示例）。

### 9.4 用 `src` 模块

```python
from src.db import Database
from src.analyzer import SignalAnalyzer
import yaml

config = yaml.safe_load(open("config.yaml", encoding="utf-8"))
db = Database("data/radio_monitor.db")

# 用工厂方法建分析器，保证参数和监听时完全一致
analyzer = SignalAnalyzer.from_config(config)

result = analyzer.analyze_file("data/recordings/xxx.wav", mode="USB")
print(result["estimated_modulation"], result["modulation_confidence"], result["snr_db"])

# 常用查询
db.get_signals_with_analysis(days=7, limit=100, min_snr_db=10, with_recording=True)
db.get_frequency_stats(days=30)
db.get_modulation_stats(days=30)     # 带平均置信度
db.get_snr_distribution(days=30)
db.get_daily_activity(days=14)
db.get_hourly_activity(days=7)

db.close()
```

`Database` 也支持 `with` 语句：`with Database(path) as db: ...`

---

## 10. HTTP API 与 WebSocket

`python main.py web` 起的服务同时提供页面、REST API 和 WebSocket 推送。
**没有鉴权** —— 见 §5.6 的提醒。

### 10.1 REST API

| 方法 | 路径 | 参数 | 说明 |
|------|------|------|------|
| `GET` | `/api/status` | — | 当前监听状态、RMS/S-meter 历史、带内 SNR、噪声基底、丢帧、重连次数、最近一列频谱 |
| `GET` | `/api/nodes` | — | 节点列表 + 数据库里的可用性与延迟 |
| `POST` | `/api/nodes/check` | — | 现场探测所有节点（15s 超时） |
| `GET` | `/api/frequencies` | — | `frequencies.yaml` 展开后的频率列表 |
| `GET` | `/api/signals` | `days`(1-365, 默认7) `limit`(1-1000, 默认100) | 最近信号记录 |
| `GET` | `/api/recordings` | `days` `limit`(≤500) `frequency` `min_snr` `with_recording` | 信号 + 分析记录，供浏览回放 |
| `GET` | `/api/recordings/{id}/audio` | — | 回放 WAV（路径限制在录音目录内） |
| `GET` | `/api/recordings/{id}/spectrogram` | `bins`(16-256) `cols`(16-600) | 频谱图矩阵 + 包络 |
| `GET` | `/api/sessions` | `limit`(1-200) | 最近监听会话 |
| `GET` | `/api/stats` | `days` | 频率活跃度 / 24h 活动 / 调制分布 / SNR 分布 / 按天活动 |
| `GET` | `/api/squelch` | — | 当前模式/阈值 + 实测底噪 + 建议值 + 生效阈值 + 静噪开关状态 |
| `POST` | `/api/squelch` | `mode` `open_threshold` `close_threshold` `tail_time` `open_margin_db` `close_margin_db` | 在线调整，立刻生效；阈值低于底噪时返回 `warning` |
| `POST` | `/api/monitor/start` | `frequency` `mode` `node_host` | 开始监听 |
| `POST` | `/api/monitor/stop` | — | 停止监听 |

```bash
# 看一眼当前状态
curl -s localhost:8888/api/status | python -m json.tool

# 按底噪把阈值调到 0.06 / 0.05
curl -s -X POST localhost:8888/api/squelch \
     -H 'Content-Type: application/json' \
     -d '{"open_threshold":0.06,"close_threshold":0.05}'

# 或者直接交给自适应（阈值跟着实测底噪走）
curl -s -X POST localhost:8888/api/squelch \
     -H 'Content-Type: application/json' -d '{"mode":"adaptive"}'

# 启动监听
curl -s -X POST localhost:8888/api/monitor/start \
     -H 'Content-Type: application/json' \
     -d '{"frequency":11175,"mode":"USB"}'

# 拉最近 7 天 SNR ≥ 12 dB 且有录音的记录
curl -s 'localhost:8888/api/recordings?days=7&min_snr=12&with_recording=1'
```

校验规则：频率 100-30000 kHz；模式仅 `USB/LSB/AM/CW/CWN`；
静噪阈值 0-1 且 `close < open`；`tail_time` 0-60 秒；`mode` 仅 `absolute/adaptive`；
余量 `*_margin_db` 0-40 dB。不合法一律 400 + 中文错误信息。

### 10.2 WebSocket `/ws`

连上先收一条 `init`（当前状态 + 最近 20 条信号 + 最近 200 点 RMS 历史 +
通带 + 频谱格数），之后持续收推送：

| `type` | 时机 | 主要字段 |
|--------|------|----------|
| `init` | 连接建立 | `monitoring` `frequency` `mode` `passband` `spectrum_bins` `recent_signals` `rms_history` |
| `spectrum` | 每约 170 ms | `bins`(128) `f_max` `snr_db` `noise_floor_db` `peak_frequency_hz` `signal_active` |
| `realtime` | 每 5 个音频块 | `rms` `smeter` `snr_db` `noise_floor_db` `rms_noise_floor` `dropped_frames` `total_signals` `elapsed` `reconnects` `squelch_mode` `effective_open` `effective_close` `threshold_below_floor` `signal_seconds` |
| `signal_detected` | 静噪关闭、录音落库后 | `signal_id` `duration` `peak_rms` `smeter_dbm` `snr_db` `bandwidth_hz` `modulation` `modulation_confidence` `has_audio` |
| `monitor_started` / `monitor_stopped` | 开始/停止 | `frequency` `mode` `node` `passband` / `total_reconnects` |
| `reconnecting` / `reconnect_wait` / `reconnected` / `node_switch` | 断线恢复 | `message` `attempt` `seconds` `node` |
| `squelch_updated` | 阈值/模式被改 | `open_threshold` `close_threshold` `tail_time` `mode` `effective_open` `effective_close` `warning` |
| `error` | 监听异常 | `message` |

客户端可以发 `{"action":"ping"}`，服务端回 `{"type":"pong"}`。

---

## 11. 公开日志下载与对比

从公开来源抓 HFGCS/EAM 监听日志，和你自己的数据对比，用来验证监听系统是不是真的在工作。

### 数据来源

| 来源 | 内容 |
|------|------|
| [EAM.watch](https://eam.watch) | EAM / Skyking 消息：发送者、时间戳、加密内容、录音链接 |
| [Shortwave Archive](https://shortwavearchive.com) | 爱好者上传的短波录音存档 |

### 用法

```bash
python fetch_public_logs.py                          # 所有来源，最近 7 天
python fetch_public_logs.py --source eam             # 只抓 EAM.watch
python fetch_public_logs.py --source archive         # 只抓短波存档
python fetch_public_logs.py --days 30
python fetch_public_logs.py --compare                # 抓完顺便和本地数据对比
python fetch_public_logs.py --source archive --download-audio   # 连音频一起下（最多 10 个）
```

### 输出

```
data/public_logs/
├── eam_watch/
│   ├── messages_20260601.json
│   └── messages_20260601.csv        # Excel 可直接打开
├── shortwave_archive/
│   ├── recordings_20260601.json
│   ├── recordings_20260601.csv
│   └── audio/
└── comparison/
    ├── comparison_20260601.html
    └── comparison_20260601.json
```

### `--compare` 做了什么

1. 读本地 `data/radio_monitor.db` 的信号记录
2. 读已下载的公开日志
3. 按**时间窗口 ±5 分钟**和**频率**匹配
4. 生成 HTML 报告：

| 维度 | 说明 |
|------|------|
| 匹配成功 | 两边都有 → 你的系统工作正常 |
| 仅本地检测 | 你捕到了、公开日志没有 → 可能是你独有的发现，也可能是误触发 |
| 仅公开记录 | 公开日志有、你没捕到 → **漏检**，通常意味着静噪阈值太高或节点选得不好 |
| 频率活跃度 | 本地 vs 公开的对比 |

EAM 消息示例：

```json
{
  "type": "ALLSTATIONS",
  "sender": "SHORTHAND",
  "time": "2026-06-01 15:07:00",
  "message": "CBVN2JRPNDH5D25YZZ6KUHJULNOOMM",
  "recordings": [{"link": "https://eamwatch-production.s3.amazonaws.com/recordings/..."}]
}
```

> EAM 内容是一次性密码本加密的，**解不开也不用试**。这里能做的只有对比
> 时间戳和频率来验证监听覆盖率。

另有 `compare_eam.py`：直接下载 EAM.watch 的录音、算它们的 RMS，
和你 `config.yaml` 里的静噪阈值对比 —— 用来回答"为什么我没检测到这条 EAM"。

---

## 12. 辅助脚本与测试

### 12.1 脚本清单

| 脚本 | 用途 |
|------|------|
| `fetch_nodes.py` | 从 rx.linkfanel.net 拉当前在线、有空闲通道的 KiwiSDR 节点 |
| `fetch_public_logs.py` | 下载公开监听日志并对比（§11） |
| `compare_eam.py` | 下载 EAM.watch 录音，算 RMS 对比本地静噪阈值 |
| `download_eam_today.py` | 下载当天的 EAM 录音 |
| `clean_recordings.py` | 录音清理（§8），比 `main.py clean` 多几个阈值参数 |
| `analyze_recordings.py` | 批量分析指定录音，判断是信号还是噪音 |
| `diagnose_network.py` | 逐层排查连接问题：DNS → TCP → WebSocket 握手 → KiwiSDR 协议 |
| `diagnose_rms.py` | 完整握手后实测音频 RMS，用来定静噪阈值 |
| `inspect_frames.py` | 打印原始 SND 帧结构，调协议时用 |
| `check_signals.py` / `check_recent.py` / `check_detail.py` | 快速查库：会话、最近信号、单条明细 |
| `example_query.py` / `example_batch_analyze.py` | 数据查询与批量分析示例 |

> **连不上节点时的排查顺序**：`python main.py nodes` → 全灭的话
> `python diagnose_network.py` 看卡在哪一层 → DNS/TCP 就通不过说明是网络环境
> （防火墙拦 8073 端口很常见）。

### 12.2 测试

```bash
pip install pytest
python -m pytest tests -q
```

```
......................................................................   [100%]
70 passed in 2.26s
```

| 文件 | 数量 | 覆盖 |
|------|------|------|
| `tests/test_analyzer.py` | 22 | 带内 SNR、阻带不参与计算、通带随模式变化、各调制类型分类、带宽区分度、并列输出 UNKNOWN |
| `tests/test_web_api.py` | 19 | 录音浏览/回放/频谱图、**路径穿越防护**、静噪调整校验 |
| `tests/test_kiwi_client.py` | 13 | 构造 SND 帧断言样本数/seq/S-meter、多帧拼接后**不再出现 23.4 Hz 帧率谐波** |
| `tests/test_db.py` | 10 | 会话 CRUD、信号记录、分析保存、自动补列、统计查询 |
| `tests/test_squelch.py` | 6 | 状态机、deque 缓冲、pre-roll、工厂方法 |

---

## 13. 从旧版本升级

v1.3.0 修掉了[信号链审计](reports/milradio-audit.html)里记的三处读数缺陷。
这三处都是**读数错误**，不是崩溃 —— 也就是说旧版本一直在安静地给你错数据。

### 13.1 缺陷 01：SND 帧解析偏移 2 字节

旧代码把 SND 帧 body 当成 `flags(1) + seq(2, 大端) + smeter(2)`，
而 KiwiSDR 的真实布局是 `flags(1) + seq(4, 小端) + smeter(2, 大端)`。整帧偏移 2 字节：

- **S-meter 列记的是帧计数器的高位字节** —— 744 条记录只有 16 个取值，
  全是 256 的整数倍，其中 676 条恒为 -160 dBm；
- 音频起点提前 2 字节，**每帧多注入 1 个假样本** —— 在全部 747 段录音里
  产生 `12000/512 = 23.4375 Hz` 的帧率谐波嗡声。

同时修了 dBm 换算（`raw/65535×150−160` → `0.1 × (raw & 0x0FFF) − 127`）、
帧类型判断（比完整 3 字节 tag，`STA` 之类的帧不会再被当成音频）、
并按 seq 跳变统计丢帧。

### 13.2 缺陷 02：调制识别由字典顺序决定

旧打分器给带宽 2 分、平坦度 1 分、峰均比 1 分。但在固定 300-3000 Hz 通带下，
带宽量的其实是**接收机的滤波器**，三种"不同"调制的平均带宽只差 25 Hz。
重放全部 744 条记录：**690 条（98.4%）三向并列**，最终由 `MODULATION_PROFILES`
的书写顺序决定输出 —— "93% 的信号是 USB 语音"只是因为 `USB_VOICE` 写在第一个。

现在换成：包络音节率与深度、键控率、音调数与间距、音调纯度、扣除底噪的占用带宽；
连续隶属度加权 + 否决条件；输出置信度，证据不足返回 `UNKNOWN`；
新增 `CARRIER` 类别；USB/LSB/AM 由解调模式决定而不是从音频里猜。

### 13.3 缺陷 03：SNR 把滤波器阻带当噪声基准

旧实现取峰值两侧 ±10% 当信号、其余当噪声。但频谱跨 0-6000 Hz，
信号只可能在 300-3000 Hz —— "噪声区"里塞了一大片几乎没有能量的滤波器阻带，
分母被压低。症状是 744 条记录里只有 6 条 SNR 超过 10 dB。

现在在解调通带内用低分位数估噪声基底。新增 `src/modes.py`，
让解调滤波器和音频通带共用同一张表 —— 这个缺陷的根因就是收发两端各写各的。

### 13.4 你需要做什么

| 事项 | 说明 |
|------|------|
| **数据库** | 什么都不用做。`analysis` 表的 6 个新列在下次打开数据库时自动补上，历史数据不丢 |
| **旧录音的嗡声** | **改不掉**。假样本已经写进 WAV 文件了。想要干净的录音只能重录 |
| **旧 S-meter 值** | 仍然是帧计数器残留，**不要和新数据混在一起比较**。建议按时间切开看 |
| **旧调制判定** | 分布严重偏向 `USB_VOICE`，不可信。对还有 WAV 文件的记录可以用 `main.py analyze` 重跑 |
| **旧 SNR 值** | 系统性偏低。同样建议重跑 |
| **配置文件** | `analysis` 段可以加 `noise_percentile` / `noise_snr_threshold_db` / `min_confidence`。不加也能跑，代码里有默认值 |
| **`bandwidth_hz` 的含义变了** | 从"峰值 -20 dB 跨度"变成"90% 能量占用带宽"。旧口径保留在 `bandwidth_20db_hz` |

想重跑历史录音：

```python
import os, yaml
from src.db import Database
from src.analyzer import SignalAnalyzer

config = yaml.safe_load(open("config.yaml", encoding="utf-8"))
db = Database("data/radio_monitor.db")
analyzer = SignalAnalyzer.from_config(config)

for row in db.get_signals_with_analysis(days=365, limit=500, with_recording=True):
    path = row.get("recording_path")
    if not path or not os.path.exists(path):
        continue
    loaded = SignalAnalyzer.load_wav(path)
    if loaded is None:
        continue
    samples, sr = loaded
    analyzer.analyze_and_save(db, row["id"], samples, sr, mode=row.get("mode") or "USB")

db.close()
```

> 注意这会**追加**新的 `analysis` 行，不会删掉旧行 —— 查询时按 `timestamp`
> 取最新的一条。

### 13.5 Web 页面

v1.3.0 把监听页面重做了。`analyzer.get_spectrogram()` 早就写好却从没被界面调用过，
747 段录音过去只能去文件夹里翻。现在频谱瀑布、录音回放、频谱图缩略图、
真实 S-meter、带内 SNR、静噪在线标定、统计页都在页面上（§6）。

---

## 14. 常见问题

### Q: 所有节点都不可用？

多半是网络环境挡了出站的 8073 端口（防火墙/VPN/校园网很常见）。

1. 先跑 `python diagnose_network.py` 看卡在哪一层（DNS / TCP / WebSocket / 协议）
2. 去 <http://rx.kiwisdr.com> 在浏览器里找能打开的节点
3. 把能用的加进 `config.yaml`，或者跑 `python fetch_nodes.py` 拉一批在线的
4. KiwiSDR 通道有限，高峰期满员会连不上 —— 换个时段或换个节点

### Q: 监听了很久一个信号都没有？

**先看左侧 Squelch 面板的"静噪状态"**，它一句话就能把两种完全相反的
情况分开：

- **一直是"关"** —— 阈值太高，信号根本没越过开启阈值
- **一直是"开 (录制中)"** —— 阈值**太低**（低于底噪）。信号只在静噪
  **关闭**的那一刻才落库，静噪关不掉就一条记录都不会有，界面上的
  Signals 会一直停在 0，而录音机在后台一直写盘。这种情况面板顶部会有
  黄色告警，日志里是 `[SQUELCH-STUCK]`

按可能性排序：

1. **静噪阈值不对**（最常见）。勾上"自适应"，或点"按底噪设定"（§6.4）。
   注意阈值要和**实测底噪**比，不要和别的接收机的经验值比
2. HFGCS 本来就不是一直有信号，EAM 播发有间隔
3. 频率和时段对不上 —— 参考 `frequencies.yaml` 里的 `active_hours`，
   白天 11175、夜间 4724、全天 8992
4. 节点位置太远、传播条件不好 —— 收 HFGCS 优先用北美/欧洲的节点
5. 根本没收到音频 —— 命令行 `monitor` 的 `[MONITORING]` 日志里有
   `frames=`，它不涨就是链路的问题，和静噪无关

### Q: RMS 曲线上那几条线分别是什么？

- **红色实/虚线（较亮）** = 静噪**开启**阈值。RMS 越过它就开始录
- **红色虚线（较淡）** = 静噪**关闭**阈值，比开启阈值低，形成滞后防止频繁开关
- **灰色虚线** = 实测的**噪声基底**（10 分钟窗口内 RMS 的第 10 百分位，
  静噪开着的时候也照常统计）

阈值应该在噪声基底上方约 6 dB（≈ 2 倍）。三条线并排就能一眼看出卡得对不对：
**开启线掉到灰线下面** = 静噪会一直开着，一条记录都不会出。

### Q: S-meter 一直是 -160 dBm / 录音里有低频嗡声？

这是 v1.3.0 之前的 bug，已修复，见 §13.1。**修复只对新录音生效** ——
旧 WAV 里的假样本和数据库里的旧 S-meter 值改不掉，不要和新数据混着比。

### Q: 调制类型老是输出 `UNKNOWN`？

这是**设计行为**，不是故障：证据不足时它不硬猜。先看两个数：

- **带内 SNR** —— 低于 3 dB 会直接判 `NOISE`，其余特征本来就没意义
- **录音时长** —— 包络分析需要足够长的样本，太短会被强制压低置信度

如果确认信号是好的但还是 `UNKNOWN`，可以把 `config.yaml` 的
`analysis.min_confidence` 从 0.35 调低一点。**别调到 0** —— 那就退回旧版本
"按字典顺序挑第一个"的行为了。

### Q: 录音文件太多，磁盘要满了？

```bash
python main.py clean                       # 先预览
python main.py clean --delete --clean-db   # 确认后删，连数据库记录一起清
```

详见 §8。长期跑的话建议顺手把静噪阈值按底噪重新标一次 —— 垃圾录音多
通常说明阈值卡得太低。

### Q: 换了节点之后阈值全不对了？

正常。底噪 RMS 取决于对面节点的 AGC 设置，换节点必然变。
不用改配置文件重启：Web 界面左侧 Squelch 面板在线调，或者直接点"按底噪设定"。

### Q: Web 页面能开给别人用吗？

技术上可以（默认 `--host 0.0.0.0`），但**服务端没有任何鉴权**：
任何能访问这个端口的人都能控制你的监听、回放你的录音。
放在不可信网络里请加 `--host 127.0.0.1`，或者用带认证的反向代理挡一层。

### Q: 监听时断线了会自动恢复吗？

Web 监听会（指数退避重连 + 连续失败 3 次自动换节点，见 §6.6）。
命令行 `monitor` 不会自动换节点，长时间无人值守建议用 Web。

---

## 免责声明

本项目只接收和分析**公开的**无线电信号元数据，通过全球公开的 KiwiSDR 接收机网络。
不解密任何内容（EAM 是一次性密码本加密的，也解不开），不发射，不干扰。
使用前请确认所在司法辖区对无线电监听的相关规定。
