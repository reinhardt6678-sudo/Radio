# 更新日志 (CHANGELOG)

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
