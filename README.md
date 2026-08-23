# MilRadio

**Military radio signal reception and metadata analysis** — receives HF signals over the
worldwide network of public KiwiSDR receivers, records automatically on squelch, analyses
the spectrum, classifies modulation, writes all metadata into SQLite, and ships with a
live web listening interface.

**军事无线电信号接收与元数据分析系统** —— 通过全球公开的 KiwiSDR 网络接收 HF 信号，
自动静噪录音、频谱分析、调制识别，全部元数据落进 SQLite，附带实时 Web 监听界面。

Current version **v1.4.2** · 当前版本 **v1.4.2**

> **All documentation in this repository is bilingual: English first, Chinese second.**
> 本仓库所有文档均为中英双语，英文在前、中文在后。

---

## Documentation map / 文档导航

| Document / 文档 | Contents / 内容 |
|------|------|
| **[USAGE.md](USAGE.md)** | **Complete manual** — install, configuration, every command, web UI, API, data model, upgrade notes, FAQ<br>**完整使用说明** —— 安装、配置、全部命令、Web 界面、API、数据结构、升级须知、FAQ |
| [CHANGELOG.md](CHANGELOG.md) | Release history / 版本更新日志 |
| [reports/milradio-audit.html](reports/milradio-audit.html) | Signal-chain audit: full reproduction and evidence for three measurement defects<br>信号链审计报告：三处读数缺陷的完整复现与证据 |
| [walkthrough.md](walkthrough.md) / [implementation_plan.md](implementation_plan.md) | Design and implementation records / 设计与实现记录 |
| [BUGFIX_NOTES.md](BUGFIX_NOTES.md) | Early diagnosis log: protocol parsing and squelch thresholds<br>早期问题诊断记录：协议解析与静噪阈值 |

