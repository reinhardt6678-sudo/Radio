"""
squelch.py - 静噪检测 / 信号活动检测 (VOX)

通过分析音频流的 RMS 能量来检测信号活动，
在信号出现时触发录制，在信号消失后延迟关闭。

三种取阈方式:
  smeter    阈值 = 实测 S-meter 底噪 + N dB (默认)。S-meter 是节点在音频
            AGC **之前** 测的射频电平，因此不受 AGC 压缩影响。93 小时
            实测里它是唯一在 AGC 开和关两种状态下都能用的判据。
  adaptive  阈值 = 实测音频底噪 + N dB。只有在 AGC 关闭时才有意义 ——
            AGC 开着时有信号/没信号的音频 RMS 只差 1.6 dB (中位数)。
  absolute  固定 RMS 阈值 (最老的行为)，需要人工按节点校准。

无论用哪种方式，检测器都持续统计底噪，并且带两层保护:
  - 关闭阈值低于底噪时告警 (这种配置下静噪打开后永远关不掉)
  - 静噪连续打开超过 max_open_seconds 时强制收尾，避免一直挂着不出记录
"""

import numpy as np
import logging
import time
from typing import Optional, Callable, List, Tuple
from collections import deque

logger = logging.getLogger(__name__)

MODE_ABSOLUTE = "absolute"
MODE_ADAPTIVE = "adaptive"
MODE_SMETER = "smeter"
VALID_MODES = (MODE_ABSOLUTE, MODE_ADAPTIVE, MODE_SMETER)


def db_to_ratio(db: float) -> float:
    """dB 差值转幅度比 (RMS 是幅度量, 用 20log10)。"""
    return float(10.0 ** (db / 20.0))


