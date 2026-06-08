"""API 路由层 - SQLite 商品搜索接口"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from config.logging_config import get_logger

logger = get_logger("api.product_search")
router = APIRouter(prefix="/api/product-search", tags=["product-search"])


class ProductSearchRequest(BaseModel):
    """结构化商品搜索请求模型。"""
    keyword: Optional[str] = None
    brand: Optional[str] = None
    category: Optional[str] = None
    sub_category: Optional[str] = None
    attr_filters: Optional[List[Dict[str, str]]] = None
    limit: int = 10
    show_skus: bool = False


class ProductTextSearchRequest(BaseModel):
    """自然语言商品搜索请求模型。"""
    text: str
    limit: int = 10
    show_skus: bool = False


class ToolCallRequest(BaseModel):
    """调试 OpenAI 工具调用时使用的请求模型。"""
    tool_name: str
    arguments: Optional[Dict[str, Any]] = None


@router.get("/search/text")
async def search_by_text(
    text: str = Query(..., description="自然语言查询文本"),
    limit: int = Query(10, ge=1, le=100, description="返回结果数量"),
    show_skus: bool = Query(False, description="是否显示 SKU 详情")
):
    """自然语言搜索接口
    
    使用自然语言查询商品数据库，自动解析品牌、分类、属性等信息。
    
    Args:
        text: 自然语言查询文本，如 "银色 1TB 的数码平板"
        limit: 返回结果数量限制
        show_skus: 是否显示 SKU 详情
    
    Returns:
        查询结果
    """
    try:
        from service import sqlite_product_search_service
        
        # 自然语言入口会交给 engine 解析品牌、品类和属性，再执行结构化查询。
        result = sqlite_product_search_service.search_by_rule_parsed_text(text=text, limit=limit, show_skus=show_skus)
        
        if not result.get("ok"):
            raise HTTPException(status_code=500, detail=result.get("error", "查询失败"))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("自然语言搜索接口错误: %s", e)
        raise HTTPException(status_code=500, detail="服务器内部错误")


@router.post("/search")
async def search_products(request: ProductSearchRequest):
    """结构化商品搜索接口
    
    使用结构化参数查询商品数据库。
    
    Args:
        request: 搜索请求，包含关键词、品牌、分类、属性过滤器等
    
    Returns:
        查询结果
    """
    try:
        from service import sqlite_product_search_service
        
        # POST 结构化搜索适合后端/测试直接传入明确过滤条件。
        result = sqlite_product_search_service.search_products(
            keyword=request.keyword,
            brand=request.brand,
            category=request.category,
            sub_category=request.sub_category,
            attr_filters=request.attr_filters,
            limit=request.limit,
            show_skus=request.show_skus
        )
        
        if not result.get("ok"):
            raise HTTPException(status_code=500, detail=result.get("error", "查询失败"))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("商品搜索接口错误: %s", e)
        raise HTTPException(status_code=500, detail="服务器内部错误")


@router.get("/search")
async def search_products_get(
    keyword: Optional[str] = Query(None, description="关键词"),
    brand: Optional[str] = Query(None, description="品牌"),
    category: Optional[str] = Query(None, description="分类"),
    sub_category: Optional[str] = Query(None, description="子分类"),
    attr_key: Optional[List[str]] = Query(None, description="属性键"),
    attr_value: Optional[List[str]] = Query(None, description="属性值"),
    limit: int = Query(10, ge=1, le=100),
    show_skus: bool = Query(False)
):
    """GET 方式的结构化商品搜索接口
    
    Args:
        keyword: 关键词
        brand: 品牌
        category: 分类
        sub_category: 子分类
        attr_key: 属性键列表（与 attr_value 对应）
        attr_value: 属性值列表（与 attr_key 对应）
        limit: 返回结果数量
        show_skus: 是否显示 SKU 详情
    
    Returns:
        查询结果
    """
    try:
        from service import sqlite_product_search_service
        
        attr_filters = []
        if attr_key and attr_value:
            # GET 参数用并列数组表示属性过滤器，zip 后转成服务层统一格式。
            for key, value in zip(attr_key, attr_value):
                attr_filters.append({"key": key, "value": value})
        
        result = sqlite_product_search_service.search_products(
            keyword=keyword,
            brand=brand,
            category=category,
            sub_category=sub_category,
            attr_filters=attr_filters,
            limit=limit,
            show_skus=show_skus
        )
        
        if not result.get("ok"):
            raise HTTPException(status_code=500, detail=result.get("error", "查询失败"))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("商品搜索(GET)接口错误: %s", e)
        raise HTTPException(status_code=500, detail="服务器内部错误")


@router.get("/tool/spec")
async def get_tool_spec():
    """获取 SQLite 商品搜索工具的调用规范
    
    返回符合 OpenAI 工具调用格式的规范描述，供 LLM 进行工具调用。
    
    Returns:
        工具调用规范
    """
    try:
        from service.product_search.query_tool import get_tool_spec as get_sqlite_query_tool_spec

        # 直接返回 Function Calling schema，方便前端或调试工具查看可传参数。
        return get_sqlite_query_tool_spec()
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取工具规范接口错误: %s", e)
        raise HTTPException(status_code=500, detail="服务器内部错误")


@router.post("/tool/run")
async def run_tool(request: ToolCallRequest):
    """执行工具调用
    
    执行 LLM 请求的工具调用，查询商品数据库。
    
    Args:
        request: 工具调用请求，包含工具名称和参数
    
    Returns:
        工具执行结果
    """
    try:
        from service.product_search.query_tool import run_tool as run_sqlite_query_tool

        # 该接口绕过 LLM，便于复现某一次工具调用的参数和结果。
        result = run_sqlite_query_tool(request.tool_name, request.arguments)
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("工具调用接口错误: %s", e)
        raise HTTPException(status_code=500, detail="服务器内部错误")


@router.get("/health")
async def health_check():
    """SQLite 商品搜索服务健康检查
    
    Returns:
        服务状态信息
    """
    try:
        from service import sqlite_product_search_service
        
        return {
            "status": "healthy",
            "db_available": sqlite_product_search_service.db_available,
            "db_path": str(sqlite_product_search_service.db_path)
        }
        
    except Exception as e:
        error_msg = str(e)
        logger.error("健康检查接口错误: %s", error_msg)
        raise HTTPException(status_code=500, detail=error_msg)


@router.get("/products/{product_id}")
async def get_product_detail(product_id: str):
    """获取单个商品详情
    
    Args:
        product_id: 商品ID
    
    Returns:
        商品详情 JSON
    """
    try:
        from service import sqlite_product_search_service
        
        result = sqlite_product_search_service.get_products_by_ids([product_id])
        
        if not result.get("ok"):
            raise HTTPException(status_code=500, detail=result.get("error", "查询失败"))
        
        items = result.get("items", [])
        if not items:
            raise HTTPException(status_code=404, detail="商品不存在")
        
        return items[0]
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("商品详情接口错误: %s", e)
        raise HTTPException(status_code=500, detail="服务器内部错误")


@router.get("/products/{product_id}/page")
async def get_product_landing_page(product_id: str):
    """获取商品落地页 HTML
    
    返回轻量 HTML 页面，展示标题、品牌、分类、价格、图片、营销描述
    
    Args:
        product_id: 商品ID
    
    Returns:
        HTML 落地页
    """
    try:
        from service import sqlite_product_search_service
        
        result = sqlite_product_search_service.get_products_by_ids([product_id])
        
        if not result.get("ok"):
            raise HTTPException(status_code=500, detail=result.get("error", "查询失败"))
        
        items = result.get("items", [])
        if not items:
            raise HTTPException(status_code=404, detail="商品不存在")
        
        product = items[0]
        
        # 生成简洁 HTML 落地页
        title = product.get("title", "商品详情")
        brand = product.get("brand", "")
        category = product.get("category", "")
        sub_category = product.get("sub_category", "")
        price = product.get("base_price", "价格待定")
        image_path = product.get("image_path", "")
        marketing_desc = product.get("marketing_desc", "")
        
        # 构造图片 URL
        image_url = f"/api/product-search/images/{image_path}" if image_path else ""
        
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; margin: 0; padding: 16px; background: #f5f5f5; }}
        .container {{ max-width: 600px; margin: 0 auto; background: white; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); padding: 16px; }}
        .product-image {{ width: 100%; height: 300px; background: #f0f0f0; border-radius: 8px; margin-bottom: 16px; display: flex; align-items: center; justify-content: center; color: #999; }}
        .product-image img {{ width: 100%; height: 100%; object-fit: cover; border-radius: 8px; }}
        .product-title {{ font-size: 20px; font-weight: bold; color: #222; margin-bottom: 12px; }}
        .product-meta {{ display: flex; gap: 12px; margin-bottom: 12px; font-size: 14px; color: #666; }}
        .meta-item {{ flex: 1; }}
        .meta-label {{ color: #999; font-size: 12px; }}
        .meta-value {{ color: #333; }}
        .product-price {{ font-size: 24px; color: #e63946; font-weight: bold; margin-bottom: 12px; }}
        .product-desc {{ background: #f9f9f9; padding: 12px; border-radius: 4px; font-size: 14px; line-height: 1.6; color: #555; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="product-image">
            {f'<img src="{image_url}" alt="{title}">' if image_url else '无图'}
        </div>
        <div class="product-title">{title}</div>
        <div class="product-meta">
            <div class="meta-item">
                <div class="meta-label">品牌</div>
                <div class="meta-value">{brand or '未知'}</div>
            </div>
            <div class="meta-item">
                <div class="meta-label">分类</div>
                <div class="meta-value">{category or ''}{' / ' + sub_category if sub_category else ''}</div>
            </div>
        </div>
        <div class="product-price">¥{price}</div>
        {f'<div class="product-desc">{marketing_desc}</div>' if marketing_desc else ''}
    </div>
</body>
</html>
"""
        return {"content": html_content}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("商品落地页接口错误: %s", e)
        raise HTTPException(status_code=500, detail="服务器内部错误")


@router.get("/images/{image_path:path}")
async def get_product_image(image_path: str):
    """获取商品图片
    
    安全访问 ecommerce_agent_dataset 内的图片，禁止路径穿越
    
    Args:
        image_path: 图片相对路径
    
    Returns:
        图片文件
    """
    try:
        import os
        from pathlib import Path
        from fastapi.responses import FileResponse
        from config.settings import settings
        
        # 禁止路径穿越
        if ".." in image_path or image_path.startswith("/"):
            raise HTTPException(status_code=400, detail="非法路径")
        
        # 构造完整路径
        dataset_dir = Path(settings.sqlite_product_db_path).parent
        image_file = dataset_dir / image_path
        
        # 验证路径确实在数据集目录内
        try:
            image_file.resolve().relative_to(dataset_dir.resolve())
        except ValueError:
            raise HTTPException(status_code=400, detail="非法路径")
        
        if not image_file.exists():
            raise HTTPException(status_code=404, detail="图片不存在")
        
        if not image_file.is_file():
            raise HTTPException(status_code=400, detail="无效的文件请求")
        
        return FileResponse(image_file)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("图片访问接口错误: %s", e)
        raise HTTPException(status_code=500, detail="服务器内部错误")
