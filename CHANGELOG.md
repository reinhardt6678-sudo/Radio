# 更新日志 (CHANGELOG)

## v1.4.0 (2026-08-18)

第三次"守了一整天，一个信号都没有"之后，这次没有再去猜阈值，而是**跑了一次
93 小时的对照实验**（两个频率 11175 / 8992、4 个接收节点、109 个连接段、
7.7 GB 音频），把问题量到底。

结论：**前两次故障是同一个根因的两个方向，而根因不是阈值，是节点的 AGC。**

### 实验测到了什么

节点开着 AGC（`SET agc=1`）时，输出电平被钉死。以 S-meter（KiwiSDR 在音频
AGC **之前**测的射频电平）为参照：

| 状态 | 段数 | 时长 | 射频动 1 dB 音频跟多少 | 有信号/没信号的音频电平差 |
|------|------|------|------------------------|---------------------------|
| AGC 关 | 62 | 47.9 h | **0.89 dB** | **8.0 dB**（中位 7.1） |
| AGC 开 | 15 | 10.2 h | **0.23 dB** | **2.2 dB**（中位 1.6） |

四个节点分别看方向完全一致（AGC 关 0.72–0.91，AGC 开 0.15–0.36）。

在这批数据里**同时复现了历史上的两次故障**，只差一个参数：

| 设置（AGC 开着的 20.4 小时，8 段真通联） | 抓到 | 录制占比 |
|------------------------------------------|------|----------|
| RMS 底噪 +15 dB 及以上 | **0/8** | **0%** ← v1.3.2 的"一整天 0 条" |
| 绝对阈值 0.10 | 8/8 | **74%** ← v1.3.1 的"213 个背靠背 WAV" |

**同一批音频，挪一下阈值就在"什么都不录"和"几乎一直在录"之间跳，中间没有
可用档位。**关掉 AGC 之后这个悬崖消失：+6 dB 抓 100%、+12 dB 抓 94%、
+18 dB 抓 72%，平滑可调。

### ✨ 改动

- **节点 AGC 默认关闭**（`receiver.agc: false`），改用每节点标定的固定增益
  `man_gain`。config.yaml 里 7 个节点的值都是实测标定过的（目标底噪
  RMS ≈ 0.015）。
  - AGC **只能在连接建立时设定**：实测节点一旦进入 AGC 模式，之后再发
    `SET agc=0` 会被忽略（增益停在高位，与 AGC 开着无异，差 22 dB）。
    所以每次重连都重新下发，绝不在流中途切换。
  - 影响文件：`src/kiwi_client.py`, `config.yaml`

- **新增 `mode: smeter` 静噪判据并设为默认** —— 阈值 = S-meter 底噪 + 14 dB。
  S-meter 在音频 AGC 之前测量，是实验里**唯一在 AGC 开和关两种状态下都能用**
  的判据。实测（AGC 关，75 段真通联）：

  | 余量 | 抓到通联 | 录制时间占比 |
  |------|----------|--------------|
  | +6 dB | 100% | 48% |
  | +10 dB | 100% | 34% |
  | **+14 dB（默认）** | **96%** | **17%** |
  | +18 dB | 69% | 5% |

  - 影响文件：`src/squelch.py`, `config.yaml`, `src/receiver.py`, `src/web_server.py`

- **录完之后给每段录音打人声结构分**（两段式的第二段）。单靠静噪做不到又准
  又全 —— 真通联只占全程 0.8% 的时间，要抓住 96% 就得录 17%。所以闸门放宽，
  录完再按"是不是人声"排序：
  - `syllabic_ratio` 包络能量落在 0.5–4 Hz 的占比（人说话一句一句的）
  - `passband_tilt_db` USB 通带低频端比高频端强多少
  - `speech_score` 0–1 连续分，`is_speech` 布尔判定
  - 三个量都是"形状"不是"电平"，所以 AGC 开不开都成立
  - 影响文件：`src/analyzer.py`, `src/db.py`（三个新列，老库自动补列）

- **Web 面板静噪区改成三档判据下拉**（S-meter / 自适应 RMS / 固定 RMS），
  S-meter 档带打开/关闭余量滑块、实测 S-meter 底噪和生效阈值读数。
  `POST /api/squelch` 新增 `smeter_open_margin_db` / `smeter_close_margin_db`，
  关闭余量 ≥ 打开余量时拒绝（否则没有滞后）。
  - 影响文件：`web/index.html`, `src/web_server.py`

### 🐛 修复

