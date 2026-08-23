# MilRadio — build walkthrough / 军事无线电信号接收系统 Walkthrough

> **Historical record of the initial build (v1.0).** It is kept for the design rationale, not as
> current documentation — several details below (5 subcommands, `src/reporter.py` as a single
> file, the rule-based modulation matcher) have since changed. For how the system works today,
> read [USAGE.md](USAGE.md); for what changed and why, read [CHANGELOG.md](CHANGELOG.md).
>
> **这是初版（v1.0）的建设记录**，保留下来是为了设计思路，不是当前文档 —— 下面有几处
> 已经变了（5 个子命令、`src/reporter.py` 还是单文件、基于规则的调制匹配）。
> 系统现在是怎么运作的看 [USAGE.md](USAGE.md)，改了什么、为什么改看 [CHANGELOG.md](CHANGELOG.md)。
>
> **Bilingual document: English first, Chinese second. / 本文为中英双语，英文在前、中文在后。**

## Overview / 概述

A monitoring and metadata-analysis system for military HF signals was built on top of the
worldwide network of open KiwiSDR nodes. It is implemented entirely in Python and needs no
physical hardware of its own.

成功构建了一套基于全球开源 KiwiSDR 节点网络的军事 HF 信号监听与元数据分析系统。
系统完全使用 Python 实现，无需任何物理硬件设备。

## Project layout / 项目结构

```
Radio/
├── config.yaml              # Main configuration: node list, reception/recording/analysis parameters
│                            # 主配置 (节点列表、接收/录制/分析参数)
├── frequencies.yaml         # Military frequency database: HFGCS / NATO / military air / digital
│                            # 军事频率数据库 (HFGCS/NATO/军航/数字信号)
├── requirements.txt         # Python dependencies / Python 依赖
├── main.py                  # CLI entry point (5 subcommands at v1.0) / CLI 主入口 (v1.0 时 5 个子命令)
├── src/
│   ├── __init__.py
│   ├── kiwi_client.py       # Purpose-built lightweight KiwiSDR WebSocket client
│   │                        # 自研轻量级 KiwiSDR WebSocket 客户端
│   ├── node_manager.py      # SDR node discovery, health checks, failover
│   │                        # SDR 节点发现、健康检查、故障转移
│   ├── receiver.py          # Reception controller (single/multi frequency)
│   │                        # 信号接收控制器 (单频/多频模式)
│   ├── squelch.py           # VOX squelch detection (RMS threshold + hysteresis + tail)
│   │                        # VOX 静噪检测 (RMS阈值+滞后+tail)
│   ├── recorder.py          # WAV recording (pre-roll + automatic segmentation)
│   │                        # WAV 音频录制 (pre-roll+自动分段)
│   ├── analyzer.py          # FFT spectrum analysis, SNR, modulation identification
│   │                        # FFT频谱分析、SNR、调制类型识别
│   ├── db.py                # SQLite metadata storage / SQLite 元数据存储
│   └── reporter.py          # matplotlib charts + HTML report / matplotlib 图表 + HTML 报告
├── data/
│   ├── recordings/          # Recordings / 录音文件
│   └── radio_monitor.db     # SQLite database / SQLite 数据库
└── reports/                 # Analysis reports / 分析报告
```

## Core modules / 核心模块说明

### KiwiSDR client ([src/kiwi_client.py](src/kiwi_client.py)) / KiwiSDR 客户端

- A lightweight WebSocket client written for this project, with no dependency on the external
  kiwiclient library
  自行实现的轻量级 WebSocket 客户端，不依赖外部 kiwiclient 库
- Implements the KiwiSDR handshake (SET auth → SET AR → SET mod)
  实现 KiwiSDR 握手协议 (SET auth → SET AR → SET mod)
- Parses binary audio frames (big-endian int16 PCM)
  解析二进制音频帧 (big-endian int16 PCM)
- Reads S-meter signal strength, sends keepalives automatically
  支持 S-meter 信号强度读取、自动 keepalive

### Signal analyser ([src/analyzer.py](src/analyzer.py)) / 信号分析器

- Time domain: RMS, peak, crest factor / 时域分析: RMS、峰值、峰均比 (Crest Factor)
- Frequency domain: FFT spectrum, spectral centroid, spectral flatness
  频域分析: FFT频谱、频谱质心、频谱平坦度
