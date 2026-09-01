# Changelog / 更新日志

> English first, Chinese second. Test counts quoted in each entry are the counts at the time
> of that release.
> 英文在前、中文在后。每条记录里的测试数量是该版本发布时的数量。

## Unreleased / 未发布

The analyser was right about all 274 of them. Nobody asked it in time.

An hour of listening on 4724 kHz produced **274 recordings, every one of them classified
`NOISE`**, at in-band SNR −9.2 ~ +2.3 dB — a 68% recording duty cycle from a band that was
only over the squelch threshold 2.8% of the time. The four real exchanges heard that night sat
at +4.5 ~ +17.7 dB, nowhere near them. The classifier separated the two perfectly.

It just ran too late to matter. In `receiver.py` the order was:

```
db.record_signal(...)          # row written
analyzer.analyze_and_save(...) # "...this is noise"
```

By the time the verdict existed, the row was in the database and the WAV was on disk. The one
component that could tell noise from signal had no say in whether either was kept.

分析器对这 274 条全判对了。只是没人来得及问它。

在 4724 kHz 上听一小时录出 **274 条，条条判为 `NOISE`**，带内 SNR 在 −9.2 ~ +2.3 dB ——
一个只有 2.8% 的时间越过静噪阈值的频段，最后录音占空比 68%。当晚 4 条真通联在
+4.5 ~ +17.7 dB，和这批差得很远。分类器把两者分得干干净净。

它只是跑得太晚了。`receiver.py` 里的顺序是：先 `db.record_signal(...)` 写记录，
再 `analyzer.analyze_and_save(...)` 说"这是噪声"。等判定结果出来时，记录已经进库、
WAV 已经落盘。唯一分得清噪声和信号的那个组件，对留不留它们没有发言权。

### ✨ Changes / 改动

- **The analysis now runs before the write, and can withhold it.** A segment classified `NOISE`
  with confidence at or above `discard_noise_min_confidence` (default `0.8`) *and* an in-band
  SNR below `discard_noise_max_snr_db` (default `0.0`) gets no signal row and no recording on
  disk. This needs no new signal processing — it only connects a verdict that was already
  correct to the decision it should have been driving. Set `discard_noise: false` to file
  everything as before; the verdict is still stored either way.

  **分析改到写库之前跑，并且有权拦下这条记录。** 判为 `NOISE`、置信度不低于
  `discard_noise_min_confidence`（默认 `0.8`）**且**带内 SNR 低于
  `discard_noise_max_snr_db`（默认 `0.0`）的段，不写信号记录，也不在磁盘上留录音。
  这不需要任何新的信号处理 —— 只是把一个本来就判对了的结论，接到它该驱动的决策上。
  写 `discard_noise: false` 可恢复成以前那样全部入库；判定结果两种情况下都照常保存。

- **The two thresholds are redundant on purpose, and the confidence one binds.** On the `NOISE`
  branch the confidence is a pure function of SNR, so at stock settings the real cut is
  **SNR ≤ −0.6 dB** — 5.1 dB below the weakest real exchange measured, and still catching the
  bulk of the noise bursts. Keeping both gates means loosening either one alone cannot silently
  widen the opening.

  **两个阈值是刻意冗余的，实际起作用的是置信度那个。** `NOISE` 分支的置信度完全由 SNR
  推出，所以在默认参数下真正的切点是 **SNR ≤ −0.6 dB** —— 比实测最弱的真通联还低
  5.1 dB，同时仍能拦下绝大部分噪声突发。两道闸门都留着，意味着单独放宽其中一个
  不会悄悄把口子开大。

- **Anything uncertain is kept.** A buffer too short to analyse (under one second), a
  low-confidence verdict, any SNR at or above the threshold — all filed as before. Discarding
  is irreversible, so the burden of proof sits on discarding.

  **凡是拿不准的一律保留。** 不足一秒无法分析的、置信度不够的、SNR 不够低的，
  全部照常入库。丢弃不可逆，所以举证责任在"丢"这一边。

- **A discarded segment loses its row and its WAV together.** Keeping the file without a record
  would leave an orphan that nothing points at — which is exactly what makes
  `clean_recordings.py` risky to run. Either both exist or neither does.

  **被丢弃的段同时失去记录和 WAV。** 留下文件却没有记录就成了孤儿文件，
  没有任何记录指向它 —— 而这正是 `clean_recordings.py` 跑起来有风险的原因。
  要么两个都在，要么两个都不在。

- **Discards are counted and logged separately from signals.** `[NOISE-DISCARD]` names the
  frequency, the reason and whether the file was deleted; the count is exposed as
  `discarded_signals` on the web status API. A quiet band and a band drowning in wideband noise
  no longer look identical from the outside.

  **丢弃单独计数、单独打日志，不混进信号数。** `[NOISE-DISCARD]` 会写明频率、理由，
  以及文件删没删；计数通过 Web 状态接口的 `discarded_signals` 暴露出来。
  "真的没东西"和"全是宽带噪声"从外面看不再是同一个样子。

### Notes / 说明

This does not change what the squelch records, only what survives to the database — the radio
still opens on those noise bursts and still writes a file first. Narrowing the squelch itself
(a minimum-duration gate, or a margin that tracks variance rather than only the noise floor)
is a separate change; the measured `+14 dB` margin triggers **28× more often** on 4724 kHz than
on 11175 kHz despite noise floors within 3 dB of each other.

本次改动不影响静噪录什么，只影响什么能活到数据库里 —— 收到那些噪声突发时静噪照样打开，
录音文件也照样先写出来。收窄静噪本身（最短时长门限，或让余量跟着方差走而不只看底噪）
是另一件事：实测同样的 `+14 dB` 余量在 4724 kHz 上的触发频次是 11175 kHz 的 **28 倍**，
而两者底噪相差不到 3 dB。

Existing records are untouched; the gate only applies to segments received from now on.
已有记录不受影响；闸门只对此后收到的段生效。

Tests: 262 passed (230 before this change).
测试：262 通过（本次改动前为 230）。

## v1.4.3 (2026-08-31)

A node can pass every check the monitor makes and still be **completely silent**.

Starting a routine background listen on 8992 kHz, node selection picked K1VL Vermont — lowest
latency of the seven at 552 ms, clean handshake, no reason to doubt it. Twelve seconds after
connecting it started producing "signals", four of them in the first forty seconds. Every one
was digital silence:

```
20260831_202730_8992.0kHz_K1VL.wav | 60928 samples | min 0 max 0 | nonzero 0
```

Audio frames were arriving normally the whole time — `frames=704 (dropped=0)`. A link check
across all seven nodes confirmed it: K1VL's audio RMS had **minimum, median and maximum all
equal to 0.00003**, one constant value, while its S-meter read a perfectly healthy
−101.7 ~ −86.9 dBm.

That combination is what defeats the current design. Since v1.4.0 the squelch criterion is the
S-meter, chosen precisely because it is measured *before* the node's audio AGC and therefore
survives a flattened audio level. But it means the squelch never looks at the audio at all — so
a node with a live receiver and a dead audio path triggers happily, and the recorder writes
files of zeros that the analyser then files in the database as `NOISE`.

