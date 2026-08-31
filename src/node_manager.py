"""
node_manager.py - KiwiSDR 节点发现与管理

负责维护可用的 KiwiSDR 公共节点列表，执行健康检查，
并根据可用性和延迟选择最佳节点。
"""

import asyncio
import logging
from typing import List, Dict, Optional
from .kiwi_client import test_connection
from .db import Database

logger = logging.getLogger(__name__)

# --- 节点接收质量分档 ---
# 延迟只说明网络快慢，说明不了这个节点收不收得到目标频率: HF 靠的是地理位置。
# 实测 HB3YQQ 瑞士延迟最低，却连着 5.5 小时、85 条记录、0 条真信号,
# 因为 HFGCS 发射台在美国。
NODE_QUALITY_MIN_SNR_DB = 6.0   # 带内 SNR 到这个数才算"真听见了"
NODE_QUALITY_MIN_SAMPLES = 30   # 攒够这么多条记录，才敢断定一个节点听不见


def node_quality_tier(host: str, quality: dict,
                      audio_health: dict = None) -> int:
    """
    Rank a node from its history; lower is better.
    按历史记录给节点分档，数字越小越优先。

    0 = something was genuinely received here / 在这个节点上真收到过东西
    1 = too few records to tell / 记录太少，还不知道
    2 = enough records but not one real signal / 记录够多了但一条真信号都没有
    3 = observed sending frames without audio / 观测到它只发帧不出声

    "Unknown" outranks "proven deaf": an untried node still has a chance, one that produced
    nothing in 85 attempts should not be revisited.
    "没数据"要排在"有数据证明它不行"前面: 没试过的还有机会，
    试了 85 次 0 条的就别再回去了。

    Tier 3 sits below even that. A deaf node at least delivers audio, so the frequency might
    simply have been quiet; a muted node cannot ever produce a signal, and every recording it
    triggers is a file of zeros. It leaves tier 3 as soon as live audio is seen once.
    第 3 档比"聋"还靠后。聋节点至少还在出音频，可能只是那个频率当时没动静;
    哑音节点则永远不可能产出信号，它触发的每一条录音都是一串零。
    只要有一次收到真实音频，它立刻脱离第 3 档。

    Args:
        host: 节点地址 / node address
        quality: db.get_node_signal_quality() 的返回值 / its return value
        audio_health: db.get_node_audio_health() 的返回值 / its return value
    """
    audio = (audio_health or {}).get(host)
    if audio and audio.get("dead", 0) > 0 and audio.get("ok", 0) == 0:
        return 3

    stat = (quality or {}).get(host)
    if not stat:
        return 1
    if stat["useful"] > 0:
        return 0
    return 2 if stat["total"] >= NODE_QUALITY_MIN_SAMPLES else 1


