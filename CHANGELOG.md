# 更新日志 (CHANGELOG)

## v1.3.0 (2026-08-14)

修复 [信号链审计报告](reports/milradio-audit.html) 记录的三处读数缺陷，并重做 Web 监听页面。

### 🐛 信号链修复

- **[缺陷 01] SND 帧解析偏移 2 字节** — `kiwi_client.py` 把 SND 帧的 body 解析成
  `flags(1) + seq(2, 大端) + smeter(2)`，而官方 kiwiclient 的真实布局是
  `flags(1) + seq(4, 小端) + smeter(2, 大端)`。两个后果：
  - S-meter 列记录的其实是帧计数器的高位字节（744 条记录只有 16 个取值，全部是 256 的整数倍，676 条恒为 -160 dBm）；
  - 音频起点提前 2 字节，**每帧多注入 1 个假样本**，在全部 747 段录音里产生 12000/512 = 23.4375 Hz 的帧率谐波嗡声。

  同时修正 dBm 换算：`raw/65535×150−160` 改为 KiwiSDR 实际使用的 `0.1 × (raw & 0x0FFF) − 127`。
  帧类型判断从只比首字节改为比较完整的 3 字节 tag（`'STA'` 之类的帧不会再被当成音频），
  并按 seq 跳变统计丢帧数。
  - 影响文件: `src/kiwi_client.py`

- **[缺陷 02] 调制识别由字典顺序决定** — 旧打分器给带宽 2 分、平坦度 1 分、峰均比 1 分，
  而带宽在固定 300-3000 Hz 通带下测的是接收机的滤波器：三种"不同"调制的平均带宽只差 25 Hz。
  重放全部 744 条记录，690 条（98.4%）出现三向并列，最终由 `MODULATION_PROFILES` 的书写顺序
  决定输出——"93% 的信号是 USB 语音"只是因为 `USB_VOICE` 写在第一个。改为：
  - 换特征集：包络音节率与调制深度、键控率、音调数与间距、音调纯度、扣除底噪后的占用带宽；
  - 连续隶属度加权代替整数打分，并加一层否决条件（20 Hz 宽的信号不可能是宽带数据波形）；
  - 输出**置信度**，并列或证据不足时返回 `UNKNOWN` 而不是取字典里的第一个键；
  - USB / LSB / AM 由接收机的解调模式决定，不再从解调后的音频里猜；
  - 新增 `CARRIER`（未调制载波）类别。
  - 影响文件: `src/analyzer.py`, `src/receiver.py`, `src/web_server.py`, `main.py`

- **[缺陷 03] SNR 把滤波器阻带当噪声基准** — 旧实现取峰值两侧 ±10% 当信号、其余当噪声，
  而频谱跨度 0-6000 Hz、信号只可能在 300-3000 Hz，"噪声区"里塞了一大片几乎没有能量的
  滤波器阻带，分母被压低。症状是 744 条记录里只有 6 条 SNR 超过 10 dB。改为在解调通带内
  用低分位数估噪声基底，`SNR = 扣除底噪的信号功率 / 底噪总功率`。清晰语音现在能到 20 dB 以上，
  纯底噪是负值。
  - 新增文件: `src/modes.py`（解调滤波器与音频通带共用一张表，避免收发两端各写各的）
  - 影响文件: `src/analyzer.py`, `src/kiwi_client.py`

- **带宽口径** — `bandwidth_hz` 改为扣除底噪后包含 90% 能量的**占用带宽**（纯音收敛到几十 Hz，
  语音 1-2.5 kHz）。旧的"峰值 -20 dB"口径保留为 `bandwidth_20db_hz` 供对照。

### ✨ Web 监听页面

- **实时频谱 + 瀑布图** — `analyzer.get_spectrogram()` 早就写好了却从没被界面调用过。
  现在后端每约 170 ms 推一列 128 格频谱（`live_spectrum()`，分段平均避免单次 FFT 让
  实时 SNR 虚高），前端画频谱曲线 + 滚动瀑布图，并标出当前解调通带。
- **录音浏览与回放** — 747 段录音过去只能去文件夹里翻。新增录音标签页：按天数/SNR 筛选，
  每条显示时长、带内 SNR、调制类型+置信度、占用带宽、S-meter，可直接播放，
  也可展开这段录音的频谱图缩略图（动态曝光，弱信号也看得清）。
- **真实 S-meter** — 帧解析修好后 S-meter 才是真读数，页面显示 dBm + S 级 + 电平条。
- **带内 SNR / 噪声基底** — 实时显示带内 SNR 与噪声基底，RMS 曲线上叠加噪声基底线与
  静噪开/关阈值线。
- **静噪在线调整** — 滑块直接改开/关阈值，立刻作用到正在跑的检测器，不用停下监听重来；
  服务器持续统计 RMS 低分位数作为实测底噪，一键"按底噪设定"（底噪 +6 dB）。
  这是对 CHANGELOG 里 0.65 → 0.15 → 0.10 三次手调阈值的一次性了结。
- **统计标签页** — 频率活跃度、调制类型分布（带平均置信度）、带内 SNR 分布、24 小时活动热条。
- 频率快捷键按网络分组并标出最近 30 天有过信号的频率；节点状态、丢帧数、会话统计等
  都在页面上直接可见。
  - 影响文件: `web/index.html`, `src/web_server.py`, `src/db.py`

### 🔌 新增接口

| 接口 | 说明 |
|------|------|
| `GET /api/recordings` | 信号+分析记录，支持 days / limit / frequency / min_snr / with_recording 筛选 |
| `GET /api/recordings/{id}/audio` | 回放录音（路径限制在配置的录音目录内） |
| `GET /api/recordings/{id}/spectrogram` | 录音的频谱图矩阵与包络 |
| `GET /api/sessions` | 最近的监听会话 |
| `GET` / `POST /api/squelch` | 读取/在线调整静噪阈值，附实测底噪与建议值 |
| `GET /api/stats` | 扩展: 调制分布、带内 SNR 分布、按天活动 |

