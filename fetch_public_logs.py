"""
fetch_public_logs.py - 下载公开的 HFGCS/EAM 监听日志

从多个公开来源抓取军事 HF 信号日志，保存到本地用于与自己监听数据对比。

数据来源:
  1. EAM.watch   - 社区驱动的 EAM 消息数据库 (网页抓取)
  2. Shortwave Archive - 短波录音存档 (RSS)
  3. 本地数据库  - 你自己的监听记录

用法:
  # 抓取所有来源的最新日志
  python fetch_public_logs.py

  # 只抓取 EAM.watch
  python fetch_public_logs.py --source eam

  # 只抓取短波存档
  python fetch_public_logs.py --source archive

  # 指定时间范围（天数）
  python fetch_public_logs.py --days 7

  # 与本地数据对比
  python fetch_public_logs.py --compare

输出目录结构:
  data/public_logs/
  ├── eam_watch/
  │   ├── messages_20260601.json       # 按日期保存的消息
  │   ├── messages_20260601.csv
  │   └── skyking_20260601.json        # Skyking 消息
  ├── shortwave_archive/
  │   ├── recordings_20260601.json     # 录音元数据
  │   └── audio/                       # 下载的音频文件 (可选)
  └── comparison/
      └── comparison_20260601.html     # 对比报告
"""

import argparse
import csv
import json
import logging
import os
import re
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse
from typing import Dict, List, Optional, Tuple

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("=" * 60)
    print("  需要安装依赖:")
    print("  pip install requests beautifulsoup4")
    print("=" * 60)
    sys.exit(1)

# ============================================================
# 配置
# ============================================================

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "data" / "public_logs"
LOCAL_DB_PATH = BASE_DIR / "data" / "radio_monitor.db"

# HFGCS 频率 (kHz) - 用于关键词匹配
HFGCS_FREQUENCIES = [4724.0, 6712.0, 8992.0, 11175.0, 13200.0, 15016.0]
HFGCS_KEYWORDS = [
    "hfgcs", "eam", "skyking", "emergency action message",
    "11175", "8992", "4724", "6712", "13200", "15016",
    "foxtrot", "andrews", "offutt", "lajes", "hickam",
    "croughton", "mcclellan", "west coast",
]