- Signal features: bandwidth estimation (peak −N dB method), SNR estimation
  信号特征: 带宽估算 (peak-NdB 方法)、SNR 估算
- Modulation identification: rule matching on spectral features (USB_VOICE/AM/CW/FSK/PSK)
  调制识别: 基于频谱特征的规则匹配 (USB_VOICE/AM/CW/FSK/PSK)

> Both the bandwidth definition and the modulation matcher were replaced in v1.3.0 — see
> [CHANGELOG.md](CHANGELOG.md) and [USAGE.md §13](USAGE.md#13-upgrading-from-an-older-version--从旧版本升级).
>
> 带宽口径和调制匹配器都在 v1.3.0 被换掉了 —— 见 [CHANGELOG.md](CHANGELOG.md)
> 和 [USAGE.md §13](USAGE.md#13-upgrading-from-an-older-version--从旧版本升级)。

### Frequency database ([frequencies.yaml](frequencies.yaml)) / 频率数据库

- **HFGCS**: 4724, 6712, 6739, 8992, 11175, 13200, 15016 kHz
- **NATO**: Coast Guard, AWACS, Canadian military / 海岸警卫队、AWACS、加拿大军事
- **Military aviation / 军用航空**: air traffic control, LDOC / 航管、LDOC
- **Digital signals / 数字信号**: STANAG 4285/4481
- **Calibration references / 校准参考**: WWV 5/10/15 MHz

## Usage / 使用方法

```bash
# 1. Install dependencies / 安装依赖
pip install -r requirements.txt

# 2. Check which nodes are available / 检查可用节点
python main.py nodes

# 3. Sweep all military frequencies for activity / 扫描所有军事频率 (快速检测活动)
python main.py scan
python main.py scan --network hfgcs    # HFGCS only / 只扫描 HFGCS

# 4. Start continuous monitoring / 启动持续监听
python main.py monitor                  # High-priority frequencies / 监听高优先级频率
python main.py monitor -f 11175 8992    # Named frequencies / 监听指定频率
python main.py monitor --duration 300   # Five minutes / 监听5分钟

# 5. Analyse a recording / 分析录音文件
python main.py analyze data/recordings/xxx.wav

# 6. Generate the analysis report / 生成分析报告
python main.py report
```

## Test results / 测试结果

### Dependency installation / 依赖安装

All Python packages (websockets, numpy, scipy, matplotlib, pyyaml, aiohttp) installed successfully.
所有 Python 包 (websockets, numpy, scipy, matplotlib, pyyaml, aiohttp) 安装成功。

### Node connectivity / 节点连通性

Connected successfully to the `kiwi.aprs.fi` node in Finland. Some preset nodes were unreachable
from this network environment; users can add more nodes to `config.yaml` themselves.

成功连接到 `kiwi.aprs.fi` (芬兰) 节点。部分预设节点因网络环境限制不可用，
用户可在 `config.yaml` 中自行添加更多节点。

### Windows compatibility / Windows 兼容性

Fixed the GBK console encoding problem on Windows — stdout/stderr are forced to UTF-8 through
`io.TextIOWrapper`, and every emoji in console output was replaced with an ASCII-compatible label.

修复了 Windows GBK 控制台编码问题 — 通过 `io.TextIOWrapper` 强制 stdout/stderr 使用 UTF-8，
并将所有控制台输出的 emoji 替换为 ASCII 兼容标签。

## Directions for future work / 后续扩展方向

1. **Automatic node discovery** — fetch and refresh the node list from rx.kiwisdr.com
   **自动节点发现**: 从 rx.kiwisdr.com 自动获取并更新节点列表
2. **ALE decoding** — add MIL-STD-188-141 ALE signal decoding
   **ALE 解码**: 添加 MIL-STD-188-141 ALE 信号解码能力
3. **Web UI** — build a live web monitoring interface
   **Web UI**: 构建实时 Web 监听界面
4. **Propagation prediction** — combine HF propagation models to optimise frequency and node choice
   **传播预测**: 结合 HF 传播模型优化频率/节点选择
5. **Machine learning** — train a signal classification model to replace rule matching
   **机器学习**: 训练信号分类模型替代规则匹配

> Items 1 and 3 have shipped (`fetch_nodes.py` and the web interface in §6 of USAGE.md).
> 第 1 和第 3 项已经做了（`fetch_nodes.py` 和 USAGE.md §6 的 Web 界面）。
