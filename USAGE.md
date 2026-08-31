# MilRadio manual (v1.4.3) / MilRadio 使用说明（v1.4.3）

> Military radio signal reception and metadata analysis — receives HF signals over the worldwide
> network of public KiwiSDR receivers, records on squelch, analyses the spectrum, classifies
> modulation, and writes all metadata into SQLite.
>
> 军事无线电信号接收与元数据分析系统 —— 通过全球公开的 KiwiSDR 网络接收 HF 信号，
> 自动静噪录音、频谱分析、调制识别，并把全部元数据落进 SQLite。
>
> This is **the complete operating manual for the current version**. Release history is in
> [CHANGELOG.md](CHANGELOG.md); the full audit of the signal-chain defects is in
> [reports/milradio-audit.html](reports/milradio-audit.html).
>
> 本文是**当前版本的完整操作手册**。版本历史见 [CHANGELOG.md](CHANGELOG.md)，
> 信号链缺陷的完整审计过程见 [reports/milradio-audit.html](reports/milradio-audit.html)。
>
> **This document is bilingual: English first, Chinese second.**
> **本文为中英双语，英文在前、中文在后。**

If you have used an older version, start with
[§13 Upgrading from an older version](#13-upgrading-from-an-older-version--从旧版本升级): it lists
the three measurement defects fixed in v1.3.0 and explains why **old data must not be compared
against new data**.

如果你用过老版本，先看 [§13 从旧版本升级](#13-upgrading-from-an-older-version--从旧版本升级)，
那里列了 v1.3.0 修掉的三处读数缺陷和**旧数据不能和新数据混着比**的原因。

---

## Contents / 目录

1. [System components / 系统组成](#1-system-components--系统组成)
2. [Installation / 安装](#2-installation--安装)
3. [Five-minute quick start / 五分钟上手](#3-five-minute-quick-start--五分钟上手)
4. [Configuration files / 配置文件](#4-configuration-files--配置文件)
5. [Command-line reference / 命令行完整参考](#5-command-line-reference--命令行完整参考)
6. [Live web interface / Web 实时监听界面](#6-live-web-interface--web-实时监听界面)
7. [Reading the analysis output / 读懂分析读数](#7-reading-the-analysis-output--读懂分析读数)
8. [Recording cleanup / 录音清理](#8-recording-cleanup--录音清理)
9. [Data storage and queries / 数据存储与查询](#9-data-storage-and-queries--数据存储与查询)
10. [HTTP API and WebSocket / HTTP API 与 WebSocket](#10-http-api-and-websocket--http-api-与-websocket)
11. [Public log download and comparison / 公开日志下载与对比](#11-public-log-download-and-comparison--公开日志下载与对比)
12. [Helper scripts and tests / 辅助脚本与测试](#12-helper-scripts-and-tests--辅助脚本与测试)
13. [Upgrading from an older version / 从旧版本升级](#13-upgrading-from-an-older-version--从旧版本升级)
14. [FAQ / 常见问题](#14-faq--常见问题)

---

## 1. System components / 系统组成

```
Public KiwiSDR node ──WebSocket──>  kiwi_client.py   Parse SND frames -> audio samples + S-meter
KiwiSDR 公开节点                                      解析 SND 帧 → 音频样本 + S-meter
                                       │
                                       ▼
                                  squelch.py       Squelch state machine, pre-roll and tail delay
                                                   静噪状态机（带 pre-roll 和尾部延迟）
                                       │ open -> record, close -> finish / 开→录音，关→收尾
                                       ▼
                                  recorder.py      Write WAV, auto-segment when over-long
                                                   写 WAV（超长自动分段）
                                       │
                                       ▼
                                  analyzer.py      In-band SNR / occupied BW / envelope / tones
                                                   -> modulation type + confidence
                                                   带内 SNR / 占用带宽 / 包络 / 音调 → 调制类型 + 置信度
                                       │
                                       ▼
                                  db.py            SQLite: sessions / signals / analysis / nodes
                                       │
                        ┌──────────────┴──────────────┐
                        ▼                             ▼
                  web_server.py                  reporter/           HTML report + 10 charts
                  Live page + REST API                               HTML 报告 + 10 张图表
                  实时页面 + REST API
```

| Module / 模块 | Responsibility / 职责 |
|------|------|
| `src/kiwi_client.py` | KiwiSDR WebSocket protocol, SND frame parsing, S-meter, dropped-frame counting<br>KiwiSDR WebSocket 协议、SND 帧解析、S-meter、丢帧统计 |
| `src/modes.py` | **The single source of truth for demodulation filters and audio passbands** (shared by both ends, see §13)<br>**解调滤波器与音频通带的唯一真值表**（收发两端共用，见 §13） |
| `src/squelch.py` | Squelch state machine (S-meter / adaptive RMS / fixed RMS), pre-roll ring buffer<br>静噪状态机（S-meter / 自适应 RMS / 固定 RMS）、pre-roll 环形缓冲 |
| `src/recorder.py` | WAV recording, automatic segmentation callback<br>WAV 录制、自动分段回调 |
| `src/analyzer.py` | Frequency/time-domain analysis, modulation classification, live spectrum, spectrogram<br>频域/时域分析、调制分类、实时频谱、频谱图 |
| `src/db.py` | SQLite persistence, automatic column migration, statistics queries<br>SQLite 持久化、自动补列迁移、统计查询 |
| `src/receiver.py` | CLI scan and continuous monitoring, reconnect and node switching<br>命令行的扫描与持续监听编排、断线重连与换节点 |
| `src/web_server.py` | aiohttp service, REST API, WebSocket push, online squelch calibration, auto-reconnect<br>aiohttp 服务、REST API、WebSocket 推送、在线静噪标定、自动重连 |
| `src/node_manager.py` | Node probing and selection, tiered by historical reception quality (see §5.3)<br>节点连通性探测与择优（按历史接收质量分档，见 §5.3） |
| `src/schedule.py` | Parses the `active_hours` annotations in the frequency library<br>解析频率库里的 `active_hours` 活跃时段 |
| `src/reporter/` | HTML report and charts (`theme.py` / `charts.py` / `reporter.py`)<br>HTML 报告与图表（`theme.py` / `charts.py` / `reporter.py`） |

---

## 2. Installation / 安装

### Requirements / 环境要求

- **Python 3.9+** (uses `zoneinfo`, `asyncio.get_running_loop()` and similar; 3.10+ recommended)
  **Python 3.9+**（用到 `zoneinfo`、`asyncio.get_running_loop()` 等，推荐 3.10+）
- Public internet access. KiwiSDR nodes are `ws://host:8073` — a **plain-text HTTP/WebSocket
  port** that many corporate and campus networks block.
  能访问公网（KiwiSDR 节点是 `ws://host:8073`，走 **HTTP/WebSocket 明文端口**，
  很多企业网/校园网会拦）

### Installing dependencies / 安装依赖

```bash
git clone https://github.com/reinhardt6678-sudo/Radio.git
cd Radio

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

Dependency list (`requirements.txt`) / 依赖清单（`requirements.txt`）：

| Package / 包 | Purpose / 用途 |
|----|------|
| `websockets>=12.0` | KiwiSDR WebSocket client / KiwiSDR WebSocket 客户端 |
| `numpy>=1.24` / `scipy>=1.10` | FFT, spectrogram, signal processing / FFT、频谱图、信号处理 |
| `matplotlib>=3.7` | Report charts / 报告图表 |
| `pyyaml>=6.0` | Configuration and frequency library / 配置与频率库 |
| `aiohttp>=3.9` | Web server / Web 服务器 |
| `requests>=2.31` / `beautifulsoup4>=4.12` | Public log scraping / 公开日志抓取 |

Running the tests additionally needs `pytest` (not in requirements): `pip install pytest`.
跑测试还需要 `pytest`（不在 requirements 里）：`pip install pytest`。

### Directories are created automatically / 目录会自动创建

`data/recordings/`, `data/radio_monitor.db` and `reports/` are all created on first run; nothing
needs to be made by hand. The whole `data/` directory is in `.gitignore`, so recordings and the
database never enter version control.

`data/recordings/`、`data/radio_monitor.db`、`reports/` 都在首次运行时自动建，
不用手工创建。`data/` 整个目录在 `.gitignore` 里，录音和数据库不会进版本库。

### Windows notes / Windows 说明

`main.py` already handles two things; no manual intervention needed:
`main.py` 已经处理了两件事，不用手工干预：

- A GBK console cannot print Unicode → stdout/stderr are wrapped as UTF-8 at startup.
  控制台 GBK 编码无法输出 Unicode → 启动时把 stdout/stderr 包成 UTF-8；
- `ProactorEventLoop` is incompatible with aiohttp/websockets → it switches to
  `WindowsSelectorEventLoopPolicy` automatically.
  `ProactorEventLoop` 与 aiohttp/websockets 的兼容问题 → 自动切到 `WindowsSelectorEventLoopPolicy`。

---

## 3. Five-minute quick start / 五分钟上手

```bash
# (1) See which KiwiSDR nodes are reachable right now
#     看哪些 KiwiSDR 节点现在能连
python main.py nodes

# (2) Open the web interface (recommended entry point: live spectrum,
#     recording playback and online squelch tuning all live here)
#     开 Web 界面（推荐的入口，实时频谱 + 录音回放 + 在线调静噪都在这）
python main.py web
#     Browse to / 浏览器打开 http://localhost:8888

# --- or use the command line / 或者走命令行 ---

# (3) Monitor the HFGCS daytime primary / 监听 HFGCS 日间主频
python main.py monitor -f 11175

# (4) Analyse one recording / 分析一段录音
python main.py analyze data/recordings/20260601_034521_11175.0kHz_KPH_California.wav

# (5) Generate the HTML report / 生成 HTML 报告
python main.py report
```

**Which frequency first?** **11175 kHz** by day and **8992 kHz** around the clock are the busiest
HFGCS channels; use **4724 kHz** at night. All three are USB.

**先听哪个频率？** 白天 **11175 kHz**、全天 **8992 kHz** 是 HFGCS 最活跃的，
夜间用 **4724 kHz**。这三个都是 USB。

**How to run the first session properly:** the default squelch criterion is `smeter` — the
threshold is the measured S-meter noise floor +14 dB. Leave it alone; the S-meter is measured by
the node *before* its audio AGC, so this works whether or not the far end runs AGC, and no signal
decision is made until a floor has been measured (pre-roll keeps buffering, so the start of a
signal is never lost). If you want a fixed RMS threshold instead, let it run for 3–5 minutes,
wait for the left-hand Squelch panel to report a **measured noise floor**, then press "set from
noise floor". A squelch threshold depends on the AGC of the far-end node and cannot be guessed
(details in §6.4).

**第一次监听的正确姿势**：默认静噪判据是 `smeter` —— 阈值 = 实测 S-meter 底噪 +14 dB，
开着不用管。S-meter 是节点在音频 AGC **之前**测的电平，所以对面开不开 AGC 都成立；
底噪还没测出来之前不判信号（pre-roll 照常攒着，信号开头不会丢）。
想用固定 RMS 阈值就让它跑 3-5 分钟，左侧 Squelch 面板统计出**实测底噪**之后
点"按底噪设定"。静噪阈值取决于对面节点的 AGC，猜是猜不准的（详见 §6.4）。

---

## 4. Configuration files / 配置文件

### 4.1 `config.yaml`

#### `nodes` — the KiwiSDR node list / KiwiSDR 节点列表

```yaml
nodes:
  - host: "kphsdr.com"       # Node address / 节点地址
    port: 8073               # Port, usually 8073, occasionally 8074/8075 / 端口（通常 8073，个别是 8074/8075）
    man_gain: 82             # Per-node calibrated fixed gain / 每节点标定的固定增益
    name: "KPH California"   # Display name / 显示名
    location: "California, USA"
    lat: 38.10               # Latitude, used by the map in reports / 纬度（报告里的地图用）
    lon: -122.95             # Longitude / 经度
```

Find public nodes at <http://rx.kiwisdr.com> or <http://rx.linkfanel.net>. The bundled
`fetch_nodes.py` pulls a list of nodes from linkfanel that are **online right now and have a free
channel**.

去 <http://rx.kiwisdr.com> 或 <http://rx.linkfanel.net> 找公开节点。
仓库里的 `fetch_nodes.py` 可以直接从 linkfanel 拉一份**当前在线且有空闲通道**的列表。

> Practical advice on choosing nodes: for HFGCS, prefer North American or European receivers.
> Channels are limited and nodes are frequently full at peak times, so configure several spares
> (web monitoring switches automatically after three consecutive failures, see §6.6).
>
> 选节点的经验：接收 HFGCS 优先选北美/欧洲的节点；节点通道有限，
> 高峰期常常满员，多配几个备用（Web 监听会在连续失败 3 次后自动切换，见 §6.6）。

#### `receiver` — reception parameters / 接收参数

| Field / 字段 | Default / 默认 | Description / 说明 |
|------|------|------|
| `max_concurrent` | 2 | Concurrent connection limit; each takes one KiwiSDR channel<br>并发连接上限，每个连接占 KiwiSDR 一个通道 |
| `listen_duration` | 0 | Seconds per frequency, 0 = unlimited / 每频率监听时长（秒），0 = 无限 |
| `scan_dwell_time` | 30 | Dwell time per frequency in scan mode, seconds / 扫描模式每频率停留秒数 |
| `sample_rate` | 12000 | Audio sample rate; KiwiSDR is fixed at 12 kHz / 音频采样率，KiwiSDR 固定 12 kHz |
| `bandwidth` | 6000 | Audio bandwidth in Hz / 音频带宽 Hz |

#### `receiver.agc` / `man_gain` — node AGC (**read this first**) / 节点 AGC（**先看这个**）

```yaml
receiver:
  agc: false        # Keep this false / 保持 false
  man_gain: 70      # Fallback gain; each node uses its own nodes[].man_gain
                    # 兜底增益；每个节点用自己的 nodes[].man_gain
```

With AGC on at the node the output level is pinned, and **the audio level differs by only 1.6 dB
(median) between having a signal and not having one**; with AGC off the gap is 7.1 dB. Measured
over 93 hours, 4 nodes and 109 segments, with the same direction on every node. The consequence
is that **no RMS threshold works while AGC is on**: set it slightly high and you get zero records
all day, slightly low and you record all day, with nothing usable in between (full data in §6.4).

节点开着 AGC 时输出电平会被钉死，**有信号和没信号的音频电平只差 1.6 dB
（中位数）**，关掉之后是 7.1 dB。93 小时 / 4 节点 / 109 段实测，方向在每个
节点上都一致。这意味着 AGC 开着时**任何 RMS 阈值都无解**：定高一点一整天
0 条，定低一点录满整天，中间没有可用档位（§6.4 有完整数据）。

Two traps / 两个坑：

- **AGC can only be set at the moment the connection is established.** Once a node has entered
  AGC mode, a later `SET agc=0` is ignored and the gain stays high (22 dB off, measured). The
  program re-sends it on every reconnect; keep this in mind if you write your own scripts.
  **AGC 只能在连接建立的那一刻设定。** 节点一旦进入 AGC 模式，之后再发
  `SET agc=0` 会被忽略，增益停在高位（实测差 22 dB）。程序每次重连都会重新
  下发，你自己写脚本时也要注意。
- **`man_gain` must be calibrated per node, and re-calibrated when changing band.** The target is
  a noise floor RMS around 0.015:
  **`man_gain` 每个节点都要单独标定，换频段还要重标。** 目标是让底噪 RMS
  落在 0.015 附近：

```bash
python diagnose_rms.py -f 11175 --all-nodes
```

#### `squelch` — squelch (VOX) / 静噪（VOX）

```yaml
squelch:
  mode: smeter            # smeter   = S-meter noise floor +N dB (default, recommended)
                          #            S-meter 底噪 +N dB（默认，推荐）
                          # adaptive = audio noise floor +N dB / 音频底噪 +N dB
                          # absolute = fixed RMS threshold / 固定 RMS 阈值

  # --- smeter mode (default) / smeter 模式（默认）---
  smeter_open_margin_db: 14.0   # dB above the S-meter floor to open
                                # 打开阈值高于 S-meter 底噪多少 dB
  smeter_close_margin_db: 10.0  # Close threshold; must be < the open margin, for hysteresis
                                # 关闭阈值，必须 < 打开余量，形成滞后
  smeter_floor_window_seconds: 600
  smeter_floor_percentile: 10

  # --- dead-audio guard (all modes) / 哑音链路保护（所有模式）---
  # Some nodes keep sending frames with a healthy S-meter while every audio sample is the
  # same constant. In smeter mode the squelch only looks at RF level, so such a node would
  # trigger and record files of zeros. Judged by RMS spread: real audio always jitters.
  # 有些节点帧照发、S-meter 也健康，但每个音频采样都是同一个常数。smeter 模式只看
  # 射频电平，这种节点会照样触发并录出一串零。按 RMS 极差判定：真实音频总在抖。
  dead_audio_window_seconds: 30.0
  dead_audio_min_blocks: 50       # 0 disables the guard / 写 0 则关闭该判定
  dead_audio_rms_spread: 0.000001 # Spread at or below this counts as muted
                                  # 极差小于等于此值即判为哑音

  # --- adaptive mode / adaptive 模式 ---
  open_margin_db: 6.0     # dB above the measured floor to open (6 dB = floor x 2)
                          # 开启阈值高于实测底噪多少 dB（6 dB = 底噪 × 2）
  close_margin_db: 3.0    # dB above the measured floor to close (3 dB = floor x 1.41)
                          # 关闭阈值高于实测底噪多少 dB（3 dB = 底噪 × 1.41）
  min_open_threshold: 0.005   # Absolute floor for the open threshold, so digital silence is
                              # not mistaken for a signal
                              # 开启阈值绝对下限，防止把数字静音当信号
  floor_window_seconds: 600   # Noise-floor window, seconds / 底噪统计窗口（秒）
  floor_percentile: 10        # Percentile taken as the floor / 底噪取窗口内第几百分位

  # --- absolute mode / absolute 模式 ---
  open_threshold: 0.10    # Open threshold (RMS, 0-1): below this is noise, do not record
                          # 开启阈值 (RMS, 0-1)：低于它认为是底噪，不录
  close_threshold: 0.085  # Close threshold; must be < open, for hysteresis
                          # 关闭阈值：必须 < open，形成滞后防止频繁开关

  tail_time: 3.0          # Keep recording this long after the signal disappears
                          # 信号消失后继续录几秒，防止截断尾音
  max_open_seconds: 300   # Longest continuous open before a forced close (0 = unlimited)
                          # 静噪最长连续打开时间，到点强制收尾（0 = 不限）
  window_size: 1024       # RMS analysis window in samples / RMS 分析窗口（采样点）
```

> With AGC on at the node (`SET agc=1`) the noise floor is amplified to a roughly constant level,
> and the absolute RMS of the same signal can differ several-fold between nodes and times of day.
> That is why the default is `smeter`: the S-meter is the RF level measured *before* the audio
> AGC, so the threshold holds across nodes and frequencies without retuning. `adaptive` follows
> the measured audio floor instead and is only meaningful with `agc: false`.
>
> 节点开着 AGC（`SET agc=1`），底噪会被自动放大到一个差不多恒定的电平，
> 同一个信号的绝对 RMS 在不同节点/不同时段能差好几倍。所以默认用 `smeter`：
> S-meter 是音频 AGC **之前**的射频电平，换节点换频率都不用重调。
> `adaptive` 跟的是实测音频底噪，只在 `agc: false` 时才有意义。
>
> If you use `absolute`, **do not copy the number above** — measure the noise floor first and then
> pick a value (§6.4, or `diagnose_rms.py`). **Any threshold below the noise floor leaves the
> squelch permanently open once it opens**, and the signal count stays at zero forever. This case
> is now called out explicitly in the log and the web UI, and forcibly segmented per
> `max_open_seconds`.
>
> 要用 `absolute` 就**不要照抄这个数**，先量一次底噪再定值（§6.4 或 `diagnose_rms.py`）。
> **阈值只要低于底噪，静噪打开后就再也关不掉**，信号数会一直停在 0 ——
> 现在这种情况会在日志和 Web 界面上明确告警，并按 `max_open_seconds` 强制分段。

> `max_open_seconds` does two jobs: a genuinely long exchange lasting tens of minutes is stored in
> segments of that length (aligned with `recording.max_duration`, so one recording maps to one
> signal record); and a misconfigured threshold cannot hold everything back all day without
> producing a single record.
>
> `max_open_seconds` 有两个作用：一段几十分钟的连续通联按这个长度分段入库
> （和 `recording.max_duration` 对齐，一段录音对应一条信号记录）；
> 阈值配错时也不会一整天憋着不出任何记录。

#### `recording` — recording / 录制

| Field / 字段 | Default / 默认 | Description / 说明 |
|------|------|------|
| `output_dir` | `data/recordings` | WAV output directory (the web playback endpoint only serves files inside it)<br>WAV 输出目录（Web 回放接口只允许访问这个目录内的文件） |
| `max_duration` | 300 | Longest segment in seconds; longer recordings are split<br>单段最长秒数，超过自动分段 |
| `bit_depth` | 16 | WAV bit depth, 16 or 32 / WAV 位深（16 或 32） |
| `pre_roll` | 2.0 | Seconds kept from **before** the signal started, so the opening is not clipped<br>保留信号开始**之前**的秒数，避免掐头 |

#### `analysis` — analysis parameters (3 added in v1.3.0) / 分析参数（v1.3.0 新增了 3 个）

```yaml
analysis:
  fft_size: 4096
  window_type: "hann"           # hann / hamming / blackman
  bandwidth_threshold_db: 20    # Old bandwidth definition, kept only to compare with history
                                # 旧口径带宽阈值，仅用于和历史数据对照
  noise_percentile: 20          # Percentile taken as the in-band noise floor    [new in v1.3.0]
                                # 带内噪声基底取第几百分位                        【v1.3.0 新增】
  noise_snr_threshold_db: 3.0   # Below this in-band SNR, classify as NOISE       [new in v1.3.0]
                                # 低于此带内 SNR 直接判 NOISE                     【v1.3.0 新增】
  min_confidence: 0.35          # Below this confidence, emit UNKNOWN not a guess [new in v1.3.0]
                                # 低于此置信度输出 UNKNOWN 而非硬猜               【v1.3.0 新增】
```

- **`noise_percentile`** — the lower it is, the harder it is for a strong signal filling the
  passband to lift the noise floor. On busy frequencies, 10–15 works well.
  **`noise_percentile`** 越低，噪声基底越不容易被"占满通带的强信号"抬高。
  信号密集的频率上可以调到 10-15。
- **`min_confidence`** — raise it for more `UNKNOWN` but more trustworthy labels; lower it for
  more labels but the return of guessing. **Do not set it to 0** — that reverts to the old
  behaviour of emitting whatever comes first in the dictionary.
  **`min_confidence`** 调高 → 更多 `UNKNOWN`，但输出的标签更可信；
  调低 → 标签更多，但会开始出现硬猜。**不要调到 0**，那就退回旧版本
  "按字典顺序输出第一个"的行为了。

#### `report` — reports / 报告

| Field / 字段 | Default / 默认 | Description / 说明 |
|------|------|------|
| `output_dir` | `reports` | HTML and PNG output directory / HTML 与 PNG 输出目录 |
| `recent_days` | 7 | How many recent days the report covers / 报告覆盖的最近天数 |
| `chart_dpi` | 150 | Chart DPI / 图表 DPI |
| `chart_theme` | `dark` | `dark` / `light` |

### 4.2 `frequencies.yaml`

Grouped by network; five groups ship by default: `hfgcs`, `nato`, `military_air`, `digital`,
`reference`.

按网络分组，目前内置 5 组：`hfgcs`、`nato`、`military_air`、`digital`、`reference`。

```yaml
my_frequencies:                     # Group name (what scan --network matches against)
                                    # 组名（= scan --network 的匹配对象）
  description: "My own watch list / 我的自定义监听频率"
  frequencies:
    - freq: 7850.0                  # kHz
      mode: "USB"                   # USB / LSB / AM / CW / CWN / NFM
      description: "Some frequency of interest / 某个感兴趣的频率"
      active_hours: "all day / 全天" # Advisory only; monitoring is not blocked by it
                                    # 仅作说明，程序不据此过滤
      priority: high                # high / medium / low
```

- Without `-f`, `monitor` **only listens to `priority: high` frequencies**.
  `monitor` 不带 `-f` 时，**默认只监听 `priority: high` 的频率**。
- `scan --network` performs a **substring match** (`--network hf` also matches `hfgcs`).
  `scan --network` 做的是**子串匹配**（`--network hf` 也能命中 `hfgcs`）。
- `mode` selects the demodulation filter of the receiver **and** the analysis passband — since
  v1.3.0 both come from the same table in `src/modes.py`, so they cannot drift apart.
  `mode` 决定接收机的解调滤波器**和**分析通带 —— 这两个从 v1.3.0 起
  共用 `src/modes.py` 里的同一张表，不会再各写各的。
- `active_hours` is parsed by `src/schedule.py` and used to warn at monitor start when the
  frequency is currently outside its window. It advises; it does not block.
  `active_hours` 由 `src/schedule.py` 解析，监听启动时提示当前不在时段内的频率。
  只提示，不拦截。

---

## 5. Command-line reference / 命令行完整参考

### Global options / 全局参数

```bash
python main.py [-v] [-c CONFIG] <subcommand> [subcommand options]
python main.py [-v] [-c CONFIG] <子命令> [子命令参数]
```

| Option / 参数 | Description / 说明 |
|------|------|
| `-v, --verbose` | DEBUG-level logging (frame parsing and handshake detail are printed)<br>DEBUG 级日志（帧解析、握手细节都会打出来） |
| `-c, --config` | Configuration file, default `config.yaml` / 指定配置文件，默认 `config.yaml` |

### 5.1 `nodes` — node connectivity check / 节点连通性检查

```bash
python main.py nodes
python main.py nodes --timeout 20      # Longer timeout on a slow network (default 10s)
                                       # 网络慢时加长超时（默认 10s）
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

Results are written into the `nodes` table (average latency, cumulative connections and failures),
and the node dropdown in the web UI reads straight from it.

结果会写进 `nodes` 表（含平均延迟、累计连接/失败次数），Web 界面的节点下拉框直接用它。

### 5.2 `scan` — frequency sweep / 频率扫描

Dwells on each frequency for a while and counts activity — a quick way to find out which
frequencies are worth watching today.

在每个频率停留一段时间，统计有没有活动，用来快速筛"今天哪个频率有戏"。

```bash
python main.py scan                        # Sweep every frequency in frequencies.yaml
                                           # 扫描 frequencies.yaml 里所有频率
python main.py scan --network hfgcs        # HFGCS only / 只扫 HFGCS
python main.py scan --priority high        # High priority only / 只扫高优先级
python main.py scan --dwell 10             # 10 s per frequency (default from config: 30)
                                           # 每频率停 10 秒（默认取 config 的 30）
python main.py scan --node "KPH"           # Pick a node by name substring / 指定节点（按名字子串匹配）
python main.py scan --freq-file my.yaml    # Use a different frequency library / 换一个频率库
```

### 5.3 `monitor` — continuous monitoring (the core) / 持续监听（核心）

```bash
# Basics / 基本
python main.py monitor -f 11175            # One frequency / 单频率
python main.py monitor -f 11175 8992 4724  # Rotate over several / 多频率轮询
python main.py monitor                     # No -f: all high-priority frequencies
                                           # 不给 -f 则监听所有 high 优先级频率

# Mode and node / 模式与节点
python main.py monitor -f 5000 -m AM       # USB / LSB / AM / CW
python main.py monitor -f 11175 --node "KPH California"

# Duration / 时长
python main.py monitor -f 11175 --duration 300   # Stop after 300 s; 0 or omitted = unlimited
                                                 # 300 秒后停，0/省略 = 无限

# Debugging / 调试
python main.py -v monitor -f 11175
```

A frequency given with `-f` that is not in `frequencies.yaml` gets a temporary target created for
it (mode from `-m`, default USB).

`-f` 给的频率如果不在 `frequencies.yaml` 里，会自动建一个临时目标
（模式取 `-m`，默认 USB）。

**What happens while monitoring / 监听时发生了什么：**

1. Connect to the KiwiSDR node and set the demodulation filter from `mode`
   (`DEMOD_FILTERS` in `src/modes.py`).
   连接 KiwiSDR 节点，按 `mode` 设置解调滤波器（`src/modes.py` 的 `DEMOD_FILTERS`）
2. Parse SND frames one by one:
   `tag(3) + flags(1) + seq(4, little-endian) + smeter(2, big-endian)` + audio samples.
   逐帧解析 SND：`tag(3) + flags(1) + seq(4, 小端) + smeter(2, 大端)` + 音频样本
3. The squelch detector evaluates each block: above the open threshold → start recording (with
   2 s of pre-roll). In `mode: smeter` (the default) the threshold is the S-meter floor +14 dB —
   the S-meter is the RF level the node measures **before** its audio AGC, so it works whether or
   not AGC is on; no signal decision is made before a floor exists (pre-roll keeps buffering, so
   the start of the signal is not lost).
   静噪检测器算每块 RMS：超过开启阈值 → 开录（带 2 秒 pre-roll）。
   `mode: smeter`（默认）时阈值 = S-meter 底噪 +14 dB —— S-meter 是节点在
   音频 AGC **之前**测的射频电平，所以 AGC 开不开都有效；底噪还没测出来之前
   不判信号（pre-roll 照常攒着，信号开头不会丢）
4. When the level drops below the close threshold, wait `tail_time` seconds → finish and write the
   WAV. Staying open longer than `max_open_seconds` forces a close and a segment boundary; a
   recording in progress when the link drops or monitoring stops is also closed and stored
   properly, so you never end up with a WAV that has no record.
   RMS 掉到关闭阈值以下，再等 `tail_time` 秒 → 收尾，写 WAV。
   连续打开超过 `max_open_seconds` 会强制收尾分段；断线/停止监听时
   正在录的那段也会正常收尾入库，不会只剩一个没有记录的 WAV
5. Run frequency- and time-domain analysis over the whole segment, taking the passband from the
   demodulation mode to compute SNR and the modulation type.
   对整段音频做频域+时域分析，按解调模式取通带算 SNR 与调制类型
6. Write one row each into `signals` and `analysis`.
   `signals` + `analysis` 两张表各写一行
7. Reconnect automatically on a drop: exponential backoff 5s → 10s → 20s → 40s → 60s, capped;
   three consecutive failures on one node switch to another. Reconnects and node switches remain
   the same session, and the remaining `--duration` keeps counting down rather than being renewed.
   断线自动重连：指数退避 5s → 10s → 20s → 40s → 60s 封顶，同一节点
   连续失败 3 次自动换节点。重连和换节点都算同一个会话，`--duration`
   的剩余时长接着扣，不会被续期
8. After being forced onto another node, retry the preferred node every 30 minutes — the preferred
   node being the one selected at startup.
   被迫换走之后，每 30 分钟回首选节点试一次 —— 首选节点是启动时挑中的那个
9. `Ctrl+C` stops (on Windows `Ctrl+Break` works too).
   `Ctrl+C` 停止（Windows 上 `Ctrl+Break` 同样有效）

**How a node is chosen / 节点是怎么挑的：**

Both at startup and when switching mid-run, the ordering is **historical reception quality first,
latency only as a tie-break within a tier**:

不管是启动时选节点还是中途换节点，排序都是**先看历史接收质量，同档之内才比延迟**：

| Tier / 档 | Condition / 条件 | Meaning / 含义 |
|----|------|------|
| 0 | Has produced a signal with in-band SNR ≥ 6 dB on this node<br>这个节点上出现过带内 SNR ≥ 6 dB 的信号 | It has actually heard something — prefer it / 真收到过东西，优先 |
| 1 | Fewer than 30 records / 记录不足 30 条 | Not known yet / 还不知道行不行 |
| 2 | 30+ records and not one qualifies / 记录 ≥ 30 条但一条达标的都没有 | Evidence that it cannot hear — last / 有实据说明它听不见，排最后 |

Within a tier, order by qualifying-signal count descending, then latency ascending. **"No data"
ranks ahead of "data proving it does not work"** — an untried node still has a chance.

同档内按达标信号数降序，再按延迟升序。**"没数据"排在"有数据证明它不行"前面** ——
没试过的还有机会。

Latency cannot be the primary criterion: what you can hear on HF depends on geography. In the
measured data, HB3YQQ in Switzerland had the lowest latency, yet ran 5.5 hours across 85 records
with **zero** real signals, because the HFGCS transmitters are in the United States.

延迟不能当主判据：HF 收得到什么取决于地理位置。实测里 HB3YQQ 瑞士延迟最低，
却连了 5.5 小时、85 条记录、**0 条**真信号，因为 HFGCS 发射台在美国。

To bypass this selection entirely, name the node with `--node "KPH California"`.
要绕开这套自动择优，用 `--node "KPH California"` 直接指定。

### 5.4 `analyze` — analyse a recording / 分析录音文件

```bash
python main.py analyze data/recordings/xxx.wav
python main.py analyze data/recordings/xxx.wav -m AM   # Required if the recording is not USB
                                                       # 录音时不是 USB 就要指定
```

`-m` accepts `USB / LSB / AM / CW / CWN` and **defaults to USB**. It decides the analysis
passband, and therefore the in-band SNR and the modulation verdict — the wrong mode drags the SNR
down and skews the classification.

`-m` 可选 `USB / LSB / AM / CW / CWN`，**默认 USB**。它决定分析通带，
进而决定带内 SNR 和调制判定 —— 用错模式会让 SNR 偏低、调制判定失准。

Output / 输出：

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

How to read each line: [§7](#7-reading-the-analysis-output--读懂分析读数).
每一项怎么读见 [§7](#7-reading-the-analysis-output--读懂分析读数)。

### 5.5 `report` — generate the HTML report / 生成 HTML 报告

```bash
python main.py report
```

Writes `report_YYYYMMDD_HHMM.html` into `reports/`, covering the last `report.recent_days` days
(default 7), containing:

在 `reports/` 下生成 `report_YYYYMMDD_HHMM.html`，覆盖最近
`report.recent_days` 天（默认 7），包含：

- Frequency activity ranking, 24-hour activity heatmap, signal strength distribution, signal
  timeline scatter
  频率活跃度排名、24 小时活动热力图、信号强度分布、信号时间线散点
- Modulation distribution, SNR/bandwidth scatter, duration distribution, network distribution, a
  combined dashboard
  调制类型分布、SNR/带宽散点、时长分布、网络分布、综合仪表盘
- Frequency statistics table and a list of recent signals
  频率统计表与最近信号列表

The charts are the `chart_*.png` files in the same directory, referenced by relative path — **do
not move the PNGs on their own**, or previously generated reports turn into broken images (there
is a note about this in `.gitignore`).

图表是同目录的 `chart_*.png`，HTML 用相对路径引用 —— **别单独挪走 PNG**，
否则已生成的报告会变成坏图（`.gitignore` 里专门留了说明）。

### 5.6 `web` — live monitoring interface / 实时监听界面

```bash
python main.py web                     # Default 0.0.0.0:8888 / 默认 0.0.0.0:8888
python main.py web --port 9090
python main.py web --host 127.0.0.1    # Local only / 只监听本机
python main.py web --freq-file my.yaml
```

> **`--host` defaults to `0.0.0.0`**, which means anyone on the same LAN can open this page,
> control your monitoring and play back your recordings. The server has no authentication. On an
> untrusted network, pass `--host 127.0.0.1` explicitly or put a reverse proxy in front.
>
> **`--host` 默认是 `0.0.0.0`**，也就是同一局域网内任何人都能打开这个页面并
> 控制你的监听、回放你的录音。服务端没有鉴权。放在不可信网络里请显式加
> `--host 127.0.0.1`，或用反向代理挡一层。

Details in [§6](#6-live-web-interface--web-实时监听界面).
详见 [§6](#6-live-web-interface--web-实时监听界面)。

### 5.7 `clean` — remove junk recordings / 清理垃圾录音

```bash
python main.py clean                   # Preview (the default; deletes nothing) / 预览（默认，不删任何东西）
python main.py clean --delete          # Actually delete / 实际删除
python main.py clean --delete --clean-db   # Also drop the matching signal rows / 同时删数据库里对应的信号记录
python main.py clean --min-duration 3 --min-snr 8
python main.py clean -m AM             # Required for AM recordings; sets the analysis passband
                                       # 录音是 AM 的话要指定，决定分析通带
```

Details in [§8](#8-recording-cleanup--录音清理).
详见 [§8](#8-recording-cleanup--录音清理)。

---

## 6. Live web interface / Web 实时监听界面

```bash
python main.py web
# → http://localhost:8888
```

### 6.1 Page layout / 页面布局

| Area / 区域 | Contents / 内容 |
|------|------|
| **Left: Control Panel / 左侧 Control Panel** | Frequency shortcuts grouped by network · monitoring parameters (frequency/mode/node) · **online squelch adjustment** · node check and status<br>频率快捷键（按网络分组）· 监听参数（频率/模式/节点）· **Squelch 在线调整** · 节点检查与状态 |
| **Centre / 中央** | Five gauges (Frequency / S-Meter / in-band SNR / RMS Level / Signals) · **spectrum + waterfall** · live RMS curve<br>五块仪表（Frequency / S-Meter / 带内 SNR / RMS Level / Signals）· **频谱 + 瀑布图** · 实时 RMS 曲线 |
| **Right / 右侧** | Three tabs: **Live** (signal log + session statistics) · **Recordings** (browse and play) · **Statistics**<br>三个标签页：**实时**（信号日志 + 会话统计）· **录音**（浏览与回放）· **统计** |

### 6.2 Workflow / 操作流程

1. Press **Check Node Availability** to probe the nodes (results are written to the database too).
   点 **Check Node Availability** 探测节点（结果同时写进数据库）
2. Pick a frequency on the left — shortcut buttons are grouped by network and show only
   `priority: high` by default; tick "all" to expand the rest. **Frequencies that have produced
   signals in the last 30 days are highlighted with a count** (hover for detail). You can also
   type any frequency from 100 to 30000 kHz.
   左侧选频率 —— 快捷按钮按网络分组，默认只列 `priority: high` 的，勾"全部"展开其余；
   **最近 30 天有过信号的频率会被高亮并标出条数**（悬停看详情）；
   也可以直接在输入框敲任意 100-30000 kHz
3. Choose the mode (USB/LSB/AM/CW) and the node (`Auto` picks an available one).
   选模式（USB/LSB/AM/CW）和节点（`Auto` = 自动挑可用的）
4. **Start Monitoring**
5. The centre refreshes the spectrum, waterfall and RMS curve live; the gauges show the real
   S-meter and in-band SNR.
   中央实时刷新频谱、瀑布图、RMS 曲线；仪表给出真实 S-meter 与带内 SNR
6. On a detected signal the Live tab appends a row immediately, which can be played (▶) or opened
   as a spectrogram (▤) on the spot.
   检测到信号 → 右侧"实时"标签自动追加一条，可以立刻 ▶ 回放 / ▤ 看频谱图
7. **Stop Monitoring** ends the run. / **Stop Monitoring** 停止

### 6.3 Spectrum and waterfall / 频谱与瀑布图

Every 2048 samples (about 170 ms) the backend computes a 128-bin spectrum column and pushes it to
the front end, displayed up to 4000 Hz. It uses `analyzer.live_spectrum()` — **segment averaged**,
not a single FFT, because a single FFT inflates the live SNR.

后端每积够 2048 个样本（约 170 ms）算一列 128 格频谱推给前端，显示上限 4000 Hz。
用的是 `analyzer.live_spectrum()`（**分段平均**，不是单次 FFT —— 单次 FFT 会让
实时 SNR 虚高）。

The upper half is the spectrum curve, the lower half a waterfall scrolling downwards (newest on
top), with the demodulation passband of the current mode shaded in blue (300–3000 Hz for USB).
The peak frequency is shown live in the top-right corner.

上半是频谱曲线，下半是向下滚动的瀑布图（最新在最上面），蓝色阴影标出当前模式的
解调通带（USB 是 300-3000 Hz）。右上角实时显示峰值频率。

> The waterfall is the densest control on this page:
> **a steady horizontal line** = an unmodulated carrier; **regular short dashes** = CW keying;
> **a solid block filling the passband** = voice or data; **a diagonal streak sweeping down** =
> a sweeping interferer.
>
> 瀑布图是这个页面信息密度最高的控件：
> **稳定的水平亮线** = 未调制载波；**规律的短横** = CW 键控；
> **占满通带的连续色块** = 语音或数据；**从上到下扫过的斜线** = 扫频干扰。

### 6.4 Online squelch calibration (no need to stop monitoring) / 静噪在线标定（不用停下监听）

The **Squelch** area on the left / 左侧 **Squelch** 区：

- **Criterion dropdown** — three settings: `S-meter RF level` (default, recommended),
  `adaptive RMS`, `fixed RMS threshold`. Choosing S-meter reveals open/close margin sliders plus
  readouts of the measured S-meter floor and the effective threshold; the RMS sliders apply only
  to the other two.
  **判据**下拉 —— 三档：`S-meter 射频电平`（默认，推荐）、`自适应 RMS`、
  `固定 RMS 阈值`。选 S-meter 时下面会出现打开/关闭余量滑块，以及实测
  S-meter 底噪和生效阈值；选另外两档才用 RMS 滑块
- **Adaptive RMS** — threshold = measured audio floor +6 dB / +3 dB, following the floor by
  itself; clear it to use the fixed values of the two sliders below.
  **自适应 RMS** —— 阈值 = 实测音频底噪 +6 dB / +3 dB，
  跟着底噪自己走；取消勾选才用下面两个滑块的固定值
- The two sliders set the open/close thresholds directly; **Apply** takes effect on the running
  detector **immediately**, without restarting (the backend `POST /api/squelch` mutates the live
  `SquelchDetector` instance).
  两个滑块直接改开启/关闭阈值，点**应用**后**立刻作用到正在跑的检测器**，
  不用停下重来（后端 `POST /api/squelch` 直接改 `SquelchDetector` 实例）
- **Measured noise floor (RMS)** — the detector continuously tracks the 10th percentile of RMS
  over a 10-minute window. It keeps measuring while the squelch is open (a low percentile is not
  lifted by intermittent signals anyway); it shows `sampling...` until there are enough samples.
  **实测底噪 (RMS)** —— 检测器持续统计 RMS 的第 10 百分位，窗口 10 分钟。
  静噪打开期间也照常统计（低百分位本来就不会被间歇性的信号抬起来），
  刚启动时样本不够会显示 `采样中...`
- **Suggested threshold (floor +6/+3 dB)** — that is `floor × 2` and `floor × 1.41`.
  **建议阈值 (底噪 +6/+3 dB)** —— 就是 `底噪 × 2` 和 `底噪 × 1.41`
- **Effective threshold** — the open/close values actually in use right now (in adaptive mode they
  move with the floor).
  **生效阈值** —— 当前真正在用的开/关阈值（自适应模式下它会随底噪变）
- **Squelch state** — `open (recording)` / `closed`.
  **静噪状态** —— `开 (录制中)` / `关`
- **Set from noise floor** — one click switches to fixed mode and applies the suggested values.
  **按底噪设定** —— 一键切到固定模式并把阈值设成上面的建议值

> ⚠ If the close threshold is below the measured floor, a yellow warning appears at the top of the
> panel: in that configuration the squelch never closes once open, and **no signal record is
> produced all day** (signals are only stored at the moment the squelch closes). When you see the
> warning, press "set from noise floor" or tick "adaptive".
>
> ⚠ 如果关闭阈值低于实测底噪，面板顶部会出现黄色告警：这种配置下静噪
> 打开后永远关不掉，**一整天都不会产生任何信号记录**（信号只在静噪关闭
> 的那一刻才落库）。看到告警就点"按底噪设定"或勾上"自适应"。

Constraints (the server validates and returns 400): thresholds must be between 0 and 1, with
`close < open` (otherwise there is no hysteresis); `tail_time` between 0 and 60 seconds; `mode` one
of `absolute` / `adaptive` / `smeter`; margins 0–40 dB.

约束（服务端会校验并返回 400）：阈值必须在 0-1 之间，`close < open`（否则没有滞后），
`tail_time` 在 0-60 秒，`mode` 只能是 `absolute` / `adaptive` / `smeter`，余量 0-40 dB。

Three lines are drawn on the RMS curve at the same time: the **noise floor**, the **squelch open**
threshold and the **squelch close** threshold. Side by side they show at a glance whether the
threshold is set correctly — the open line should sit about 6 dB above the noise floor.

RMS 曲线上同时画三条线：**噪声基底线**、**静噪开启线**、**静噪关闭线**。
三条线摆在一起就能一眼看出阈值卡得对不对 —— 开启线应该在噪声基底上方约 6 dB。

> This is the definitive end to the three manual threshold adjustments (`0.65 → 0.15 → 0.10`) in
> the history of this project. Those three were really adjustments to differences in node AGC, not
> a search for "the correct threshold".
>
> 这是对历史上 `0.65 → 0.15 → 0.10` 三次手调阈值的一次性了结。
> 那三次调的其实都是节点的 AGC 差异，不是"正确的阈值"。

### 6.5 Recording browser and playback / 录音浏览与回放

The Recordings tab reads signal + analysis records from the database and shows, per row: duration,
in-band SNR, modulation type + confidence, occupied bandwidth, S-meter and file size.

"录音"标签页从数据库读信号 + 分析记录，每条显示：
时长、带内 SNR、调制类型 + 置信度、占用带宽、S-meter、文件大小。

- **▶** plays in the browser (`GET /api/recordings/{id}/audio`)
  **▶** 浏览器内直接播放（`GET /api/recordings/{id}/audio`）
- **▤** expands a **spectrogram thumbnail** plus the envelope waveform for that recording
  (`GET /api/recordings/{id}/spectrogram`; exposure scales dynamically to the 25th/99.7th
  percentile of that recording, so weak signals stay visible)
  **▤** 展开这段录音的**频谱图缩略图** + 包络波形
  （`GET /api/recordings/{id}/spectrogram`，动态曝光按这段录音自身的
  25/99.7 百分位定标，弱信号也看得清）
- The top bar filters by age (24 h / 7 days / 30 days / all), minimum SNR (6/12/20 dB), or "with
  recording only"
  顶部可按时间范围（24h / 7天 / 30天 / 全部）、最低 SNR（6/12/20 dB）筛选，
  或勾"只看有录音"

> The playback endpoint `resolve()`s the path from the database and checks that it lands inside
> the configured recordings directory; anything outside returns 404 with a warning logged. The
> listing endpoint does not hand server absolute paths to the page either.
>
> 回放接口会把数据库里的路径 `resolve()` 后校验是否落在配置的录音目录内，
> 目录外的路径一律 404 并记警告；列表接口也不会把服务器绝对路径吐给页面。

### 6.6 Auto-reconnect and node switching / 自动重连与节点切换

Web monitoring recovers from drops on its own; nobody needs to watch it:
Web 监听内置了断线恢复，不需要人守着：

- **Exponential backoff** after a disconnect: 5s → 10s → 20s → 40s → 60s, capped
  断开后**指数退避**重连：5s → 10s → 20s → 40s → 60s 封顶
- **Three consecutive failures** on one node switch to another available node and carry on
  同一节点**连续失败 3 次**自动换一个可用节点继续监听
- Reconnects, node switches and wait times are all pushed to the page over WebSocket
  重连、切换节点、等待秒数都会通过 WebSocket 推到页面上
- Session statistics show **Reconnects** and **dropped frame** counts
  会话统计里能看到 **Reconnects** 和 **丢帧** 计数

The dropped-frame count comes from `kiwi_client` tracking seq discontinuities in SND frames — a
number that keeps climbing means this node or your own network is losing packets, and switching
node usually fixes it.

丢帧数来自 `kiwi_client` 按 SND 帧 seq 跳变的统计 —— 数字持续上涨说明这个节点
或你的网络在丢包，换个节点通常就好了。

### 6.7 Statistics tab / 统计标签页

- **Frequency activity** — signal count and total duration per frequency
  **频率活跃度** —— 各频率信号数与总时长
- **Modulation distribution** — count and **mean confidence** per class (a low mean confidence
  means this batch of verdicts is not very trustworthy)
  **调制类型分布** —— 每类的数量与**平均置信度**（平均置信度低说明这批判定不太可信）
- **In-band SNR distribution** — a bucketed histogram
  **带内 SNR 分布** —— 分档直方图
- **24-hour activity strip** — which UTC hours are busiest
  **24 小时活动热条** —— 哪个 UTC 时段最活跃

---

## 7. Reading the analysis output / 读懂分析读数

### 7.1 Core metrics / 核心指标

| Metric / 指标 | Field / 字段 | How to read it / 怎么读 |
|------|------|--------|
| **In-band SNR / 带内 SNR** | `snr_db` | Estimated only within the demodulation passband: the noise floor is a low percentile of in-band power, and `SNR = signal power with the floor removed / total floor power`. Clear speech **> 20 dB**, an intelligible weak signal 6–15 dB, **pure noise is negative**<br>只在解调通带内估算：噪声基底取带内功率的低分位数，`SNR = 扣除底噪的信号功率 / 底噪总功率`。清晰语音 **> 20 dB**，可辨认的弱信号 6-15 dB，**纯底噪是负值** |
| **Noise floor / 噪声基底** | `noise_floor_db` | The power at the `noise_percentile` percentile within the passband. It is the denominator of SNR, and the basis for judging how quiet a node is<br>通带内第 `noise_percentile` 百分位的功率。它是 SNR 的分母，也是判断"这个节点安不安静"的依据 |
| **Occupied bandwidth / 占用带宽** | `bandwidth_hz` | The frequency span holding **90% of the energy** with the noise floor removed. A pure tone is tens of Hz, CW around a hundred, speech 1–2.5 kHz<br>扣除底噪后包含 **90% 能量**的频率跨度。纯音几十 Hz，CW 百来 Hz，语音 1-2.5 kHz |
| **Old-definition bandwidth / 旧口径带宽** | `bandwidth_20db_hz` | The peak −20 dB span, **kept only to compare with historical data**; in a fixed passband it barely discriminates at all<br>峰值 -20 dB 的跨度，**只为和历史数据对照保留**，在固定通带下几乎没有区分度 |
| **Crest factor / 峰均比** | `crest_factor_db` | Speech 8–22 dB; constant-envelope data signals lower; impulse interference > 15 dB and very short<br>语音 8-22 dB；恒包络数据信号偏低；脉冲干扰 > 15 dB 且很短 |
| **Spectral flatness / 频谱平坦度** | `spectral_flatness` | The closer to 1, the more it resembles white noise / 越接近 1 越像白噪声 |
| **Envelope syllabic rate / depth<br>包络音节率 / 深度** | `envelope_rate_hz` / `envelope_depth` | Speech concentrates at **2–8 Hz** with obvious depth; FSK/PSK are constant-envelope (extremely low depth); Morse keying approaches 100% depth<br>语音音节率集中在 **2-8 Hz** 且深度明显；FSK/PSK 是恒包络（深度极低）；莫尔斯键控深度接近 100% |
| **Keying rate / 键控率** | `keying_rate_hz` | The on/off rate of CW / CW 的通断速率 |
| **Tone count / spacing / purity<br>音调数 / 间距 / 纯度** | `tone_count` / `tone_spacing_hz` / `tone_purity` | 2–4 tones at stable spacing → looks like FSK; one high-purity tone → looks like an unmodulated carrier; a large count → it is a continuous spectrum<br>2-4 个稳定间距的音调 → 像 FSK；一个高纯度音调 → 像未调载波；数出一大把 → 是连续谱 |
| **Confidence / 置信度** | `modulation_confidence` | Considers both the absolute score and the margin over the runner-up. **Below `min_confidence` (default 0.35) the output is `UNKNOWN`**<br>同时看绝对得分和领先第二名的幅度。**低于 `min_confidence`（默认 0.35）输出 `UNKNOWN`** |

### 7.2 Modulation types / 调制类型

| Label / 标签 | Meaning / 含义 | Principal evidence / 主要证据 |
|------|------|----------|
| `USB_VOICE` / `LSB_VOICE` / `AM_VOICE` | Voice / 语音 | A 2–8 Hz syllabic envelope with clear variation, occupying most of the voice band. **The sideband type comes from the demodulation mode of the receiver, it is not guessed from the audio**<br>2-8 Hz 音节包络 + 明显起伏 + 占据大半话音带。**边带类型由接收机的解调模式决定，不从音频里猜** |
| `CW` | Morse / 莫尔斯 | Very narrow with a deeply keyed envelope / 极窄 + 深度键控包络 |
| `CARRIER` | Unmodulated carrier / 未调制载波 | Very narrow, constant, energy concentrated in one high-purity tone<br>极窄 + 恒定 + 能量集中在单个高纯度音调 |
| `FSK` | Frequency-shift keying / 移频键控 | A few tones at stable spacing, constant envelope / 少数几个稳定间距的音调 + 恒包络 |
| `PSK` | Phase-shift keying / data / 相移键控 / 数据 | Constant envelope, continuous spectrum, no syllabic rate / 恒包络 + 连续谱 + 没有音节率 |
| `NOISE` | Noise floor only / 只有底噪 | In-band SNR < `noise_snr_threshold_db` (default 3 dB); every other feature is meaningless at that point<br>带内 SNR < `noise_snr_threshold_db`（默认 3 dB），此时其余特征都没有意义 |
| `UNKNOWN` | Insufficient evidence / 证据不足 | Confidence below `min_confidence`. **This is a normal output, not an error**<br>置信度低于 `min_confidence`。**这是一个正常输出，不是错误** |

Besides weighted scoring, the classifier applies a layer of **vetoes**: a 20 Hz-wide signal cannot
be a wideband data waveform however good its other features look, and FSK is constant-envelope, so
a large envelope depth vetoes it. This layer did not exist in older versions, and its absence is
exactly why everything used to tie for first place (see §13).

分类器除了加权打分还有一层**否决条件**：比如一个 20 Hz 宽的信号，
无论其它特征多好看都不可能是宽带数据波形；FSK 是恒包络的，包络深度大就否掉。
这一层是旧版本没有的，也是"什么都能并列第一"的根因（见 §13）。

### 7.3 S-meter

`s_meter_dbm` is the receive level reported by KiwiSDR, converted with
`0.1 × (raw & 0x0FFF) − 127`. The web page shows dBm, S-unit and a level bar together.

`s_meter_dbm` 是 KiwiSDR 报告的接收电平，换算式 `0.1 × (raw & 0x0FFF) − 127`。
Web 页面同时显示 dBm、S 级和电平条。

Typical values: S9 ≈ −73 dBm; the HF noise floor is commonly −110 to −95 dBm; a strong EAM
broadcast reaches around −70 dBm.

典型值：S9 ≈ -73 dBm；HF 底噪常在 -110 ~ -95 dBm；强 EAM 播发能到 -70 dBm 上下。

> **Before v1.3.0 this column was broken** (it recorded the high bytes of the frame counter), see §13.
> **v1.3.0 之前这一列是坏的**（记的是帧计数器的高位字节），见 §13。

---

## 8. Recording cleanup / 录音清理

The squelch is triggered by short noise bursts, impulse interference (a single "click") and
fluctuations in the noise floor; after a night of monitoring, junk recordings can be more than half
the total. `clean` exists to remove them in bulk.

静噪会被短促噪声爆发、脉冲干扰（"滴"一声）、底噪波动触发，跑一夜下来
垃圾录音可能占一半以上。`clean` 就是用来批量清掉它们的。

### 8.1 Criteria / 判定标准

Every WAV gets a full frequency- and time-domain analysis; meeting **any** of these makes it junk:
对每个 WAV 做完整的频域+时域分析，满足**任一**条件即判为垃圾：

| Condition / 条件 | Default / 默认阈值 | Description / 说明 |
|------|----------|------|
| Extremely short / 时长极短 | < 2.0 s | A brief noise trigger / 噪声的短促触发 |
| Very low SNR / SNR 极低 | < 5.0 dB | Almost no usable signal / 几乎没有有效信号 |
| Flat spectrum / 频谱平坦 | > 0.5 | Close to white noise / 接近白噪声 |
| Modulation type / 调制类型 | `NOISE` | The analyser said so overall / 分析器综合判定 |
| Impulse interference / 脉冲干扰 | duration < 3 s and crest factor > 15 dB<br>时长 < 3 s 且 峰均比 > 15 dB | The "click" kind / "滴"一声那种 |
| Near silence / 近乎静音 | RMS < 0.005 | Recorded, but essentially empty / 录下来了但基本是空的 |

### 8.2 Usage / 用法

```bash
# Step 1: preview first (this is the default and deletes nothing)
# 第一步：先预览（默认就是预览，不会删任何东西）
python main.py clean

# Step 2: delete once the classification looks reasonable
# 第二步：确认分类合理后再删
python main.py clean --delete

# Also clear the matching signal rows in the database
# 连带清掉数据库里对应的信号记录
python main.py clean --delete --clean-db

# Adjust the thresholds / 调阈值
python main.py clean --min-duration 3 --min-snr 8

# Specify the mode if the recordings are not USB (it sets the analysis passband)
# 录音不是 USB 的话要指定模式（决定分析通带）
python main.py clean -m AM

# See the detailed verdict for every file / 看每个文件的详细判定过程
python main.py -v clean
```

The standalone `clean_recordings.py` exposes more parameters:
独立脚本 `clean_recordings.py` 暴露了更多参数：

```bash
python clean_recordings.py --help

python clean_recordings.py \
    --recordings-dir /path/to/recordings \
    --min-duration 3 --min-snr 8 --max-flatness 0.4 \
    --impulse-max-dur 3.0 --impulse-min-crest 15.0 \
    --mode USB --delete
```

### 8.3 Output / 输出

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

> Deletion cannot be undone. **Always run once without `--delete` first** and check that the
> classification is sensible before acting. Aggressive thresholds will take weak but real signals
> with them.
>
> 删除不可撤销。**永远先跑一次不带 `--delete` 的**，看看分类结果合不合理
> 再动手。阈值设得太激进会把弱但真实的信号一起删掉。

---

## 9. Data storage and queries / 数据存储与查询

### 9.1 Directory layout / 目录结构

```
Radio/
├── data/                            <-- The whole directory is in .gitignore / 整个目录在 .gitignore 里
│   ├── recordings/                  <-- Recorded WAVs / 录音 WAV
│   │   └── 20260601_034521_11175.0kHz_KPH_California.wav
│   ├── radio_monitor.db             <-- SQLite (kept forever, appended on every run)
│   │                                    SQLite（永久保存，每次运行追加）
│   └── public_logs/                 <-- Downloaded by fetch_public_logs.py / fetch_public_logs.py 下载的公开日志
│       ├── eam_watch/
│       ├── shortwave_archive/
│       └── comparison/
└── reports/
    ├── report_20260601_0400.html
    ├── chart_*.png
    └── milradio-audit.html          <-- The signal-chain audit / 信号链审计报告
```

Recording filename format: `YYYYMMDD_HHMMSS_<frequency>kHz_<node name>.wav`
录音文件名格式：`YYYYMMDD_HHMMSS_<频率>kHz_<节点名>.wav`

### 9.2 Database tables / 数据库表

| Table / 表 | Contents / 内容 | Key fields / 关键字段 |
|----|------|----------|
| `sessions` | One row per monitoring run / 每次监听会话 | `start_time`, `end_time`, `node_host`, `node_name`, `frequencies`, `status` |
| `signals` | One row per detected signal / 每个检测到的信号 | `timestamp`, `frequency_khz`, `mode`, `duration_seconds`, `peak_rms`, `avg_rms`, `s_meter_dbm`, `recording_path` |
| `analysis` | Spectral analysis of a signal / 信号的频谱分析 | `snr_db`, `bandwidth_hz`, `estimated_modulation`, `spectral_centroid_hz`, `spectral_flatness`, `crest_factor_db`, `fft_peak_magnitudes` |
| `nodes` | Node status history / 节点状态历史 | `host`, `port`, `is_available`, `avg_latency_ms`, `total_connections`, `total_failures` |

**v1.3.0 added 6 columns to `analysis`** (migrated automatically on old databases, losing no
history):

**v1.3.0 给 `analysis` 加了 6 列**（老数据库自动补列，不丢历史数据）：

| Column / 列 | Meaning / 含义 |
|----|------|
| `modulation_confidence` | Modulation verdict confidence, 0–1 / 调制判定置信度 0-1 |
| `demod_mode` | The demodulation mode used for the analysis (it sets the passband) / 分析时用的解调模式（决定通带） |
| `noise_floor_db` | In-band noise floor / 带内噪声基底 |
| `envelope_rate_hz` | Envelope syllabic rate / 包络音节率 |
| `envelope_depth` | Envelope modulation depth / 包络调制深度 |
| `tone_count` | Number of tones detected / 检出的音调数 |

Indexes: `signals(timestamp)`, `signals(frequency_khz)`, `signals(session_id)`,
`analysis(signal_id)`.

索引：`signals(timestamp)`、`signals(frequency_khz)`、`signals(session_id)`、
`analysis(signal_id)`。

> **All data is kept permanently.** Every run appends to the same database; history is never
> overwritten.
> **所有数据永久保存。** 每次运行都追加到同一个库，历史不会被覆盖。

### 9.3 Querying SQLite directly / 直接查 SQLite

```python
import sqlite3

conn = sqlite3.connect("data/radio_monitor.db")
conn.row_factory = sqlite3.Row

# The 10 most recent signals / 最近 10 个信号
for row in conn.execute("""
    SELECT timestamp, frequency_khz, mode, duration_seconds, peak_rms, s_meter_dbm, node_name
    FROM signals ORDER BY timestamp DESC LIMIT 10
"""):
    print(f"{row['timestamp'][:19]} | {row['frequency_khz']:>8.1f} kHz | "
          f"{row['duration_seconds']:.1f}s | {row['s_meter_dbm']:.0f} dBm | {row['node_name']}")

# High-confidence verdicts only (meaningful from v1.3.0 onwards)
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

Ready-made scripts: `example_query.py` (query examples) and `example_batch_analyze.py` (batch
analysis example).

现成脚本：`example_query.py`（查询示例）、`example_batch_analyze.py`（批量分析示例）。

### 9.4 Using the `src` modules / 用 `src` 模块

```python
from src.db import Database
from src.analyzer import SignalAnalyzer
import yaml

config = yaml.safe_load(open("config.yaml", encoding="utf-8"))
db = Database("data/radio_monitor.db")

# Build the analyser through the factory so the parameters match monitoring exactly
# 用工厂方法建分析器，保证参数和监听时完全一致
analyzer = SignalAnalyzer.from_config(config)

result = analyzer.analyze_file("data/recordings/xxx.wav", mode="USB")
print(result["estimated_modulation"], result["modulation_confidence"], result["snr_db"])

# Common queries / 常用查询
db.get_signals_with_analysis(days=7, limit=100, min_snr_db=10, with_recording=True)
db.get_frequency_stats(days=30)
db.get_modulation_stats(days=30)     # With mean confidence / 带平均置信度
db.get_snr_distribution(days=30)
db.get_daily_activity(days=14)
db.get_hourly_activity(days=7)
db.get_node_signal_quality()         # Per-node reception quality, used to pick nodes
                                     # 按节点统计接收质量，用于择优

db.close()
```

`Database` also supports `with`: `with Database(path) as db: ...`
`Database` 也支持 `with` 语句：`with Database(path) as db: ...`

---

## 10. HTTP API and WebSocket / HTTP API 与 WebSocket

The server started by `python main.py web` serves the page, the REST API and WebSocket push at
once. **There is no authentication** — see the warning in §5.6.

`python main.py web` 起的服务同时提供页面、REST API 和 WebSocket 推送。
**没有鉴权** —— 见 §5.6 的提醒。

### 10.1 REST API

| Method / 方法 | Path / 路径 | Parameters / 参数 | Description / 说明 |
|------|------|------|------|
| `GET` | `/api/status` | — | Current monitoring state, RMS/S-meter history, in-band SNR, noise floor, dropped frames, reconnect count, the latest spectrum column<br>当前监听状态、RMS/S-meter 历史、带内 SNR、噪声基底、丢帧、重连次数、最近一列频谱 |
| `GET` | `/api/nodes` | — | Node list plus availability and latency from the database / 节点列表 + 数据库里的可用性与延迟 |
| `POST` | `/api/nodes/check` | — | Probe every node live (15 s timeout) / 现场探测所有节点（15s 超时） |
| `GET` | `/api/frequencies` | — | The expanded frequency list from `frequencies.yaml` / `frequencies.yaml` 展开后的频率列表 |
| `GET` | `/api/signals` | `days` (1–365, default 7) `limit` (1–1000, default 100) | Recent signal records / 最近信号记录 |
| `GET` | `/api/recordings` | `days` `limit` (≤500) `frequency` `min_snr` `with_recording` | Signal + analysis records for browsing and playback / 信号 + 分析记录，供浏览回放 |
| `GET` | `/api/recordings/{id}/audio` | — | Play back the WAV (paths restricted to the recordings directory) / 回放 WAV（路径限制在录音目录内） |
| `GET` | `/api/recordings/{id}/spectrogram` | `bins` (16–256) `cols` (16–600) | Spectrogram matrix + envelope / 频谱图矩阵 + 包络 |
| `GET` | `/api/sessions` | `limit` (1–200) | Recent monitoring sessions / 最近监听会话 |
| `GET` | `/api/stats` | `days` | Frequency activity / 24 h activity / modulation distribution / SNR distribution / activity by day<br>频率活跃度 / 24h 活动 / 调制分布 / SNR 分布 / 按天活动 |
| `GET` | `/api/squelch` | — | Current mode and thresholds + measured floor + suggested values + effective thresholds + squelch state<br>当前模式/阈值 + 实测底噪 + 建议值 + 生效阈值 + 静噪开关状态 |
| `POST` | `/api/squelch` | `mode` `open_threshold` `close_threshold` `tail_time` `open_margin_db` `close_margin_db` `smeter_open_margin_db` `smeter_close_margin_db` | Adjust online, effective immediately; returns `warning` when a threshold falls below the floor<br>在线调整，立刻生效；阈值低于底噪时返回 `warning` |
| `POST` | `/api/monitor/start` | `frequency` `mode` `node_host` | Start monitoring / 开始监听 |
| `POST` | `/api/monitor/stop` | — | Stop monitoring / 停止监听 |

```bash
# Check the current state / 看一眼当前状态
curl -s localhost:8888/api/status | python -m json.tool

# Set thresholds to 0.06 / 0.05 from the noise floor / 按底噪把阈值调到 0.06 / 0.05
curl -s -X POST localhost:8888/api/squelch \
     -H 'Content-Type: application/json' \
     -d '{"open_threshold":0.06,"close_threshold":0.05}'

# Or just hand it to adaptive mode (the threshold follows the measured floor)
# 或者直接交给自适应（阈值跟着实测底噪走）
curl -s -X POST localhost:8888/api/squelch \
     -H 'Content-Type: application/json' -d '{"mode":"adaptive"}'

# Start monitoring / 启动监听
curl -s -X POST localhost:8888/api/monitor/start \
     -H 'Content-Type: application/json' \
     -d '{"frequency":11175,"mode":"USB"}'

# Pull the last 7 days of records with SNR >= 12 dB that have a recording
# 拉最近 7 天 SNR ≥ 12 dB 且有录音的记录
curl -s 'localhost:8888/api/recordings?days=7&min_snr=12&with_recording=1'
```

Validation rules: frequency 100–30000 kHz; mode one of `USB/LSB/AM/CW/CWN`; squelch thresholds
0–1 with `close < open`; `tail_time` 0–60 seconds; squelch `mode` one of
`absolute/adaptive/smeter`; margins `*_margin_db` 0–40 dB. Anything invalid returns 400 with a
Chinese error message.

校验规则：频率 100-30000 kHz；模式仅 `USB/LSB/AM/CW/CWN`；
静噪阈值 0-1 且 `close < open`；`tail_time` 0-60 秒；静噪 `mode` 仅
`absolute/adaptive/smeter`；余量 `*_margin_db` 0-40 dB。不合法一律 400 + 中文错误信息。

### 10.2 WebSocket `/ws`

On connect you first receive one `init` message (current state + the 20 most recent signals + the
last 200 RMS history points + passband + spectrum bin count), then a continuous stream:

连上先收一条 `init`（当前状态 + 最近 20 条信号 + 最近 200 点 RMS 历史 +
通带 + 频谱格数），之后持续收推送：

| `type` | When / 时机 | Main fields / 主要字段 |
|--------|------|----------|
| `init` | On connect / 连接建立 | `monitoring` `frequency` `mode` `passband` `spectrum_bins` `recent_signals` `rms_history` |
| `spectrum` | Roughly every 170 ms / 每约 170 ms | `bins`(128) `f_max` `snr_db` `noise_floor_db` `peak_frequency_hz` `signal_active` |
| `realtime` | Every 5 audio blocks / 每 5 个音频块 | `rms` `smeter` `snr_db` `noise_floor_db` `rms_noise_floor` `dropped_frames` `total_signals` `elapsed` `reconnects` `squelch_mode` `effective_open` `effective_close` `threshold_below_floor` `signal_seconds` |
| `signal_detected` | Squelch closed and the recording stored / 静噪关闭、录音落库后 | `signal_id` `duration` `peak_rms` `smeter_dbm` `snr_db` `bandwidth_hz` `modulation` `modulation_confidence` `has_audio` |
| `monitor_started` / `monitor_stopped` | Start/stop / 开始/停止 | `frequency` `mode` `node` `passband` / `total_reconnects` |
| `reconnecting` / `reconnect_wait` / `reconnected` / `node_switch` | Drop recovery / 断线恢复 | `message` `attempt` `seconds` `node` |
| `squelch_updated` | A threshold or mode changed / 阈值或模式被改 | `open_threshold` `close_threshold` `tail_time` `mode` `effective_open` `effective_close` `warning` |
| `error` | Monitoring error / 监听异常 | `message` |

Clients may send `{"action":"ping"}`; the server replies `{"type":"pong"}`.
客户端可以发 `{"action":"ping"}`，服务端回 `{"type":"pong"}`。

---

## 11. Public log download and comparison / 公开日志下载与对比

Pulls HFGCS/EAM monitoring logs from public sources and compares them with your own data, to
verify that the monitoring system really is working.

从公开来源抓 HFGCS/EAM 监听日志，和你自己的数据对比，用来验证监听系统是不是真的在工作。

### Sources / 数据来源

| Source / 来源 | Contents / 内容 |
|------|------|
| [EAM.watch](https://eam.watch) | EAM / Skyking messages: sender, timestamp, encrypted content, recording links<br>EAM / Skyking 消息：发送者、时间戳、加密内容、录音链接 |
| [Shortwave Archive](https://shortwavearchive.com) | Shortwave recordings uploaded by enthusiasts / 爱好者上传的短波录音存档 |

### Usage / 用法

```bash
python fetch_public_logs.py                          # All sources, last 7 days / 所有来源，最近 7 天
python fetch_public_logs.py --source eam             # EAM.watch only / 只抓 EAM.watch
python fetch_public_logs.py --source archive         # Shortwave archive only / 只抓短波存档
python fetch_public_logs.py --days 30
python fetch_public_logs.py --compare                # Fetch, then compare against local data
                                                     # 抓完顺便和本地数据对比
python fetch_public_logs.py --source archive --download-audio   # Also download audio (max 10)
                                                                # 连音频一起下（最多 10 个）
```

### Output / 输出

```
data/public_logs/
├── eam_watch/
│   ├── messages_20260601.json
│   └── messages_20260601.csv        # Opens directly in Excel / Excel 可直接打开
├── shortwave_archive/
│   ├── recordings_20260601.json
│   ├── recordings_20260601.csv
│   └── audio/
└── comparison/
    ├── comparison_20260601.html
    └── comparison_20260601.json
```

### What `--compare` does / `--compare` 做了什么

1. Reads the signal records from the local `data/radio_monitor.db`
   读本地 `data/radio_monitor.db` 的信号记录
2. Reads the public logs already downloaded
   读已下载的公开日志
3. Matches on a **±5 minute time window** and frequency
   按**时间窗口 ±5 分钟**和**频率**匹配
4. Generates an HTML report / 生成 HTML 报告：

| Dimension / 维度 | Description / 说明 |
|------|------|
| Matched / 匹配成功 | Present on both sides → your system is working / 两边都有 → 你的系统工作正常 |
| Local only / 仅本地检测 | You caught it, the public log did not → possibly your own find, possibly a false trigger<br>你捕到了、公开日志没有 → 可能是你独有的发现，也可能是误触发 |
| Public only / 仅公开记录 | In the public log, missed by you → **a miss**, usually a squelch threshold set too high or a poorly chosen node<br>公开日志有、你没捕到 → **漏检**，通常意味着静噪阈值太高或节点选得不好 |
| Frequency activity / 频率活跃度 | Local versus public / 本地 vs 公开的对比 |

An EAM message looks like / EAM 消息示例：

```json
{
  "type": "ALLSTATIONS",
  "sender": "SHORTHAND",
  "time": "2026-06-01 15:07:00",
  "message": "CBVN2JRPNDH5D25YZZ6KUHJULNOOMM",
  "recordings": [{"link": "https://eamwatch-production.s3.amazonaws.com/recordings/..."}]
}
```

> EAM content is one-time-pad encrypted. **It cannot be broken and there is no point trying.**
> All that can be done here is comparing timestamps and frequencies to verify monitoring coverage.
>
> EAM 内容是一次性密码本加密的，**解不开也不用试**。这里能做的只有对比
> 时间戳和频率来验证监听覆盖率。

There is also `compare_eam.py`: it downloads EAM.watch recordings directly, computes their RMS and
compares that with the squelch thresholds in your `config.yaml` — the tool for answering "why did
I not detect this EAM?".

另有 `compare_eam.py`：直接下载 EAM.watch 的录音、算它们的 RMS，
和你 `config.yaml` 里的静噪阈值对比 —— 用来回答"为什么我没检测到这条 EAM"。

---

## 12. Helper scripts and tests / 辅助脚本与测试

### 12.1 Script list / 脚本清单

| Script / 脚本 | Purpose / 用途 |
|------|------|
| `fetch_nodes.py` | Pull KiwiSDR nodes that are online with a free channel from rx.linkfanel.net<br>从 rx.linkfanel.net 拉当前在线、有空闲通道的 KiwiSDR 节点 |
| `fetch_public_logs.py` | Download public monitoring logs and compare (§11) / 下载公开监听日志并对比（§11） |
| `compare_eam.py` | Download EAM.watch recordings, compute RMS, compare with local squelch thresholds<br>下载 EAM.watch 录音，算 RMS 对比本地静噪阈值 |
| `download_eam_today.py` | Download today's EAM recordings / 下载当天的 EAM 录音 |
| `clean_recordings.py` | Recording cleanup (§8), with more threshold options than `main.py clean`<br>录音清理（§8），比 `main.py clean` 多几个阈值参数 |
| `analyze_recordings.py` | Batch-analyse given recordings and decide signal versus noise<br>批量分析指定录音，判断是信号还是噪音 |
| `diagnose_network.py` | Work through connection problems layer by layer: DNS → TCP → WebSocket handshake → KiwiSDR protocol<br>逐层排查连接问题：DNS → TCP → WebSocket 握手 → KiwiSDR 协议 |
| `diagnose_rms.py` | Measure audio RMS after a full handshake, to set the squelch threshold<br>完整握手后实测音频 RMS，用来定静噪阈值 |
| `inspect_frames.py` | Print the raw SND frame structure, for protocol work / 打印原始 SND 帧结构，调协议时用 |
| `check_signals.py` / `check_recent.py` / `check_detail.py` | Quick database checks: sessions, recent signals, one record in detail<br>快速查库：会话、最近信号、单条明细 |
| `example_query.py` / `example_batch_analyze.py` | Query and batch-analysis examples / 数据查询与批量分析示例 |

> **Order of investigation when nodes will not connect**: `python main.py nodes` → if everything
> fails, `python diagnose_network.py` to see which layer it stops at → failing at DNS/TCP means it
> is your network environment (a firewall blocking port 8073 is very common).
>
> **连不上节点时的排查顺序**：`python main.py nodes` → 全灭的话
> `python diagnose_network.py` 看卡在哪一层 → DNS/TCP 就通不过说明是网络环境
> （防火墙拦 8073 端口很常见）。

### 12.2 Tests / 测试

```bash
pip install pytest
python -m pytest tests -q
```

```
........................................................................ [100%]
208 passed
```

| File / 文件 | Count / 数量 | Coverage / 覆盖 |
|------|------|------|
| `tests/test_receiver_reconnect.py` | 32 | Alternative-node selection, reconnect main loop, session not fragmented, duration not renewed, quality ordering, returning to the preferred node<br>备用节点挑选、重连主循环、会话不被切碎、时长不被续期、质量排序、回首选节点 |
| `tests/test_schedule.py` | 31 | Parsing `active_hours`, UTC windows, wrap-around across midnight, advisory-only behaviour<br>`active_hours` 解析、UTC 时段、跨零点回绕、只提示不拦截 |
| `tests/test_smeter_squelch.py` | 26 | The S-meter criterion, including the two historical failures: audio pinned by AGC with quiet RF, and unchanging audio with a rising S-meter<br>S-meter 判据，含两个历史故障：AGC 钉住音频而射频安静、音频不动但 S-meter 抬起 |
| `tests/test_web_api.py` | 23 | Recording browse/playback/spectrogram, **path-traversal protection**, squelch adjustment validation<br>录音浏览/回放/频谱图、**路径穿越防护**、静噪调整校验 |
| `tests/test_analyzer.py` | 22 | In-band SNR, stopband excluded, passband follows the mode, per-type classification, bandwidth discrimination, ties emitting UNKNOWN<br>带内 SNR、阻带不参与计算、通带随模式变化、各调制类型分类、带宽区分度、并列输出 UNKNOWN |
| `tests/test_squelch.py` | 20 | State machine, deque buffer, pre-roll, factory method, floor tracking while open, force-close<br>状态机、deque 缓冲、pre-roll、工厂方法、打开期间继续统计底噪、强制收尾 |
| `tests/test_kiwi_client.py` | 18 | Constructed SND frames asserting sample count/seq/S-meter, and **no 23.4 Hz frame-rate harmonic** after stitching frames<br>构造 SND 帧断言样本数/seq/S-meter、多帧拼接后**不再出现 23.4 Hz 帧率谐波** |
| `tests/test_db.py` | 14 | Session CRUD, signal records, analysis storage, automatic column migration, statistics queries, node signal quality<br>会话 CRUD、信号记录、分析保存、自动补列、统计查询、节点接收质量 |
| `tests/test_node_manager.py` | 13 | Tiering rules, choosing between "low latency but deaf" and "higher latency but has heard something", degrading to latency-only on a database error<br>分档规则、"低延迟但听不见"与"高延迟但收到过"之间怎么选、数据库报错退化成按延迟挑 |
| `tests/test_clean_recordings.py` | 9 | Junk classification criteria, preview mode deleting nothing, threshold options<br>垃圾判定标准、预览模式不删文件、阈值参数 |

---

## 13. Upgrading from an older version / 从旧版本升级

v1.3.0 fixed the three measurement defects recorded in the
[signal-chain audit](reports/milradio-audit.html). All three are **wrong readings**, not crashes —
which means older versions were quietly handing you incorrect data.

v1.3.0 修掉了[信号链审计](reports/milradio-audit.html)里记的三处读数缺陷。
这三处都是**读数错误**，不是崩溃 —— 也就是说旧版本一直在安静地给你错数据。

### 13.1 Defect 01: SND frame parsing off by 2 bytes / 缺陷 01：SND 帧解析偏移 2 字节

The old code treated the SND frame body as `flags(1) + seq(2, big-endian) + smeter(2)`, whereas the
real KiwiSDR layout is `flags(1) + seq(4, little-endian) + smeter(2, big-endian)`. The whole frame
was off by 2 bytes:

旧代码把 SND 帧 body 当成 `flags(1) + seq(2, 大端) + smeter(2)`，
而 KiwiSDR 的真实布局是 `flags(1) + seq(4, 小端) + smeter(2, 大端)`。整帧偏移 2 字节：

- **The S-meter column recorded the high bytes of the frame counter** — 744 records held only 16
  distinct values, all multiples of 256, and 676 of them were a constant −160 dBm;
  **S-meter 列记的是帧计数器的高位字节** —— 744 条记录只有 16 个取值，
  全是 256 的整数倍，其中 676 条恒为 -160 dBm；
- Audio started 2 bytes early, **injecting one fake sample per frame** — producing a
  `12000/512 = 23.4375 Hz` frame-rate harmonic hum across all 747 recordings.
  音频起点提前 2 字节，**每帧多注入 1 个假样本** —— 在全部 747 段录音里
  产生 `12000/512 = 23.4375 Hz` 的帧率谐波嗡声。

Fixed at the same time: the dBm conversion (`raw/65535×150−160` → `0.1 × (raw & 0x0FFF) − 127`),
frame-type detection (comparing the full 3-byte tag, so frames such as `STA` are no longer taken
for audio), and dropped-frame counting from seq discontinuities.

同时修了 dBm 换算（`raw/65535×150−160` → `0.1 × (raw & 0x0FFF) − 127`）、
帧类型判断（比完整 3 字节 tag，`STA` 之类的帧不会再被当成音频）、
并按 seq 跳变统计丢帧。

### 13.2 Defect 02: modulation decided by dictionary order / 缺陷 02：调制识别由字典顺序决定

The old scorer gave bandwidth 2 points, flatness 1 and crest factor 1. But in a fixed 300–3000 Hz
passband, bandwidth measures **the receiver filter**: the mean bandwidth of three "different"
modulations differed by only 25 Hz. Replaying all 744 records: **690 (98.4%) were three-way ties**,
and the output was decided by the order in which `MODULATION_PROFILES` happened to be written —
"93% of signals are USB voice" only because `USB_VOICE` was listed first.

旧打分器给带宽 2 分、平坦度 1 分、峰均比 1 分。但在固定 300-3000 Hz 通带下，
带宽量的其实是**接收机的滤波器**，三种"不同"调制的平均带宽只差 25 Hz。
重放全部 744 条记录：**690 条（98.4%）三向并列**，最终由 `MODULATION_PROFILES`
的书写顺序决定输出 —— "93% 的信号是 USB 语音"只是因为 `USB_VOICE` 写在第一个。

It now uses: envelope syllabic rate and depth, keying rate, tone count and spacing, tone purity,
and occupied bandwidth with the floor removed; continuous membership weighting plus veto
conditions; a confidence figure, returning `UNKNOWN` when the evidence is thin; a new `CARRIER`
class; and USB/LSB/AM taken from the demodulation mode rather than guessed from the audio.

现在换成：包络音节率与深度、键控率、音调数与间距、音调纯度、扣除底噪的占用带宽；
连续隶属度加权 + 否决条件；输出置信度，证据不足返回 `UNKNOWN`；
新增 `CARRIER` 类别；USB/LSB/AM 由解调模式决定而不是从音频里猜。

### 13.3 Defect 03: SNR used the filter stopband as its noise reference / 缺陷 03：SNR 把滤波器阻带当噪声基准

The old implementation took ±10% either side of the peak as signal and everything else as noise.
But the spectrum spans 0–6000 Hz while a signal can only be in 300–3000 Hz — the "noise region"
was packed with a wide stretch of filter stopband holding almost no energy, dragging the
denominator down. The symptom: of 744 records, only 6 had an SNR above 10 dB.

旧实现取峰值两侧 ±10% 当信号、其余当噪声。但频谱跨 0-6000 Hz，
信号只可能在 300-3000 Hz —— "噪声区"里塞了一大片几乎没有能量的滤波器阻带，
分母被压低。症状是 744 条记录里只有 6 条 SNR 超过 10 dB。

The noise floor is now estimated from a low percentile inside the demodulation passband. New
`src/modes.py` makes the demodulation filter and the audio passband share one table — the root
cause of this defect was each end keeping its own copy.

现在在解调通带内用低分位数估噪声基底。新增 `src/modes.py`，
让解调滤波器和音频通带共用同一张表 —— 这个缺陷的根因就是收发两端各写各的。

### 13.4 What you need to do / 你需要做什么

| Item / 事项 | Description / 说明 |
|------|------|
| **Database / 数据库** | Nothing. The 6 new columns on `analysis` are added the next time the database is opened, and history is preserved<br>什么都不用做。`analysis` 表的 6 个新列在下次打开数据库时自动补上，历史数据不丢 |
| **Hum in old recordings / 旧录音的嗡声** | **Cannot be fixed.** The fake samples are already written into the WAV files. Clean recordings require re-recording<br>**改不掉**。假样本已经写进 WAV 文件了。想要干净的录音只能重录 |
| **Old S-meter values / 旧 S-meter 值** | Still frame-counter residue. **Do not compare them with new data**; split the analysis by date<br>仍然是帧计数器残留，**不要和新数据混在一起比较**。建议按时间切开看 |
| **Old modulation verdicts / 旧调制判定** | Heavily skewed towards `USB_VOICE` and untrustworthy. Records that still have a WAV can be re-run with `main.py analyze`<br>分布严重偏向 `USB_VOICE`，不可信。对还有 WAV 文件的记录可以用 `main.py analyze` 重跑 |
| **Old SNR values / 旧 SNR 值** | Systematically low. Re-running is recommended as well / 系统性偏低。同样建议重跑 |
| **Configuration / 配置文件** | The `analysis` section can gain `noise_percentile` / `noise_snr_threshold_db` / `min_confidence`. It also runs without them; the code has defaults<br>`analysis` 段可以加 `noise_percentile` / `noise_snr_threshold_db` / `min_confidence`。不加也能跑，代码里有默认值 |
| **`bandwidth_hz` changed meaning / `bandwidth_hz` 的含义变了** | From "peak −20 dB span" to "90% energy occupied bandwidth". The old definition is retained in `bandwidth_20db_hz`<br>从"峰值 -20 dB 跨度"变成"90% 能量占用带宽"。旧口径保留在 `bandwidth_20db_hz` |

To re-run historical recordings / 想重跑历史录音：

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

> Note that this **appends** new `analysis` rows and does not delete the old ones — take the latest
> by `timestamp` when querying.
>
> 注意这会**追加**新的 `analysis` 行，不会删掉旧行 —— 查询时按 `timestamp`
> 取最新的一条。

### 13.5 The web page / Web 页面

v1.3.0 rebuilt the monitoring page. `analyzer.get_spectrogram()` had been written long ago and
never called by the UI, and the 747 recordings could only be dug out of a folder. The spectrum
waterfall, recording playback, spectrogram thumbnails, a real S-meter, in-band SNR, online squelch
calibration and the statistics tab are all on the page now (§6).

v1.3.0 把监听页面重做了。`analyzer.get_spectrogram()` 早就写好却从没被界面调用过，
747 段录音过去只能去文件夹里翻。现在频谱瀑布、录音回放、频谱图缩略图、
真实 S-meter、带内 SNR、静噪在线标定、统计页都在页面上（§6）。

---

## 14. FAQ / 常见问题

### Q: No node is available at all? / 所有节点都不可用？

Most likely your network is blocking outbound port 8073 (firewalls, VPNs and campus networks do
this often).

多半是网络环境挡了出站的 8073 端口（防火墙/VPN/校园网很常见）。

1. Run `python diagnose_network.py` first to see which layer it stops at (DNS / TCP / WebSocket /
   protocol)
   先跑 `python diagnose_network.py` 看卡在哪一层（DNS / TCP / WebSocket / 协议）
2. Visit <http://rx.kiwisdr.com> and find a node that opens in your browser
   去 <http://rx.kiwisdr.com> 在浏览器里找能打开的节点
3. Add the working ones to `config.yaml`, or run `python fetch_nodes.py` to pull a batch that is
   currently online
   把能用的加进 `config.yaml`，或者跑 `python fetch_nodes.py` 拉一批在线的
4. KiwiSDR channels are limited and nodes fill up at peak times — try another hour or another node
   KiwiSDR 通道有限，高峰期满员会连不上 —— 换个时段或换个节点

### Q: Monitored for hours with no signal at all? / 监听了很久一个信号都没有？

**Look at "squelch state" in the left-hand Squelch panel first.** One glance separates two opposite
situations:

**先看左侧 Squelch 面板的"静噪状态"**，它一句话就能把两种完全相反的
情况分开：

- **Always "closed"** — the threshold is too high; signals never crossed the open threshold.
  **一直是"关"** —— 阈值太高，信号根本没越过开启阈值
- **Always "open (recording)"** — the threshold is **too low** (below the noise floor). Signals are
  only stored at the moment the squelch *closes*, so a squelch that cannot close produces no
  records at all: Signals stays at 0 on screen while the recorder keeps writing to disk in the
  background. The panel shows a yellow warning for this, and the log says `[SQUELCH-STUCK]`.
  **一直是"开 (录制中)"** —— 阈值**太低**（低于底噪）。信号只在静噪
  **关闭**的那一刻才落库，静噪关不掉就一条记录都不会有，界面上的
  Signals 会一直停在 0，而录音机在后台一直写盘。这种情况面板顶部会有
  黄色告警，日志里是 `[SQUELCH-STUCK]`

In order of likelihood / 按可能性排序：

1. **The squelch threshold is wrong** (most common). Tick "adaptive", or press "set from noise
   floor" (§6.4). Compare the threshold against **your own measured floor**, never against a
   number from a different receiver.
   **静噪阈值不对**（最常见）。勾上"自适应"，或点"按底噪设定"（§6.4）。
   注意阈值要和**实测底噪**比，不要和别的接收机的经验值比
2. HFGCS is not continuously active anyway; EAM broadcasts come in gaps.
   HFGCS 本来就不是一直有信号，EAM 播发有间隔
3. The frequency does not match the hour — check `active_hours` in `frequencies.yaml`: 11175 by
   day, 4724 at night, 8992 around the clock.
   频率和时段对不上 —— 参考 `frequencies.yaml` 里的 `active_hours`，
   白天 11175、夜间 4724、全天 8992
4. The node is too far away or propagation is poor — prefer North American or European nodes for
   HFGCS.
   节点位置太远、传播条件不好 —— 收 HFGCS 优先用北美/欧洲的节点
5. No audio is arriving at all — the `[MONITORING]` log line of the CLI `monitor` carries
   `frames=`; if that is not increasing, the problem is the link, not the squelch.
   根本没收到音频 —— 命令行 `monitor` 的 `[MONITORING]` 日志里有
   `frames=`，它不涨就是链路的问题，和静噪无关

### Q: What are those lines on the RMS curve? / RMS 曲线上那几条线分别是什么？

- **Red solid/dashed line (brighter)** = the squelch **open** threshold. Recording starts when RMS
  crosses it
  **红色实/虚线（较亮）** = 静噪**开启**阈值。RMS 越过它就开始录
- **Red dashed line (dimmer)** = the squelch **close** threshold, below the open threshold, giving
  hysteresis against rapid toggling
  **红色虚线（较淡）** = 静噪**关闭**阈值，比开启阈值低，形成滞后防止频繁开关
- **Grey dashed line** = the measured **noise floor** (the 10th percentile of RMS over a 10-minute
  window, measured even while the squelch is open)
  **灰色虚线** = 实测的**噪声基底**（10 分钟窗口内 RMS 的第 10 百分位，
  静噪开着的时候也照常统计）

The threshold should sit about 6 dB (≈ 2×) above the noise floor. Side by side, the three lines
show at a glance whether it is set right: **the open line dropping below the grey line** means the
squelch will stay open and not a single record will appear.

阈值应该在噪声基底上方约 6 dB（≈ 2 倍）。三条线并排就能一眼看出卡得对不对：
**开启线掉到灰线下面** = 静噪会一直开着，一条记录都不会出。

### Q: S-meter stuck at −160 dBm / low-frequency hum in recordings? / S-meter 一直是 -160 dBm / 录音里有低频嗡声？

That is the pre-v1.3.0 bug, now fixed — see §13.1. **The fix only affects new recordings**: the
fake samples inside old WAVs and the old S-meter values in the database cannot be repaired, so do
not mix them with new data.

这是 v1.3.0 之前的 bug，已修复，见 §13.1。**修复只对新录音生效** ——
旧 WAV 里的假样本和数据库里的旧 S-meter 值改不掉，不要和新数据混着比。

### Q: Modulation keeps coming out as `UNKNOWN`? / 调制类型老是输出 `UNKNOWN`？

That is **by design**, not a fault: it does not guess when the evidence is thin. Check two numbers
first:

这是**设计行为**，不是故障：证据不足时它不硬猜。先看两个数：

- **In-band SNR** — below 3 dB it is classified `NOISE` outright, and the other features are
  meaningless anyway
  **带内 SNR** —— 低于 3 dB 会直接判 `NOISE`，其余特征本来就没意义
- **Recording length** — envelope analysis needs enough samples; something too short has its
  confidence forced down
  **录音时长** —— 包络分析需要足够长的样本，太短会被强制压低置信度

If the signal really is good and it is still `UNKNOWN`, lower `analysis.min_confidence` in
`config.yaml` a little from 0.35. **Do not set it to 0** — that reverts to the old behaviour of
picking whatever comes first in the dictionary.

如果确认信号是好的但还是 `UNKNOWN`，可以把 `config.yaml` 的
`analysis.min_confidence` 从 0.35 调低一点。**别调到 0** —— 那就退回旧版本
"按字典顺序挑第一个"的行为了。

### Q: Too many recordings, the disk is filling up? / 录音文件太多，磁盘要满了？

```bash
python main.py clean                       # Preview first / 先预览
python main.py clean --delete --clean-db   # Delete once confirmed, database rows included
                                           # 确认后删，连数据库记录一起清
```

Details in §8. For long-running setups, take the opportunity to re-calibrate the squelch threshold
against the noise floor — a lot of junk recordings usually means the threshold is set too low.

详见 §8。长期跑的话建议顺手把静噪阈值按底噪重新标一次 —— 垃圾录音多
通常说明阈值卡得太低。

### Q: The thresholds are all wrong after switching node? / 换了节点之后阈值全不对了？

Expected. The noise floor RMS depends on the AGC setting of the far-end node, so it necessarily
changes. You do not need to edit the config and restart: adjust it online in the left-hand Squelch
panel of the web UI, or just press "set from noise floor".

正常。底噪 RMS 取决于对面节点的 AGC 设置，换节点必然变。
不用改配置文件重启：Web 界面左侧 Squelch 面板在线调，或者直接点"按底噪设定"。

### Q: Can I open the web page to other people? / Web 页面能开给别人用吗？

Technically yes (the default is `--host 0.0.0.0`), but **the server has no authentication at all**:
anyone who can reach the port can control your monitoring and play back your recordings. On an
untrusted network use `--host 127.0.0.1`, or put an authenticating reverse proxy in front.

技术上可以（默认 `--host 0.0.0.0`），但**服务端没有任何鉴权**：
任何能访问这个端口的人都能控制你的监听、回放你的录音。
放在不可信网络里请加 `--host 127.0.0.1`，或者用带认证的反向代理挡一层。

### Q: Does it recover automatically if the link drops? / 监听时断线了会自动恢复吗？

Yes. Both the CLI `monitor` and web monitoring use exponential backoff plus an automatic node
switch after three consecutive failures (see §6.6).

会，命令行 `monitor` 和 Web 监听都是指数退避重连 + 连续失败 3 次自动换节点
（见 §6.6）。

Public nodes kick users off by design; this is not a fault. K1VL enforces `ip_limit=240`
(240 minutes per IP per day) and busy nodes answer `too_busy` outright. Reconnecting to the same
node is pointless once you hit a quota, which is why consecutive failures move to the next node —
and alternatives are drawn only from `nodes` in `config.yaml`, because only there does each node
carry its calibrated `man_gain`.

公共节点本来就会主动踢人，这不是故障：K1VL 是 `ip_limit=240`（单 IP 每天
240 分钟），忙的节点直接回 `too_busy`。撞上配额时重连原节点没用，所以连续
失败会换到下一个节点 —— 备用节点只从 `config.yaml` 的 `nodes` 里挑，因为
只有那里有每个节点标定过的 `man_gain`。

After being forced away, the CLI `monitor` retries the preferred node every 30 minutes and switches
back once it recovers.

被迫换走之后，命令行 `monitor` 每 30 分钟会回首选节点试一次，节点恢复了就切回去。

The only remaining difference between the two is the number of frequencies: the CLI `monitor` can
rotate over several, while the web UI listens to one at a time.

两者的区别只剩频率数：命令行 `monitor` 能多频轮询，Web 一次只听一个频率。

---

## Disclaimer / 免责声明

This project only receives and analyses **publicly transmitted** radio signal metadata, through the
worldwide network of public KiwiSDR receivers. It decrypts nothing (EAM traffic is one-time-pad
encrypted and cannot be broken anyway), transmits nothing and interferes with nothing. Check the
rules on radio monitoring in your own jurisdiction before using it.

本项目只接收和分析**公开的**无线电信号元数据，通过全球公开的 KiwiSDR 接收机网络。
不解密任何内容（EAM 是一次性密码本加密的，也解不开），不发射，不干扰。
使用前请确认所在司法辖区对无线电监听的相关规定。
