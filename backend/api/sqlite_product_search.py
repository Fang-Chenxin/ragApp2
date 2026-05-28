"""API 路由层 - SQLite 商品搜索接口"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Dict, Any

router = APIRouter(prefix="/api/product-search", tags=["product-search"])


class ProductSearchRequest(BaseModel):
    """商品搜索请求模型"""
    keyword: Optional[str] = None
    brand: Optional[str] = None
    category: Optional[str] = None
    sub_category: Optional[str] = None
    attr_filters: Optional[List[Dict[str, str]]] = None
    limit: int = 10
    show_skus: bool = False


class ProductTextSearchRequest(BaseModel):
    """自然语言搜索请求模型"""
    text: str
    limit: int = 10
    show_skus: bool = False


class ToolCallRequest(BaseModel):
    """工具调用请求模型"""
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
        
        result = sqlite_product_search_service.search_by_rule_parsed_text(text=text, limit=limit, show_skus=show_skus)
        
        if not result.get("ok"):
            raise HTTPException(status_code=500, detail=result.get("error", "查询失败"))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"自然语言搜索接口错误: {e}")
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
        print(f"商品搜索接口错误: {e}")
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
        print(f"商品搜索(GET)接口错误: {e}")
        raise HTTPException(status_code=500, detail="服务器内部错误")


@router.get("/tool/spec")
async def get_tool_spec():
    """获取 SQLite 商品搜索工具的调用规范
    
    返回符合 OpenAI 工具调用格式的规范描述，供 LLM 进行工具调用。
    
    Returns:
        工具调用规范
    """
    try:
        from service.sqlite_product_query_tool import get_tool_spec as get_sqlite_query_tool_spec

        return get_sqlite_query_tool_spec()
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"获取工具规范接口错误: {e}")
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
        from service.sqlite_product_query_tool import run_tool as run_sqlite_query_tool

        result = run_sqlite_query_tool(request.tool_name, request.arguments)
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"工具调用接口错误: {e}")
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
        print(f"健康检查接口错误: {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)