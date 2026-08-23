# Early diagnosis and fix log / KiwiSDR 信号监听系统 - 问题诊断与修复记录

> **Historical record from the early days of the project** (previously the file named
> `底噪bug修复`). The threshold values quoted here have long been superseded: v1.3.2 replaced the
> fixed threshold with an adaptive one, and v1.4.0 made the S-meter criterion the default. See
> [CHANGELOG.md](CHANGELOG.md) for the full story, and [USAGE.md §6.4](USAGE.md#64-online-squelch-calibration-no-need-to-stop-monitoring--静噪在线标定不用停下监听)
> for how squelch is calibrated today.
>
> **这是项目早期的记录**（原文件名 `底噪bug修复`）。这里的阈值数字早就被取代了：
> v1.3.2 把固定阈值换成了自适应，v1.4.0 把 S-meter 判据设为默认。
> 完整经过见 [CHANGELOG.md](CHANGELOG.md)，现在静噪怎么标定见
> [USAGE.md §6.4](USAGE.md#64-online-squelch-calibration-no-need-to-stop-monitoring--静噪在线标定不用停下监听)。
>
> **Bilingual document: English first, Chinese second. / 本文为中英双语，英文在前、中文在后。**

## Summary / 问题总结

The system monitored for hours without detecting a single signal. Working through the stack layer
by layer turned up two classes of problem: **protocol parsing** and **threshold configuration**.

系统监听数小时未检测到任何信号。经过逐层排查，发现了 **协议解析** 和 **阈值配置** 两大类问题。

---

## Problems fixed / 修复的问题

### 1. Wrong frame parsing format (kiwi_client.py) / 帧解析格式错误

Compared against the official [jks-prv/kiwiclient](https://github.com/jks-prv/kiwiclient)
implementation:

对照官方 [jks-prv/kiwiclient](https://github.com/jks-prv/kiwiclient) 的实现：

```python
# Official kiwiclient (client.py, lines 220-223) / 官方 kiwiclient (client.py 第220-223行)
def _process_ws_message(self, message):
    tag = bytearray2str(message[0:3])  # First 3 bytes are the tag: 'SND' or 'MSG'
                                       # 前3字节是 tag: 'SND' 或 'MSG'
    body = message[3:]                 # The body starts at byte 4 / 第4字节起是 body
    self._process_message(tag, body)
```

| Item / 问题 | Old code / 原来的代码 | Correct format / 正确格式 |
|------|-----------|---------|
| Frame tag / 帧标识 | `data[0]` = 'S' (1 byte / 1字节) | `data[0:3]` = 'SND' (3 bytes / 3字节) |
| flags | none / 无 | `body[0]` = flags |
| Sequence number / 序列号 | `data[1:3]` | `body[1:3]` |
| S-meter | `data[3:5]` | `body[3:5]` |
| Audio PCM / 音频PCM | `data[5:]` or `data[9:]` | `body[5:]` = `data[8:]` |

> This was corrected again in v1.3.0: the real layout is
> `flags(1) + seq(4, little-endian) + smeter(2, big-endian)`, which is why the frame was still off
> by 2 bytes after this round of fixes. See
> [USAGE.md §13.1](USAGE.md#131-defect-01-snd-frame-parsing-off-by-2-bytes--缺陷-01snd-帧解析偏移-2-字节).
>
> 这一处在 v1.3.0 又改了一次：真实布局是
> `flags(1) + seq(4, 小端) + smeter(2, 大端)`，所以这轮修完之后帧仍然偏移 2 字节。见
> [USAGE.md §13.1](USAGE.md#131-defect-01-snd-frame-parsing-off-by-2-bytes--缺陷-01snd-帧解析偏移-2-字节)。

### 2. Incompatible sample-rate parameter / 采样率参数不兼容

```diff
- SET AR OK in=12000 out=44100    # Many nodes do not support 44100 / 许多节点不支持 44100
+ SET AR OK in=12000 out=12000    # The native KiwiSDR sample rate / KiwiSDR 原生采样率
```

### 3. Handshake messages are all binary / 握手消息全是二进制

Every KiwiSDR message (including `MSG client_public_ip` and friends) is sent as **binary**, not as
a text string. The `isinstance(msg, str)` branch in the old code could therefore never execute.

KiwiSDR 所有消息（包括 `MSG client_public_ip` 等）都是以 **二进制** 发送的，不是文本字符串。
原代码的 `isinstance(msg, str)` 分支永远不会被执行。

### 4. False positives in the rejection check / 拒绝检查误报

`_REJECT_KEYWORDS` used substring matching (`"admin" in msg`), and the `load_cfg` configuration
JSON (18 KB) contains fields such as `"admin_password"` — so every node was misjudged as "server
refused the connection".

`_REJECT_KEYWORDS` 使用子串匹配 (`"admin" in msg`)，而 `load_cfg` 配置 JSON (18KB) 中包含
`"admin_password"` 等字段，导致所有节点都被误判为"服务器拒绝连接"。

```diff
- _REJECT_KEYWORDS = ["too_busy", "redirect", "down", "locked", "admin", ...]
- if keyword in msg_text:  # Substring match -> false positive! / 子串匹配 -> 误报!
+ _REJECT_MESSAGES = ["MSG too_busy", "MSG redirect", "MSG down", "MSG locked", ...]
+ if msg_text.startswith(reject_msg):  # Exact prefix match / 精确前缀匹配
```

### 5. Wrong squelch threshold (config.yaml) — **the fatal one** / 静噪阈值错误 — **最致命的问题**

> [!CAUTION]
> This is the direct reason for hours of monitoring without a single signal!
> 这是监听几小时没有信号的直接原因！

Measured RMS data (11175 kHz USB, 15 seconds, 333 frames):
实测 RMS 数据 (11175 kHz USB, 15秒, 333帧):

| Metric / 指标 | Value / 值 |
|------|-----|
| Mean noise-floor RMS / 底噪 RMS 均值 | **0.072** |
| Maximum noise-floor RMS / 底噪 RMS 最大值 | **0.104** |
| Configured open_threshold / 配置的 open_threshold | **0.65** ← 9× the noise floor! / 是底噪的 9 倍！ |
| Frames above the threshold / 高于阈值的帧数 | **0/333 (0%)** |

```diff
  squelch:
-   open_threshold: 0.65     # Can never trigger / 永远不会触发
-   close_threshold: 0.61
+   open_threshold: 0.15     # 2x the noise floor, detects real voice signals
+                            # 底噪的 2 倍，能检测到真正的语音信号
+   close_threshold: 0.13
```

> This number was hand-tuned twice more afterwards (0.15 → 0.10), and the 93-hour experiment of
> v1.4.0 finally showed why: all three adjustments were really chasing the AGC setting of the
> far-end node, not converging on a correct threshold. The default criterion today is the S-meter
> noise floor +14 dB, which calibrates itself.
>
> 这个数字后面又手调了两次（0.15 → 0.10），v1.4.0 那次 93 小时实验才说明白原因：
> 三次调的其实都是对面节点的 AGC，不是在逼近某个正确阈值。现在的默认判据是
> S-meter 底噪 +14 dB，它自己会标定。

---

## Open-source references / 开源参考资料

| Project / 项目 | Description / 说明 | Link / 链接 |
|------|------|------|
| **kiwiclient** | The official KiwiSDR Python client (kiwirecorder)<br>官方 KiwiSDR Python 客户端 (kiwirecorder) | [github.com/jks-prv/kiwiclient](https://github.com/jks-prv/kiwiclient) |
| `client.py` | WebSocket protocol, frame parsing, ADPCM decoding<br>WebSocket 协议实现、帧解析、ADPCM 解码 | [client.py](https://github.com/jks-prv/kiwiclient/blob/master/kiwi/client.py) |
| `kiwirecorder.py` | A complete recording/monitoring example / 完整的录音/监听示例 | [kiwirecorder.py](https://github.com/jks-prv/kiwiclient/blob/master/kiwirecorder.py) |
| KiwiSDR source / KiwiSDR 源码 | The server-side implementation / 服务器端实现 | [github.com/jks-prv/Beagle_SDR_GPS](https://github.com/jks-prv/Beagle_SDR_GPS) |

---

## Files changed / 修改的文件

- [src/kiwi_client.py](src/kiwi_client.py) — frame parsing, handshake, rejection check
  帧解析、握手、拒绝检查
- [config.yaml](config.yaml) — squelch thresholds / 静噪阈值
