# MilRadio - 军事无线电信号接收系统 Walkthrough

## 概述

成功构建了一套基于全球开源 KiwiSDR 节点网络的军事 HF 信号监听与元数据分析系统。系统完全使用 Python 实现，无需任何物理硬件设备。

## 项目结构

```
无线电/
├── config.yaml              # 主配置 (节点列表、接收/录制/分析参数)
├── frequencies.yaml         # 军事频率数据库 (HFGCS/NATO/军航/数字信号)
├── requirements.txt         # Python 依赖
├── main.py                  # CLI 主入口 (5个子命令)
├── src/
│   ├── __init__.py
│   ├── kiwi_client.py       # 自研轻量级 KiwiSDR WebSocket 客户端
│   ├── node_manager.py      # SDR 节点发现、健康检查、故障转移
│   ├── receiver.py          # 信号接收控制器 (单频/多频模式)
│   ├── squelch.py           # VOX 静噪检测 (RMS阈值+滞后+tail)
│   ├── recorder.py          # WAV 音频录制 (pre-roll+自动分段)
│   ├── analyzer.py          # FFT频谱分析、SNR、调制类型识别
│   ├── db.py                # SQLite 元数据存储
│   └── reporter.py          # matplotlib 图表 + HTML 报告
├── data/
│   ├── recordings/          # 录音文件
│   └── radio_monitor.db     # SQLite 数据库
└── reports/                 # 分析报告
```

## 核心模块说明

### KiwiSDR 客户端 ([kiwi_client.py](file:///c:/Users/xiexiaokai/Documents/无线电/src/kiwi_client.py))
- 自行实现的轻量级 WebSocket 客户端，不依赖外部 kiwiclient 库
- 实现 KiwiSDR 握手协议 (SET auth → SET AR → SET mod)
- 解析二进制音频帧 (big-endian int16 PCM)
- 支持 S-meter 信号强度读取、自动 keepalive

### 信号分析器 ([analyzer.py](file:///c:/Users/xiexiaokai/Documents/无线电/src/analyzer.py))
- 时域分析: RMS、峰值、峰均比 (Crest Factor)
- 频域分析: FFT频谱、频谱质心、频谱平坦度
- 信号特征: 带宽估算 (peak-NdB 方法)、SNR 估算
- 调制识别: 基于频谱特征的规则匹配 (USB_VOICE/AM/CW/FSK/PSK)

### 频率数据库 ([frequencies.yaml](file:///c:/Users/xiexiaokai/Documents/无线电/frequencies.yaml))
- **HFGCS**: 4724, 6712, 6739, 8992, 11175, 13200, 15016 kHz
- **NATO**: 海岸警卫队、AWACS、加拿大军事
- **军用航空**: 航管、LDOC
- **数字信号**: STANAG 4285/4481
- **校准参考**: WWV 5/10/15 MHz

## 使用方法

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 检查可用节点
python main.py nodes

# 3. 扫描所有军事频率 (快速检测活动)
python main.py scan
python main.py scan --network hfgcs    # 只扫描 HFGCS

# 4. 启动持续监听
python main.py monitor                  # 监听高优先级频率
python main.py monitor -f 11175 8992   # 监听指定频率
python main.py monitor --duration 300  # 监听5分钟

# 5. 分析录音文件
python main.py analyze data/recordings/xxx.wav

# 6. 生成分析报告
python main.py report
```

## 测试结果

### 依赖安装
所有 Python 包 (websockets, numpy, scipy, matplotlib, pyyaml, aiohttp) 安装成功。

### 节点连通性
成功连接到 `kiwi.aprs.fi` (芬兰) 节点。部分预设节点因网络环境限制不可用，用户可在 `config.yaml` 中自行添加更多节点。

### Windows 兼容性
修复了 Windows GBK 控制台编码问题 — 通过 `io.TextIOWrapper` 强制 stdout/stderr 使用 UTF-8，并将所有控制台输出的 emoji 替换为 ASCII 兼容标签。

## 后续扩展方向

1. **自动节点发现**: 从 rx.kiwisdr.com 自动获取并更新节点列表
2. **ALE 解码**: 添加 MIL-STD-188-141 ALE 信号解码能力
3. **Web UI**: 构建实时 Web 监听界面
4. **传播预测**: 结合 HF 传播模型优化频率/节点选择
5. **机器学习**: 训练信号分类模型替代规则匹配