class NodeManager:
    """KiwiSDR 节点管理器。"""

    def __init__(self, config: dict, db: Database):
        """
        初始化节点管理器。

        Args:
            config: 包含 nodes 列表的配置字典
            db: 数据库实例
        """
        self.config = config
        self.db = db
        self.nodes: List[Dict] = config.get("nodes", [])
        self._available_nodes: List[Dict] = []

    async def check_all_nodes(self, timeout: float = 10.0,
                               max_concurrent: int = 4) -> List[Dict]:
        """
        并行检查所有节点的可用性。

        Args:
            timeout: 每个节点的超时时间
            max_concurrent: 最大并发检查数

        Returns:
            可用节点列表
        """
        logger.info(f"开始检查 {len(self.nodes)} 个 KiwiSDR 节点...")
        semaphore = asyncio.Semaphore(max_concurrent)

        async def check_with_limit(node):
            async with semaphore:
                return await self._check_node(node, timeout)

        tasks = [check_with_limit(node) for node in self.nodes]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        self._available_nodes = []
        for node, result in zip(self.nodes, results):
            if isinstance(result, Exception):
                logger.error(f"检查节点异常 {node['host']}: {result}")
                self._update_node_status(node, False)
                continue

            if result.get("available"):
                node_info = {**node, **result}
                self._available_nodes.append(node_info)
                self._update_node_status(node, True, result.get("latency_ms"))
                logger.info(
                    f"  [OK] {node.get('name', node['host'])} "
                    f"({result['latency_ms']:.0f}ms)"
                )
            else:
                self._update_node_status(node, False)
                logger.warning(
                    f"  [FAIL] {node.get('name', node['host'])} "
                    f"- {result.get('error', '未知错误')}"
                )

        # 按延迟排序
        self._available_nodes.sort(
            key=lambda n: n.get("latency_ms", 9999)
        )

        logger.info(
            f"节点检查完成: {len(self._available_nodes)}/{len(self.nodes)} 可用"
        )
        return self._available_nodes

    async def _check_node(self, node: dict, timeout: float) -> dict:
        """检查单个节点。"""
        return await test_connection(
            host=node["host"],
            port=node.get("port", 8073),
            timeout=timeout
        )

    def _update_node_status(self, node: dict, available: bool,
                            latency_ms: float = None):
        """更新节点状态到数据库。"""
        try:
            self.db.upsert_node(
                host=node["host"],
                port=node.get("port", 8073),
                name=node.get("name", ""),
                location=node.get("location", ""),
                lat=node.get("lat"),
                lon=node.get("lon"),
                is_available=available,
                latency_ms=latency_ms
            )
        except Exception as e:
            logger.error(f"更新节点状态失败: {e}")

    def get_available_nodes(self) -> List[Dict]:
        """获取已缓存的可用节点列表。"""
        return self._available_nodes

    def get_best_node(self) -> Optional[Dict]:
        """
        挑最佳可用节点。

        先看历史上在这个节点收到过真信号没有，同档之内才比延迟 ——
        反过来按延迟挑的话，很容易挑中一个网络很快但压根听不见目标频率的节点。

        Nodes known to send frames without audio are ranked last: latency and the handshake
        both look perfect on such a node, which is exactly how the fastest node in the list
        gets picked and then records nothing but zeros.
        已知只发帧不出声的节点排在最后: 这种节点的延迟和握手都完美,
        正是这样才会被当成"最快的节点"选中，然后录一整串零。
        """
        if not self._available_nodes:
            return None

        quality = {}
        audio_health = {}
        try:
            quality = self.db.get_node_signal_quality(NODE_QUALITY_MIN_SNR_DB)
        except Exception:
            # 读不出历史就退化成按延迟挑，跟以前一样
            pass
        try:
            audio_health = self.db.get_node_audio_health()
        except Exception:
            # 老库还没补上这几列时照常工作 / older databases lack these columns
            pass

        return min(self._available_nodes, key=lambda n: (
            node_quality_tier(n["host"], quality, audio_health),
            -((quality.get(n["host"]) or {}).get("useful") or 0),
            n.get("latency_ms", 9999),
        ))

    def get_node_by_name(self, name: str) -> Optional[Dict]:
        """按名称查找节点。"""
        for node in self.nodes:
            if name.lower() in node.get("name", "").lower():
                return node
        return None

    def format_node_table(self) -> str:
        """格式化节点列表为可读表格。"""
        lines = []
        lines.append("=" * 78)
        lines.append(f"{'状态':^4} {'名称':<20} {'地址':<30} {'延迟':>8} {'位置':<14}")
        lines.append("-" * 78)

        for node in self.nodes:
            # 检查是否在可用列表中
            avail = next(
                (n for n in self._available_nodes
                 if n["host"] == node["host"]),
                None
            )
            status = "+" if avail else "-"
            latency = f"{avail['latency_ms']:.0f}ms" if avail else "N/A"
            name = node.get("name", "")[:20]
            host = f"{node['host']}:{node.get('port', 8073)}"[:30]
            location = node.get("location", "")[:14]

            lines.append(f" {status}   {name:<20} {host:<30} {latency:>8} {location:<14}")

        lines.append("=" * 78)
        lines.append(
            f"总计: {len(self._available_nodes)}/{len(self.nodes)} 节点可用"
        )
        return "\n".join(lines)
