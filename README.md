# MilRadio

**军事无线电信号接收与元数据分析系统** —— 通过全球公开的 KiwiSDR 网络接收 HF 信号，
自动静噪录音、频谱分析、调制识别，全部元数据落进 SQLite，附带实时 Web 监听界面。

当前版本 **v1.3.0**

---

## 文档导航

| 文档 | 内容 |
|------|------|
| **[USAGE.md](USAGE.md)** | **完整使用说明** —— 安装、配置、全部命令、Web 界面、API、数据结构、升级须知、FAQ |
| [CHANGELOG.md](CHANGELOG.md) | 版本更新日志 |
| [reports/milradio-audit.html](reports/milradio-audit.html) | 信号链审计报告：三处读数缺陷的完整复现与证据 |
| [walkthrough.md](walkthrough.md) / [implementation_plan.md](implementation_plan.md) | 设计与实现记录 |

> 用过 v1.2 及更早版本的，先看 [USAGE.md §13 从旧版本升级](USAGE.md#13-从旧版本升级)：
> v1.3.0 修了三处**读数缺陷**，旧的 S-meter、SNR、调制类型数据不能和新数据混着比。

---

## 能做什么

- **接收** —— 连接全球公开 KiwiSDR 节点，USB/LSB/AM/CW 解调，断线自动重连并切换节点
- **录音** —— RMS 静噪自动开录，带 pre-roll 和尾部延迟，超长自动分段
- **分析** —— 带内 SNR、占用带宽、包络音节率、音调结构 → 调制类型 **+ 置信度**，
  证据不足时输出 `UNKNOWN` 而不是硬猜
- **实时界面** —— 频谱 + 瀑布图、真实 S-meter、录音浏览回放、频谱图缩略图、
  **静噪在线标定**（按实测底噪一键设定，不用停下监听）
- **持久化** —— SQLite 永久保存会话/信号/分析/节点状态，老库自动补列
- **报告** —— HTML 分析报告 + 10 张图表
- **清理** —— 自动识别并删除噪声触发产生的垃圾录音
- **交叉验证** —— 抓取 EAM.watch 等公开日志，和本地数据按时间/频率比对覆盖率

---

## 安装

需要 **Python 3.9+**（推荐 3.10+）。

```bash
git clone https://github.com/reinhardt6678-sudo/Radio.git
cd Radio

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

`data/` 和 `reports/` 会在首次运行时自动创建。

---

## 五分钟上手

```bash
# ① 看哪些 KiwiSDR 节点现在能连
python main.py nodes

# ② 开 Web 界面（推荐入口：实时频谱、录音回放、在线调静噪都在这）
python main.py web
#    浏览器打开 http://localhost:8888

# —— 或者走命令行 ——

python main.py monitor -f 11175              # 监听 HFGCS 日间主频
python main.py analyze data/recordings/xxx.wav   # 分析一段录音
python main.py report                        # 生成 HTML 报告
python main.py clean                         # 预览垃圾录音（加 --delete 才真删）
```

**先听哪个频率？** 白天 **11175 kHz**、全天 **8992 kHz**、夜间 **4724 kHz**（都是 USB）。

**第一次监听**：开 Web 界面先让它跑 3-5 分钟别动，左侧 Squelch 面板会统计出这个节点的
实测底噪，然后点一下"按底噪设定"。静噪阈值取决于对面节点的 AGC，猜不准的。

---

## 命令一览

| 命令 | 作用 |
|------|------|
| `python main.py nodes` | 测试 KiwiSDR 节点可用性 |
| `python main.py scan` | 扫描频率库，看哪个频率有活动 |
| `python main.py monitor -f 11175` | 持续监听（核心功能） |
| `python main.py analyze <file.wav>` | 分析单个录音 |
| `python main.py report` | 生成 HTML 分析报告 |
| `python main.py web` | 启动实时 Web 监听界面 |
| `python main.py clean` | 清理噪声触发的垃圾录音 |

每个命令的全部参数、输出解读见 **[USAGE.md §5](USAGE.md#5-命令行完整参考)**。

---

## 项目结构

```
Radio/
├── main.py                  CLI 入口（7 个子命令）
├── config.yaml              节点、静噪、录制、分析、报告参数
├── frequencies.yaml         军事 HF 频率库（hfgcs / nato / military_air / digital / reference）
├── src/
│   ├── kiwi_client.py       KiwiSDR WebSocket 协议、SND 帧解析、S-meter、丢帧统计
│   ├── modes.py             解调滤波器与音频通带的唯一真值表
│   ├── squelch.py           RMS 静噪状态机、pre-roll 缓冲
│   ├── recorder.py          WAV 录制与自动分段
│   ├── analyzer.py          频域/时域分析、调制分类、实时频谱、频谱图
│   ├── db.py                SQLite 持久化、自动补列迁移、统计查询
│   ├── receiver.py          扫描与持续监听编排
│   ├── web_server.py        aiohttp 服务、REST API、WebSocket 推送
│   ├── node_manager.py      节点探测与择优
│   └── reporter/            HTML 报告与图表
├── web/index.html           实时监听页面（单文件）
├── tests/                   70 个测试
├── reports/                 生成的报告与图表 + 信号链审计报告
└── data/                    运行时数据（录音 / 数据库 / 公开日志，不进版本库）
```

---

## 测试

```bash
pip install pytest
python -m pytest tests -q
# 70 passed
```

---

## 免责声明

本项目只接收和分析**公开的**无线电信号元数据，通过全球公开的 KiwiSDR 接收机网络。
不解密任何内容，不发射，不干扰。使用前请确认所在司法辖区对无线电监听的相关规定。