- **移除 `greatlakesreceiver.hopto.me`** —— 整个 `hopto.me` 域已经
  NXDOMAIN（`hopto.org` 下同名主机也连不上）。它在数据库里延迟最低，
  于是每次开始监听都会被自动选中，然后连不上。这是上一次"一整天 0 条"
  的第二个独立原因。
  - 影响文件：`config.yaml`

### 🧪 测试

- 新增 `tests/test_smeter_squelch.py` 26 个用例，其中两个直接锁住历史故障：
  - 音频被 AGC 钉在高电平但射频安静 → 静噪不能开（v1.3.1 的卡开）
  - 音频电平一动不动但 S-meter 抬起来 → 静噪必须开（v1.3.2 的 0 条）
- 全量 150 个用例通过。

### ⚠️ 实验本身的局限

- 判定"真通联"用的是自写的声学判据，没有外部日志对照（EAM.watch 最新记录
  停在 2026-07-26）。剔掉了 718 段候选里 437 段只有 5 秒的孤立窗口，
  只保留 ≥20 秒的 75 段。
- AGC 开着的对照样本只有 8 段通联 / 20.4 小时，比 AGC 关的 67 段少得多。
- `man_gain` 会随波段噪声漂移，换频段要重标（8992 和 11175 的值不一样）。

---

## v1.3.2 (2026-08-15)

又一次"守了一整天，一个信号都没有"，但这次和 v1.3.1 那次不是一回事：
音频链路完全正常（S-meter -108 dBm、RMS 0.0939、瀑布图满的），
**静噪从头到尾一直是打开的** —— 而信号只在静噪**关闭**的那一刻才落库。

一条完整的因果链：

1. 节点开着 AGC，底噪被抬到 RMS ≈ 0.09；
2. 底噪统计只在静噪关闭时累积，于是监听刚起步、音频还没真正上来的那几秒
   （RMS ≈ 0.0038）就把"实测底噪"定死了；
3. 用户照着这个死值点了"按底噪设定"，阈值变成 0.010 / 0.004；
4. 阈值比真实底噪低了一个数量级 → 静噪第一帧就打开、再也关不掉 →
   底噪统计从此停止更新（回到第 2 步）→ 界面上 18 小时 0 个信号，
   而录音机在后台一直按 300 秒分段写盘。

### 🐛 修复

- **底噪统计在静噪打开期间被冻结** — 改为一直统计（10 分钟窗口取第 10 百分位，
  低百分位本来就不会被间歇性的信号抬起来），并挪进 `SquelchDetector`，
  命令行和 Web 共用同一份。之前那个"只在静噪关闭时统计"的写法在阈值配错时
  会自我锁死：越锁越低。
  - 影响文件: `src/squelch.py`, `src/web_server.py`

- **静噪打开后关不掉时永远不出记录** — 新增 `max_open_seconds`（默认跟
  `recording.max_duration` 对齐，300 秒）：连续打开到点强制收尾，信号照常入库。
  如果收尾时电平仍压在关闭阈值之上，说明是阈值低于底噪而不是收到了长信号，
  日志打 `[SQUELCH-STUCK]` 并在 Web 面板上出黄色告警。长通联则只是分段，不告警。
  - 影响文件: `src/squelch.py`

- **断线时正在录的信号被整段丢掉** — 之前直接停录音器：WAV 留在磁盘上，
  数据库里没有任何记录。24/7 监听每次重连都会丢一段。现在断线、异常、
  停止监听、命令行切频率都走 `force_close()`，回调照常跑完。
  - 影响文件: `src/web_server.py`, `src/receiver.py`

### ✨ 新增

- **自适应静噪 `mode: adaptive`（新默认）** — 阈值 = 实测底噪 +6 dB / +3 dB，
  跟着底噪自己走。节点开着 AGC 时绝对电平本来就没有可比性，
  CHANGELOG 里 0.65 → 0.15 → 0.10 三次手调，调的其实都是对面节点的 AGC。
  底噪还没测出来的头两秒不判信号（pre-roll 照常攒着，信号开头不会丢），
  而不是退回一个没在本节点校准过的绝对值。`mode: absolute` 保留原行为。
  - 影响文件: `src/squelch.py`, `config.yaml`

- **Web 面板把静噪状态摊开** — 新增"自适应"开关、生效阈值、静噪开/关状态，
  以及"关闭阈值低于实测底噪"的黄色告警。`POST /api/squelch` 支持 `mode` 和
  `*_margin_db`，并在阈值压到底噪以下时返回 `warning`。
  命令行 `[MONITORING]` 状态行也带上了底噪、生效阈值和静噪开关。
  - 影响文件: `web/index.html`, `src/web_server.py`, `src/receiver.py`

