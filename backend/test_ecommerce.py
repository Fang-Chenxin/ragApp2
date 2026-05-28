"""电商服务测试脚本"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import settings
from service import ecommerce_service


async def test_ecommerce_service():
    """测试电商服务"""
    print("=" * 60)
    print("📦 电商服务测试")
    print("=" * 60)

    # 初始化服务
    print("\n1️⃣ 初始化电商服务...")
    ecommerce_service.initialize()
    print(f"   数据库可用: {ecommerce_service.db_available}")
    print(f"   数据库路径: {ecommerce_service.db_path}")

    # 测试自然语言搜索
    print("\n2️⃣ 测试自然语言搜索...")
    result = ecommerce_service.search_from_text("银色手机", limit=5)
    print(f"   输入: '银色手机'")
    print(f"   成功: {result.get('ok')}")
    print(f"   总数: {result.get('total', 0)}")
    if result.get('items'):
        for i, item in enumerate(result['items'][:3], 1):
            print(f"   商品{i}: {item.get('name', '未知')}")

    # 测试结构化搜索
    print("\n3️⃣ 测试结构化搜索...")
    result = ecommerce_service.search_products(
        keyword="手机",
        limit=3
    )
    print(f"   输入: keyword='手机'")
    print(f"   成功: {result.get('ok')}")
    print(f"   总数: {result.get('total', 0)}")

    # 测试工具调用
    print("\n4️⃣ 测试工具调用...")
    result = ecommerce_service.run_tool(
        "query_products",
        {"text": "红色笔记本电脑"}
    )
    print(f"   工具: query_products")
    print(f"   参数: text='红色笔记本电脑'")
    print(f"   成功: {result.get('ok')}")
    print(f"   总数: {result.get('total', 0)}")

    # 测试工具规范
    print("\n5️⃣ 测试工具规范...")
    spec = ecommerce_service.get_tool_spec()
    print(f"   工具名称: {spec['function']['name']}")
    print(f"   工具描述: {spec['function']['description']}")

    # 测试错误处理
    print("\n6️⃣ 测试错误处理...")
    result = ecommerce_service.run_tool("unknown_tool", {})
    print(f"   未知工具测试 - 成功: {result.get('ok')}, 错误: {result.get('error')}")

    # 关闭服务
    print("\n7️⃣ 关闭服务...")
    ecommerce_service.close()

    print("\n✅ 测试完成！")


if __name__ == "__main__":
    asyncio.run(test_ecommerce_service())