> Coming from v1.2 or earlier? Read [USAGE.md §13 Upgrading](USAGE.md#13-upgrading-from-an-older-version--从旧版本升级) first:
> v1.3.0 fixed three **measurement defects**, so old S-meter, SNR and modulation values
> cannot be compared against new ones.
>
> 用过 v1.2 及更早版本的，先看 [USAGE.md §13 从旧版本升级](USAGE.md#13-upgrading-from-an-older-version--从旧版本升级)：
> v1.3.0 修了三处**读数缺陷**，旧的 S-meter、SNR、调制类型数据不能和新数据混着比。

---

## What it can do / 能做什么

- **Receive / 接收** — connects to public KiwiSDR nodes worldwide, demodulates USB/LSB/AM/CW,
  reconnects automatically and switches node on failure
  连接全球公开 KiwiSDR 节点，USB/LSB/AM/CW 解调，断线自动重连并切换节点
- **Record / 录音** — squelch-triggered recording with pre-roll and tail delay, automatic
  segmentation for over-long transmissions
  静噪自动开录，带 pre-roll 和尾部延迟，超长自动分段
- **Analyse / 分析** — in-band SNR, occupied bandwidth, envelope syllabic rate and tonal
  structure → modulation type **plus a confidence figure**; emits `UNKNOWN` instead of
  guessing when the evidence is thin
  带内 SNR、占用带宽、包络音节率、音调结构 → 调制类型 **+ 置信度**，
  证据不足时输出 `UNKNOWN` 而不是硬猜
- **Live interface / 实时界面** — spectrum + waterfall, a real S-meter, recording browser and
  playback, spectrogram thumbnails, and **online squelch calibration** (set the threshold from
  the measured noise floor without stopping the monitor)
  频谱 + 瀑布图、真实 S-meter、录音浏览回放、频谱图缩略图、
  **静噪在线标定**（按实测底噪一键设定，不用停下监听）
- **Persistence / 持久化** — SQLite keeps sessions, signals, analyses and node state forever;
  older databases gain new columns automatically
  SQLite 永久保存会话/信号/分析/节点状态，老库自动补列
- **Reports / 报告** — HTML analysis report plus 10 charts
  HTML 分析报告 + 10 张图表
- **Cleanup / 清理** — finds and deletes the junk recordings produced by noise triggers
  自动识别并删除噪声触发产生的垃圾录音
- **Cross-checking / 交叉验证** — pulls public logs such as EAM.watch and matches them against
  local data by time and frequency to measure coverage
  抓取 EAM.watch 等公开日志，和本地数据按时间/频率比对覆盖率

---

## Install / 安装

Requires **Python 3.9+** (3.10+ recommended).
需要 **Python 3.9+**（推荐 3.10+）。

```bash
git clone https://github.com/reinhardt6678-sudo/Radio.git
cd Radio

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

`data/` and `reports/` are created automatically on first run.
`data/` 和 `reports/` 会在首次运行时自动创建。

---

## Five-minute quick start / 五分钟上手

```bash
# (1) See which KiwiSDR nodes are reachable right now
#     看哪些 KiwiSDR 节点现在能连
python main.py nodes

# (2) Open the web interface (recommended entry point: live spectrum,
#     recording playback and online squelch tuning all live here)
#     开 Web 界面（推荐入口：实时频谱、录音回放、在线调静噪都在这）
python main.py web
#     Browse to / 浏览器打开 http://localhost:8888

# --- or use the command line / 或者走命令行 ---

# Monitor the HFGCS daytime primary / 监听 HFGCS 日间主频
python main.py monitor -f 11175

# Analyse one recording / 分析一段录音
python main.py analyze data/recordings/xxx.wav

# Generate the HTML report / 生成 HTML 报告
python main.py report

# Preview junk recordings; add --delete to really remove them
# 预览垃圾录音（加 --delete 才真删）
python main.py clean
```

**Which frequency should I listen to first?** **11175 kHz** by day, **8992 kHz** around the
clock, **4724 kHz** at night — all USB.

**先听哪个频率？** 白天 **11175 kHz**、全天 **8992 kHz**、夜间 **4724 kHz**（都是 USB）。

**First run:** the default squelch criterion is `smeter` — the threshold is the measured
S-meter noise floor plus 14 dB, so it calibrates itself and you do not have to touch it.
The S-meter is read *before* the audio AGC of the node, which is why this criterion holds
whether or not the far end runs AGC. If you would rather use a fixed RMS threshold, let the
monitor run for 3–5 minutes first, then press "set from noise floor" in the left-hand
Squelch panel — the right value depends on the far-end AGC and cannot be guessed.

**第一次监听**：默认静噪判据是 `smeter` —— 阈值 = 实测 S-meter 底噪 +14 dB，
它自己会标定，不用管。S-meter 是节点在音频 AGC **之前**测的电平，所以对面开不开
AGC 都成立。想改用固定 RMS 阈值的话，先让它跑 3-5 分钟，左侧 Squelch 面板统计出
实测底噪之后点"按底噪设定" —— 阈值取决于对面节点的 AGC，猜不准的。

---

## Commands at a glance / 命令一览

| Command / 命令 | Purpose / 作用 |
|------|------|
| `python main.py nodes` | Test KiwiSDR node availability / 测试 KiwiSDR 节点可用性 |
| `python main.py scan` | Sweep the frequency library for activity / 扫描频率库，看哪个频率有活动 |
| `python main.py monitor -f 11175` | Continuous monitoring, the core feature / 持续监听（核心功能） |
| `python main.py analyze <file.wav>` | Analyse a single recording / 分析单个录音 |
| `python main.py report` | Generate the HTML analysis report / 生成 HTML 分析报告 |
| `python main.py web` | Start the live web interface / 启动实时 Web 监听界面 |
| `python main.py clean` | Clean up noise-triggered junk recordings / 清理噪声触发的垃圾录音 |

Every flag and how to read each output: **[USAGE.md §5](USAGE.md#5-command-line-reference--命令行完整参考)**.
每个命令的全部参数、输出解读见 **[USAGE.md §5](USAGE.md#5-command-line-reference--命令行完整参考)**。

---

## Project layout / 项目结构

```
Radio/
├── main.py                  CLI entry point, 7 subcommands
│                            CLI 入口（7 个子命令）
├── config.yaml              Nodes, squelch, recording, analysis, report settings
│                            节点、静噪、录制、分析、报告参数
├── frequencies.yaml         Military HF frequency library
│                            军事 HF 频率库
│                            (hfgcs / nato / military_air / digital / reference)
├── src/
│   ├── kiwi_client.py       KiwiSDR WebSocket protocol, SND frame parsing, S-meter, drop counting
│   │                        KiwiSDR WebSocket 协议、SND 帧解析、S-meter、丢帧统计
│   ├── modes.py             Single source of truth for demod filters and audio passbands
│   │                        解调滤波器与音频通带的唯一真值表
│   ├── squelch.py           RMS / S-meter squelch state machine, pre-roll buffer
│   │                        RMS / S-meter 静噪状态机、pre-roll 缓冲
│   ├── recorder.py          WAV recording and automatic segmentation
│   │                        WAV 录制与自动分段
│   ├── analyzer.py          Frequency/time-domain analysis, modulation classification,
│   │                        live spectrum, spectrogram
│   │                        频域/时域分析、调制分类、实时频谱、频谱图
│   ├── db.py                SQLite persistence, automatic column migration, statistics
│   │                        SQLite 持久化、自动补列迁移、统计查询
│   ├── receiver.py          Scan and continuous-monitor orchestration, reconnect and node switch
│   │                        扫描与持续监听编排、断线重连与换节点
│   ├── web_server.py        aiohttp service, REST API, WebSocket push
│   │                        aiohttp 服务、REST API、WebSocket 推送
│   ├── node_manager.py      Node probing and selection by reception quality
│   │                        节点探测与择优（按历史接收质量分档）
│   ├── schedule.py          active_hours parsing for the frequency library
│   │                        频率库活跃时段解析
│   └── reporter/            HTML report and charts
│                            HTML 报告与图表
├── web/index.html           Live monitoring page, single file
│                            实时监听页面（单文件）
├── tests/                   208 tests / 208 个测试
├── reports/                 Generated reports and charts + the signal-chain audit
│                            生成的报告与图表 + 信号链审计报告
└── data/                    Runtime data: recordings / database / public logs, git-ignored
                             运行时数据（录音 / 数据库 / 公开日志，不进版本库）
```

---

## Tests / 测试

```bash
pip install pytest
python -m pytest tests -q
# 208 passed
```

---

## Disclaimer / 免责声明

This project only receives and analyses **publicly transmitted** radio signal metadata,
through the worldwide network of public KiwiSDR receivers. It decrypts nothing, transmits
nothing and interferes with nothing. Check the rules on radio monitoring in your own
jurisdiction before using it.

本项目只接收和分析**公开的**无线电信号元数据，通过全球公开的 KiwiSDR 接收机网络。
不解密任何内容，不发射，不干扰。使用前请确认所在司法辖区对无线电监听的相关规定。