# HTTP 请求头
HEADERS = {
    "User-Agent": "MilRadio-Monitor/1.0 (Research; +https://github.com)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)


# ============================================================
# EAM.Watch 抓取器
# ============================================================

class EAMWatchScraper:
    """
    从 EAM.watch 抓取 EAM 和 Skyking 消息日志。

    EAM.watch 是一个 Vue SPA，数据通过内部 API 加载。
    我们尝试多种方式获取数据：
      1. 直接请求 HTML 页面解析 (服务端渲染的部分)
      2. 尝试已知的 API 端点
    """

    BASE_URL = "https://eam.watch"

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir / "eam_watch"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.audio_dir = self.output_dir / "audio"
        self._messages: List[Dict] = []
        self._skyking: List[Dict] = []

    def fetch(self, days: int = 7, download_audio: bool = False) -> Tuple[List[Dict], List[Dict]]:
        """
        抓取最近 N 天的消息。

        Returns:
            (messages, skyking_messages) 两个列表
        """
        logger.info(f"[EAM.watch] 开始抓取最近 {days} 天的数据...")

        # 尝试多种 API 路径
        api_paths = [
            "/api/messages",
            "/api/v1/messages",
            "/messages",
        ]

        messages = []
        for path in api_paths:
            try:
                result = self._try_api(path, days)
                if result:
                    messages.extend(result)
                    logger.info(f"[EAM.watch] API {path} 返回 {len(result)} 条消息")
                    break
            except Exception as e:
                logger.debug(f"[EAM.watch] API {path} 失败: {e}")

        # 如果 API 不可用，尝试抓取网页
        if not messages:
            logger.info("[EAM.watch] API 不可用，尝试网页抓取...")
            messages = self._scrape_html(days)

        # 尝试抓取 Skyking 消息
        skyking = []
        try:
            skyking = self._fetch_skyking(days)
        except Exception as e:
            logger.warning(f"[EAM.watch] Skyking 抓取失败: {e}")

        self._messages = messages
        self._skyking = skyking

        # 下载音频 (可选)
        if download_audio and (messages or skyking):
            self.audio_dir.mkdir(parents=True, exist_ok=True)
            self._download_audio(messages + skyking)

        # 保存
        if messages or skyking:
            self._save(messages, skyking)

        logger.info(
            f"[EAM.watch] 完成: {len(messages)} 条 EAM, "
            f"{len(skyking)} 条 Skyking"
        )
        return messages, skyking

    def _try_api(self, path: str, days: int) -> Optional[List[Dict]]:
        """尝试 JSON API 端点。"""
        url = f"{self.BASE_URL}{path}"
        params = {"days": days, "limit": 500}

        resp = self.session.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return None

        content_type = resp.headers.get("Content-Type", "")
        if "json" in content_type:
            data = resp.json()
            if isinstance(data, list):
                return data
            elif isinstance(data, dict) and "data" in data:
                return data["data"]

        return None

    def _scrape_html(self, days: int) -> List[Dict]:
        """
        从 HTML 页面抓取消息列表。

        EAM.watch 使用 Vue SPA，大部分内容通过 JS 动态加载。
        我们尝试解析任何可能的服务端渲染内容。
        """
        messages = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        # 尝试抓取消息列表页面
        for page in range(1, 11):  # 最多抓10页
            try:
                url = f"{self.BASE_URL}/messages?page={page}"
                resp = self.session.get(url, timeout=15)
                if resp.status_code != 200:
                    break

                soup = BeautifulSoup(resp.text, "html.parser")

                # 寻找表格行
                rows = soup.select("table tbody tr")
                if not rows:
                    # 也尝试其他可能的容器
                    rows = soup.select(".message-row, .card, [class*=message]")

                if not rows:
                    logger.debug(f"[EAM.watch] 页面 {page} 无表格数据 (SPA)")
                    break

                page_has_old = False
                for row in rows:
                    msg = self._parse_message_row(row)
                    if msg:
                        # 检查日期
                        try:
                            msg_time = datetime.fromisoformat(
                                msg["timestamp"].replace("Z", "+00:00")
                            )
                            if msg_time < cutoff:
                                page_has_old = True
                                continue
                        except (ValueError, KeyError):
                            pass
                        messages.append(msg)

                if page_has_old:
                    break

                time.sleep(1)  # 友好抓取，不过度请求

            except requests.RequestException as e:
                logger.warning(f"[EAM.watch] 页面 {page} 请求失败: {e}")
                break

        # 如果 HTML 也无法解析（纯 SPA），生成占位信息
        if not messages:
            logger.warning(
                "[EAM.watch] 无法解析页面内容 (纯 Vue SPA)。\n"
                "  建议手动访问 https://eam.watch 查看最新消息，\n"
                "  或等待 EAM.watch 提供公开 API。"
            )
            # 写入一个说明文件
            readme = self.output_dir / "README.txt"
            readme.write_text(
                "EAM.watch 是一个 Vue.js SPA 应用，内容通过 JavaScript 动态加载。\n"
                "自动抓取受限，建议以下替代方案：\n\n"
                "1. 手动访问 https://eam.watch 查看/导出消息\n"
                "2. 加入 Discord (https://discord.gg/YaK4vK8) 获取实时 EAM 数据\n"
                "3. 使用 Shortwave Archive RSS 获取 HFGCS 录音\n"
                "4. 加入 RadioReference.com 论坛的军事监听板块\n",
                encoding="utf-8"
            )

        return messages

    def _parse_message_row(self, row) -> Optional[Dict]:
        """解析 HTML 表格行为消息字典。"""
        cells = row.find_all("td")
        if len(cells) < 3:
            return None

        try:
            msg = {
                "source": "eam.watch",
                "timestamp": cells[0].get_text(strip=True),
                "message": cells[1].get_text(strip=True) if len(cells) > 1 else "",
                "frequency_khz": None,
                "type": "EAM",
            }

            # 尝试从文本中提取频率
            full_text = " ".join(c.get_text(strip=True) for c in cells)
            for freq in HFGCS_FREQUENCIES:
                freq_str = str(int(freq))
                if freq_str in full_text:
                    msg["frequency_khz"] = freq
                    break

            return msg
        except Exception:
            return None

    def _fetch_skyking(self, days: int) -> List[Dict]:
        """抓取 Skyking 消息。"""
        messages = []
        try:
            url = f"{self.BASE_URL}/skyking"
            resp = self.session.get(url, timeout=15)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                rows = soup.select("table tbody tr")
                for row in rows:
                    msg = self._parse_message_row(row)
                    if msg:
                        msg["type"] = "SKYKING"
                        messages.append(msg)
        except Exception as e:
            logger.debug(f"[EAM.watch] Skyking 请求失败: {e}")
        return messages

    def _download_audio(self, messages: List[Dict], max_files: int = 10):
        """下载 EAM.watch 的音频文件。"""
        downloaded = 0
        for msg in messages:
            if downloaded >= max_files:
                break
                
            recordings = msg.get("recordings", [])
            if not recordings or not isinstance(recordings, list):
                continue
                
            audio_url = recordings[0].get("link")
            if not audio_url:
                continue

            filename = os.path.basename(urlparse(audio_url).path)
            if not filename or filename == "None":
                filename = f"eam_recording_{msg.get('id', downloaded)}.wav"
            elif "." not in filename:
                filename = f"{filename}.wav"

            filepath = self.audio_dir / filename
            if filepath.exists():
                logger.debug(f"  跳过已存在: {filename}")
                continue

            try:
                logger.info(f"  [EAM.watch] 下载音频: {filename}...")
                resp = self.session.get(audio_url, timeout=60, stream=True)
                if resp.status_code == 200:
                    with open(filepath, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=8192):
                            f.write(chunk)
                    downloaded += 1
                    msg["local_audio_path"] = str(filepath)
            except Exception as e:
                logger.warning(f"  [EAM.watch] 下载失败 {filename}: {e}")

        if downloaded > 0:
            logger.info(f"[EAM.watch] 下载了 {downloaded} 个音频文件")

    def _save(self, messages: List[Dict], skyking: List[Dict]):
        """保存数据到文件。"""
        today = datetime.now().strftime("%Y%m%d")

        # JSON
        if messages:
            json_path = self.output_dir / f"messages_{today}.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(messages, f, indent=2, ensure_ascii=False)
            logger.info(f"  保存: {json_path}")

            # CSV
            csv_path = self.output_dir / f"messages_{today}.csv"
            if messages:
                with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.DictWriter(f, fieldnames=messages[0].keys())
                    writer.writeheader()
                    writer.writerows(messages)
                logger.info(f"  保存: {csv_path}")

        if skyking:
            json_path = self.output_dir / f"skyking_{today}.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(skyking, f, indent=2, ensure_ascii=False)
            logger.info(f"  保存: {json_path}")


# ============================================================
# Shortwave Archive 抓取器
# ============================================================

class ShortwaveArchiveScraper:
    """
    从 Shortwave Radio Audio Archive 获取 HFGCS 相关录音。

    主要方式:
      1. RSS/Podcast feed 解析
      2. 网站搜索页面抓取
    """

    BASE_URL = "https://shortwavearchive.com"
    RSS_URL = "https://shortwavearchive.com/feed"

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir / "shortwave_archive"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.audio_dir = self.output_dir / "audio"
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def fetch(self, days: int = 7, download_audio: bool = False) -> List[Dict]:
        """
        获取 HFGCS 相关录音列表。

        Args:
            days: 获取最近 N 天的数据
            download_audio: 是否下载音频文件

        Returns:
            录音元数据列表
        """
        logger.info(f"[Shortwave Archive] 开始获取最近 {days} 天的 HFGCS 录音...")

        recordings = []

        # 1. 从 RSS feed 获取
        rss_recordings = self._fetch_rss(days)
        recordings.extend(rss_recordings)

        # 2. 从网站搜索获取
        search_recordings = self._search_recordings(days)
        # 去重
        existing_urls = {r.get("url") for r in recordings}
        for r in search_recordings:
            if r.get("url") not in existing_urls:
                recordings.append(r)

        # 过滤 HFGCS 相关
        hfgcs_recordings = self._filter_hfgcs(recordings)

        # 下载音频 (可选)
        if download_audio and hfgcs_recordings:
            self.audio_dir.mkdir(parents=True, exist_ok=True)
            self._download_audio(hfgcs_recordings)

        # 保存
        if hfgcs_recordings:
            self._save(hfgcs_recordings)

        logger.info(
            f"[Shortwave Archive] 完成: {len(hfgcs_recordings)} 条 HFGCS 相关录音 "
            f"(总共 {len(recordings)} 条)"
        )
        return hfgcs_recordings

    def _fetch_rss(self, days: int) -> List[Dict]:
        """解析 RSS feed。"""
        recordings = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        try:
            resp = self.session.get(self.RSS_URL, timeout=15)
            if resp.status_code != 200:
                logger.warning(f"[Shortwave Archive] RSS 返回 {resp.status_code}")
                return []

            root = ET.fromstring(resp.content)
            # RSS 2.0 格式
            channel = root.find("channel")
            if channel is None:
                return []

            for item in channel.findall("item"):
                title = item.findtext("title", "").strip()
                link = item.findtext("link", "").strip()
                pub_date = item.findtext("pubDate", "").strip()
                description = item.findtext("description", "").strip()

                # 解析发布日期
                parsed_date = self._parse_rss_date(pub_date)
                if parsed_date and parsed_date < cutoff:
                    continue  # 跳过旧的条目

                # 提取音频文件链接
                enclosure = item.find("enclosure")
                audio_url = enclosure.get("url") if enclosure is not None else None

                recording = {
                    "source": "shortwave_archive",
                    "title": title,
                    "url": link,
                    "audio_url": audio_url,
                    "pub_date": pub_date,
                    "timestamp": parsed_date.isoformat() if parsed_date else pub_date,
                    "description": self._clean_html(description),
                    "frequency_khz": self._extract_frequency(title + " " + description),
                }
                recordings.append(recording)

            logger.info(f"[Shortwave Archive] RSS 解析得到 {len(recordings)} 条")

        except requests.RequestException as e:
            logger.warning(f"[Shortwave Archive] RSS 请求失败: {e}")
        except ET.ParseError as e:
            logger.warning(f"[Shortwave Archive] RSS 解析失败: {e}")

        return recordings

    def _search_recordings(self, days: int) -> List[Dict]:
        """通过搜索页面查找 HFGCS 录音。"""
        recordings = []
        search_terms = ["HFGCS", "EAM", "11175", "8992", "skyking"]

        for term in search_terms:
            try:
                url = f"{self.BASE_URL}/?s={term}"
                resp = self.session.get(url, timeout=15)
                if resp.status_code != 200:
                    continue

                soup = BeautifulSoup(resp.text, "html.parser")
                articles = soup.select("article, .post, .entry")

                for article in articles:
                    title_el = article.find(["h2", "h3", "h1"])
                    link_el = article.find("a", href=True)
                    time_el = article.find("time")
                    desc_el = article.find(
                        ["p", "div"],
                        class_=lambda x: x and ("excerpt" in x or "summary" in x or "content" in x)
                    )

                    if not title_el:
                        continue

                    title = title_el.get_text(strip=True)
                    link = link_el["href"] if link_el else ""
                    pub_date = time_el.get("datetime", "") if time_el else ""
                    description = desc_el.get_text(strip=True) if desc_el else ""

                    recording = {
                        "source": "shortwave_archive",
                        "title": title,
                        "url": link,
                        "audio_url": None,
                        "pub_date": pub_date,
                        "timestamp": pub_date,
                        "description": description[:500],
                        "frequency_khz": self._extract_frequency(title + " " + description),
                    }
                    recordings.append(recording)

                time.sleep(1)  # 友好抓取

            except requests.RequestException as e:
                logger.debug(f"[Shortwave Archive] 搜索 '{term}' 失败: {e}")

        logger.info(f"[Shortwave Archive] 搜索得到 {len(recordings)} 条")
        return recordings

    def _filter_hfgcs(self, recordings: List[Dict]) -> List[Dict]:
        """过滤出 HFGCS 相关的录音。"""
        result = []
        for rec in recordings:
            text = (
                f"{rec.get('title', '')} {rec.get('description', '')}"
            ).lower()
            if any(kw in text for kw in HFGCS_KEYWORDS):
                result.append(rec)
        return result

    def _download_audio(self, recordings: List[Dict], max_files: int = 10):
        """下载音频文件。"""
        downloaded = 0
        for rec in recordings:
            if downloaded >= max_files:
                break

            audio_url = rec.get("audio_url")
            
            # 如果没有直接的 audio_url，尝试从原文链接中抓取
            if not audio_url and rec.get("url"):
                try:
                    logger.debug(f"  尝试从页面获取音频链接: {rec['url']}")
                    # 如果 URL 不是绝对路径，拼接 BASE_URL
                    page_url = rec['url'] if rec['url'].startswith('http') else urljoin(self.BASE_URL, rec['url'])
                    resp_page = self.session.get(page_url, timeout=15)
                    if resp_page.status_code == 200:
                        soup_page = BeautifulSoup(resp_page.text, "html.parser")
                        audio_el = soup_page.find("audio")
                        if audio_el and audio_el.get("src"):
                            audio_url = audio_el["src"]
                        else:
                            mp3_el = soup_page.find("a", href=lambda h: h and h.endswith(".mp3"))
                            if mp3_el:
                                audio_url = mp3_el["href"]
                        if audio_url:
                            rec["audio_url"] = audio_url
                except Exception as e:
                    logger.debug(f"  抓取页面音频链接失败: {e}")

            if not audio_url:
                continue

            filename = os.path.basename(urlparse(audio_url).path)
            if not filename:
                filename = f"recording_{downloaded + 1}.mp3"

            filepath = self.audio_dir / filename
            if filepath.exists():
                logger.debug(f"  跳过已存在: {filename}")
                continue

            try:
                logger.info(f"  下载: {filename}...")
                resp = self.session.get(audio_url, timeout=60, stream=True)
                if resp.status_code == 200:
                    with open(filepath, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=8192):
                            f.write(chunk)
                    downloaded += 1
                    rec["local_audio_path"] = str(filepath)
            except Exception as e:
                logger.warning(f"  下载失败 {filename}: {e}")

        logger.info(f"[Shortwave Archive] 下载了 {downloaded} 个音频文件")

    def _save(self, recordings: List[Dict]):
        """保存录音元数据。"""
        today = datetime.now().strftime("%Y%m%d")
        json_path = self.output_dir / f"recordings_{today}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(recordings, f, indent=2, ensure_ascii=False)
        logger.info(f"  保存: {json_path}")

        # CSV
        csv_path = self.output_dir / f"recordings_{today}.csv"
        if recordings:
            fields = ["source", "title", "timestamp", "frequency_khz",
                       "url", "audio_url", "description"]
            with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(recordings)
            logger.info(f"  保存: {csv_path}")

    @staticmethod
    def _parse_rss_date(date_str: str) -> Optional[datetime]:
        """解析 RSS 日期格式。"""
        formats = [
            "%a, %d %b %Y %H:%M:%S %z",    # RFC 822
            "%a, %d %b %Y %H:%M:%S %Z",
            "%Y-%m-%dT%H:%M:%S%z",          # ISO 8601
            "%Y-%m-%d %H:%M:%S",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        return None

    @staticmethod
    def _extract_frequency(text: str) -> Optional[float]:
        """从文本中提取频率 (kHz)。"""
        # 匹配 "11175 kHz" 或 "11175kHz" 或 "11.175 MHz"
        patterns = [
            r"(\d{4,5}(?:\.\d+)?)\s*kHz",
            r"(\d{1,2}\.\d+)\s*MHz",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                val = float(match.group(1))
                if "MHz" in pattern:
                    val *= 1000  # 转换为 kHz
                return val
        # 直接匹配已知频率
        for freq in HFGCS_FREQUENCIES:
            if str(int(freq)) in text:
                return freq
        return None

    @staticmethod
    def _clean_html(html: str) -> str:
        """清理 HTML 标签。"""
        clean = re.sub(r"<[^>]+>", " ", html)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean[:500]


# ============================================================
# 数据对比器
# ============================================================

class LogComparator:
    """
    将公开日志与本地监听数据进行对比。

    对比维度:
    - 时间: 公开日志中有信号的时段，你是否也检测到了?
    - 频率: 公开日志中的活跃频率与你监听的频率是否一致?
    - 覆盖率: 你捕获了多少公开记录的信号?
    """

    def __init__(self, db_path: Path, public_logs_dir: Path):
        self.db_path = db_path
        self.public_logs_dir = public_logs_dir
        self.comparison_dir = public_logs_dir / "comparison"
        self.comparison_dir.mkdir(parents=True, exist_ok=True)

    def compare(self, days: int = 7) -> Dict:
        """执行对比分析。"""
        logger.info(f"[对比] 开始对比最近 {days} 天的数据...")

        # 加载本地数据
        local_signals = self._load_local_signals(days)

        # 加载公开日志
        public_messages = self._load_public_logs()

        if not local_signals and not public_messages:
            logger.warning("[对比] 本地和公开数据都为空，无法对比")
            return {}

        # 分析
        result = {
            "generated_at": datetime.now().isoformat(),
            "days": days,
            "local": {
                "total_signals": len(local_signals),
                "frequencies": self._freq_stats(local_signals, "frequency_khz"),
                "hourly": self._hourly_stats(local_signals),
            },
            "public": {
                "total_messages": len(public_messages),
                "sources": self._source_stats(public_messages),
                "frequencies": self._freq_stats(public_messages, "frequency_khz"),
            },
            "comparison": self._do_compare(local_signals, public_messages),
        }

        # 生成报告
        self._generate_report(result)

        return result

    def _load_local_signals(self, days: int) -> List[Dict]:
        """从本地数据库加载信号。"""
        if not self.db_path.exists():
            logger.warning(f"[对比] 本地数据库不存在: {self.db_path}")
            return []

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("""
            SELECT * FROM signals
            WHERE timestamp >= datetime('now', ?)
            ORDER BY timestamp DESC
        """, (f"-{days} days",))
        signals = [dict(row) for row in cursor.fetchall()]
        conn.close()

        logger.info(f"[对比] 本地数据库: {len(signals)} 条信号")
        return signals

    def _load_public_logs(self) -> List[Dict]:
        """加载所有已下载的公开日志。"""
        messages = []

        # EAM.watch 消息
        eam_dir = self.public_logs_dir / "eam_watch"
        if eam_dir.exists():
            for f in sorted(eam_dir.glob("messages_*.json")):
                try:
                    with open(f, "r", encoding="utf-8") as fp:
                        data = json.load(fp)
                        if isinstance(data, list):
                            for msg in data:
                                # 标准化字段: EAM.watch API 用 'time' 而非 'timestamp'
                                if "time" in msg and "timestamp" not in msg:
                                    msg["timestamp"] = msg["time"]
                                if "source" not in msg:
                                    msg["source"] = "eam.watch"
                            messages.extend(data)
                except Exception as e:
                    logger.warning(f"[对比] 加载 {f.name} 失败: {e}")

        # Shortwave Archive 录音
        archive_dir = self.public_logs_dir / "shortwave_archive"
        if archive_dir.exists():
            for f in sorted(archive_dir.glob("recordings_*.json")):
                try:
                    with open(f, "r", encoding="utf-8") as fp:
                        data = json.load(fp)
                        if isinstance(data, list):
                            messages.extend(data)
                except Exception as e:
                    logger.warning(f"[对比] 加载 {f.name} 失败: {e}")

        logger.info(f"[对比] 公开日志: {len(messages)} 条记录")
        return messages

    @staticmethod
    def _freq_stats(records: List[Dict], freq_key: str) -> Dict:
        """统计频率分布。"""
        stats = {}
        for r in records:
            freq = r.get(freq_key)
            if freq:
                freq = float(freq)
                stats[freq] = stats.get(freq, 0) + 1
        return dict(sorted(stats.items(), key=lambda x: x[1], reverse=True))

    @staticmethod
    def _hourly_stats(records: List[Dict]) -> Dict:
        """统计小时分布。"""
        stats = {str(h).zfill(2): 0 for h in range(24)}
        for r in records:
            ts = r.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                hour = str(dt.hour).zfill(2)
                stats[hour] = stats.get(hour, 0) + 1
            except (ValueError, AttributeError):
                pass
        return stats

    @staticmethod
    def _source_stats(records: List[Dict]) -> Dict:
        """统计来源分布。"""
        stats = {}
        for r in records:
            src = r.get("source", "unknown")
            stats[src] = stats.get(src, 0) + 1
        return stats

    def _do_compare(self, local: List[Dict], public: List[Dict]) -> Dict:
        """
        执行实际对比。

        基于时间窗口 (±5 分钟) 和频率匹配来判断"同一个信号"。
        """
        TIME_WINDOW = 300  # 5 分钟容差

        matches = []
        local_only = 0
        public_only = 0

        # 将本地信号按时间排序
        local_times = []
        for s in local:
            try:
                ts = s.get("timestamp", "")
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                local_times.append((dt, s))
            except (ValueError, AttributeError):
                pass

        # 对每条公开记录，检查是否有本地匹配
        matched_local_ids = set()
        for pm in public:
            try:
                pm_ts = pm.get("timestamp", "")
                pm_dt = datetime.fromisoformat(pm_ts.replace("Z", "+00:00"))
                pm_freq = pm.get("frequency_khz")
            except (ValueError, AttributeError):
                public_only += 1
                continue

            found_match = False
            for lt_dt, lt_sig in local_times:
                time_diff = abs((pm_dt - lt_dt).total_seconds())
                if time_diff <= TIME_WINDOW:
                    # 时间匹配
                    freq_match = True
                    if pm_freq and lt_sig.get("frequency_khz"):
                        freq_match = abs(float(pm_freq) - float(lt_sig["frequency_khz"])) < 1.0

                    if freq_match:
                        matches.append({
                            "public_time": pm_ts,
                            "local_time": lt_sig["timestamp"],
                            "time_diff_seconds": round(time_diff, 1),
                            "frequency_khz": pm_freq or lt_sig.get("frequency_khz"),
                            "public_source": pm.get("source"),
                            "local_duration": lt_sig.get("duration_seconds"),
                            "local_peak_rms": lt_sig.get("peak_rms"),
                        })
                        matched_local_ids.add(id(lt_sig))
                        found_match = True
                        break

            if not found_match:
                public_only += 1

        local_only = len(local_times) - len(matched_local_ids)

        return {
            "matched": len(matches),
            "local_only": local_only,
            "public_only": public_only,
            "match_rate_local": (
                f"{len(matched_local_ids) / len(local_times) * 100:.1f}%"
                if local_times else "N/A"
            ),
            "match_details": matches[:50],  # 最多显示 50 条匹配
        }

    def _generate_report(self, result: Dict):
        """生成 HTML 对比报告。"""
        today = datetime.now().strftime("%Y%m%d")
        report_path = self.comparison_dir / f"comparison_{today}.html"

        local = result.get("local", {})
        public = result.get("public", {})
        comparison = result.get("comparison", {})

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>MilRadio - 监听数据对比报告</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Inter', 'Microsoft YaHei', sans-serif;
            background: #0a0e17; color: #e2e8f0;
            padding: 30px; line-height: 1.6;
        }}
        h1 {{
            font-size: 24px; margin-bottom: 8px;
            background: linear-gradient(90deg, #60a5fa, #a78bfa);
            -webkit-background-clip: text; background-clip: text;
            -webkit-text-fill-color: transparent; color: transparent;
        }}
        .subtitle {{ color: #718096; font-size: 14px; margin-bottom: 30px; }}
        .grid {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 20px; margin-bottom: 30px; }}
        .card {{
            background: #1a2035; border: 1px solid #2a3550;
            border-radius: 12px; padding: 20px;
        }}
        .card h3 {{ font-size: 12px; text-transform: uppercase; letter-spacing: 1px; color: #718096; margin-bottom: 12px; }}
        .big-num {{ font-size: 36px; font-weight: 700; }}
        .big-num.blue {{ color: #60a5fa; }}
        .big-num.green {{ color: #34d399; }}
        .big-num.purple {{ color: #a78bfa; }}
        .big-num.yellow {{ color: #fbbf24; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
        th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #2a3550; font-size: 13px; }}
        th {{ color: #718096; font-weight: 600; text-transform: uppercase; font-size: 11px; }}
        .tag {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
        .tag-match {{ background: rgba(52,211,153,0.15); color: #34d399; }}
        .tag-miss {{ background: rgba(248,113,113,0.15); color: #f87171; }}
        .section {{ margin-bottom: 30px; }}
        .section h2 {{ font-size: 16px; color: #e2e8f0; margin-bottom: 16px; padding-bottom: 8px; border-bottom: 1px solid #2a3550; }}
        .empty {{ text-align: center; color: #4a5568; padding: 40px; }}
    </style>
</head>
<body>
    <h1>📡 MilRadio 监听数据对比报告</h1>
    <div class="subtitle">生成时间: {result.get('generated_at', '')} | 对比范围: 最近 {result.get('days', 7)} 天</div>

    <div class="grid">
        <div class="card">
            <h3>本地检测信号</h3>
            <div class="big-num blue">{local.get('total_signals', 0)}</div>
        </div>
        <div class="card">
            <h3>公开日志记录</h3>
            <div class="big-num purple">{public.get('total_messages', 0)}</div>
        </div>
        <div class="card">
            <h3>匹配成功</h3>
            <div class="big-num green">{comparison.get('matched', 0)}</div>
            <div style="color:#718096; font-size:12px; margin-top:4px;">
                本地匹配率: {comparison.get('match_rate_local', 'N/A')}
            </div>
        </div>
    </div>

    <div class="grid" style="grid-template-columns: 1fr 1fr;">
        <div class="card">
            <h3>仅本地检测到</h3>
            <div class="big-num yellow">{comparison.get('local_only', 0)}</div>
            <div style="color:#718096; font-size:12px; margin-top:4px;">
                公开日志中未出现的信号
            </div>
        </div>
        <div class="card">
            <h3>仅公开记录有</h3>
            <div class="big-num" style="color:#f87171;">{comparison.get('public_only', 0)}</div>
            <div style="color:#718096; font-size:12px; margin-top:4px;">
                你可能错过的信号
            </div>
        </div>
    </div>

    <div class="section">
        <h2>📊 频率活跃度对比</h2>
        <table>
            <thead><tr><th>频率 (kHz)</th><th>本地检测</th><th>公开记录</th><th>匹配状态</th></tr></thead>
            <tbody>
                {self._freq_comparison_rows(local.get('frequencies', {}), public.get('frequencies', {}))}
            </tbody>
        </table>
    </div>

    <div class="section">
        <h2>🔗 匹配详情 (最近 50 条)</h2>
        {self._match_detail_table(comparison.get('match_details', []))}
    </div>

    <div class="section">
        <h2>📡 公开数据来源</h2>
        <table>
            <thead><tr><th>来源</th><th>记录数</th></tr></thead>
            <tbody>
                {"".join(f'<tr><td>{src}</td><td>{cnt}</td></tr>' for src, cnt in public.get('sources', {}).items())}
            </tbody>
        </table>
    </div>
</body>
</html>"""

        with open(report_path, "w", encoding="utf-8") as f:
            f.write(html)

        logger.info(f"[对比] 报告已生成: {report_path}")

        # 同时保存 JSON
        json_path = self.comparison_dir / f"comparison_{today}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

    @staticmethod
    def _freq_comparison_rows(local_freq: Dict, public_freq: Dict) -> str:
        """生成频率对比表格行。"""
        all_freqs = sorted(set(
            [float(k) for k in local_freq.keys()] +
            [float(k) for k in public_freq.keys()]
        ))
        rows = []
        for freq in all_freqs:
            local_cnt = local_freq.get(freq, local_freq.get(str(freq), 0))
            public_cnt = public_freq.get(freq, public_freq.get(str(freq), 0))
            if local_cnt > 0 and public_cnt > 0:
                tag = '<span class="tag tag-match">匹配</span>'
            elif local_cnt > 0:
                tag = '<span class="tag" style="background:rgba(96,165,250,0.15);color:#60a5fa;">仅本地</span>'
            else:
                tag = '<span class="tag tag-miss">未捕获</span>'
            rows.append(
                f"<tr><td>{freq:.1f}</td><td>{local_cnt}</td>"
                f"<td>{public_cnt}</td><td>{tag}</td></tr>"
            )
        return "\n".join(rows) if rows else '<tr><td colspan="4" class="empty">暂无数据</td></tr>'

    @staticmethod
    def _match_detail_table(matches: List[Dict]) -> str:
        """生成匹配详情表格。"""
        if not matches:
            return '<div class="empty">暂无匹配记录</div>'

        rows = []
        for m in matches:
            rows.append(
                f"<tr>"
                f"<td>{m.get('frequency_khz', '--')}</td>"
                f"<td>{m.get('public_time', '')[:19]}</td>"
                f"<td>{m.get('local_time', '')[:19]}</td>"
                f"<td>{m.get('time_diff_seconds', '')}s</td>"
                f"<td>{m.get('local_duration', '--')}s</td>"
                f"<td>{m.get('public_source', '')}</td>"
                f"</tr>"
            )

        return f"""
        <table>
            <thead><tr>
                <th>频率</th><th>公开时间</th><th>本地时间</th>
                <th>时差</th><th>本地时长</th><th>来源</th>
            </tr></thead>
            <tbody>{"".join(rows)}</tbody>
        </table>"""


# ============================================================
# 主程序
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="下载公开 HFGCS/EAM 监听日志并与本地数据对比",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
数据来源:
  eam        EAM.watch (EAM/Skyking 消息数据库)
  archive    Shortwave Radio Audio Archive (录音存档)
  all        所有来源 (默认)

示例:
  python fetch_public_logs.py                    # 抓取所有来源
  python fetch_public_logs.py --source eam       # 只抓取 EAM.watch
  python fetch_public_logs.py --days 30          # 最近 30 天
  python fetch_public_logs.py --compare          # 与本地数据对比
  python fetch_public_logs.py --download-audio   # 同时下载音频文件
        """
    )

    parser.add_argument(
        "--source", choices=["eam", "archive", "all"], default="all",
        help="数据来源 (默认: all)"
    )
    parser.add_argument(
        "--days", type=int, default=7,
        help="获取最近 N 天的数据 (默认: 7)"
    )
    parser.add_argument(
        "--compare", action="store_true",
        help="与本地监听数据进行对比"
    )
    parser.add_argument(
        "--download-audio", action="store_true",
        help="下载 Shortwave Archive 的音频文件"
    )
    parser.add_argument(
        "--output-dir", type=str, default=None,
        help=f"输出目录 (默认: {OUTPUT_DIR})"
    )

    args = parser.parse_args()

    output_dir = Path(args.output_dir) if args.output_dir else OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    print()
    print("=" * 60)
    print("  MilRadio - 公开监听日志下载器")
    print("=" * 60)
    print(f"  来源: {args.source}")
    print(f"  时间范围: 最近 {args.days} 天")
    print(f"  输出目录: {output_dir}")
    print(f"  下载音频: {'是' if args.download_audio else '否'}")
    print(f"  数据对比: {'是' if args.compare else '否'}")
    print("=" * 60)
    print()

    total_records = 0

    # 1. EAM.watch
    if args.source in ("eam", "all"):
        try:
            scraper = EAMWatchScraper(output_dir)
            messages, skyking = scraper.fetch(
                days=args.days,
                download_audio=args.download_audio
            )
            total_records += len(messages) + len(skyking)
        except Exception as e:
            logger.error(f"[EAM.watch] 抓取失败: {e}")

    # 2. Shortwave Archive
    if args.source in ("archive", "all"):
        try:
            scraper = ShortwaveArchiveScraper(output_dir)
            recordings = scraper.fetch(
                days=args.days,
                download_audio=args.download_audio
            )
            total_records += len(recordings)
        except Exception as e:
            logger.error(f"[Shortwave Archive] 抓取失败: {e}")

    # 3. 数据对比
    if args.compare:
        try:
            comparator = LogComparator(LOCAL_DB_PATH, output_dir)
            comparator.compare(days=args.days)
        except Exception as e:
            logger.error(f"[对比] 分析失败: {e}")

    # 摘要
    print()
    print("=" * 60)
    print(f"  完成! 共获取 {total_records} 条记录")
    print(f"  输出目录: {output_dir}")
    if args.compare:
        print(f"  对比报告: {output_dir / 'comparison'}")
    print()
    print("  提示: 用 --compare 参数生成与本地数据的对比报告")
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()
