"""
charts.py - 图表生成方法集合

通过 ChartMixin 混入类提供所有图表生成方法，
被 Reporter 类继承使用。
"""

import os
import logging
from datetime import datetime
from typing import List, Dict, Optional

import numpy as np

from .theme import HAS_MATPLOTLIB

if HAS_MATPLOTLIB:
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.gridspec import GridSpec

logger = logging.getLogger(__name__)


class ChartMixin:
    """
    图表生成混入类。

    提供所有图表生成方法，使用 self.colors / self.output_dir /
    self.chart_dpi 等属性（由 Reporter.__init__ 初始化）。
    """

    def _apply_theme(self, fig, ax):
        """应用图表主题。"""
        c = self.colors
        fig.patch.set_facecolor(c["bg"])
        ax.set_facecolor(c["surface"])
        ax.tick_params(colors=c["text_dim"], labelsize=9)
        ax.xaxis.label.set_color(c["text"])
        ax.yaxis.label.set_color(c["text"])
        ax.title.set_color(c["text"])
        for spine in ax.spines.values():
            spine.set_color(c["grid"])
        ax.grid(True, color=c["grid"], alpha=0.5, linewidth=0.5)

    def _chart_frequency_activity(self, freq_stats: List[Dict]) -> Optional[str]:
        """生成频率活跃度柱状图。"""
        fig = None
        try:
            c = self.colors
            fig, ax = plt.subplots(figsize=(12, 6))
            self._apply_theme(fig, ax)

            freqs = [f"{s['frequency_khz']:.1f}" for s in freq_stats[:15]]
            counts = [s["signal_count"] for s in freq_stats[:15]]
            durations = [s.get("total_duration", 0) or 0 for s in freq_stats[:15]]

            x = np.arange(len(freqs))
            width = 0.35

            bars1 = ax.bar(x - width/2, counts, width,
                          label='信号数量', color=c["accent1"], alpha=0.85,
                          edgecolor=c["bg"], linewidth=0.5)
            ax2 = ax.twinx()
            bars2 = ax2.bar(x + width/2, durations, width,
                           label='总时长 (秒)', color=c["accent2"], alpha=0.85,
                           edgecolor=c["bg"], linewidth=0.5)

            ax.set_xlabel('频率 (kHz)', fontsize=11)
            ax.set_ylabel('信号数量', fontsize=11, color=c["accent1"])
            ax2.set_ylabel('总时长 (秒)', fontsize=11, color=c["accent2"])
            ax2.tick_params(colors=c["text_dim"])
            ax2.spines['right'].set_color(c["grid"])

            ax.set_title('频率活跃度排名', fontsize=14, fontweight='bold', pad=15)
            ax.set_xticks(x)
            ax.set_xticklabels(freqs, rotation=45, ha='right')

            # 合并图例
            lines1, labels1 = ax.get_legend_handles_labels()
            lines2, labels2 = ax2.get_legend_handles_labels()
            ax.legend(lines1 + lines2, labels1 + labels2,
                     loc='upper right', facecolor=c["surface"],
                     edgecolor=c["grid"], labelcolor=c["text"])

            plt.tight_layout()
            path = os.path.join(self.output_dir, "chart_freq_activity.png")
            fig.savefig(path, dpi=self.chart_dpi, facecolor=c["bg"])
            return path

        except Exception as e:
            logger.error(f"生成频率活跃度图表失败: {e}")
            return None
        finally:
            if fig:
                plt.close(fig)

    def _chart_hourly_heatmap(self, hourly: List[Dict]) -> Optional[str]:
        """生成 24 小时活动热力图。"""
        fig = None
        try:
            c = self.colors
            fig, ax = plt.subplots(figsize=(12, 4))
            self._apply_theme(fig, ax)

            hours = list(range(24))
            hour_data = {int(h["hour"]): h["signal_count"] for h in hourly}
            counts = [hour_data.get(h, 0) for h in hours]

            # 渐变色条形图
            max_count = max(counts) if max(counts) > 0 else 1
            colors_list = []
            for cnt in counts:
                intensity = cnt / max_count
                r = int(9 + intensity * 79)  # 0x09 -> 0x58
                g = int(105 + intensity * 61)  # 0x69 -> 0xa6
                b = int(218 + intensity * 37)  # 0xda -> 0xff
                colors_list.append(f"#{r:02x}{g:02x}{b:02x}")

            bars = ax.bar(hours, counts, color=colors_list, alpha=0.9,
                         edgecolor=c["bg"], linewidth=0.5)

            ax.set_xlabel('UTC 小时', fontsize=11)
            ax.set_ylabel('信号数量', fontsize=11)
            ax.set_title('24 小时信号活动分布 (UTC)', fontsize=14,
                        fontweight='bold', pad=15)
            ax.set_xticks(hours)
            ax.set_xticklabels([f"{h:02d}" for h in hours])

            plt.tight_layout()
            path = os.path.join(self.output_dir, "chart_hourly.png")
            fig.savefig(path, dpi=self.chart_dpi, facecolor=c["bg"])
            return path

        except Exception as e:
            logger.error(f"生成小时活动图表失败: {e}")
            return None
        finally:
            if fig:
                plt.close(fig)

    def _chart_signal_strength(self, signals: List[Dict]) -> Optional[str]:
        """生成信号强度分布直方图。"""
        fig = None
        try:
            c = self.colors
            fig, ax = plt.subplots(figsize=(10, 5))
            self._apply_theme(fig, ax)

            rms_values = [s["peak_rms"] for s in signals if s.get("peak_rms")]
            if not rms_values:
                return None

            ax.hist(rms_values, bins=30, color=c["accent5"], alpha=0.85,
                   edgecolor=c["bg"], linewidth=0.5)

            ax.set_xlabel('峰值 RMS', fontsize=11)
            ax.set_ylabel('信号数量', fontsize=11)
            ax.set_title('信号强度分布', fontsize=14, fontweight='bold', pad=15)

            # 标注平均值
            mean_rms = np.mean(rms_values)
            ax.axvline(mean_rms, color=c["accent4"], linestyle='--',
                      linewidth=1.5, label=f'平均值: {mean_rms:.4f}')
            ax.legend(facecolor=c["surface"], edgecolor=c["grid"],
                     labelcolor=c["text"])

            plt.tight_layout()
            path = os.path.join(self.output_dir, "chart_signal_strength.png")
            fig.savefig(path, dpi=self.chart_dpi, facecolor=c["bg"])
            return path

        except Exception as e:
            logger.error(f"生成信号强度图表失败: {e}")
            return None
        finally:
            if fig:
                plt.close(fig)

    def _chart_signal_timeline(self, signals: List[Dict]) -> Optional[str]:
        """生成信号时间线散点图。"""
        fig = None
        try:
            c = self.colors
            fig, ax = plt.subplots(figsize=(14, 6))
            self._apply_theme(fig, ax)

            times = []
            freqs = []
            sizes = []
            colors_list = []

            for s in signals:
                try:
                    t = datetime.fromisoformat(s["timestamp"])
                    times.append(t)
                    freqs.append(s["frequency_khz"])
                    dur = s.get("duration_seconds", 1) or 1
                    sizes.append(min(dur * 5, 200))  # 气泡大小代表持续时间
                    rms = s.get("peak_rms", 0) or 0
                    colors_list.append(min(rms * 20, 1.0))  # 颜色深浅代表强度
                except (ValueError, KeyError):
                    continue

            if not times:
                return None

            scatter = ax.scatter(times, freqs, s=sizes, c=colors_list,
                               cmap='cool', alpha=0.7, edgecolors=c["grid"],
                               linewidth=0.3)

            ax.set_xlabel('时间 (UTC)', fontsize=11)
            ax.set_ylabel('频率 (kHz)', fontsize=11)
            ax.set_title('信号检测时间线', fontsize=14, fontweight='bold', pad=15)

            # 格式化时间轴
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
            plt.xticks(rotation=30, ha='right')

            # 颜色条
            cbar = fig.colorbar(scatter, ax=ax, pad=0.02)
            cbar.set_label('信号强度', color=c["text"], fontsize=10)
            cbar.ax.tick_params(colors=c["text_dim"])

            plt.tight_layout()
            path = os.path.join(self.output_dir, "chart_timeline.png")
            fig.savefig(path, dpi=self.chart_dpi, facecolor=c["bg"])
            return path

        except Exception as e:
            logger.error(f"生成时间线图表失败: {e}")
            return None
        finally:
            if fig:
                plt.close(fig)

    def _chart_modulation_distribution(self, modulation_stats: List[Dict]) -> Optional[str]:
        """生成调制类型分布饼图。"""
        fig = None
        try:
            c = self.colors
            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
            fig.patch.set_facecolor(c["bg"])

            # --- 左: 饼图 ---
            labels = [s.get("estimated_modulation", "UNKNOWN") or "UNKNOWN"
                      for s in modulation_stats]
            counts = [s["count"] for s in modulation_stats]
            pie_colors = [c["accent1"], c["accent2"], c["accent3"],
                         c["accent4"], c["accent5"], c["accent6"]]
            # 循环颜色
            pie_colors = (pie_colors * ((len(labels) // len(pie_colors)) + 1))[:len(labels)]

            wedges, texts, autotexts = ax1.pie(
                counts, labels=labels, autopct='%1.1f%%',
                colors=pie_colors, startangle=90,
                textprops={'color': c["text"], 'fontsize': 10},
                wedgeprops={'edgecolor': c["bg"], 'linewidth': 2},
                pctdistance=0.75
            )
            for t in autotexts:
                t.set_fontsize(9)
                t.set_color(c["text"])

            ax1.set_title('调制类型分布', fontsize=14, fontweight='bold',
                         color=c["text"], pad=15)

            # --- 右: 条形图 (含 SNR 对比) ---
            ax2.set_facecolor(c["surface"])
            for spine in ax2.spines.values():
                spine.set_color(c["grid"])
            ax2.tick_params(colors=c["text_dim"], labelsize=9)

            x = np.arange(len(labels))
            snr_values = [s.get("avg_snr", 0) or 0 for s in modulation_stats]
            bw_values = [s.get("avg_bandwidth", 0) or 0 for s in modulation_stats]

            bars = ax2.bar(x, snr_values, color=pie_colors, alpha=0.85,
                          edgecolor=c["bg"], linewidth=0.5)
            ax2.set_xlabel('调制类型', fontsize=11, color=c["text"])
            ax2.set_ylabel('平均 SNR (dB)', fontsize=11, color=c["text"])
            ax2.set_title('各调制类型平均信噪比', fontsize=14, fontweight='bold',
                         color=c["text"], pad=15)
            ax2.set_xticks(x)
            ax2.set_xticklabels(labels, rotation=30, ha='right')
            ax2.grid(True, color=c["grid"], alpha=0.5, linewidth=0.5)

            # 在条形上方标注数值
            for bar, val in zip(bars, snr_values):
                if val > 0:
                    ax2.text(bar.get_x() + bar.get_width() / 2., bar.get_height() + 0.3,
                            f'{val:.1f}', ha='center', va='bottom',
                            color=c["text_dim"], fontsize=9)

            plt.tight_layout()
            path = os.path.join(self.output_dir, "chart_modulation.png")
            fig.savefig(path, dpi=self.chart_dpi, facecolor=c["bg"])
            return path

        except Exception as e:
            logger.error(f"生成调制类型图表失败: {e}")
            return None
        finally:
            if fig:
                plt.close(fig)

    def _chart_snr_bandwidth_analysis(self, modulation_stats: List[Dict]) -> Optional[str]:
        """生成 SNR 与带宽关联散点图。"""
        fig = None
        try:
            c = self.colors
            fig, ax = plt.subplots(figsize=(10, 6))
            self._apply_theme(fig, ax)

            colors_map = {
                "USB_VOICE": c["accent1"], "AM_VOICE": c["accent2"],
                "CW": c["accent3"], "FSK": c["accent4"],
                "PSK": c["accent5"], "NOISE": c["accent6"],
            }

            for s in modulation_stats:
                mod = s.get("estimated_modulation", "UNKNOWN") or "UNKNOWN"
                snr = s.get("avg_snr", 0) or 0
                bw = s.get("avg_bandwidth", 0) or 0
                count = s.get("count", 1)
                color = colors_map.get(mod, c["text_dim"])

                ax.scatter(bw, snr, s=max(count * 15, 50), c=color, alpha=0.8,
                          edgecolors='white', linewidth=0.5, label=mod, zorder=5)

                # 标注调制类型
                ax.annotate(f'{mod}\n(n={count})', (bw, snr),
                           textcoords="offset points", xytext=(10, 5),
                           fontsize=8, color=c["text_dim"],
                           arrowprops=dict(arrowstyle='->', color=c["text_dim"],
                                         lw=0.5))

            ax.set_xlabel('平均带宽 (Hz)', fontsize=11)
            ax.set_ylabel('平均 SNR (dB)', fontsize=11)
            ax.set_title('SNR 与带宽关联分析', fontsize=14, fontweight='bold', pad=15)

            # 去重图例
            handles, labels = ax.get_legend_handles_labels()
            by_label = dict(zip(labels, handles))
            if by_label:
                ax.legend(by_label.values(), by_label.keys(),
                         loc='upper right', facecolor=c["surface"],
                         edgecolor=c["grid"], labelcolor=c["text"],
                         fontsize=9)

            plt.tight_layout()
            path = os.path.join(self.output_dir, "chart_snr_bandwidth.png")
            fig.savefig(path, dpi=self.chart_dpi, facecolor=c["bg"])
            return path

        except Exception as e:
            logger.error(f"生成 SNR 带宽分析图表失败: {e}")
            return None
        finally:
            if fig:
                plt.close(fig)

    def _chart_duration_distribution(self, signals: List[Dict]) -> Optional[str]:
        """生成信号时长分布直方图 (含累计分布)。"""
        fig = None
        try:
            c = self.colors
            fig, ax = plt.subplots(figsize=(10, 5))
            self._apply_theme(fig, ax)

            durations = [s.get("duration_seconds", 0) or 0 for s in signals]
            durations = [d for d in durations if d > 0]

            if not durations:
                return None

            # 直方图
            n, bins, patches = ax.hist(durations, bins=25, color=c["accent6"],
                                       alpha=0.85, edgecolor=c["bg"], linewidth=0.5)

            # 累计分布 (CDF) 叠加
            ax2 = ax.twinx()
            sorted_dur = np.sort(durations)
            cdf = np.arange(1, len(sorted_dur) + 1) / len(sorted_dur) * 100
            ax2.plot(sorted_dur, cdf, color=c["accent4"], linewidth=2,
                    linestyle='-', alpha=0.9, label='累计百分比')
            ax2.set_ylabel('累计百分比 (%)', fontsize=11, color=c["accent4"])
            ax2.tick_params(colors=c["text_dim"])
            ax2.spines['right'].set_color(c["grid"])
            ax2.set_ylim(0, 105)

            ax.set_xlabel('信号时长 (秒)', fontsize=11)
            ax.set_ylabel('信号数量', fontsize=11)
            ax.set_title('信号时长分布', fontsize=14, fontweight='bold', pad=15)

            # 统计标注
            mean_dur = np.mean(durations)
            median_dur = np.median(durations)
            ax.axvline(mean_dur, color=c["accent3"], linestyle='--', linewidth=1.5,
                      label=f'平均值: {mean_dur:.1f}s')
            ax.axvline(median_dur, color=c["accent5"], linestyle=':', linewidth=1.5,
                      label=f'中位数: {median_dur:.1f}s')

            # 合并图例
            lines1, labels1 = ax.get_legend_handles_labels()
            lines2, labels2 = ax2.get_legend_handles_labels()
            ax.legend(lines1 + lines2, labels1 + labels2,
                     loc='upper right', facecolor=c["surface"],
                     edgecolor=c["grid"], labelcolor=c["text"], fontsize=9)

            plt.tight_layout()
            path = os.path.join(self.output_dir, "chart_duration.png")
            fig.savefig(path, dpi=self.chart_dpi, facecolor=c["bg"])
            return path

        except Exception as e:
            logger.error(f"生成时长分布图表失败: {e}")
            return None
        finally:
            if fig:
                plt.close(fig)

    def _chart_network_activity(self, signals: List[Dict]) -> Optional[str]:
        """生成网络/频段活动对比水平条形图。"""
        fig = None
        try:
            c = self.colors
            fig, ax = plt.subplots(figsize=(10, 6))
            self._apply_theme(fig, ax)

            # 按网络分组统计
            network_data = {}
            for s in signals:
                net = s.get("network", "") or "未分类"
                if net not in network_data:
                    network_data[net] = {"count": 0, "total_dur": 0, "rms_sum": 0}
                network_data[net]["count"] += 1
                network_data[net]["total_dur"] += (s.get("duration_seconds", 0) or 0)
                network_data[net]["rms_sum"] += (s.get("peak_rms", 0) or 0)

            if not network_data:
                return None

            # 按信号数排序
            sorted_nets = sorted(network_data.items(), key=lambda x: x[1]["count"],
                                reverse=True)[:12]

            labels = [n[0].upper() for n in sorted_nets]
            counts = [n[1]["count"] for n in sorted_nets]
            avg_dur = [n[1]["total_dur"] / max(n[1]["count"], 1) for n in sorted_nets]

            y = np.arange(len(labels))
            height = 0.35

            bars1 = ax.barh(y - height/2, counts, height,
                           label='信号数量', color=c["accent1"], alpha=0.85,
                           edgecolor=c["bg"], linewidth=0.5)
            ax2 = ax.twiny()
            bars2 = ax2.barh(y + height/2, avg_dur, height,
                            label='平均时长 (秒)', color=c["accent3"], alpha=0.85,
                            edgecolor=c["bg"], linewidth=0.5)

            ax.set_ylabel('网络/频段', fontsize=11)
            ax.set_xlabel('信号数量', fontsize=11, color=c["accent1"])
            ax2.set_xlabel('平均时长 (秒)', fontsize=11, color=c["accent3"])
            ax.set_title('网络/频段活动对比', fontsize=14, fontweight='bold', pad=25)
            ax.set_yticks(y)
            ax.set_yticklabels(labels)
            ax2.tick_params(colors=c["text_dim"])

            # 数值标注
            for bar, val in zip(bars1, counts):
                ax.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height()/2.,
                       str(val), ha='left', va='center',
                       color=c["text_dim"], fontsize=9)

            # 合并图例
            lines1, labels1 = ax.get_legend_handles_labels()
            lines2, labels2 = ax2.get_legend_handles_labels()
            ax.legend(lines1 + lines2, labels1 + labels2,
                     loc='lower right', facecolor=c["surface"],
                     edgecolor=c["grid"], labelcolor=c["text"], fontsize=9)

            plt.tight_layout()
            path = os.path.join(self.output_dir, "chart_network.png")
            fig.savefig(path, dpi=self.chart_dpi, facecolor=c["bg"])
            return path

        except Exception as e:
            logger.error(f"生成网络活动图表失败: {e}")
            return None
        finally:
            if fig:
                plt.close(fig)

    def _chart_dashboard_overview(self, signals: List[Dict],
                                  freq_stats: List[Dict],
                                  hourly: List[Dict],
                                  modulation_stats: List[Dict]) -> Optional[str]:
        """生成综合仪表盘概览图 (2×2 多面板)。"""
        fig = None
        try:
            c = self.colors
            fig = plt.figure(figsize=(16, 10))
            fig.patch.set_facecolor(c["bg"])
            gs = GridSpec(2, 2, figure=fig, hspace=0.35, wspace=0.3)

            fig.suptitle('信号监测综合分析概览', fontsize=18,
                        fontweight='bold', color=c["text"], y=0.98)

            # ---- 左上: Top 5 频率活跃度 ----
            ax1 = fig.add_subplot(gs[0, 0])
            ax1.set_facecolor(c["surface"])
            for spine in ax1.spines.values():
                spine.set_color(c["grid"])
            ax1.tick_params(colors=c["text_dim"], labelsize=8)

            top_freqs = freq_stats[:5]
            if top_freqs:
                freq_labels = [f"{s['frequency_khz']:.0f}" for s in top_freqs]
                freq_counts = [s["signal_count"] for s in top_freqs]
                bars = ax1.bar(freq_labels, freq_counts,
                              color=c["gradient"][:len(freq_labels)],
                              alpha=0.9, edgecolor=c["bg"])
                for bar, val in zip(bars, freq_counts):
                    ax1.text(bar.get_x() + bar.get_width()/2., bar.get_height() + 0.2,
                            str(val), ha='center', va='bottom',
                            color=c["text_dim"], fontsize=9, fontweight='bold')
            ax1.set_title('Top 5 活跃频率', fontsize=12,
                         fontweight='bold', color=c["text"], pad=10)
            ax1.set_xlabel('频率 (kHz)', fontsize=9, color=c["text_dim"])
            ax1.set_ylabel('信号数', fontsize=9, color=c["text_dim"])
            ax1.grid(True, color=c["grid"], alpha=0.3, linewidth=0.5)

            # ---- 右上: 24h 活动热力条 ----
            ax2 = fig.add_subplot(gs[0, 1])
            ax2.set_facecolor(c["surface"])
            for spine in ax2.spines.values():
                spine.set_color(c["grid"])
            ax2.tick_params(colors=c["text_dim"], labelsize=8)

            hours = list(range(24))
            hour_data = {}
            if hourly:
                hour_data = {int(h["hour"]): h["signal_count"] for h in hourly}
            hour_counts = [hour_data.get(h, 0) for h in hours]
            max_count = max(hour_counts) if any(hour_counts) else 1

            bar_colors = []
            for cnt in hour_counts:
                ratio = cnt / max_count if max_count > 0 else 0
                # 蓝色渐变
                r = int(9 + ratio * 79)
                g = int(105 + ratio * 61)
                b = int(218 + ratio * 37)
                bar_colors.append(f"#{r:02x}{g:02x}{b:02x}")

            ax2.bar(hours, hour_counts, color=bar_colors, alpha=0.9,
                   edgecolor=c["bg"], linewidth=0.3, width=0.8)
            ax2.set_title('24 小时活动热力图', fontsize=12,
                         fontweight='bold', color=c["text"], pad=10)
            ax2.set_xlabel('UTC 小时', fontsize=9, color=c["text_dim"])
            ax2.set_ylabel('信号数', fontsize=9, color=c["text_dim"])
            ax2.set_xticks(range(0, 24, 2))
            ax2.grid(True, color=c["grid"], alpha=0.3, linewidth=0.5)

            # ---- 左下: 信号强度箱线图 (按频率分组) ----
            ax3 = fig.add_subplot(gs[1, 0])
            ax3.set_facecolor(c["surface"])
            for spine in ax3.spines.values():
                spine.set_color(c["grid"])
            ax3.tick_params(colors=c["text_dim"], labelsize=8)

            # 按频率分组 RMS 数据
            freq_rms = {}
            for s in signals:
                fk = f"{s['frequency_khz']:.0f}"
                rms_val = s.get("peak_rms")
                if rms_val:
                    freq_rms.setdefault(fk, []).append(rms_val)

            if freq_rms:
                top_freq_keys = sorted(freq_rms.keys(),
                                       key=lambda k: len(freq_rms[k]),
                                       reverse=True)[:6]
                box_data = [freq_rms[k] for k in top_freq_keys]
                bp = ax3.boxplot(box_data, patch_artist=True,
                                showfliers=True, flierprops=dict(marker='o',
                                markerfacecolor=c["accent4"], markersize=3))
                ax3.set_xticklabels(top_freq_keys)
                for patch, color in zip(bp['boxes'],
                                       [c["accent1"], c["accent2"], c["accent3"],
                                        c["accent5"], c["accent6"], c["accent4"]]):
                    patch.set_facecolor(color)
                    patch.set_alpha(0.6)
                for element in ['whiskers', 'caps', 'medians']:
                    for line in bp[element]:
                        line.set_color(c["text_dim"])

            ax3.set_title('信号强度分布 (按频率)', fontsize=12,
                         fontweight='bold', color=c["text"], pad=10)
            ax3.set_xlabel('频率 (kHz)', fontsize=9, color=c["text_dim"])
            ax3.set_ylabel('峰值 RMS', fontsize=9, color=c["text_dim"])
            ax3.grid(True, color=c["grid"], alpha=0.3, linewidth=0.5, axis='y')

            # ---- 右下: 调制类型环形图 ----
            ax4 = fig.add_subplot(gs[1, 1])
            ax4.set_facecolor(c["bg"])

            if modulation_stats:
                mod_labels = [s.get("estimated_modulation", "?") or "?"
                             for s in modulation_stats]
                mod_counts = [s["count"] for s in modulation_stats]
                mod_colors = [c["accent1"], c["accent2"], c["accent3"],
                             c["accent4"], c["accent5"], c["accent6"]]
                mod_colors = (mod_colors * 3)[:len(mod_labels)]

                wedges, texts, autotexts = ax4.pie(
                    mod_counts, labels=mod_labels, autopct='%1.0f%%',
                    colors=mod_colors, startangle=140,
                    textprops={'color': c["text"], 'fontsize': 9},
                    wedgeprops={'edgecolor': c["bg"], 'linewidth': 2, 'width': 0.5},
                    pctdistance=0.75
                )
                for t in autotexts:
                    t.set_fontsize(8)
                    t.set_color(c["text"])

                # 中心文字
                total = sum(mod_counts)
                ax4.text(0, 0, f'{total}\n信号总数', ha='center', va='center',
                        fontsize=14, fontweight='bold', color=c["text"])

            ax4.set_title('调制类型构成', fontsize=12,
                         fontweight='bold', color=c["text"], pad=10)

            path = os.path.join(self.output_dir, "chart_dashboard.png")
            fig.savefig(path, dpi=self.chart_dpi, facecolor=c["bg"],
                       bbox_inches='tight')
            return path

        except Exception as e:
            logger.error(f"生成综合仪表盘失败: {e}")
            return None
        finally:
            if fig:
                plt.close(fig)
