"""RAG 向量检索测试脚本。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

from config.settings import settings
from service.rag_service import VectorStore


class RAGVectorQueryTest(unittest.TestCase):
    def setUp(self):
        self.expected_chroma_path = ROOT / "ecommerce_agent_dataset" / ".chroma"

    def test_chroma_config_points_to_ecommerce_dataset(self):
        self.assertEqual(Path(settings.chroma_path).resolve(), self.expected_chroma_path.resolve())
        self.assertEqual(settings.chroma_collection_name, "product_knowledge")

    def test_query_product_knowledge_returns_documents(self):
        self.assertTrue(
            self.expected_chroma_path.exists(),
            f"Chroma 数据库不存在: {self.expected_chroma_path}",
        )

        vector_store = VectorStore()
        vector_store.initialize()

        self.assertGreater(vector_store.get_count(), 0)

        results = vector_store.query_with_sources("新手适合用的眉笔", top_k=3)
        self.assertTrue(results)

        first = results[0]
        metadata = first["metadata"]
        self.assertTrue(first["content"].strip())
        self.assertEqual(metadata["product_id"], "p_beauty_025")
        self.assertIn("眉笔", metadata["title"])
        self.assertEqual(metadata["chunk_type"], "review")

        context_text = vector_store.format_results_as_context(results)
        self.assertIn("商品ID: p_beauty_025", context_text)
        self.assertIn("片段类型: review", context_text)

        print("\nRAG 查询结果预览:")
        for index, item in enumerate(results, start=1):
            metadata = item["metadata"]
            preview = str(item["content"]).replace("\n", " ")[:140]
            print(
                f"{index}. product_id={metadata.get('product_id')} | "
                f"title={metadata.get('title')} | chunk={metadata.get('chunk_type')} | {preview}"
            )


if __name__ == "__main__":
    unittest.main()