### 🧪 测试

- `tests/test_squelch.py` 增加 13 个用例：底噪在静噪打开期间继续更新、
  阈值低于底噪的标记与强制收尾、长通联分段不误报卡死、断线强制收尾、
  自适应阈值跟随底噪、静音链路不误判。
- `tests/test_web_api.py` 增加 4 个用例：模式切换、非法模式、生效状态字段、
  阈值低于底噪时返回告警。
- 全量 123 个用例通过。

---

## v1.3.1 (2026-08-14)

起因是"守了 11175 一上午，一个信号都没有"。频率本身没问题（11175.0 kHz USB
确实是 HFGCS 的日间主频），问题在于**这套系统区分不了"没有信号"和"根本没有音频"**，
一整天下来日志和界面上是一样的。

### 🐛 修复

- **音频看门狗按错了东西计时** — `_receive_loop` 收到任意一帧（包括 MSG、keepalive
  回执）就刷新看门狗计时器，而且这个检查只写在 `recv()` 超时那一支里。两个后果叠加
  之后：服务器把频道静音、或者 SND 流压根没起来但 MSG 照发的时候，`connected` 永远
  是 True，看门狗一次都轮不到，命令行和 Web 两条路都看不出异常 —— 界面显示"监听中"，
  RMS 恒为 0，守多久都不会有信号。现在改为按 SND 音频帧计时、每轮都检查，并把
  "socket 全静默"和"socket 活着但没有音频"分开报，后者正是之前完全不可见的那一种。
  Web 端的自动重连也因此才真正能对这种情况生效。
  - 影响文件: `src/kiwi_client.py`

- **命令行监听的状态行会整条跳过** — `if int(elapsed) % 30 == 0` 配合 `sleep(1)`，
  每轮多走的几毫秒累积起来会让 `int(elapsed)` 偶尔从 29 直接跳到 31，那一次状态行
  就没了。改成按下一次报告的时间戳判断，并在状态行里加上音频帧数和丢帧数——
  RMS 恒为 0 且帧数不涨，和"有音频但没信号"是两回事，日志里现在分得开。
  连接结束时也会说明是从没收到过音频，还是中途断线（命令行 `monitor` 不会自动重连）。
  - 影响文件: `src/receiver.py`

- **`active_hours` 从来没有被读过** — `frequencies.yaml` 里每个频率都标了活跃时段，
  但没有任何代码读它，纯粹是注释。"频率没错、只是时段不对"（比如 01:00 UTC 去守
  11175 这个日间频率）因此完全不可见。新增 `src/schedule.py` 解析活跃时段，监听启动
  时提示当前不在时段内的频率，并列出同一时刻标注为活跃的高优先级频率。只提示，不拦截。
  - 新增文件: `src/schedule.py`
  - 影响文件: `main.py`, `src/receiver.py`（`FrequencyTarget` 增加 `active_hours`）

### 🔧 工具

- **`diagnose_rms.py` 重写** — 这是"收不到信号"时最该用的工具，但它自己抄了一份帧解析，
  停在 v1.3.0 修复之前的布局（seq 读成 2 字节大端、S-meter 取 `body[3:5]`、音频取
  `body[5:]`），量出来的 S-meter 是错的，打印的阈值建议还停在早就废弃的 0.65，节点也
  硬编码成了澳洲那一个。现在直接复用 `KiwiSDRClient`——体检和实际监听必须共用同一份
  解析——并支持 `-f/-m/-d/--node/--all-nodes`，阈值从 `config.yaml` 读。
  输出直接给结论：没有音频帧（链路问题）／阈值相对本节点定高了／定低了会常开，
  并按实测底噪给出建议值。

### 📝 说明

- `config.yaml` 里补上了阈值的来龙去脉：0.10 是拿 EAM.watch 的录音（另一台接收机、
  另一套电平）按 1024 点窗口回放算出来的，而实时检测器是按每个 KiwiSDR 音频帧
  （512 点）算 RMS，两边口径不一样，这个值并没有在自己的接收链路上校准过。
  节点开着 AGC，阈值是绝对电平，换节点就得重新量。
- `squelch.window_size` 只有 `compare_eam.py` 的离线回放在用，实时检测器不读它，
  已在配置里注明。

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
