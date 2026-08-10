# MilRadio - 使用教程与示例

## 目录
- [数据存储说明](#数据存储说明)
- [快速开始](#快速开始)
- [命令详解与示例](#命令详解与示例)
- [Web 实时监听界面](#web-实时监听界面)
- [录音清理工具](#录音清理工具)
- [数据分析教程](#数据分析教程)
- [公开日志下载与对比](#公开日志下载与对比)
- [高级用法](#高级用法)
- [常见问题](#常见问题)

---

## 数据存储说明

### 数据保存位置

```
无线电/
├── data/
│   ├── recordings/              <-- 所有录音文件 (WAV)
│   │   ├── 20260601_034521_11175.0kHz_OH_Finland.wav
│   │   ├── 20260601_035012_8992.0kHz_OH_Finland.wav
│   │   └── ...
│   ├── radio_monitor.db         <-- SQLite 元数据库 (永久保存)
│   └── public_logs/             <-- 公开监听日志 (fetch_public_logs.py 下载)
│       ├── eam_watch/           <-- EAM.watch 消息
│       ├── shortwave_archive/   <-- 短波录音存档
│       └── comparison/          <-- 对比报告 (HTML)
└── reports/
    ├── report_20260601_0400.html <-- HTML 分析报告
    ├── chart_freq_activity.png   <-- 频率活跃度图表
    ├── chart_hourly.png          <-- 24h 活动热力图
    ├── chart_signal_strength.png <-- 信号强度分布
    └── chart_timeline.png        <-- 信号时间线
```

### SQLite 数据库结构

数据库 `data/radio_monitor.db` 包含 4 张表：

| 表名 | 内容 | 关键字段 |
|------|------|----------|
| `sessions` | 每次监听会话 | start_time, end_time, node, frequencies |
| `signals` | 每个检测到的信号 | timestamp, frequency, duration, peak_rms, recording_path |
| `analysis` | 信号的频谱分析 | snr_db, bandwidth_hz, estimated_modulation |
| `nodes` | 节点状态历史 | host, is_available, avg_latency_ms |

> **所有数据永久保存！** 每次运行都会追加数据到同一个数据库，历史记录不会丢失。

---

## 快速开始

### 第一步：检查节点

```bash
python main.py nodes
```

这会测试 config.yaml 中的所有 KiwiSDR 节点，显示哪些可以连接。

### 第二步：扫描频率

```bash
# 快速扫描所有军事频率，看哪些有信号活动
python main.py scan
```

### 第三步：开始监听

```bash
# 监听 HFGCS 日间主频 (最容易听到信号的频率)
python main.py monitor -f 11175
```

### 第四步：查看结果

```bash
# 生成分析报告
python main.py report

# 分析特定录音文件
python main.py analyze data/recordings/你的录音文件.wav
```

---

## 命令详解与示例

### 1. `nodes` - 节点检查

```bash
# 基本用法
python main.py nodes

# 加长超时（网络慢时使用）
python main.py nodes --timeout 20
```

输出示例：
```
==============================================================================
 状态  名称                   地址                              延迟  位置
------------------------------------------------------------------------------
 +   OH Finland           kiwi.aprs.fi:8073                5947ms Finland
 -   SK3W Sweden          kiwisdr.sk3w.se:8073                N/A Sweden
==============================================================================
总计: 1/6 节点可用
```

### 2. `scan` - 频率扫描

```bash
# 扫描所有频率
python main.py scan

# 只扫描 HFGCS 网络
python main.py scan --network hfgcs

# 只扫描高优先级频率
python main.py scan --priority high

# 每个频率停留 10 秒（默认 30 秒）
python main.py scan --dwell 10

# 指定使用特定节点
python main.py scan --node "Finland"
```

### 3. `monitor` - 持续监听（核心功能）

```bash
# ===== 基本监听 =====

# 监听 HFGCS 11175 kHz (日间最活跃)
python main.py monitor -f 11175

# 监听 HFGCS 8992 kHz
python main.py monitor -f 8992

# 同时监听多个频率（轮询模式）
python main.py monitor -f 11175 8992 4724

# 监听 5 分钟后自动停止
python main.py monitor -f 11175 --duration 300

# ===== 指定模式和节点 =====

# 用 AM 模式监听
python main.py monitor -f 5000 -m AM

# 指定使用芬兰节点
python main.py monitor -f 11175 --node "Finland"

# ===== 长时间监听 =====

# 在后台运行一整夜 (Windows)
start /min python main.py monitor -f 4724

# 监听并显示详细调试信息
python main.py -v monitor -f 11175
```

**监听过程中会发生什么？**
1. 连接到 KiwiSDR 节点
2. 调谐到指定频率
3. 静噪检测器持续分析音频流的 RMS 能量
4. 当 RMS 超过阈值 (0.02) → 自动开始录音
5. 信号消失 3 秒后 → 停止录音，保存 WAV 文件
6. 自动对录音做 FFT 频谱分析
7. 所有数据写入 SQLite 数据库
8. 按 `Ctrl+C` 停止

### 4. `analyze` - 分析录音

```bash
# 分析单个文件
python main.py analyze data/recordings/20260601_034521_11175.0kHz_OH_Finland.wav
```

输出示例：
```
==================================================
  [RESULT] 信号分析结果
==================================================
  文件: 20260601_034521_11175.0kHz_OH_Finland.wav
  时长: 12.50s
  采样率: 12000 Hz
--------------------------------------------------
  [TIME-DOMAIN] 时域分析:
     RMS 能量: 0.034521
     峰值幅度: 0.287654
     峰均比: 18.4 dB
--------------------------------------------------
  [FREQ-DOMAIN] 频域分析:
     峰值频率: 1200.0 Hz
     信号带宽: 2800.0 Hz
     信噪比 (SNR): 15.3 dB
     频谱平坦度: 0.089000
--------------------------------------------------
  [MODULATION] 估计调制类型: USB_VOICE
==================================================
```

### 5. `report` - 生成报告

```bash
# 生成最近 7 天的报告
python main.py report
```

会在 `reports/` 目录生成 HTML 报告，包含：
- 频率活跃度排名柱状图
- 24 小时活动热力图
- 信号强度分布直方图
- 信号检测时间线散点图
- 详细的频率统计表
- 最近信号列表

### 6. `web` - 实时 Web 监听界面 ⭐

```bash
# 启动 Web 界面（默认端口 8888）
python main.py web

# 指定端口
python main.py web --port 9090

# 然后在浏览器打开:
# http://localhost:8888
```

---

## Web 实时监听界面

Web 界面提供了图形化的监听控制和实时数据展示。

### 启动方式

```bash
python main.py web
```

然后在浏览器中打开 **http://localhost:8888**

### 界面功能

| 区域 | 功能 |
|------|------|
| **左侧面板** | 频率快速选择、监听参数设置、节点管理 |
| **中央区域** | 实时仪表盘（频率、RMS、S-meter、信号数）+ 波形图 |
| **右侧面板** | 信号检测日志、会话统计 |

### Web 界面操作流程

1. 点击 **Check Node Availability** 检查节点
2. 在左侧选择频率（点击快速按钮或手动输入）
3. 选择节点（或 Auto 自动选择）
4. 点击 **Start Monitoring** 开始监听
5. 中央区域会实时显示 RMS 波形和信号强度
6. 检测到信号时，右侧会自动追加信号日志
7. 点击 **Stop Monitoring** 停止

---

## 录音清理工具

监听过程中，静噪检测器可能会被短促的噪声爆发、脉冲干扰（如“滴”一声）或底噪波动触发，产生大量无意义的录音文件。`clean` 命令可以自动分析并清理这些垃圾录音。

### 7. `clean` - 清理无意义的录音 🧹

```bash
# 预览模式（只分析，不删除，先运行这个看看）
python main.py clean

# 或使用独立脚本（更多参数可调）
python clean_recordings.py
```

#### 判定标准

工具会对每个 WAV 文件做完整的频域 + 时域分析，满足以下**任一**条件即判定为垃圾录音：

| 判定条件 | 默认阈值 | 说明 |
|----------|----------|------|
| 时长极短 | < 2.0s | 噪声的短促触发，不是眉正的通信 |
| SNR 极低 | < 5.0 dB | 几乎没有有效信号，只是底噪 |
| 频谱平坦 | > 0.5 | 接近白噪声特征 |
| 调制类型 | NOISE | 分析器综合判定为噪声 |
| 脉冲干扰 | 时长<3s 且 峰均比>15dB | 如“滴”一声的短促干扰 |
| 近乎静音 | RMS < 0.005 | 录制了但内容几乎是空的 |

#### 基本用法

```bash
# 第一步：先预览，确认哪些会被删除
python main.py clean

# 第二步：确认无误后，实际删除
python main.py clean --delete

# 删除文件的同时清理数据库中对应的信号记录
python main.py clean --delete --clean-db
```

#### 高级参数

```bash
# 调整最短时长阈值（默认 2 秒）
python main.py clean --min-duration 3

# 调整最低 SNR 阈值（默认 5 dB）
python main.py clean --min-snr 8

# 显示每个文件的详细分析信息
python main.py -v clean --delete
```

使用独立脚本 `clean_recordings.py` 可以调整更多参数：

```bash
# 查看所有可用参数
python clean_recordings.py --help

# 自定义所有阈值
python clean_recordings.py --min-duration 3 --min-snr 8 --max-flatness 0.4 --delete

# 指定录音目录
python clean_recordings.py --recordings-dir /path/to/recordings --delete
```

#### 输出示例

```
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

> **安全提示：** 默认是预览模式，不会删除任何文件。建议先运行不带 `--delete` 的命令查看报告，确认分类结果合理后再实际删除。

---

## 数据分析教程

### 用 Python 直接查询数据库

```python
# example_query.py - 数据查询示例
import sqlite3
import json

# 连接数据库
conn = sqlite3.connect("data/radio_monitor.db")
conn.row_factory = sqlite3.Row

# 1. 查看最近的信号
print("=== 最近 10 个信号 ===")
for row in conn.execute("""
    SELECT timestamp, frequency_khz, mode, duration_seconds, peak_rms, node_name
    FROM signals ORDER BY timestamp DESC LIMIT 10
"""):
    print(f"  {row['timestamp'][:19]} | {row['frequency_khz']:>8.1f} kHz | "
          f"{row['duration_seconds']:.1f}s | RMS={row['peak_rms']:.4f} | {row['node_name']}")

# 2. 各频率活跃度统计
print("\n=== 频率活跃度 ===")
for row in conn.execute("""
    SELECT frequency_khz, COUNT(*) as cnt, SUM(duration_seconds) as total_dur
    FROM signals GROUP BY frequency_khz ORDER BY cnt DESC
"""):
    print(f"  {row['frequency_khz']:>8.1f} kHz: {row['cnt']} signals, "
          f"total {row['total_dur']:.0f}s")

# 3. 调制类型分布
print("\n=== 调制类型分布 ===")
for row in conn.execute("""
    SELECT estimated_modulation, COUNT(*) as cnt, AVG(snr_db) as avg_snr
    FROM analysis GROUP BY estimated_modulation ORDER BY cnt DESC
"""):
    print(f"  {row['estimated_modulation']}: {row['cnt']} signals, "
          f"avg SNR={row['avg_snr']:.1f} dB")

conn.close()
```

### 用 src 模块做更高级的分析

```python
# example_advanced.py - 高级分析示例
from src.db import Database
from src.analyzer import SignalAnalyzer
import os

db = Database("data/radio_monitor.db")
analyzer = SignalAnalyzer(fft_size=4096)

# 获取所有录音文件并批量分析
recordings_dir = "data/recordings"
for filename in os.listdir(recordings_dir):
    if filename.endswith(".wav"):
        filepath = os.path.join(recordings_dir, filename)
        result = analyzer.analyze_file(filepath)
        if result:
            print(f"{filename}:")
            print(f"  Modulation: {result['estimated_modulation']}")
            print(f"  SNR: {result['snr_db']:.1f} dB")
            print(f"  Bandwidth: {result['bandwidth_hz']:.0f} Hz")
            print()

db.close()
```

---

## 高级用法

### 添加新的 KiwiSDR 节点

编辑 `config.yaml`，在 `nodes` 列表中添加：

```yaml
nodes:
  # ... 现有节点 ...

  - host: "your-kiwi-host.com"    # 节点地址
    port: 8073                     # 端口（通常 8073）
    name: "My KiwiSDR"            # 显示名称
    location: "Shanghai"           # 位置
    lat: 31.23                     # 纬度
    lon: 121.47                    # 经度
```

去 http://rx.kiwisdr.com 可以找到全球公共 KiwiSDR 节点列表。

### 调整静噪灵敏度

编辑 `config.yaml`：

```yaml
squelch:
  open_threshold: 0.01   # 降低 = 更灵敏（检测更多弱信号，但可能误触发）
  close_threshold: 0.008  # 关闭阈值（低于开启阈值）
  tail_time: 5.0          # 信号消失后继续录 5 秒
```

### 添加自定义频率

编辑 `frequencies.yaml`，添加新的频率组：

```yaml
my_frequencies:
  description: "我的自定义监听频率"
  frequencies:
    - freq: 7850.0
      mode: "USB"
      description: "某个感兴趣的频率"
      active_hours: "全天"
      priority: high
```

---

## 常见问题

### Q: 所有节点都不可用？
A: 可能是你的网络环境（防火墙/VPN）限制了出站连接。尝试：
1. 去 http://rx.kiwisdr.com 在浏览器中找能打开的节点
2. 把能用的节点地址添加到 `config.yaml`

### Q: 监听时一直没检测到信号？
A: 可能原因：
1. HFGCS 并非一直有信号，需要耐心等待
2. 当前频率不适合当前时间段（参考 frequencies.yaml 中的 active_hours）
3. 静噪阈值太高 → 降低 `config.yaml` 中的 `open_threshold`

### Q: 录音文件太多 / 很多无意义的噪音录音怎么办？
A: 使用录音清理工具自动识别并删除垃圾录音：
```bash
python main.py clean            # 先预览
python main.py clean --delete   # 确认后删除
```
详见[录音清理工具](#录音清理工具)章节。

### Q: 建议先监听哪个频率？
A: **11175 kHz** (白天) 或 **8992 kHz** (全天) 是 HFGCS 最活跃的频率，最容易听到 EAM 通信。

### Q: Web 波形图中的红色虚线 (SQUELCH) 是什么？
A: 那条红色虚线是**静噪阈值线**，对应 `config.yaml` 中的 `open_threshold`（默认 0.15）。
- RMS 波形**低于**该线 → 只是背景底噪，不录音
- RMS 波形**高于**该线 → 检测到有效信号，自动开始录音

> **注意：** 底噪 RMS 通常在 0.07~0.08 左右，信号出现时会升至 0.15 以上。如果你在图表中看到波形一直在线下方，说明当前频率暂时没有信号活动，这是正常的。

---

## 公开日志下载与对比

### 7. `fetch_public_logs` - 下载公开监听日志 🆕

从公开来源自动下载 HFGCS/EAM 监听日志，并与你自己的监听数据对比，用于验证你的监听系统是否正常工作。

#### 数据来源

| 来源 | 内容 | 说明 |
|------|------|------|
| [EAM.watch](https://eam.watch) | EAM / Skyking 消息 | 社区驱动的 EAM 消息数据库，含发送者、时间戳、加密内容、录音链接 |
| [Shortwave Archive](https://shortwavearchive.com) | 短波录音存档 | 爱好者上传的短波录音，搜索 HFGCS 相关内容 |

#### 基本用法

```bash
# 抓取所有来源的最新日志（默认最近 7 天）
python fetch_public_logs.py

# 只抓取 EAM.watch
python fetch_public_logs.py --source eam

# 只抓取短波录音存档
python fetch_public_logs.py --source archive

# 指定时间范围
python fetch_public_logs.py --days 30

# 抓取 + 与本地数据对比（生成 HTML 报告）
python fetch_public_logs.py --compare

# 同时下载音频文件（最多 10 个）
python fetch_public_logs.py --source archive --download-audio
```

#### 输出目录结构

```
data/public_logs/
├── eam_watch/
│   ├── messages_20260601.json       # EAM 消息 (JSON)
│   └── messages_20260601.csv        # EAM 消息 (CSV, Excel 可直接打开)
├── shortwave_archive/
│   ├── recordings_20260601.json     # 录音元数据
│   ├── recordings_20260601.csv
│   └── audio/                       # 下载的音频文件 (可选)
└── comparison/
    ├── comparison_20260601.html      # 对比报告 (浏览器打开)
    └── comparison_20260601.json      # 对比数据
```

#### 数据对比功能

使用 `--compare` 参数时，程序会自动：

1. 读取你本地 `data/radio_monitor.db` 中的信号记录
2. 读取已下载的所有公开日志
3. 按**时间窗口 (±5 分钟)** 和**频率**匹配，判断是否捕获了同一个信号
4. 生成 HTML 对比报告，包含：

| 对比维度 | 说明 |
|----------|------|
| 匹配成功 | 公开日志和本地都检测到的信号数量 |
| 仅本地检测 | 你捕获了但公开日志没有的信号（可能是你独有的发现） |
| 仅公开记录 | 公开日志有但你没捕获的信号（可能错过的信号） |
| 频率活跃度 | 各频率在本地和公开数据中的活跃度对比 |

#### EAM 消息格式说明

从 EAM.watch 获取的消息示例：

```json
{
  "type": "ALLSTATIONS",
  "sender": "SHORTHAND",
  "time": "2026-06-01 15:07:00",
  "message": "CBVN2JRPNDH5D25YZZ6KUHJULNOOMM",
  "recordings": [
    {
      "link": "https://eamwatch-production.s3.amazonaws.com/recordings/..."
    }
  ]
}
```

> **注意：** EAM 消息内容是用一次性密码本加密的，无法解密。我们只能对比时间戳和频率来验证监听覆盖率。
