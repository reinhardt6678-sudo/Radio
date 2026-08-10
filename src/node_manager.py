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
        """获取延迟最低的可用节点。"""
        if not self._available_nodes:
            return None
        return self._available_nodes[0]

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
