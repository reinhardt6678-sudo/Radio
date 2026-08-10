"""
reporter.py - 分析报告生成器主模块

从数据库读取信号记录和分析结果，生成可视化图表和 HTML 报告。
"""

import os
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional

from .theme import DARK_COLORS, LIGHT_COLORS, HAS_MATPLOTLIB, configure_chinese_font
from .charts import ChartMixin
from ..db import Database

logger = logging.getLogger(__name__)

# 模块加载时自动配置字体
configure_chinese_font()


class Reporter(ChartMixin):
    """分析报告生成器。"""

    def __init__(self, db: Database, config: dict):
        """
        初始化报告生成器。

        Args:
            db: 数据库实例
            config: 报告相关配置
        """
        self.db = db
        report_cfg = config.get("report", {})
        self.output_dir = report_cfg.get("output_dir", "reports")
        self.recent_days = report_cfg.get("recent_days", 7)
        self.chart_dpi = report_cfg.get("chart_dpi", 150)
        self.theme = report_cfg.get("chart_theme", "dark")
        self.colors = DARK_COLORS if self.theme == "dark" else LIGHT_COLORS

        os.makedirs(self.output_dir, exist_ok=True)

    def generate_full_report(self) -> str:
        """
        生成完整分析报告。

        Returns:
            HTML 报告文件路径
        """
        if not HAS_MATPLOTLIB:
            logger.error("matplotlib 未安装，无法生成图表")
            return self._generate_text_report()

        logger.info(f"正在生成分析报告 (最近 {self.recent_days} 天)...")

        # 获取数据
        signals = self.db.get_all_signals(days=self.recent_days)
        freq_stats = self.db.get_frequency_stats(days=self.recent_days)
        hourly = self.db.get_hourly_activity(days=self.recent_days)
        sessions = self.db.get_recent_sessions(limit=50)

        # 获取分析数据
        modulation_stats = self.db.get_modulation_stats(days=self.recent_days)

        chart_paths = []

        # ===== 基础图表 =====
        if freq_stats:
            path = self._chart_frequency_activity(freq_stats)
            if path:
                chart_paths.append(("频率活跃度排名", path))

        if hourly:
            path = self._chart_hourly_heatmap(hourly)
            if path:
                chart_paths.append(("24 小时活动分布", path))

        if signals:
            path = self._chart_signal_strength(signals)
            if path:
                chart_paths.append(("信号强度分布", path))

            path = self._chart_signal_timeline(signals)
            if path:
                chart_paths.append(("信号时间线", path))

        # ===== 新增: 综合分析图表 =====

        # 调制类型分布饼图
        if modulation_stats:
            path = self._chart_modulation_distribution(modulation_stats)
            if path:
                chart_paths.append(("调制类型分布", path))

        # SNR 与带宽关联分析
        if modulation_stats and len(modulation_stats) > 0:
            path = self._chart_snr_bandwidth_analysis(modulation_stats)
            if path:
                chart_paths.append(("SNR 与带宽关联分析", path))

        # 信号时长分布
        if signals:
            path = self._chart_duration_distribution(signals)
            if path:
                chart_paths.append(("信号时长分布", path))

        # 网络/频段活动对比
        if signals:
            path = self._chart_network_activity(signals)
            if path:
                chart_paths.append(("网络/频段活动对比", path))

        # 综合仪表盘 (多合一概览)
        if signals and freq_stats:
            path = self._chart_dashboard_overview(signals, freq_stats, hourly, modulation_stats)
            if path:
                chart_paths.append(("综合分析概览", path))

        # 生成 HTML 报告
        html_path = self._generate_html_report(
            signals, freq_stats, hourly, sessions, chart_paths
        )

        logger.info(f"报告已生成: {html_path}")
        return html_path

    def _generate_html_report(self, signals: List[Dict],
                               freq_stats: List[Dict],
                               hourly: List[Dict],
                               sessions: List[Dict],
                               chart_paths: List[tuple]) -> str:
        """生成 HTML 格式报告。"""
        now = datetime.now(timezone.utc)
        bg = self.colors["bg"]
        surface = self.colors["surface"]
        text = self.colors["text"]
        text_dim = self.colors["text_dim"]
        accent = self.colors["accent1"]
        grid = self.colors["grid"]

        total_signals = len(signals)
        total_duration = sum(s.get("duration_seconds", 0) or 0 for s in signals)
        unique_freqs = len(set(s["frequency_khz"] for s in signals)) if signals else 0

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>军事无线电信号分析报告</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, 'Segoe UI', 'Microsoft YaHei', sans-serif;
            background: {bg};
            color: {text};
            line-height: 1.6;
            padding: 20px;
        }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        .header {{
            text-align: center;
            padding: 30px 0;
            border-bottom: 1px solid {grid};
            margin-bottom: 30px;
        }}
        .header h1 {{
            font-size: 28px;
            background: linear-gradient(135deg, {accent}, #bc8cff);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .header .subtitle {{ color: {text_dim}; margin-top: 8px; font-size: 14px; }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 30px;
        }}
        .stat-card {{
            background: {surface};
            border: 1px solid {grid};
            border-radius: 10px;
            padding: 20px;
            text-align: center;
        }}
        .stat-card .value {{
            font-size: 32px;
            font-weight: bold;
            color: {accent};
        }}
        .stat-card .label {{ color: {text_dim}; font-size: 13px; margin-top: 4px; }}
        .section {{
            margin-bottom: 30px;
            background: {surface};
            border: 1px solid {grid};
            border-radius: 10px;
            padding: 20px;
        }}
        .section h2 {{
            font-size: 18px;
            margin-bottom: 15px;
            padding-bottom: 8px;
            border-bottom: 1px solid {grid};
        }}
        .chart-img {{
            width: 100%;
            border-radius: 8px;
            margin: 10px 0;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }}
        th, td {{
            padding: 8px 12px;
            text-align: left;
            border-bottom: 1px solid {grid};
        }}
        th {{ color: {text_dim}; font-weight: 600; }}
        tr:hover {{ background: rgba(88,166,255,0.05); }}
        .badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
        }}
        .badge-active {{ background: rgba(63,185,80,0.2); color: #3fb950; }}
        .badge-mod {{ background: rgba(188,140,255,0.2); color: #bc8cff; }}
        .footer {{
            text-align: center;
            padding: 20px;
            color: {text_dim};
            font-size: 12px;
            border-top: 1px solid {grid};
            margin-top: 30px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>\U0001f4fb 军事无线电信号分析报告</h1>
            <div class="subtitle">
                生成时间: {now.strftime('%Y-%m-%d %H:%M UTC')} |
                统计范围: 最近 {self.recent_days} 天
            </div>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="value">{total_signals}</div>
                <div class="label">检测到的信号总数</div>
            </div>
            <div class="stat-card">
                <div class="value">{total_duration:.0f}s</div>
                <div class="label">信号总时长</div>
            </div>
            <div class="stat-card">
                <div class="value">{unique_freqs}</div>
                <div class="label">活跃频率数</div>
            </div>
            <div class="stat-card">
                <div class="value">{len(sessions)}</div>
                <div class="label">监听会话数</div>
            </div>
        </div>
"""

        # 插入图表
        for title, chart_path in chart_paths:
            rel_path = os.path.basename(chart_path)
            html += f"""
        <div class="section">
            <h2>\U0001f4ca {title}</h2>
            <img class="chart-img" src="{rel_path}" alt="{title}">
        </div>
"""

        # 频率统计表
        if freq_stats:
            html += """
        <div class="section">
            <h2>\U0001f4cb 频率活跃度统计</h2>
            <table>
                <tr>
                    <th>频率 (kHz)</th><th>模式</th><th>网络</th>
                    <th>信号数</th><th>总时长</th><th>平均时长</th>
                    <th>平均峰值 RMS</th>
                </tr>
"""
            for s in freq_stats:
                dur = s.get("total_duration", 0) or 0
                avg_dur = s.get("avg_duration", 0) or 0
                avg_rms = s.get("avg_peak_rms", 0) or 0
                html += f"""
                <tr>
                    <td><strong>{s['frequency_khz']:.1f}</strong></td>
                    <td><span class="badge badge-mod">{s.get('mode', 'N/A')}</span></td>
                    <td>{s.get('network', '') or '-'}</td>
                    <td>{s['signal_count']}</td>
                    <td>{dur:.1f}s</td>
                    <td>{avg_dur:.1f}s</td>
                    <td>{avg_rms:.4f}</td>
                </tr>
"""
            html += """
            </table>
        </div>
"""

        # 最近信号列表
        if signals:
            html += """
        <div class="section">
            <h2>\U0001f4e1 最近检测到的信号</h2>
            <table>
                <tr>
                    <th>时间 (UTC)</th><th>频率</th><th>模式</th>
                    <th>节点</th><th>时长</th><th>峰值 RMS</th>
                    <th>录音文件</th>
                </tr>
"""
            for s in signals[:50]:  # 最多显示 50 条
                timestamp = s.get("timestamp", "")[:19]
                rec_file = os.path.basename(s.get("recording_path", "")) if s.get("recording_path") else "-"
                html += f"""
                <tr>
                    <td>{timestamp}</td>
                    <td><strong>{s['frequency_khz']:.1f} kHz</strong></td>
                    <td><span class="badge badge-mod">{s.get('mode', 'N/A')}</span></td>
                    <td>{s.get('node_name', '-')}</td>
                    <td>{(s.get('duration_seconds') or 0):.1f}s</td>
                    <td>{(s.get('peak_rms') or 0):.4f}</td>
                    <td style="font-size:11px">{rec_file}</td>
                </tr>
"""
            html += """
            </table>
        </div>
"""

        html += f"""
        <div class="footer">
            MilRadio Signal Analyzer v1.1 | 仅供无线电爱好者合法监听使用
        </div>
    </div>
</body>
</html>
"""

        # 写入 HTML 文件
        html_path = os.path.join(self.output_dir,
                                  f"report_{now.strftime('%Y%m%d_%H%M')}.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)

        return html_path

    def _generate_text_report(self) -> str:
        """无 matplotlib 时生成纯文本报告。"""
        signals = self.db.get_all_signals(days=self.recent_days)
        freq_stats = self.db.get_frequency_stats(days=self.recent_days)

        lines = []
        lines.append("=" * 60)
        lines.append("  MilRadio - Signal Analysis Report")
        lines.append(f"  时间范围: 最近 {self.recent_days} 天")
        lines.append("=" * 60)
        lines.append(f"\n总信号数: {len(signals)}")
        lines.append(f"活跃频率: {len(freq_stats)}")

        if freq_stats:
            lines.append("\n--- 频率统计 ---")
            for s in freq_stats:
                lines.append(
                    f"  {s['frequency_khz']:>10.1f} kHz | "
                    f"信号数: {s['signal_count']:>4} | "
                    f"总时长: {(s.get('total_duration', 0) or 0):>8.1f}s"
                )

        report_text = "\n".join(lines)
        path = os.path.join(self.output_dir, "report.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(report_text)

        print(report_text)
        return path

    def print_scan_results(self, results: List[Dict]):
        """格式化打印扫描结果。"""
        print("\n" + "=" * 78)
        print(f"{'状态':^4} {'频率':>10} {'模式':^5} {'RMS':>8} {'SNR':>8} "
              f"{'带宽':>8} {'调制':^12} {'描述':<16}")
        print("-" * 78)

        for r in results:
            status = "[+]" if r.get("has_signal") else "[-]"
            print(
                f" {status}  {r['frequency_khz']:>9.1f} {r['mode']:^5} "
                f"{r['rms']:>8.4f} {r['snr_db']:>7.1f}dB "
                f"{r['bandwidth_hz']:>7.0f}Hz "
                f"{r.get('estimated_modulation', '?'):^12} "
                f"{r.get('description', '')[:16]:<16}"
            )

        print("=" * 78)
        active = sum(1 for r in results if r.get("has_signal"))
        print(f"扫描完成: {active}/{len(results)} 个频率检测到活动")