class SquelchDetector:
    """
    信号活动检测器（静噪/VOX）。

    使用 RMS 能量阈值检测信号活动，具有以下特性：
    - 开/关阈值分离（滞后），避免在阈值附近频繁切换
    - 可配置的尾部延迟（tail_time），防止截断信号末尾
    - pre-roll 缓冲区，保留信号开始前的音频
    - 滚动底噪统计，支持"底噪 + N dB"的自适应阈值
    - 卡死保护：静噪打开过久时强制收尾并告警
    """

    # 底噪重算间隔 (秒)，不必每块都重算百分位数
    FLOOR_RECALC_INTERVAL = 0.5

    def __init__(self, open_threshold: float = 0.02,
                 close_threshold: float = 0.015,
                 tail_time: float = 3.0,
                 pre_roll_seconds: float = 2.0,
                 sample_rate: int = 12000,
                 window_size: int = 1024,
                 mode: str = MODE_ABSOLUTE,
                 open_margin_db: float = 6.0,
                 close_margin_db: float = 3.0,
                 min_open_threshold: float = 0.005,
                 floor_window_seconds: float = 600.0,
                 floor_percentile: float = 10.0,
                 floor_min_blocks: int = 50,
                 max_open_seconds: float = 300.0,
                 smeter_open_margin_db: float = 14.0,
                 smeter_close_margin_db: float = 10.0,
                 smeter_floor_window_seconds: float = 600.0,
                 smeter_floor_percentile: float = 10.0,
                 smeter_floor_min_samples: int = 50,
                 dead_audio_window_seconds: float = 30.0,
                 dead_audio_min_blocks: int = 50,
                 dead_audio_rms_spread: float = 1e-6):
        """
        初始化静噪检测器。

        Args:
            open_threshold: 静噪打开阈值 (RMS, 0-1)，mode=absolute 时使用
            close_threshold: 静噪关闭阈值 (RMS, 通常 < open_threshold)
            tail_time: 信号消失后继续录制的秒数
            pre_roll_seconds: 保留信号开始前的音频秒数
            sample_rate: 音频采样率
            window_size: RMS 计算窗口大小（采样点数）
            mode: absolute (固定阈值) 或 adaptive (底噪 + N dB)
            open_margin_db: adaptive 模式下打开阈值高于底噪多少 dB
            close_margin_db: adaptive 模式下关闭阈值高于底噪多少 dB
            min_open_threshold: adaptive 模式下打开阈值的绝对下限，
                                防止链路完全静音时把数字静音当成信号
            floor_window_seconds: 底噪统计窗口长度 (秒)
            floor_percentile: 底噪取窗口内第几百分位
            floor_min_blocks: 至少积累多少块才给出底噪
            max_open_seconds: 静噪最长连续打开时间 (秒)，0 = 不限制
            smeter_open_margin_db: smeter 模式下打开阈值高于 S-meter 底噪多少 dB
            smeter_close_margin_db: smeter 模式下关闭阈值高于 S-meter 底噪多少 dB
            smeter_floor_window_seconds: S-meter 底噪统计窗口 (秒)
            smeter_floor_percentile: S-meter 底噪取窗口内第几百分位
            smeter_floor_min_samples: 至少积累多少个 S-meter 样本才给出底噪
            dead_audio_window_seconds: rolling window used to judge audio liveness (seconds)
                                       判定音频活性的滚动窗口 (秒)
            dead_audio_min_blocks: minimum blocks before the liveness verdict is made at all
                                   至少积累多少块才给出活性判定
            dead_audio_rms_spread: peak-to-peak RMS spread at or below which the link counts
                                   as muted (real audio always jitters by far more)
                                   RMS 极差小于等于这个值就判为链路哑音
                                   (真实音频的抖动远大于此)
        """
        self.open_threshold = open_threshold
        self.close_threshold = close_threshold
        self.tail_time = tail_time
        self.pre_roll_seconds = pre_roll_seconds
        self.sample_rate = sample_rate
        self.window_size = window_size

        self.mode = mode if mode in VALID_MODES else MODE_ABSOLUTE
        self.open_margin_db = open_margin_db
        self.close_margin_db = close_margin_db
        self.min_open_threshold = min_open_threshold
        self.floor_window_seconds = floor_window_seconds
        self.floor_percentile = floor_percentile
        self.floor_min_blocks = floor_min_blocks
        self.max_open_seconds = max_open_seconds

        self.smeter_open_margin_db = smeter_open_margin_db
        self.smeter_close_margin_db = smeter_close_margin_db
        self.smeter_floor_window_seconds = smeter_floor_window_seconds
        self.smeter_floor_percentile = smeter_floor_percentile
        self.smeter_floor_min_samples = smeter_floor_min_samples

        self.dead_audio_window_seconds = dead_audio_window_seconds
        self.dead_audio_min_blocks = dead_audio_min_blocks
        self.dead_audio_rms_spread = dead_audio_rms_spread

        # 状态
        self.is_open = False           # 静噪是否打开（有信号）
        self._last_above_time = 0.0    # 上次高于阈值的时间
        self._signal_start_time = 0.0  # 当前信号开始时间

        # RMS 历史记录
        self._rms_history: deque = deque(maxlen=100)
        self._current_rms = 0.0
        self._peak_rms = 0.0

        # 滚动底噪统计 (timestamp, rms)。
        # 注意: 打开和关闭状态都要统计。只在关闭时统计的话，一旦阈值低于
        # 底噪，静噪会一直开着 —— 底噪就被冻结在监听刚起步那一瞬间的值上，
        # 之后"按底噪设定"只会照着这个死值把阈值调得更低。
        self._floor_samples: deque = deque()
        self._noise_floor = 0.0
        self._last_floor_calc = 0.0

        # 滚动 S-meter 底噪 (timestamp, dBm)。S-meter 在音频 AGC 之前测量，
        # 所以它反映的是真实射频电平，AGC 开不开都成立。
        self._smeter_samples: deque = deque()
        self._smeter_floor: Optional[float] = None
        self._last_smeter_calc = 0.0
        self._current_smeter: Optional[float] = None

        # Audio liveness window (timestamp, rms). Deliberately separate from _rms_history,
        # which _close() clears on every signal -- liveness has to be judged across the whole
        # connection, not reset each time the squelch closes.
        #
        # 音频活性窗口 (timestamp, rms)。刻意不复用 _rms_history ——
        # 那个每出一条信号就被 _close() 清空，而活性要跨整条连接判断，
        # 不能每次静噪关闭就归零。
        self._audio_samples: deque = deque()
        self._audio_dead = False
        self._audio_dead_warned = False
        self._audio_alive_confirmed = False

        # 卡死保护
        self._forced_closes = 0
        self._stuck_open = False
        self._below_floor_warned = False

        # Pre-roll 块级缓冲区 (存储完整音频块而非逐样本)
        self._pre_roll_max_samples = int(pre_roll_seconds * sample_rate)
        self._pre_roll_blocks: deque = deque()
        self._pre_roll_total_samples = 0

        # 回调
        self._on_open: Optional[Callable] = None   # 信号开始
        self._on_close: Optional[Callable] = None  # 信号结束
        self._on_audio: Optional[Callable] = None   # 活跃期间的音频

    @classmethod
    def from_config(cls, config: dict) -> "SquelchDetector":
        """
        从配置字典创建静噪检测器。

        Args:
            config: 完整配置字典 (包含 squelch 和 recording/receiver 部分)
        """
        sq_cfg = config.get("squelch", {})
        rec_cfg = config.get("recording", {})
        recv_cfg = config.get("receiver", {})

        # 没显式配就按录音分段推导，并且要比录音器早一点收尾。
        # 录音器是按"已写入样本数"分段的，pre-roll 先占掉了一段;
        # 让静噪先收尾，一个 WAV 才刚好对应一条信号记录 ——
        # 反过来的话录音器会自己切一刀，那个文件不会有任何数据库记录。
        max_open = sq_cfg.get("max_open_seconds")
        if max_open is None:
            max_duration = float(rec_cfg.get("max_duration", 300.0))
            pre_roll = float(rec_cfg.get("pre_roll", 2.0))
            max_open = max(30.0, max_duration - pre_roll - 5.0)

        return cls(
            open_threshold=sq_cfg.get("open_threshold", 0.02),
            close_threshold=sq_cfg.get("close_threshold", 0.015),
            tail_time=sq_cfg.get("tail_time", 3.0),
            pre_roll_seconds=rec_cfg.get("pre_roll", 2.0),
            sample_rate=recv_cfg.get("sample_rate", 12000),
            mode=sq_cfg.get("mode", MODE_SMETER),
            open_margin_db=sq_cfg.get("open_margin_db", 6.0),
            close_margin_db=sq_cfg.get("close_margin_db", 3.0),
            min_open_threshold=sq_cfg.get("min_open_threshold", 0.005),
            floor_window_seconds=sq_cfg.get("floor_window_seconds", 600.0),
            floor_percentile=sq_cfg.get("floor_percentile", 10.0),
            max_open_seconds=max_open,
            smeter_open_margin_db=sq_cfg.get("smeter_open_margin_db", 14.0),
            smeter_close_margin_db=sq_cfg.get("smeter_close_margin_db", 10.0),
            dead_audio_window_seconds=sq_cfg.get(
                "dead_audio_window_seconds", 30.0),
            dead_audio_min_blocks=sq_cfg.get("dead_audio_min_blocks", 50),
            dead_audio_rms_spread=sq_cfg.get("dead_audio_rms_spread", 1e-6),
            smeter_floor_window_seconds=sq_cfg.get(
                "smeter_floor_window_seconds", 600.0),
            smeter_floor_percentile=sq_cfg.get("smeter_floor_percentile", 10.0),
        )

    def set_callbacks(self,
                      on_open: Callable = None,
                      on_close: Callable = None,
                      on_audio: Callable = None):
        """
        设置事件回调。

        Args:
            on_open: 信号开始时调用 (pre_roll_samples, signal_start_time)
            on_close: 信号结束时调用 (duration, peak_rms, avg_rms)
            on_audio: 信号活跃期间每收到音频块时调用 (samples)
        """
        self._on_open = on_open
        self._on_close = on_close
        self._on_audio = on_audio

    def process(self, samples: np.ndarray,
                smeter: Optional[float] = None) -> bool:
        """
        处理一块音频样本，更新静噪状态。

        Args:
            samples: float32 音频样本数组 (-1.0 到 1.0)
            smeter: 该帧的 S-meter 读数 (dBm)。mode=smeter 时必须传，
                    不传则该帧不参与判信号 (但音频照常进 pre-roll)。

        Returns:
            当前是否有信号活动
        """
        now = time.time()

        # 计算 RMS
        rms = self._compute_rms(samples)
        self._current_rms = rms
        self._rms_history.append(rms)
        self._update_noise_floor(now, rms)
        self._update_audio_liveness(now, rms)

        if smeter is not None:
            self._current_smeter = float(smeter)
            self._update_smeter_floor(now, float(smeter))

        open_threshold = self.effective_open_threshold
        close_threshold = self.effective_close_threshold

        # 判据分派: smeter 模式看射频电平，其余看音频 RMS
        if self.mode == MODE_SMETER:
            level = self._current_smeter
            if level is None or self._smeter_floor is None:
                above_open = above_close = False
            else:
                above_open = level >= self.effective_smeter_open
                above_close = level >= self.effective_smeter_close
        else:
            above_open = rms >= open_threshold
            above_close = rms >= close_threshold

        if not self.is_open:
            # 当前静噪关闭状态 - 保存到 pre-roll 缓冲区
            self._pre_roll_blocks.append(samples.copy())
            self._pre_roll_total_samples += len(samples)
            # 修剪超出上限的旧块
            while (self._pre_roll_total_samples > self._pre_roll_max_samples
                   and len(self._pre_roll_blocks) > 1):
                removed = self._pre_roll_blocks.popleft()
                self._pre_roll_total_samples -= len(removed)

            # 检查是否应该打开静噪。
            # adaptive 模式下底噪还没测出来之前不判信号: 这时候退回到一个
            # 没在本节点校准过的绝对阈值，正是这套系统最容易出问题的地方。
            # pre-roll 一直在攒，等底噪出来照样能把开头补回去。
            # 链路哑音时不许开静噪: smeter 模式只看射频电平，节点不出声也照样过阈值,
            # 放行的话录下来的是一串全零样本，还会被当成信号写进库。
            # Never open on a muted link: in smeter mode the criterion is RF level alone, so a
            # silent node still crosses the threshold -- letting it through records all-zero
            # samples and files them in the database as if they were signals.
            if not self.warming_up and not self._audio_dead and above_open:
                self.is_open = True
                self._signal_start_time = now
                self._last_above_time = now
                self._peak_rms = rms

                if self.mode == MODE_SMETER:
                    logger.info(
                        f"[SIGNAL-ON] S-meter={self._current_smeter:.1f} dBm "
                        f"(threshold={self.effective_smeter_open:.1f} dBm, "
                        f"底噪={self._smeter_floor:.1f} dBm, RMS={rms:.4f})")
                else:
                    logger.info(
                        f"[SIGNAL-ON] RMS={rms:.4f} (threshold={open_threshold:.4f})")

                # 回调: 信号开始，传递 pre-roll 数据
                if self._on_open:
                    if self._pre_roll_blocks:
                        pre_roll_data = np.concatenate(self._pre_roll_blocks)
                    else:
                        pre_roll_data = np.array([], dtype=np.float32)
                    self._on_open(pre_roll_data, self._signal_start_time)

                # 当前块也作为活跃音频
                if self._on_audio:
                    self._on_audio(samples)
        else:
            # 当前静噪打开状态 - 有信号
            if above_close:
                self._last_above_time = now
                self._peak_rms = max(self._peak_rms, rms)

            # 传递活跃音频
            if self._on_audio:
                self._on_audio(samples)

            # 检查是否应该关闭静噪 (信号消失 + tail_time 已过)
            closed = False
            if not above_close:
                if (now - self._last_above_time) >= self.tail_time:
                    self._close(now, reason="signal-end")
                    closed = True

            # 卡死保护: 电平一直压在关闭阈值之上时，上面那条永远不成立。
            # 到点强制收尾，信号才会真正落库 —— 否则录制一直挂着，
            # 界面上的信号数会永远停在 0。
            if (not closed and self.max_open_seconds > 0
                    and (now - self._signal_start_time) >= self.max_open_seconds):
                self._close(now, reason="max-open")

        return self.is_open

    def force_close(self, reason: str = "external") -> bool:
        """
        外部强制结束当前信号 (断线、停止监听时调用)。

        不这么做的话，断线瞬间正在录的那段信号会被直接丢掉:
        录音文件留在磁盘上，但没有任何数据库记录。

        Returns:
            是否确实关闭了一段正在进行的信号
        """
        if not self.is_open:
            return False
        self._close(time.time(), reason=reason)
        return True

    def _close(self, now: float, reason: str):
        """结束当前信号，触发 on_close 回调并复位状态。"""
        duration = now - self._signal_start_time
        avg_rms = (float(np.mean(list(self._rms_history)))
                   if self._rms_history else 0.0)
        peak_rms = self._peak_rms

        self.is_open = False

        if reason == "max-open":
            self._forced_closes += 1
            # 到点强制收尾时电平还压在关闭阈值上 = 阈值低于当前底噪，
            # 静噪不是"收到一段长信号"，而是根本关不掉
            if self.mode == MODE_SMETER:
                self._stuck_open = (
                    self._current_smeter is not None
                    and self._smeter_floor is not None
                    and self._current_smeter >= self.effective_smeter_close)
            else:
                self._stuck_open = self._current_rms >= self.effective_close_threshold
            if self._stuck_open:
                if self.mode == MODE_SMETER:
                    logger.warning(
                        f"[SQUELCH-STUCK] 静噪已连续打开 {duration:.0f}s 且 S-meter "
                        f"{self._current_smeter:.1f} dBm 始终不低于关闭阈值 "
                        f"{self.effective_smeter_close:.1f} dBm (实测底噪 "
                        f"{self._smeter_floor:.1f} dBm) —— 强制收尾。"
                        f"多半是这个节点底噪一直在涨，或 smeter_close_margin_db 定得太低"
                    )
                else:
                    logger.warning(
                        f"[SQUELCH-STUCK] 静噪已连续打开 {duration:.0f}s 且 RMS "
                        f"{self._current_rms:.4f} 始终不低于关闭阈值 "
                        f"{self.effective_close_threshold:.4f} (实测底噪 "
                        f"{self._noise_floor:.4f}) —— 阈值低于底噪，强制收尾。"
                        f"请把阈值调到底噪之上，或改用 mode: smeter"
                    )
            else:
                logger.info(
                    f"[SIGNAL-SPLIT] 达到最长连续时间 {self.max_open_seconds:.0f}s，"
                    f"当前段收尾 (duration={duration:.1f}s)"
                )
        else:
            self._stuck_open = False
            logger.info(
                f"[SIGNAL-OFF] duration={duration:.1f}s, "
                f"peak_RMS={peak_rms:.4f}, "
                f"avg_RMS={avg_rms:.4f}, reason={reason}"
            )

        # 回调: 信号结束
        if self._on_close:
            self._on_close(duration, peak_rms, avg_rms)

        # 重置
        self._peak_rms = 0.0
        self._rms_history.clear()
        self._pre_roll_blocks.clear()
        self._pre_roll_total_samples = 0

    def _update_noise_floor(self, now: float, rms: float):
        """更新滚动底噪 (窗口内的低百分位数)。"""
        self._floor_samples.append((now, rms))
        cutoff = now - self.floor_window_seconds
        while self._floor_samples and self._floor_samples[0][0] < cutoff:
            self._floor_samples.popleft()

        if len(self._floor_samples) < self.floor_min_blocks:
            return
        if (now - self._last_floor_calc) < self.FLOOR_RECALC_INTERVAL:
            return

        self._last_floor_calc = now
        self._noise_floor = float(np.percentile(
            [value for _, value in self._floor_samples], self.floor_percentile))
        self._warn_if_below_floor()

    def _update_audio_liveness(self, now: float, rms: float):
        """
        Track whether the node is actually delivering audio.

        A node can complete the handshake, stream frames and report a healthy S-meter while
        every audio sample is the same constant -- the link is muted. Real audio always
        carries thermal noise, so its per-block RMS never stays bit-exact across a whole
        window; a peak-to-peak spread of zero is therefore conclusive, not a heuristic.

        判断节点是不是真的在送音频。

        节点可能握手正常、帧照发、S-meter 也健康，但每个音频采样都是同一个常数 ——
        链路是哑的。真实音频总带着热噪声，逐块 RMS 不可能整个窗口纹丝不动，
        所以"极差为零"是确凿判据，不是启发式猜测。
        """
        # dead_audio_min_blocks <= 0 关闭整个判定。留这个开关是因为判据本身
        # 会把"恒定音频"一律当成哑音，而离线回放、合成信号的测试夹具正是恒定的。
        # Setting dead_audio_min_blocks <= 0 disables the guard entirely. The escape hatch
        # exists because the criterion treats any constant audio as muted, and offline
        # replay or synthetic test fixtures are exactly that.
        if self.dead_audio_min_blocks <= 0:
            return

        self._audio_samples.append((now, rms))
        cutoff = now - self.dead_audio_window_seconds
        while self._audio_samples and self._audio_samples[0][0] < cutoff:
            self._audio_samples.popleft()

        # 样本不够就不下判断: 刚连上那几百毫秒本来就可能全是同一个值
        # Too few samples to judge: the first few hundred ms after connecting can
        # legitimately read the same value throughout.
        if len(self._audio_samples) < self.dead_audio_min_blocks:
            return

        values = [v for _, v in self._audio_samples]
        spread = max(values) - min(values)
        dead = spread <= self.dead_audio_rms_spread

        if dead and not self._audio_dead:
            self._audio_dead = True
            if not self._audio_dead_warned:
                self._audio_dead_warned = True
                logger.warning(
                    f"链路哑音: {len(values)} 块音频的 RMS 恒为 {values[-1]:.6f} "
                    f"(极差 {spread:.2e} <= {self.dead_audio_rms_spread:.2e})。"
                    f"节点在发帧但没有声音，静噪不再打开 / "
                    f"Muted link: RMS constant at {values[-1]:.6f} across {len(values)} "
                    f"blocks; node sends frames but no audio, squelch will not open"
                )
        elif not dead:
            self._audio_dead = False
            self._audio_alive_confirmed = True

    def _update_smeter_floor(self, now: float, smeter: float):
        """更新滚动 S-meter 底噪 (窗口内的低百分位)。"""
        self._smeter_samples.append((now, smeter))
        cutoff = now - self.smeter_floor_window_seconds
        while self._smeter_samples and self._smeter_samples[0][0] < cutoff:
            self._smeter_samples.popleft()

        if len(self._smeter_samples) < self.smeter_floor_min_samples:
            return
        if (now - self._last_smeter_calc) < self.FLOOR_RECALC_INTERVAL:
            return
        self._last_smeter_calc = now
        self._smeter_floor = float(np.percentile(
            [v for _, v in self._smeter_samples], self.smeter_floor_percentile))

    def _warn_if_below_floor(self):
        """关闭阈值低于底噪时告警 (这种配置下静噪打开后就关不掉了)。"""
        if self.mode != MODE_ABSOLUTE or self._noise_floor <= 0:
            return
        if self.close_threshold <= self._noise_floor:
            if not self._below_floor_warned:
                logger.warning(
                    f"关闭阈值 {self.close_threshold:.4f} 不高于实测底噪 "
                    f"{self._noise_floor:.4f}: 静噪一旦打开就再也关不掉，"
                    f"信号记录会一直是 0 条。建议 open>="
                    f"{self._noise_floor * db_to_ratio(self.open_margin_db):.4f}, "
                    f"close>={self._noise_floor * db_to_ratio(self.close_margin_db):.4f}"
                )
                self._below_floor_warned = True
        else:
            self._below_floor_warned = False

    def _compute_rms(self, samples: np.ndarray) -> float:
        """计算 RMS (Root Mean Square) 能量。"""
        if len(samples) == 0:
            return 0.0
        return float(np.sqrt(np.mean(samples ** 2)))

    @property
    def current_rms(self) -> float:
        """当前 RMS 能量值。"""
        return self._current_rms

    @property
    def noise_floor(self) -> float:
        """实测底噪 (窗口内低百分位 RMS)，还没测够时为 0。"""
        return self._noise_floor

    @property
    def floor_sample_count(self) -> int:
        """底噪统计窗口内的样本块数。"""
        return len(self._floor_samples)

    @property
    def smeter_floor(self) -> Optional[float]:
        """实测 S-meter 底噪 (dBm)，样本还不够时为 None。"""
        return self._smeter_floor

    @property
    def current_smeter(self) -> Optional[float]:
        """最近一帧的 S-meter 读数 (dBm)。"""
        return self._current_smeter

    @property
    def effective_smeter_open(self) -> Optional[float]:
        """smeter 模式下实际生效的打开阈值 (dBm)。"""
        if self._smeter_floor is None:
            return None
        return self._smeter_floor + self.smeter_open_margin_db

    @property
    def effective_smeter_close(self) -> Optional[float]:
        """smeter 模式下实际生效的关闭阈值 (dBm)，始终低于打开阈值。"""
        if self._smeter_floor is None:
            return None
        return self._smeter_floor + min(self.smeter_close_margin_db,
                                        self.smeter_open_margin_db - 0.5)

    @property
    def audio_dead(self) -> bool:
        """
        Node is sending frames but no audio (constant RMS across the whole window).
        节点在发帧但没有音频 (整个窗口内 RMS 恒定)。
        """
        return self._audio_dead

    @property
    def audio_alive_confirmed(self) -> bool:
        """
        The link has been seen delivering genuinely varying audio at least once.
        这条链路至少有一次被确认送出了真正有起伏的音频。
        """
        return self._audio_alive_confirmed

    @property
    def warming_up(self) -> bool:
        """底噪还没测出来，暂时不判信号 (pre-roll 照常攒着)。"""
        if self.mode == MODE_ADAPTIVE:
            return self._noise_floor <= 0
        if self.mode == MODE_SMETER:
            return self._smeter_floor is None
        return False

    @property
    def effective_open_threshold(self) -> float:
        """当前实际生效的打开阈值。"""
        if self.mode == MODE_ADAPTIVE and self._noise_floor > 0:
            return max(self._noise_floor * db_to_ratio(self.open_margin_db),
                       self.min_open_threshold)
        return self.open_threshold

    @property
    def effective_close_threshold(self) -> float:
        """当前实际生效的关闭阈值 (始终低于打开阈值，保留滞后)。"""
        if self.mode == MODE_ADAPTIVE and self._noise_floor > 0:
            value = max(self._noise_floor * db_to_ratio(self.close_margin_db),
                        self.min_open_threshold * 0.85)
            return min(value, self.effective_open_threshold * 0.95)
        return self.close_threshold

    @property
    def threshold_below_floor(self) -> bool:
        """关闭阈值是否已经低到底噪上 (静噪会关不掉)。"""
        if self.mode == MODE_SMETER:
            return self.smeter_close_margin_db <= 0
        return (self._noise_floor > 0
                and self.effective_close_threshold <= self._noise_floor)

    @property
    def stuck_open(self) -> bool:
        """上一次强制收尾是否是因为静噪关不掉。"""
        return self._stuck_open

    @property
    def forced_closes(self) -> int:
        """因超过最长打开时间而强制收尾的次数。"""
        return self._forced_closes

    @property
    def signal_duration(self) -> float:
        """当前信号已持续时间（秒），如果无信号则返回 0。"""
        if self.is_open:
            return time.time() - self._signal_start_time
        return 0.0

    def suggested_thresholds(self) -> Tuple[float, float]:
        """按实测底噪给出建议的 (open, close) 阈值，底噪未知时返回 (0, 0)。"""
        if self._noise_floor <= 0:
            return (0.0, 0.0)
        return (
            round(self._noise_floor * db_to_ratio(self.open_margin_db), 5),
            round(self._noise_floor * db_to_ratio(self.close_margin_db), 5),
        )

    def get_status(self) -> dict:
        """获取检测器当前状态。"""
        return {
            "is_open": self.is_open,
            "current_rms": self._current_rms,
            "peak_rms": self._peak_rms,
            "signal_duration": self.signal_duration,
            "mode": self.mode,
            "open_threshold": self.open_threshold,
            "close_threshold": self.close_threshold,
            "effective_open": self.effective_open_threshold,
            "effective_close": self.effective_close_threshold,
            "noise_floor": self._noise_floor,
            "floor_samples": self.floor_sample_count,
            "smeter_dbm": self._current_smeter,
            "smeter_floor": self._smeter_floor,
            "smeter_open": self.effective_smeter_open,
            "smeter_close": self.effective_smeter_close,
            "smeter_open_margin_db": self.smeter_open_margin_db,
            "smeter_close_margin_db": self.smeter_close_margin_db,
            "smeter_samples": len(self._smeter_samples),
            "warming_up": self.warming_up,
            "audio_dead": self._audio_dead,
            "audio_alive_confirmed": self._audio_alive_confirmed,
            "audio_samples": len(self._audio_samples),
            "threshold_below_floor": self.threshold_below_floor,
            "stuck_open": self._stuck_open,
            "forced_closes": self._forced_closes,
        }