### 🗄️ 数据库

- `analysis` 表新增 `modulation_confidence`、`demod_mode`、`noise_floor_db`、
  `envelope_rate_hz`、`envelope_depth`、`tone_count` 六列，老数据库自动补列（不丢历史数据）。
- 新增 `analysis(signal_id)` 索引和 `get_signals_with_analysis()` / `get_snr_distribution()` /
  `get_daily_activity()` 查询方法。

### 🧪 测试

- 18 → **70 个测试**：
  - `test_kiwi_client.py`（新增 13 个）— 喂进构造好的 SND 帧，断言样本数、seq、S-meter，
    并直接检查连续多帧拼接后的音频里不再出现 23.4 Hz 帧率谐波；
  - `test_analyzer.py`（新增 16 个）— 带内 SNR、阻带不参与计算、通带随模式变化、
    各调制类型分类、带宽有区分度、并列输出 UNKNOWN；
  - `test_web_api.py`（新增 19 个）— 录音浏览/回放/频谱图、路径穿越防护、静噪调整校验；
  - `test_db.py`（新增 4 个）— 自动补列迁移、带分析的信号查询、SNR 分布、按天活动。

---

## v1.2.0 (2026-06-08)

### ✨ 改进

- **自定义异常类型体系** — 新增 `src/exceptions.py`，定义 `MilRadioError`、`ConfigError`、`ConnectionError`、`HandshakeRejected`、`NodeUnavailable`、`AnalysisError` 等异常类型。`main.py` 中 `load_config()` 和 `load_frequencies()` 已改用 `ConfigError` 代替 `sys.exit(1)`。
  - 新增文件: `src/exceptions.py`
  - 影响文件: `main.py`

- **统一配置默认值** — `receiver.py` 和 `web_server.py` 中手动提取配置的代码替换为 `SquelchDetector.from_config()`、`AudioRecorder.from_config()`、`SignalAnalyzer.from_config()` 工厂方法，消除了 6 处配置默认值不一致的风险。
  - 影响文件: `src/receiver.py`, `src/web_server.py`

- **拆分 reporter.py** — 将 1100+ 行的单文件拆分为 `src/reporter/` 包：
  - `theme.py` — 配色方案和字体配置 (~85 行)
  - `charts.py` — 10 个图表生成方法的 ChartMixin 类 (~490 行)
  - `reporter.py` — Reporter 主类、HTML/文本报告、扫描结果 (~310 行)
  - `__init__.py` — 重新导出 Reporter，保持 `from src.reporter import Reporter` 兼容
  - 删除文件: `src/reporter.py` (旧单文件)

- **Windows 事件循环兼容** — `main.py` 在 Windows 平台自动设置 `WindowsSelectorEventLoopPolicy`，避免 `ProactorEventLoop` 与 aiohttp/websockets 的兼容性问题。
  - 影响文件: `main.py`

- **HTML 报告版本号修正** — 报告页脚版本号从 v1.0 更新为 v1.1。

### 🧪 测试

- **新增单元测试套件** — 添加 `tests/` 目录，包含 18 个测试用例：
  - `test_squelch.py` — 静噪状态机、deque 缓冲区、pre-roll、工厂方法 (6 tests)
  - `test_db.py` — 会话 CRUD、信号记录、分析保存、频率统计、异常保护 (6 tests)
  - `test_analyzer.py` — 纯音分析、噪声检测、analyze_and_save 流程、空 buffer 安全 (6 tests)
  - `conftest.py` — pytest 路径配置

- **消除分析代码重复** — `receiver.py` 和 `web_server.py` 中有 3 处几乎相同的"分析信号 + 保存到数据库"代码（各约 15 行），统一改为调用 `SignalAnalyzer.analyze_and_save()` 方法。确保后续修改分析逻辑时只需改一处。
  - 影响文件: `src/receiver.py`, `src/web_server.py`

- **修复废弃 API** — `web_server.py` 中的 `asyncio.get_event_loop()` 在 Python 3.10+ 已废弃且在特定上下文中不可靠。改为在 `_run_monitor()` 启动时通过 `asyncio.get_running_loop()` 保存事件循环引用，在同步回调中安全使用。
  - 影响文件: `src/web_server.py`

- **修复 pre-roll 缓冲区性能问题** — 静噪检测器的 pre-roll 缓冲区修剪使用 `list.pop(0)` (O(n))，改为 `deque.popleft()` (O(1))。在长时间监听场景下可减少不必要的 CPU 开销。
  - 影响文件: `src/squelch.py`

- **数据库关闭异常保护** — `Database.close()` 新增 try/except 保护，防止在 `finally` 块中关闭数据库时异常（如 WAL checkpoint 失败）掩盖真正的业务异常。
  - 影响文件: `src/db.py`

### ✨ 改进

- **录音自动分段回调** — `AudioRecorder` 新增可选的 `on_segment_complete` 回调参数。当录音达到 `max_duration` 自动分段时，会调用此回调通知上层保存分段信息，避免分段录音元数据丢失。
  - 影响文件: `src/recorder.py`
  - 注意: 此为向后兼容的改动，现有代码无需修改即可正常工作。

---

## v1.0.0 (2026-06-01)

- 初始版本发布
- KiwiSDR WebSocket 客户端
- 信号监听与录音
- FFT 频谱分析与调制识别
- SQLite 数据持久化
- Web 实时监听界面
- HTML 分析报告生成
- 录音清理工具