Node selection could not catch it either. [v1.4.2](#v142-2026-08-21) added quality tiers so
latency stops deciding on its own, but those tiers are built from *received signals*, and a node
nobody has listened to yet has no history — K1VL sat in the "unknown, give it a chance" tier and
won on latency.

节点可以通过监听所做的每一项检查，然后**一声不出**。

在 8992 kHz 上例行开一个后台监听，节点择优挑中了 K1VL Vermont —— 七个节点里延迟最低
（552 ms）、握手干净、没有任何可疑之处。连上 12 秒后它开始产出"信号"，头 40 秒出了四条。
每一条都是数字静音（见上面的样本统计）。

而音频帧全程都在正常到达 —— `frames=704 (dropped=0)`。对七个节点逐一做链路体检确认了：
K1VL 的音频 RMS **最小值、中位数、最大值全部等于 0.00003**，一个恒定值，
而它的 S-meter 读数完全健康，−101.7 ~ −86.9 dBm。

正是这个组合击穿了现有设计。从 v1.4.0 起静噪判据换成了 S-meter，选它恰恰是因为
它在节点音频 AGC **之前**测量，所以音频电平被压平时它依然有效。但这也意味着静噪
根本不看音频 —— 于是接收机活着、音频通路死掉的节点照样触发，录音器写出一串零，
分析器再把它当成 `NOISE` 记进数据库。

节点择优同样拦不住。[v1.4.2](#v142-2026-08-21) 加了质量分档，让延迟不再单独说了算，
但那些分档是从**已收到的信号**建立的，而没人听过的节点没有历史 ——
K1VL 落在"还不知道，给它个机会"那一档，然后靠延迟胜出。

### ✨ Changes / 改动

- **The squelch now refuses to open on a muted link.** A rolling window tracks per-block audio
  RMS; when its peak-to-peak spread stays at or below `dead_audio_rms_spread` (default `1e-6`)
  across at least `dead_audio_min_blocks` blocks, the link is declared muted and the squelch
  stays shut however far the S-meter rises. Real audio always carries thermal noise, so a
  spread of exactly zero is conclusive rather than heuristic — the same window measured on a
  working node spans 0.0076 ~ 0.0172, four orders of magnitude above the threshold. Set
  `dead_audio_min_blocks: 0` to disable the guard.

  **静噪不再在哑音链路上打开。** 用滚动窗口跟踪逐块音频 RMS；当极差在至少
  `dead_audio_min_blocks` 块内始终小于等于 `dead_audio_rms_spread`（默认 `1e-6`）时，
  判定链路哑音，此后 S-meter 抬得再高静噪也不开。真实音频总带着热噪声，
  所以"极差恰好为零"是确凿判据而非启发式猜测 —— 同样的窗口在正常节点上实测
  0.0076 ~ 0.0172，比阈值高四个数量级。写 `dead_audio_min_blocks: 0` 可关闭该判定。

- **Muted nodes are remembered across runs and ranked last.** Two counters on the `nodes` table
  (`audio_dead_checks` / `audio_ok_checks`, added by the usual automatic column migration)
  record what each node actually delivered. A node observed muted and never heard live drops to
  a new worst tier, below even "proven deaf": a deaf node at least produces audio, so the
  frequency may simply have been quiet, whereas a muted node can never produce a signal. One
  live observation clears it, so a single glitch does not condemn a node forever.

  **哑音节点跨进程记住，并排到最后。** `nodes` 表上两个计数器
  （`audio_dead_checks` / `audio_ok_checks`，走既有的自动补列迁移）记录每个节点实际
  送出了什么。被观测到哑音且从未收到过真音频的节点掉进一个新的最差档，
  比"证明听不见"还靠后：聋节点至少还在出音频，可能只是那个频率当时没动静，
  而哑音节点永远不可能产出信号。收到一次真音频即解除，一次抽风不会把节点永久判死。

- **A muted leg switches node immediately instead of backing off.** Reconnecting is pointless
  in a way it is not for a dropped connection — the node connects fine, has low latency and
  keeps streaming, so attempt 101 returns the same zeros as attempt 1.

  **判定哑音后立刻换节点，不走退避重试。** 这里的重试和普通掉线不同，是彻底没有意义的
  —— 节点连得上、延迟低、帧照发，第 101 次和第 1 次拿到的是同一串零。

- **`diagnose_rms.py` now names this failure.** It always printed minimum, median and maximum
  RMS; it never said out loud that three identical values mean the node is muted. Previously it
  fell through to the threshold advice and suggested `open_threshold=0.0001` — pointing at the
  threshold when the threshold was never the problem.

  **`diagnose_rms.py` 现在把这种坏法说破。** 它一直在打印 RMS 的最小/中位/最大值，
  却从没说出"三个数一样 = 节点是哑的"。此前它会落到阈值建议那一支，
  给出 `open_threshold=0.0001` —— 把人往阈值方向带，而阈值从来不是问题所在。

### Notes / 说明

Existing databases gain the two columns automatically on first open; no migration step is
needed and no history is lost. Recordings already made by a muted node are unaffected by this
change — they are still all-zero files with `NOISE` rows, and still need cleaning up.

老数据库首次打开时自动补上这两列，无需迁移步骤，不丢历史数据。此前由哑音节点录下的
文件不受本次改动影响 —— 它们依然是全零文件加 `NOISE` 记录，仍然需要清理。

Tests: 230 passed (208 before this release).
测试：230 通过（本次发布前为 208）。

## v1.4.2 (2026-08-21)

The reconnect logic added in v1.4.1 kept the process alive — and then it sat on a node that
could not hear anything **for five and a half hours**.

What actually happened: KPH dropped at 08:21, three reconnect attempts failed, the monitor
moved to G3SDR, G3SDR kicked it three times in a row, and at 08:26:30 it landed on HB3YQQ in
Switzerland — where it stayed. Over the following 5.5 hours:

v1.4.1 的重连让进程活下来了，但活下来之后**停在了一个听不见的节点上 5.5 小时**。

当天 08:21 KPH 断开，重连 3 次失败后换到 G3SDR，G3SDR 也连踢 3 次，
08:26:30 落到 HB3YQQ 瑞士——然后就再没动过。之后 5.5 小时：

| Frequency / 频率 | Before 08:26 (KPH California) / 08:26 之前 | After 08:26 (HB3YQQ Switzerland, 5.5 h) / 08:26 之后（瑞士，5.5h） |
|------|------|------|
| 8992 / 11175 / 15016 kHz | All producing signals / 都在出信号 | **0 records each** / **各 0 条** |
| 4724 kHz | Producing signals / 在出信号 | 76 records, 71 classified NOISE, median in-band SNR **−5.6 dB**<br>76 条，其中 71 条判定 NOISE，中位带内 SNR **-5.6 dB** |

Scanning KPH separately at the same moment, as a control: 8992 kHz came back **USB_VOICE with
an in-band SNR of +4.0 dB**. The frequency was not idle — this node simply could not hear it.
The HFGCS ground stations are in the United States, and receiving 8992/11175 from Europe in
daylight is marginal at best. KPH had recovered long before, and nothing switched back to it.

Root cause: **node selection only looked at reachability and latency**. The Swiss node had the
lowest latency in the field at 925 ms — and a history of 85 records containing zero real signals.

同一时刻单独扫 KPH 做对照：8992 kHz **USB_VOICE，带内 SNR +4.0 dB**。
不是频率没活动，是这个节点听不见——HFGCS 地面台在美国，欧洲白天收 8992/11175
本来就够呛。而 KPH 早就恢复了，没人把它切回去。

根因：**节点择优只看连通性和延迟**。瑞士节点延迟 925ms 全场偏低，却是
历史上 85 条记录 0 条真信号的节点。

### Considered and rejected / 试过但否掉的方案

The obvious fix was an "output watchdog": switch node after N minutes without a real signal.
**Running the numbers kills it.** Over 14 hours the *good* node, KPH, went as long as
**422 minutes** between two signals with SNR ≥ 6 dB, while the Swiss node produced zero across
the whole 5.5 hours. Any threshold safe for KPH (> 7 hours) would never have caught the Swiss
case. "How long since the last output" cannot tell a deaf node apart from a quiet frequency.

本想加个"产出看门狗"——多久没出真信号就换节点。**用实测数据一算就不成立**：
好节点 KPH 在 14 小时里，两条 SNR ≥ 6 dB 的信号之间最长干涸 **422 分钟**，
而瑞士节点整个 5.5 小时是 0 条。任何对 KPH 安全的阈值（>7 小时）都抓不到
瑞士这种情况。"多久没产出"分不开"节点聋"和"频率本来就闲"。

### ✨ Changes / 改动

- **Node selection is now tiered by historical reception quality**, with latency demoted to a
  tie-break *within* a tier:
  **节点择优改成按历史接收质量分档**，延迟降为同档内的平手判据：

  | Tier / 档 | Condition / 条件 |
  |----|------|
  | 0 | Has produced a signal with in-band SNR ≥ 6 dB / 出现过带内 SNR ≥ 6 dB 的信号 |
  | 1 | Fewer than 30 records — not known yet / 记录不足 30 条，还不知道 |
  | 2 | 30+ records and not one of them qualifies / 记录 ≥ 30 条但一条达标的都没有 |

  "No data" ranks ahead of "data proving it does not work": an untried node still has a chance,
  a node tried 85 times with nothing to show does not get another turn. Startup selection
  (`get_best_node`) and mid-run switching (`_pick_alternative_node`) **share the same tiering**,
  defined in `node_manager.py`.

  "没数据"排在"有数据证明它不行"前面：没试过的还有机会，试了 85 次 0 条的
  就别再回去。启动选节点（`get_best_node`）和中途换节点
  （`_pick_alternative_node`）**共用同一套分档**，定义在 `node_manager.py`。
  - Files / 影响文件：`src/node_manager.py`, `src/receiver.py`, `src/db.py`

- **New `db.get_node_signal_quality()`** — counts total and qualifying rows per node across
  `signals` × `analysis`. This data was already in the database; it had simply never been used
  to choose a node.

  **新增 `db.get_node_signal_quality()`** —— 按节点统计 `signals` × `analysis`
  的总条数与达标条数。这份数据本来就在库里，只是从来没被用来挑节点。

- **Retry the preferred node every 30 minutes after being forced off it.** The preferred node is
  whichever one was chosen when this monitoring run started. The check lives in the
  frequency-rotation loop, so it still fires on schedule even when a single connection hangs on
  for hours — had it been placed between connections, the 5.5-hour Swiss connection would never
  have triggered it once.

  **被迫离开首选节点后每 30 分钟回去试一次**。首选节点 = 这轮监听启动时挑中的
  那个；检查放在频率轮询循环里，所以一条连接挂着几小时也照样按时触发
  （放在连接之间检查的话，瑞士那条 5 小时的连接期间根本不会触发）。

- `main.py` now prints **why** a node was selected (how many qualifying historical signals, or
  no history at all) instead of just a latency figure.

  `main.py` 选节点时会把**理由**打出来（历史有效信号多少条 / 没有历史记录），
  不再只报一个延迟数字。

### 🧪 Tests / 测试

- New `tests/test_node_manager.py` (13 cases): the tiering rules, how `get_best_node` chooses
  between "low latency but deaf" and "high latency but has heard something", and degrading to
  latency-only ordering when the database errors.
  新增 `tests/test_node_manager.py`（13 个用例）：分档规则、`get_best_node`
  在"低延迟但听不见"和"高延迟但收到过"之间怎么选、数据库报错退化成按延迟挑。
- `tests/test_receiver_reconnect.py` gains two groups: quality ordering and returning to the
  preferred node.
  `tests/test_receiver_reconnect.py` 增加质量排序与回首选节点两组用例。
- `tests/test_db.py` gains a `get_node_signal_quality` case.
  `tests/test_db.py` 增加 `get_node_signal_quality` 用例。

195 passed. / 全套 195 passed。

## v1.4.1 (2026-08-20)

Another "half a day with no data", and this time it was neither the squelch nor the AGC — **the
node kicked us off, and the command-line `monitor` simply exited on disconnect.**

What the logs showed: monitoring started at 11:00 and recorded 295 signals over four hours
(165 on 11175 kHz, 94 on 8992 kHz, 32 on 15016 kHz, 4 on 4724 kHz), then stopped dead at 15:00
with the session still marked `status=completed` as if it had ended normally. On restart the
node answered with one line that settled it:

又一次"半天没数据"，这次不是静噪也不是 AGC —— 是**节点把人踢了，而命令行
`monitor` 掉线就直接退出**。

当天的实际情况：11:00 起监，4 小时里正常记了 295 条信号（11175 kHz 165 条、
8992 kHz 94 条、15016 kHz 32 条、4724 kHz 4 条），15:00 整戛然而止，会话还是
`status=completed` 正常结束的。重启时节点回了一句话，直接坐实原因：

```
ERROR | Server refused connection: MSG ip_limit=240%2c<local IP>
ERROR | 服务器拒绝连接: MSG ip_limit=240%2c<本机 IP>
```

`ip_limit=240` — K1VL limits a single IP to **240 minutes**, so a full four hours gets you
disconnected. And `_monitor_multiple_frequencies` hit `not client.connected` and simply
`break`-ed out, leaving the following hours empty. Restarting on a different node did not help
either: G3SDR answered `too_busy=0` and the same exit happened 31 seconds later.

`ip_limit=240` —— K1VL 对单 IP 限时 **240 分钟**，用满 4 小时就断开。
而 `_monitor_multiple_frequencies` 遇到 `not client.connected` 就 `break`
退出，后面几个小时整段空掉。换个节点重启也不行：G3SDR 回 `too_busy=0`，
31 秒后同样退出。

### ✨ Changes / 改动

- **Command-line `monitor` now reconnects and switches nodes**, matching the web path:
  **命令行 `monitor` 支持断线重连与换节点**，和 Web 端对齐：
  - Exponential backoff 5s → 10s → 20s → 40s → 60s, capped
    指数退避 5s → 10s → 20s → 40s → 60s 封顶
  - Three consecutive failures on one node moves to the next; once the whole list has been
    tried it wraps around
    同一节点连续失败 3 次换下一个；一圈试遍了就从头再来
  - Alternative nodes are drawn **only** from `nodes` in `config.yaml` — the `nodes` rows in
    the database carry no `man_gain`, and building a client from those would throw away the
    gain calibration and immediately wreck the noise floor
    备用节点只从 `config.yaml` 的 `nodes` 里挑 —— 数据库那份 `nodes` 记录
    没有 `man_gain`，拿它建客户端会把增益标定丢掉，底噪立刻不对
  - **Being kicked after connecting counts the same as never connecting**: a connection that
    does not survive 60 seconds counts as a failure and does not reset the backoff, otherwise
    a bad node spins without any backoff at all
    **连上就被踢和压根连不上一视同仁**：一条连接活不满 60 秒就算失败，
    不重置退避，否则会在坏节点上无退避空转
  - Files / 影响文件：`src/receiver.py`

- **A reconnect no longer fragments the session** — reconnecting and switching nodes both count
  as the same monitoring run, `sessions` still holds a single row, and the remaining
  `--duration` keeps counting down rather than being renewed.

  **会话不再被重连切碎** —— 重连和换节点都算同一次监听，
  `sessions` 表里仍然只有一行，`--duration` 的剩余时长接着扣不会被续期。

- **`Ctrl+C` is no longer held up by the backoff** — the wait is now interruptible in 1-second
  slices; previously the worst case was a full 60-second wait before exit.

  **`Ctrl+C` 不再被退避拖住** —— 退避改成 1 秒一段的可打断等待，
  以前最坏要干等满 60 秒才退出。

- The disconnect log dropped the now-obsolete line advising that the command-line `monitor` does
  not reconnect and that long runs should use the web UI.

  掉线日志去掉了"命令行 monitor 不会自动重连，长时间监听请用 web"这句
  已经过时的引导。

### 🧪 Tests / 测试

New `tests/test_receiver_reconnect.py` (16 cases): alternative-node selection (skipping nodes
already tried, ordering by availability and latency, preserving `man_gain` from the config,
wrapping around after a full pass, surviving a database error) and the reconnect main loop
(continuing after a drop, creating the session only once, switching node after consecutive
failures, finishing normally at the duration limit, not renewing the remaining duration,
`stop()` taking effect immediately, one connection error not being fatal, and the single-
frequency path reconnecting the same way).

新增 `tests/test_receiver_reconnect.py`（16 个用例）：备用节点挑选（跳过试过的、
按可用性和延迟排序、保留配置里的 `man_gain`、一圈试遍后回绕、数据库报错不致命）、
重连主循环（掉线后继续、会话只建一次、连续失败换节点、到时长正常收工、
剩余时长不被续期、`stop()` 立即生效、单条连接异常不致命、单频路径同样重连）。

166 passed. / 全套 166 passed。

## v1.4.0 (2026-08-18)

After the third "watched all day, not a single signal", the threshold did not get another guess.
Instead there was **a 93-hour controlled experiment** — two frequencies (11175 / 8992), four
receiving nodes, 109 connection segments, 7.7 GB of audio — to measure the problem properly.

The conclusion: **the first two failures were the same root cause pointing in opposite
directions, and the root cause is not the threshold, it is the AGC of the node.**

第三次"守了一整天，一个信号都没有"之后，这次没有再去猜阈值，而是**跑了一次
93 小时的对照实验**（两个频率 11175 / 8992、4 个接收节点、109 个连接段、
7.7 GB 音频），把问题量到底。

结论：**前两次故障是同一个根因的两个方向，而根因不是阈值，是节点的 AGC。**

### What the experiment measured / 实验测到了什么

With AGC on at the node (`SET agc=1`), the output level is pinned. Taking the S-meter as the
reference — KiwiSDR measures RF level *before* the audio AGC:

节点开着 AGC（`SET agc=1`）时，输出电平被钉死。以 S-meter（KiwiSDR 在音频
AGC **之前**测的射频电平）为参照：

| State / 状态 | Segments / 段数 | Hours / 时长 | Audio dB per 1 dB of RF / 射频动 1 dB 音频跟多少 | Audio level gap, signal vs. no signal / 有信号与没信号的音频电平差 |
|------|------|------|------------------------|---------------------------|
| AGC off / AGC 关 | 62 | 47.9 h | **0.89 dB** | **8.0 dB** (median 7.1 / 中位 7.1) |
| AGC on / AGC 开 | 15 | 10.2 h | **0.23 dB** | **2.2 dB** (median 1.6 / 中位 1.6) |

Per node the direction is identical (0.72–0.91 with AGC off, 0.15–0.36 with AGC on).
四个节点分别看方向完全一致（AGC 关 0.72–0.91，AGC 开 0.15–0.36）。

**Both historical failures reproduced inside this one dataset**, separated by a single parameter:
在这批数据里**同时复现了历史上的两次故障**，只差一个参数：

| Setting (20.4 h with AGC on, 8 real exchanges) / 设置（AGC 开着的 20.4 小时，8 段真通联） | Caught / 抓到 | Share of time recording / 录制占比 |
|------------------------------------------|------|----------|
| RMS noise floor +15 dB and above / RMS 底噪 +15 dB 及以上 | **0/8** | **0%** ← the "zero all day" of v1.3.2 / v1.3.2 的"一整天 0 条" |
| Absolute threshold 0.10 / 绝对阈值 0.10 | 8/8 | **74%** ← the "213 back-to-back WAVs" of v1.3.1 / v1.3.1 的"213 个背靠背 WAV" |

**Same audio, and moving the threshold jumps straight from "records nothing" to "records almost
continuously", with no usable setting in between.** Turn the AGC off and the cliff disappears:
+6 dB catches 100%, +12 dB catches 94%, +18 dB catches 72% — smooth and adjustable.

**同一批音频，挪一下阈值就在"什么都不录"和"几乎一直在录"之间跳，中间没有
可用档位。** 关掉 AGC 之后这个悬崖消失：+6 dB 抓 100%、+12 dB 抓 94%、
+18 dB 抓 72%，平滑可调。

### ✨ Changes / 改动

- **Node AGC is off by default** (`receiver.agc: false`), replaced by a per-node calibrated
  fixed gain `man_gain`. All seven nodes in config.yaml carry measured values (target noise
  floor RMS ≈ 0.015).

  **节点 AGC 默认关闭**（`receiver.agc: false`），改用每节点标定的固定增益
  `man_gain`。config.yaml 里 7 个节点的值都是实测标定过的（目标底噪
  RMS ≈ 0.015）。
  - AGC **can only be set as the connection is established**: once a node has entered AGC mode,
    a later `SET agc=0` is ignored (gain stays high — measured 22 dB off, no different from
    leaving AGC on). So it is re-sent on every reconnect and never switched mid-stream.
    AGC **只能在连接建立时设定**：实测节点一旦进入 AGC 模式，之后再发
    `SET agc=0` 会被忽略（增益停在高位，与 AGC 开着无异，差 22 dB）。
    所以每次重连都重新下发，绝不在流中途切换。
  - Files / 影响文件：`src/kiwi_client.py`, `config.yaml`

- **New squelch criterion `mode: smeter`, now the default** — threshold = S-meter noise floor
  + 14 dB. The S-meter is measured before the audio AGC and was **the only criterion in the
  experiment that worked with AGC both on and off**. Measured with AGC off across 75 real
  exchanges:

  **新增 `mode: smeter` 静噪判据并设为默认** —— 阈值 = S-meter 底噪 + 14 dB。
  S-meter 在音频 AGC 之前测量，是实验里**唯一在 AGC 开和关两种状态下都能用**
  的判据。实测（AGC 关，75 段真通联）：

  | Margin / 余量 | Exchanges caught / 抓到通联 | Share of time recording / 录制时间占比 |
  |------|----------|--------------|
  | +6 dB | 100% | 48% |
  | +10 dB | 100% | 34% |
  | **+14 dB (default) / +14 dB（默认）** | **96%** | **17%** |
  | +18 dB | 69% | 5% |

  - Files / 影响文件：`src/squelch.py`, `config.yaml`, `src/receiver.py`, `src/web_server.py`

- **Every finished recording gets a speech-structure score** (the second stage of a two-stage
  design). Squelch alone cannot be both precise and complete — real exchanges occupy only 0.8%
  of elapsed time, and catching 96% of them means recording 17%. So the gate is opened wide and
  the recordings are ranked afterwards by how much they look like speech:

  **录完之后给每段录音打人声结构分**（两段式的第二段）。单靠静噪做不到又准
  又全 —— 真通联只占全程 0.8% 的时间，要抓住 96% 就得录 17%。所以闸门放宽，
  录完再按"是不是人声"排序：
  - `syllabic_ratio` — the share of envelope energy falling in 0.5–4 Hz (speech comes in
    syllables)
    `syllabic_ratio` 包络能量落在 0.5–4 Hz 的占比（人说话一句一句的）
  - `passband_tilt_db` — how much stronger the low end of the USB passband is than the high end
    `passband_tilt_db` USB 通带低频端比高频端强多少
  - `speech_score` — a continuous 0–1 score; `is_speech` — the boolean verdict
    `speech_score` 0–1 连续分，`is_speech` 布尔判定
  - All three measure *shape*, not level, so they hold whether or not AGC is on
    三个量都是"形状"不是"电平"，所以 AGC 开不开都成立
  - Files / 影响文件：`src/analyzer.py`, `src/db.py` (three new columns, migrated automatically
    on old databases / 三个新列，老库自动补列)

- **The web squelch panel now offers three criteria in a dropdown** (S-meter / adaptive RMS /
  fixed RMS). The S-meter entry adds open/close margin sliders plus readouts for the measured
  S-meter noise floor and the effective threshold. `POST /api/squelch` accepts
  `smeter_open_margin_db` / `smeter_close_margin_db` and rejects a close margin ≥ the open
  margin (otherwise there is no hysteresis).

  **Web 面板静噪区改成三档判据下拉**（S-meter / 自适应 RMS / 固定 RMS），
  S-meter 档带打开/关闭余量滑块、实测 S-meter 底噪和生效阈值读数。
  `POST /api/squelch` 新增 `smeter_open_margin_db` / `smeter_close_margin_db`，
  关闭余量 ≥ 打开余量时拒绝（否则没有滞后）。
  - Files / 影响文件：`web/index.html`, `src/web_server.py`

### 🐛 Fixes / 修复

- **Removed `greatlakesreceiver.hopto.me`** — the whole `hopto.me` domain now NXDOMAINs (the
  same host under `hopto.org` is unreachable too). It had the lowest latency in the database, so
  it was auto-selected at the start of every monitoring run and then failed to connect. This was
  the second, independent cause of the previous "zero all day".

  **移除 `greatlakesreceiver.hopto.me`** —— 整个 `hopto.me` 域已经
  NXDOMAIN（`hopto.org` 下同名主机也连不上）。它在数据库里延迟最低，
  于是每次开始监听都会被自动选中，然后连不上。这是上一次"一整天 0 条"
  的第二个独立原因。
  - Files / 影响文件：`config.yaml`

### 🧪 Tests / 测试

- New `tests/test_smeter_squelch.py` with 26 cases, two of which pin the historical failures
  directly:
  新增 `tests/test_smeter_squelch.py` 26 个用例，其中两个直接锁住历史故障：
  - Audio pinned high by AGC while the RF is quiet → squelch must not open (the stuck-open case
    of v1.3.1)
    音频被 AGC 钉在高电平但射频安静 → 静噪不能开（v1.3.1 的卡开）
  - Audio level unchanging while the S-meter rises → squelch must open (the zero-records case
    of v1.3.2)
    音频电平一动不动但 S-meter 抬起来 → 静噪必须开（v1.3.2 的 0 条）
- 150 cases pass in total. / 全量 150 个用例通过。

### ⚠️ Limits of the experiment itself / 实验本身的局限

- "Real exchange" was decided by a self-written acoustic criterion, without an external log to
  compare against (the latest EAM.watch record stops at 2026-07-26). Of 718 candidate segments,
  437 isolated 5-second windows were discarded and only the 75 segments ≥ 20 s were kept.
  判定"真通联"用的是自写的声学判据，没有外部日志对照（EAM.watch 最新记录
  停在 2026-07-26）。剔掉了 718 段候选里 437 段只有 5 秒的孤立窗口，
  只保留 ≥20 秒的 75 段。
- The AGC-on control sample is only 8 exchanges over 20.4 hours, far smaller than the 67 with
  AGC off.
  AGC 开着的对照样本只有 8 段通联 / 20.4 小时，比 AGC 关的 67 段少得多。
- `man_gain` drifts with band noise and has to be re-calibrated when changing band (the values
  for 8992 and 11175 differ).
  `man_gain` 会随波段噪声漂移，换频段要重标（8992 和 11175 的值不一样）。

---

## v1.3.2 (2026-08-15)

Another "watched all day, not a single signal" — but not the same thing as v1.3.1. The audio
chain was entirely healthy (S-meter −108 dBm, RMS 0.0939, a full waterfall) and **the squelch
was open from beginning to end** — while signals are only written to the database at the moment
the squelch *closes*.

A complete causal chain:

又一次"守了一整天，一个信号都没有"，但这次和 v1.3.1 那次不是一回事：
音频链路完全正常（S-meter -108 dBm、RMS 0.0939、瀑布图满的），
**静噪从头到尾一直是打开的** —— 而信号只在静噪**关闭**的那一刻才落库。

一条完整的因果链：

1. The node had AGC on, lifting the noise floor to RMS ≈ 0.09;
   节点开着 AGC，底噪被抬到 RMS ≈ 0.09；
2. Noise-floor statistics only accumulated while the squelch was closed, so the few seconds at
   the very start of monitoring — before audio had really come up (RMS ≈ 0.0038) — fixed the
   "measured noise floor" for good;
   底噪统计只在静噪关闭时累积，于是监听刚起步、音频还没真正上来的那几秒
   （RMS ≈ 0.0038）就把"实测底噪"定死了；
3. The user pressed "set from noise floor" against that dead value, making the thresholds
   0.010 / 0.004;
   用户照着这个死值点了"按底噪设定"，阈值变成 0.010 / 0.004；
4. The thresholds were an order of magnitude below the true noise floor → the squelch opened on
   the first frame and never closed → noise-floor statistics stopped updating from then on
   (back to step 2) → 18 hours and zero signals on screen, while the recorder kept writing
   300-second segments to disk in the background.
   阈值比真实底噪低了一个数量级 → 静噪第一帧就打开、再也关不掉 →
   底噪统计从此停止更新（回到第 2 步）→ 界面上 18 小时 0 个信号，
   而录音机在后台一直按 300 秒分段写盘。

### 🐛 Fixes / 修复

- **Noise-floor statistics were frozen while the squelch was open** — they now run continuously
  (10-minute window, 10th percentile; a low percentile is not lifted by intermittent signals
  anyway) and have moved into `SquelchDetector`, so the CLI and the web share one implementation.
  The old "only measure while closed" arrangement self-locks when the threshold is misconfigured:
  the lower it goes, the tighter it locks.

  **底噪统计在静噪打开期间被冻结** — 改为一直统计（10 分钟窗口取第 10 百分位，
  低百分位本来就不会被间歇性的信号抬起来），并挪进 `SquelchDetector`，
  命令行和 Web 共用同一份。之前那个"只在静噪关闭时统计"的写法在阈值配错时
  会自我锁死：越锁越低。
  - Files / 影响文件: `src/squelch.py`, `src/web_server.py`

- **No records at all when the squelch opens and cannot close** — new `max_open_seconds`
  (default aligned with `recording.max_duration`, 300 s): once open that long, close it by force
  and the signal is stored as usual. If the level is still above the close threshold at that
  moment, the threshold is below the noise floor rather than a long signal being received, so the
  log emits `[SQUELCH-STUCK]` and the web panel shows a yellow warning. A genuinely long exchange
  is merely segmented, with no warning.

  **静噪打开后关不掉时永远不出记录** — 新增 `max_open_seconds`（默认跟
  `recording.max_duration` 对齐，300 秒）：连续打开到点强制收尾，信号照常入库。
  如果收尾时电平仍压在关闭阈值之上，说明是阈值低于底噪而不是收到了长信号，
  日志打 `[SQUELCH-STUCK]` 并在 Web 面板上出黄色告警。长通联则只是分段，不告警。
  - Files / 影响文件: `src/squelch.py`

- **A signal being recorded when the link dropped was thrown away whole** — previously the
  recorder was simply stopped: the WAV stayed on disk with no database record at all. A 24/7
  monitor lost one segment on every reconnect. Disconnects, exceptions, stopping the monitor and
  changing frequency on the CLI now all go through `force_close()`, so the callbacks run to
  completion.

  **断线时正在录的信号被整段丢掉** — 之前直接停录音器：WAV 留在磁盘上，
  数据库里没有任何记录。24/7 监听每次重连都会丢一段。现在断线、异常、
  停止监听、命令行切频率都走 `force_close()`，回调照常跑完。
  - Files / 影响文件: `src/web_server.py`, `src/receiver.py`

### ✨ Added / 新增

- **Adaptive squelch `mode: adaptive` (the new default)** — threshold = measured noise floor
  +6 dB / +3 dB, following the floor by itself. With AGC on at the node, absolute levels are not
  comparable in the first place; the three manual adjustments in this changelog (0.65 → 0.15 →
  0.10) were all really adjustments to the far-end AGC. The first two seconds, before a floor has
  been measured, produce no signal decision (pre-roll keeps buffering, so the start of a signal
  is not lost) rather than falling back to an absolute value never calibrated on this node.
  `mode: absolute` keeps the old behaviour.

  **自适应静噪 `mode: adaptive`（新默认）** — 阈值 = 实测底噪 +6 dB / +3 dB，
  跟着底噪自己走。节点开着 AGC 时绝对电平本来就没有可比性，
  CHANGELOG 里 0.65 → 0.15 → 0.10 三次手调，调的其实都是对面节点的 AGC。
  底噪还没测出来的头两秒不判信号（pre-roll 照常攒着，信号开头不会丢），
  而不是退回一个没在本节点校准过的绝对值。`mode: absolute` 保留原行为。
  - Files / 影响文件: `src/squelch.py`, `config.yaml`

- **The web panel lays the squelch state out in the open** — new adaptive toggle, effective
  threshold, squelch open/closed state, and a yellow warning for "close threshold below the
  measured noise floor". `POST /api/squelch` accepts `mode` and `*_margin_db`, and returns a
  `warning` when a threshold is pushed below the floor. The CLI `[MONITORING]` status line now
  carries the noise floor, effective threshold and squelch state too.

  **Web 面板把静噪状态摊开** — 新增"自适应"开关、生效阈值、静噪开/关状态，
  以及"关闭阈值低于实测底噪"的黄色告警。`POST /api/squelch` 支持 `mode` 和
  `*_margin_db`，并在阈值压到底噪以下时返回 `warning`。
  命令行 `[MONITORING]` 状态行也带上了底噪、生效阈值和静噪开关。
  - Files / 影响文件: `web/index.html`, `src/web_server.py`, `src/receiver.py`

### 🧪 Tests / 测试

- `tests/test_squelch.py` gains 13 cases: the floor keeps updating while the squelch is open,
  flagging and force-closing a threshold below the floor, a long exchange being segmented without
  a false stuck report, force-close on disconnect, adaptive thresholds tracking the floor, and a
  silent link not being misread.
  `tests/test_squelch.py` 增加 13 个用例：底噪在静噪打开期间继续更新、
  阈值低于底噪的标记与强制收尾、长通联分段不误报卡死、断线强制收尾、
  自适应阈值跟随底噪、静音链路不误判。
- `tests/test_web_api.py` gains 4 cases: mode switching, an invalid mode, the effective-state
  fields, and the warning returned when a threshold falls below the floor.
  `tests/test_web_api.py` 增加 4 个用例：模式切换、非法模式、生效状态字段、
  阈值低于底噪时返回告警。
- 123 cases pass in total. / 全量 123 个用例通过。

---

## v1.3.1 (2026-08-14)

This started with "watched 11175 all morning, not a single signal". The frequency itself was
fine (11175.0 kHz USB really is the HFGCS daytime primary); the problem is that **this system
could not tell "no signal" apart from "no audio at all"** — a whole day of either looks identical
in the logs and on screen.

起因是"守了 11175 一上午，一个信号都没有"。频率本身没问题（11175.0 kHz USB
确实是 HFGCS 的日间主频），问题在于**这套系统区分不了"没有信号"和"根本没有音频"**，
一整天下来日志和界面上是一样的。

### 🐛 Fixes / 修复

- **The audio watchdog was timing the wrong thing** — `_receive_loop` refreshed the watchdog
  timer on *any* frame (including MSG and keepalive acknowledgements), and the check only existed
  on the `recv()` timeout branch. Stack the two together and, whenever the server muted the
  channel or the SND stream never started while MSG kept flowing, `connected` stayed True forever
  and the watchdog never got a turn — neither the CLI nor the web could see anything wrong: the
  UI said "monitoring", RMS sat at 0, and no amount of waiting produced a signal. It now times
  SND audio frames, checks on every iteration, and reports "socket entirely silent" separately
  from "socket alive but no audio" — the latter being exactly the case that was previously
  invisible. This is also what finally lets the web auto-reconnect act on it.

  **音频看门狗按错了东西计时** — `_receive_loop` 收到任意一帧（包括 MSG、keepalive
  回执）就刷新看门狗计时器，而且这个检查只写在 `recv()` 超时那一支里。两个后果叠加
  之后：服务器把频道静音、或者 SND 流压根没起来但 MSG 照发的时候，`connected` 永远
  是 True，看门狗一次都轮不到，命令行和 Web 两条路都看不出异常 —— 界面显示"监听中"，
  RMS 恒为 0，守多久都不会有信号。现在改为按 SND 音频帧计时、每轮都检查，并把
  "socket 全静默"和"socket 活着但没有音频"分开报，后者正是之前完全不可见的那一种。
  Web 端的自动重连也因此才真正能对这种情况生效。
  - Files / 影响文件: `src/kiwi_client.py`

- **The CLI status line could be skipped entirely** — `if int(elapsed) % 30 == 0` combined with
  `sleep(1)`: the few extra milliseconds per iteration accumulate until `int(elapsed)` occasionally
  jumps from 29 straight to 31, dropping that status line. It now keys off the timestamp of the
  next report and includes audio frame and drop counts — "RMS pinned at 0 with a frame count that
  is not rising" and "audio present but no signal" are two different things, and the log can now
  tell them apart. The end of a connection also states whether audio was never received at all or
  the link dropped mid-run (the CLI `monitor` did not reconnect at this version).

  **命令行监听的状态行会整条跳过** — `if int(elapsed) % 30 == 0` 配合 `sleep(1)`，
  每轮多走的几毫秒累积起来会让 `int(elapsed)` 偶尔从 29 直接跳到 31，那一次状态行
  就没了。改成按下一次报告的时间戳判断，并在状态行里加上音频帧数和丢帧数——
  RMS 恒为 0 且帧数不涨，和"有音频但没信号"是两回事，日志里现在分得开。
  连接结束时也会说明是从没收到过音频，还是中途断线（该版本的命令行 `monitor`
  还不会自动重连）。
  - Files / 影响文件: `src/receiver.py`

- **`active_hours` had never once been read** — every frequency in `frequencies.yaml` carries an
  active-hours annotation, but no code read it; it was pure comment. "The frequency is right, the
  hour is wrong" (watching the daytime frequency 11175 at 01:00 UTC, say) was therefore completely
  invisible. New `src/schedule.py` parses the active hours, warns at monitor start about
  frequencies currently out of their window, and lists the high-priority frequencies marked active
  at that same moment. It advises; it does not block.

  **`active_hours` 从来没有被读过** — `frequencies.yaml` 里每个频率都标了活跃时段，
  但没有任何代码读它，纯粹是注释。"频率没错、只是时段不对"（比如 01:00 UTC 去守
  11175 这个日间频率）因此完全不可见。新增 `src/schedule.py` 解析活跃时段，监听启动
  时提示当前不在时段内的频率，并列出同一时刻标注为活跃的高优先级频率。只提示，不拦截。
  - New file / 新增文件: `src/schedule.py`
  - Files / 影响文件: `main.py`, `src/receiver.py` (`FrequencyTarget` gains `active_hours` /
    `FrequencyTarget` 增加 `active_hours`)

### 🔧 Tooling / 工具

- **`diagnose_rms.py` rewritten** — this is the tool you should reach for when nothing is being
  received, yet it carried its own copy of the frame parser, frozen at the pre-v1.3.0 layout
  (seq read as 2 bytes big-endian, S-meter taken from `body[3:5]`, audio from `body[5:]`), so the
  S-meter it measured was wrong, the threshold advice it printed was still the long-abandoned 0.65,
  and the node was hard-coded to the Australian one. It now reuses `KiwiSDRClient` directly — a
  diagnostic and the real monitor must share one parser — and supports
  `-f/-m/-d/--node/--all-nodes`, with thresholds read from `config.yaml`. The output states a
  conclusion: no audio frames (a link problem), threshold set too high for this node, or set so
  low it will sit open, plus a suggested value from the measured floor.

  **`diagnose_rms.py` 重写** — 这是"收不到信号"时最该用的工具，但它自己抄了一份帧解析，
  停在 v1.3.0 修复之前的布局（seq 读成 2 字节大端、S-meter 取 `body[3:5]`、音频取
  `body[5:]`），量出来的 S-meter 是错的，打印的阈值建议还停在早就废弃的 0.65，节点也
  硬编码成了澳洲那一个。现在直接复用 `KiwiSDRClient`——体检和实际监听必须共用同一份
  解析——并支持 `-f/-m/-d/--node/--all-nodes`，阈值从 `config.yaml` 读。
  输出直接给结论：没有音频帧（链路问题）／阈值相对本节点定高了／定低了会常开，
  并按实测底噪给出建议值。

### 📝 Notes / 说明

- `config.yaml` now records where the threshold came from: 0.10 was computed by replaying
  EAM.watch recordings (a different receiver, a different level regime) over 1024-point windows,
  whereas the live detector computes RMS per KiwiSDR audio frame (512 points). The two are not
  the same measure, and that value was never calibrated on our own receive chain. With AGC on at
  the node and an absolute threshold, changing node means measuring again.
  `config.yaml` 里补上了阈值的来龙去脉：0.10 是拿 EAM.watch 的录音（另一台接收机、
  另一套电平）按 1024 点窗口回放算出来的，而实时检测器是按每个 KiwiSDR 音频帧
  （512 点）算 RMS，两边口径不一样，这个值并没有在自己的接收链路上校准过。
  节点开着 AGC，阈值是绝对电平，换节点就得重新量。
- `squelch.window_size` is used only by the offline replay in `compare_eam.py`; the live detector
  does not read it. This is now noted in the config.
  `squelch.window_size` 只有 `compare_eam.py` 的离线回放在用，实时检测器不读它，
  已在配置里注明。

## v1.3.0 (2026-08-14)

Fixes the three measurement defects recorded in the
[signal-chain audit](reports/milradio-audit.html), and rebuilds the web monitoring page.

修复[信号链审计报告](reports/milradio-audit.html)记录的三处读数缺陷，并重做 Web 监听页面。

### 🐛 Signal-chain fixes / 信号链修复

- **[Defect 01] SND frame parsing was off by 2 bytes** — `kiwi_client.py` parsed the body of an
  SND frame as `flags(1) + seq(2, big-endian) + smeter(2)`, while the real layout used by the
  official kiwiclient is `flags(1) + seq(4, little-endian) + smeter(2, big-endian)`. Two
  consequences:

  **[缺陷 01] SND 帧解析偏移 2 字节** — `kiwi_client.py` 把 SND 帧的 body 解析成
  `flags(1) + seq(2, 大端) + smeter(2)`，而官方 kiwiclient 的真实布局是
  `flags(1) + seq(4, 小端) + smeter(2, 大端)`。两个后果：
  - The S-meter column was actually recording the high bytes of the frame counter (744 records
    held only 16 distinct values, all multiples of 256, and 676 of them were a constant −160 dBm);
    S-meter 列记录的其实是帧计数器的高位字节（744 条记录只有 16 个取值，全部是 256 的整数倍，676 条恒为 -160 dBm）；
  - Audio started 2 bytes early, **injecting one fake sample per frame**, which produced a
    12000/512 = 23.4375 Hz frame-rate harmonic hum in all 747 recordings.
    音频起点提前 2 字节，**每帧多注入 1 个假样本**，在全部 747 段录音里产生 12000/512 = 23.4375 Hz 的帧率谐波嗡声。

  The dBm conversion was corrected at the same time, from `raw/65535×150−160` to the
  `0.1 × (raw & 0x0FFF) − 127` KiwiSDR actually uses. Frame-type detection now compares the full
  3-byte tag instead of only the first byte (frames such as `STA` are no longer mistaken for
  audio), and dropped frames are counted from seq discontinuities.

  同时修正 dBm 换算：`raw/65535×150−160` 改为 KiwiSDR 实际使用的 `0.1 × (raw & 0x0FFF) − 127`。
  帧类型判断从只比首字节改为比较完整的 3 字节 tag（`'STA'` 之类的帧不会再被当成音频），
  并按 seq 跳变统计丢帧数。
  - Files / 影响文件: `src/kiwi_client.py`

- **[Defect 02] Modulation identification was decided by dictionary order** — the old scorer gave
  bandwidth 2 points, flatness 1 and crest factor 1; but in a fixed 300–3000 Hz passband,
  bandwidth measures the *receiver filter*: the mean bandwidth of three "different" modulations
  differed by only 25 Hz. Replaying all 744 records, 690 (98.4%) produced a three-way tie, and
  the output was ultimately decided by the order in which `MODULATION_PROFILES` happened to be
  written — "93% of signals are USB voice" only because `USB_VOICE` was listed first. Now:

  **[缺陷 02] 调制识别由字典顺序决定** — 旧打分器给带宽 2 分、平坦度 1 分、峰均比 1 分，
  而带宽在固定 300-3000 Hz 通带下测的是接收机的滤波器：三种"不同"调制的平均带宽只差 25 Hz。
  重放全部 744 条记录，690 条（98.4%）出现三向并列，最终由 `MODULATION_PROFILES` 的书写顺序
  决定输出——"93% 的信号是 USB 语音"只是因为 `USB_VOICE` 写在第一个。改为：
  - A new feature set: envelope syllabic rate and modulation depth, keying rate, tone count and
    spacing, tone purity, and occupied bandwidth with the noise floor removed;
    换特征集：包络音节率与调制深度、键控率、音调数与间距、音调纯度、扣除底噪后的占用带宽；
  - Continuous membership weighting instead of integer scoring, plus a veto layer (a 20 Hz-wide
    signal cannot be a wideband data waveform);
    连续隶属度加权代替整数打分，并加一层否决条件（20 Hz 宽的信号不可能是宽带数据波形）；
  - A **confidence** figure is emitted, and ties or thin evidence return `UNKNOWN` instead of the
    first key in the dictionary;
    输出**置信度**，并列或证据不足时返回 `UNKNOWN` 而不是取字典里的第一个键；
  - USB / LSB / AM come from the demodulation mode of the receiver, no longer guessed from the
    demodulated audio;
    USB / LSB / AM 由接收机的解调模式决定，不再从解调后的音频里猜；
  - New `CARRIER` class (unmodulated carrier).
    新增 `CARRIER`（未调制载波）类别。
  - Files / 影响文件: `src/analyzer.py`, `src/receiver.py`, `src/web_server.py`, `main.py`

- **[Defect 03] SNR used the filter stopband as its noise reference** — the old implementation
  took ±10% either side of the peak as signal and everything else as noise; but the spectrum spans
  0–6000 Hz while a signal can only exist in 300–3000 Hz, so the "noise region" was stuffed with a
  wide stretch of filter stopband holding almost no energy, and the denominator was dragged down.
  The symptom: of 744 records, only 6 had an SNR above 10 dB. The noise floor is now estimated
  from a low percentile *within the demodulation passband*, and
  `SNR = signal power with the floor removed / total floor power`. Clear speech now exceeds 20 dB
  and pure noise comes out negative.

  **[缺陷 03] SNR 把滤波器阻带当噪声基准** — 旧实现取峰值两侧 ±10% 当信号、其余当噪声，
  而频谱跨度 0-6000 Hz、信号只可能在 300-3000 Hz，"噪声区"里塞了一大片几乎没有能量的
  滤波器阻带，分母被压低。症状是 744 条记录里只有 6 条 SNR 超过 10 dB。改为在解调通带内
  用低分位数估噪声基底，`SNR = 扣除底噪的信号功率 / 底噪总功率`。清晰语音现在能到 20 dB 以上，
  纯底噪是负值。
  - New file / 新增文件: `src/modes.py` — one shared table for demodulation filters and audio
    passbands, so the two ends cannot drift apart
    解调滤波器与音频通带共用一张表，避免收发两端各写各的
  - Files / 影响文件: `src/analyzer.py`, `src/kiwi_client.py`

- **Bandwidth definition** — `bandwidth_hz` is now the **occupied bandwidth** containing 90% of
  the energy with the noise floor removed (a pure tone converges to tens of Hz, speech to
  1–2.5 kHz). The old "peak −20 dB" definition is retained as `bandwidth_20db_hz` for comparison.

  **带宽口径** — `bandwidth_hz` 改为扣除底噪后包含 90% 能量的**占用带宽**（纯音收敛到几十 Hz，
  语音 1-2.5 kHz）。旧的"峰值 -20 dB"口径保留为 `bandwidth_20db_hz` 供对照。

### ✨ Web monitoring page / Web 监听页面

- **Live spectrum + waterfall** — `analyzer.get_spectrogram()` had been written long ago and never
  once called by the UI. The backend now pushes a 128-bin spectrum column roughly every 170 ms
  (`live_spectrum()`, segment-averaged so a single FFT does not inflate the live SNR) and the
  front end draws the spectrum curve plus a scrolling waterfall, marking the current demodulation
  passband.

  **实时频谱 + 瀑布图** — `analyzer.get_spectrogram()` 早就写好了却从没被界面调用过。
  现在后端每约 170 ms 推一列 128 格频谱（`live_spectrum()`，分段平均避免单次 FFT 让
  实时 SNR 虚高），前端画频谱曲线 + 滚动瀑布图，并标出当前解调通带。
- **Recording browser and playback** — the 747 recordings could previously only be dug out of a
  folder. A new Recordings tab filters by age and SNR, shows duration, in-band SNR, modulation
  plus confidence, occupied bandwidth and S-meter per row, plays audio directly, and expands to a
  spectrogram thumbnail of that recording (dynamic exposure, so weak signals stay visible).

  **录音浏览与回放** — 747 段录音过去只能去文件夹里翻。新增录音标签页：按天数/SNR 筛选，
  每条显示时长、带内 SNR、调制类型+置信度、占用带宽、S-meter，可直接播放，
  也可展开这段录音的频谱图缩略图（动态曝光，弱信号也看得清）。
- **A real S-meter** — only after the frame parser was fixed is the S-meter a real reading; the
  page shows dBm, S-unit and a level bar.
  **真实 S-meter** — 帧解析修好后 S-meter 才是真读数，页面显示 dBm + S 级 + 电平条。
- **In-band SNR / noise floor** — both shown live, with the noise floor and the squelch open/close
  thresholds overlaid on the RMS curve.
  **带内 SNR / 噪声基底** — 实时显示带内 SNR 与噪声基底，RMS 曲线上叠加噪声基底线与
  静噪开/关阈值线。
- **Online squelch adjustment** — sliders change the open/close thresholds and take effect
  immediately on the running detector, with no need to stop and restart monitoring; the server
  continuously tracks a low percentile of RMS as the measured noise floor, and one click applies
  "set from noise floor" (floor +6 dB). This closes out the three manual threshold adjustments
  (0.65 → 0.15 → 0.10) recorded in this changelog.

  **静噪在线调整** — 滑块直接改开/关阈值，立刻作用到正在跑的检测器，不用停下监听重来；
  服务器持续统计 RMS 低分位数作为实测底噪，一键"按底噪设定"（底噪 +6 dB）。
  这是对 CHANGELOG 里 0.65 → 0.15 → 0.10 三次手调阈值的一次性了结。
- **Statistics tab** — frequency activity, modulation distribution (with mean confidence), in-band
  SNR distribution, and a 24-hour activity strip.
  **统计标签页** — 频率活跃度、调制类型分布（带平均置信度）、带内 SNR 分布、24 小时活动热条。
- Frequency shortcuts are grouped by network and mark the frequencies that have produced signals in
  the last 30 days; node status, dropped frames and session statistics are all visible directly on
  the page.
  频率快捷键按网络分组并标出最近 30 天有过信号的频率；节点状态、丢帧数、会话统计等
  都在页面上直接可见。
  - Files / 影响文件: `web/index.html`, `src/web_server.py`, `src/db.py`

### 🔌 New endpoints / 新增接口

| Endpoint / 接口 | Description / 说明 |
|------|------|
| `GET /api/recordings` | Signal + analysis records; filter by days / limit / frequency / min_snr / with_recording<br>信号+分析记录，支持 days / limit / frequency / min_snr / with_recording 筛选 |
| `GET /api/recordings/{id}/audio` | Play back a recording (paths restricted to the configured recordings directory)<br>回放录音（路径限制在配置的录音目录内） |
| `GET /api/recordings/{id}/spectrogram` | Spectrogram matrix and envelope for a recording<br>录音的频谱图矩阵与包络 |
| `GET /api/sessions` | Recent monitoring sessions / 最近的监听会话 |
| `GET` / `POST /api/squelch` | Read/adjust squelch thresholds online, with the measured floor and a suggested value<br>读取/在线调整静噪阈值，附实测底噪与建议值 |
| `GET /api/stats` | Extended: modulation distribution, in-band SNR distribution, activity by day<br>扩展: 调制分布、带内 SNR 分布、按天活动 |

### 🗄️ Database / 数据库

- The `analysis` table gains six columns — `modulation_confidence`, `demod_mode`,
  `noise_floor_db`, `envelope_rate_hz`, `envelope_depth`, `tone_count` — added automatically to
  older databases without losing history.
  `analysis` 表新增 `modulation_confidence`、`demod_mode`、`noise_floor_db`、
  `envelope_rate_hz`、`envelope_depth`、`tone_count` 六列，老数据库自动补列（不丢历史数据）。
- New `analysis(signal_id)` index and `get_signals_with_analysis()` / `get_snr_distribution()` /
  `get_daily_activity()` query methods.
  新增 `analysis(signal_id)` 索引和 `get_signals_with_analysis()` / `get_snr_distribution()` /
  `get_daily_activity()` 查询方法。

### 🧪 Tests / 测试

- 18 → **70 tests** / 18 → **70 个测试**：
  - `test_kiwi_client.py` (+13) — feed in constructed SND frames and assert sample count, seq and
    S-meter, then check directly that the 23.4 Hz frame-rate harmonic no longer appears in audio
    stitched from consecutive frames;
    `test_kiwi_client.py`（新增 13 个）— 喂进构造好的 SND 帧，断言样本数、seq、S-meter，
    并直接检查连续多帧拼接后的音频里不再出现 23.4 Hz 帧率谐波；
  - `test_analyzer.py` (+16) — in-band SNR, the stopband taking no part in the calculation, the
    passband following the mode, classification of each modulation type, bandwidth actually
    discriminating, and ties emitting UNKNOWN;
    `test_analyzer.py`（新增 16 个）— 带内 SNR、阻带不参与计算、通带随模式变化、
    各调制类型分类、带宽有区分度、并列输出 UNKNOWN；
  - `test_web_api.py` (+19) — recording browse/playback/spectrogram, path-traversal protection,
    squelch adjustment validation;
    `test_web_api.py`（新增 19 个）— 录音浏览/回放/频谱图、路径穿越防护、静噪调整校验；
  - `test_db.py` (+4) — automatic column migration, signals joined with analysis, SNR
    distribution, activity by day.
    `test_db.py`（新增 4 个）— 自动补列迁移、带分析的信号查询、SNR 分布、按天活动。

---

## v1.2.0 (2026-06-08)

### ✨ Improvements / 改进

- **A custom exception hierarchy** — new `src/exceptions.py` defining `MilRadioError`,
  `ConfigError`, `ConnectionError`, `HandshakeRejected`, `NodeUnavailable`, `AnalysisError` and
  friends. `load_config()` and `load_frequencies()` in `main.py` now raise `ConfigError` instead
  of calling `sys.exit(1)`.
  **自定义异常类型体系** — 新增 `src/exceptions.py`，定义 `MilRadioError`、`ConfigError`、
  `ConnectionError`、`HandshakeRejected`、`NodeUnavailable`、`AnalysisError` 等异常类型。
  `main.py` 中 `load_config()` 和 `load_frequencies()` 已改用 `ConfigError` 代替 `sys.exit(1)`。
  - New file / 新增文件: `src/exceptions.py`
  - Files / 影响文件: `main.py`

- **Unified configuration defaults** — the hand-rolled config extraction in `receiver.py` and
  `web_server.py` was replaced with the `SquelchDetector.from_config()`,
  `AudioRecorder.from_config()` and `SignalAnalyzer.from_config()` factory methods, removing six
  places where defaults could disagree.
  **统一配置默认值** — `receiver.py` 和 `web_server.py` 中手动提取配置的代码替换为
  `SquelchDetector.from_config()`、`AudioRecorder.from_config()`、`SignalAnalyzer.from_config()`
  工厂方法，消除了 6 处配置默认值不一致的风险。
  - Files / 影响文件: `src/receiver.py`, `src/web_server.py`

- **Split `reporter.py`** — the 1100+ line single file became the `src/reporter/` package:
  **拆分 reporter.py** — 将 1100+ 行的单文件拆分为 `src/reporter/` 包：
  - `theme.py` — colour scheme and font configuration (~85 lines) / 配色方案和字体配置 (~85 行)
  - `charts.py` — the ChartMixin class with 10 chart generators (~490 lines) / 10 个图表生成方法的 ChartMixin 类 (~490 行)
  - `reporter.py` — the Reporter class, HTML/text reports, scan results (~310 lines) / Reporter 主类、HTML/文本报告、扫描结果 (~310 行)
  - `__init__.py` — re-exports Reporter so `from src.reporter import Reporter` keeps working
    重新导出 Reporter，保持 `from src.reporter import Reporter` 兼容
  - Deleted / 删除文件: `src/reporter.py` (the old single file / 旧单文件)

- **Windows event-loop compatibility** — `main.py` sets `WindowsSelectorEventLoopPolicy`
  automatically on Windows, avoiding the `ProactorEventLoop` incompatibilities with
  aiohttp/websockets.
  **Windows 事件循环兼容** — `main.py` 在 Windows 平台自动设置 `WindowsSelectorEventLoopPolicy`，
  避免 `ProactorEventLoop` 与 aiohttp/websockets 的兼容性问题。
  - Files / 影响文件: `main.py`

- **HTML report version corrected** — the report footer moved from v1.0 to v1.1.
  **HTML 报告版本号修正** — 报告页脚版本号从 v1.0 更新为 v1.1。

### 🧪 Tests / 测试

- **New unit-test suite** — a `tests/` directory with 18 cases:
  **新增单元测试套件** — 添加 `tests/` 目录，包含 18 个测试用例：
  - `test_squelch.py` — squelch state machine, deque buffer, pre-roll, factory method (6 tests)
    静噪状态机、deque 缓冲区、pre-roll、工厂方法 (6 tests)
  - `test_db.py` — session CRUD, signal records, analysis storage, frequency statistics, exception
    guarding (6 tests)
    会话 CRUD、信号记录、分析保存、频率统计、异常保护 (6 tests)
  - `test_analyzer.py` — pure-tone analysis, noise detection, the analyze_and_save flow, empty
    buffer safety (6 tests)
    纯音分析、噪声检测、analyze_and_save 流程、空 buffer 安全 (6 tests)
  - `conftest.py` — pytest path configuration / pytest 路径配置

- **Removed duplicated analysis code** — `receiver.py` and `web_server.py` held three nearly
  identical "analyse signal + save to database" blocks (~15 lines each), all now routed through
  `SignalAnalyzer.analyze_and_save()`, so future changes to the analysis logic happen in one place.
  **消除分析代码重复** — `receiver.py` 和 `web_server.py` 中有 3 处几乎相同的"分析信号 + 保存到数据库"代码（各约 15 行），
  统一改为调用 `SignalAnalyzer.analyze_and_save()` 方法。确保后续修改分析逻辑时只需改一处。
  - Files / 影响文件: `src/receiver.py`, `src/web_server.py`

- **Fixed a deprecated API** — `asyncio.get_event_loop()` in `web_server.py` is deprecated on
  Python 3.10+ and unreliable in certain contexts. The loop reference is now captured with
  `asyncio.get_running_loop()` when `_run_monitor()` starts, and used safely from synchronous
  callbacks.
  **修复废弃 API** — `web_server.py` 中的 `asyncio.get_event_loop()` 在 Python 3.10+ 已废弃且在特定上下文中不可靠。
  改为在 `_run_monitor()` 启动时通过 `asyncio.get_running_loop()` 保存事件循环引用，在同步回调中安全使用。
  - Files / 影响文件: `src/web_server.py`

- **Fixed a pre-roll buffer performance issue** — the squelch pre-roll buffer trimmed with
  `list.pop(0)` (O(n)); it now uses `deque.popleft()` (O(1)), cutting needless CPU on long runs.
  **修复 pre-roll 缓冲区性能问题** — 静噪检测器的 pre-roll 缓冲区修剪使用 `list.pop(0)` (O(n))，
  改为 `deque.popleft()` (O(1))。在长时间监听场景下可减少不必要的 CPU 开销。
  - Files / 影响文件: `src/squelch.py`

- **Database close is now exception-guarded** — `Database.close()` gained try/except so that an
  exception while closing inside a `finally` block (a failed WAL checkpoint, say) cannot mask the
  real error.
  **数据库关闭异常保护** — `Database.close()` 新增 try/except 保护，防止在 `finally` 块中关闭数据库时异常
  （如 WAL checkpoint 失败）掩盖真正的业务异常。
  - Files / 影响文件: `src/db.py`

### ✨ Improvements / 改进

- **Recording segmentation callback** — `AudioRecorder` gained an optional `on_segment_complete`
  parameter, invoked when a recording is split at `max_duration`, so the caller can persist the
  segment metadata instead of losing it.
  **录音自动分段回调** — `AudioRecorder` 新增可选的 `on_segment_complete` 回调参数。
  当录音达到 `max_duration` 自动分段时，会调用此回调通知上层保存分段信息，避免分段录音元数据丢失。
  - Files / 影响文件: `src/recorder.py`
  - Note: backward compatible; existing code works unchanged.
    注意: 此为向后兼容的改动，现有代码无需修改即可正常工作。

---

## v1.0.0 (2026-06-01)

- Initial release / 初始版本发布
- KiwiSDR WebSocket client / KiwiSDR WebSocket 客户端
- Signal monitoring and recording / 信号监听与录音
- FFT spectrum analysis and modulation identification / FFT 频谱分析与调制识别
- SQLite persistence / SQLite 数据持久化
- Live web monitoring interface / Web 实时监听界面
- HTML analysis report generation / HTML 分析报告生成
- Recording cleanup tool / 录音清理工具
