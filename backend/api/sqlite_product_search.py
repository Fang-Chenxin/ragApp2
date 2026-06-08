"""API 路由层 - SQLite 商品搜索接口"""
import sqlite3
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from html import escape
from pathlib import Path
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from urllib.parse import quote
from config.logging_config import get_logger

logger = get_logger("api.product_search")
router = APIRouter(prefix="/api/product-search", tags=["product-search"])

FIELD_LABELS = {
    "product_id": "商品编号",
    "sku_id": "规格编号",
    "title": "商品标题",
    "brand": "品牌",
    "brand_norm": "品牌标准名",
    "category": "一级类目",
    "sub_category": "二级类目",
    "base_price": "基础价格",
    "price": "价格",
    "sku_label": "规格名称",
    "image_path": "图片路径",
    "image_url": "图片地址",
    "marketing_desc": "商品描述",
    "source_file": "来源文件",
    "source_hash": "来源校验值",
    "updated_at": "更新时间",
    "id": "记录编号",
    "attr_key_raw": "属性名",
    "attr_key_norm": "标准属性名",
    "attr_value_raw": "属性值",
    "attr_value_norm": "标准属性值",
    "faq_id": "问答编号",
    "question": "问题",
    "answer": "回答",
    "review_id": "评价编号",
    "nickname": "昵称",
    "rating": "评分",
    "content": "内容",
}


def _get_dataset_dir() -> Path:
    """商品数据集根目录，图片路径都应限制在该目录内。"""
    from config.settings import settings

    return Path(settings.sqlite_product_db_path).parent


def _resolve_product_image_file(image_path: str) -> Path:
    """解析商品图片路径；目录名历史变更时按文件名在 images 目录兜底查找。"""
    clean_path = str(image_path or "").strip()
    if not clean_path:
        raise HTTPException(status_code=404, detail="图片不存在")
    if ".." in clean_path or clean_path.startswith("/"):
        raise HTTPException(status_code=400, detail="非法路径")

    dataset_dir = _get_dataset_dir()
    image_file = dataset_dir / clean_path

    try:
        image_file.resolve().relative_to(dataset_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="非法路径")

    if image_file.exists() and image_file.is_file():
        return image_file

    basename = Path(clean_path).name
    if basename:
        matches = sorted(dataset_dir.glob(f"*/images/{basename}"))
        for candidate in matches:
            if candidate.is_file():
                logger.warning("商品图片路径不存在，按文件名兜底命中: %s -> %s", clean_path, candidate.relative_to(dataset_dir))
                return candidate

    raise HTTPException(status_code=404, detail="图片不存在")


def _get_product_detail_payload(product_id: str) -> Dict[str, Any]:
    """读取商品完整详情，包含 SKU、属性、FAQ、评价和原始来源字段。"""
    from config.settings import settings

    clean_product_id = str(product_id or "").strip()
    if not clean_product_id:
        raise HTTPException(status_code=404, detail="商品不存在")

    db_path = Path(settings.sqlite_product_db_path)
    if not db_path.exists():
        raise HTTPException(status_code=500, detail="SQLite 商品数据库不存在")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        product_row = conn.execute(
            "SELECT * FROM products WHERE product_id = ?",
            (clean_product_id,),
        ).fetchone()
        if not product_row:
            raise HTTPException(status_code=404, detail="商品不存在")

        product = dict(product_row)
        sku_rows = conn.execute(
            "SELECT * FROM skus WHERE product_id = ? ORDER BY sku_id",
            (clean_product_id,),
        ).fetchall()
        skus: List[Dict[str, Any]] = []
        for sku_row in sku_rows:
            sku = dict(sku_row)
            attr_rows = conn.execute(
                """
                SELECT id, attr_key_raw, attr_key_norm, attr_value_raw, attr_value_norm
                FROM sku_attributes
                WHERE product_id = ? AND sku_id = ?
                ORDER BY id
                """,
                (clean_product_id, sku["sku_id"]),
            ).fetchall()
            sku["attributes"] = [dict(row) for row in attr_rows]
            skus.append(sku)

        faq_rows = conn.execute(
            "SELECT * FROM product_faqs WHERE product_id = ? ORDER BY faq_id",
            (clean_product_id,),
        ).fetchall()
        review_rows = conn.execute(
            "SELECT * FROM product_reviews WHERE product_id = ? ORDER BY review_id",
            (clean_product_id,),
        ).fetchall()

        product["image_url"] = (
            f"/api/product-search/images/{quote(str(product.get('image_path') or ''), safe='/')}"
            if product.get("image_path")
            else ""
        )
        product["skus"] = skus
        product["faqs"] = [dict(row) for row in faq_rows]
        product["reviews"] = [dict(row) for row in review_rows]
        return product
    finally:
        conn.close()


def _choose_sku_id(product: Dict[str, Any], requested_sku_id: Optional[str] = None) -> str:
    """选择当前页面默认 SKU；优先使用卡片 URL 指定的 sku_id。"""
    skus = product.get("skus") or []
    if not skus:
        return ""

    clean_requested = str(requested_sku_id or "").strip()
    if clean_requested and any(str(sku.get("sku_id")) == clean_requested for sku in skus):
        return clean_requested

    base_price = product.get("base_price")
    for sku in skus:
        try:
            if float(sku.get("price")) == float(base_price):
                return str(sku.get("sku_id") or "")
        except (TypeError, ValueError):
            continue

    return str(skus[0].get("sku_id") or "")


def _format_value(value: Any) -> str:
    if value is None or value == "":
        return "—"
    return escape(str(value)).replace("\n", "<br>")


def _format_price(value: Any) -> str:
    try:
        return f"¥{float(value):.2f}"
    except (TypeError, ValueError):
        return _format_value(value)


def _render_kv_table(data: Dict[str, Any], skip_keys: set[str] | None = None) -> str:
    skip = skip_keys or set()
    rows = []
    for key, value in data.items():
        if key in skip:
            continue
        label = FIELD_LABELS.get(str(key), str(key))
        rows.append(
            "<tr>"
            f"<th>{escape(label)}</th>"
            f"<td>{_format_value(value)}</td>"
            "</tr>"
        )
    return "\n".join(rows)


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
        return _get_product_detail_payload(product_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("商品详情接口错误: %s", e)
        raise HTTPException(status_code=500, detail="服务器内部错误")


@router.get("/products/{product_id}/page")
async def get_product_landing_page(
    product_id: str,
    sku_id: Optional[str] = Query(None, description="需要默认选中的 SKU ID"),
):
    """获取商品落地页 HTML
    
    返回轻量 HTML 页面，展示标题、品牌、分类、价格、图片、营销描述
    
    Args:
        product_id: 商品ID
    
    Returns:
        HTML 落地页
    """
    try:
        product = _get_product_detail_payload(product_id)
        selected_sku_id = _choose_sku_id(product, sku_id)
        selected_sku = next(
            (sku for sku in product.get("skus") or [] if str(sku.get("sku_id")) == selected_sku_id),
            None,
        )

        title = escape(str(product.get("title") or "商品详情"))
        brand = escape(str(product.get("brand") or "未知品牌"))
        category = escape(str(product.get("category") or ""))
        sub_category = escape(str(product.get("sub_category") or ""))
        marketing_desc = escape(str(product.get("marketing_desc") or "")).replace("\n", "<br>")
        image_url = escape(str(product.get("image_url") or ""), quote=True)

        sku_cards: List[str] = []
        for sku in product.get("skus") or []:
            current_sku_id = str(sku.get("sku_id") or "")
            attrs = sku.get("attributes") or []
            attr_text = " / ".join(
                f"{attr.get('attr_key_raw') or attr.get('attr_key_norm')}: {attr.get('attr_value_raw') or attr.get('attr_value_norm')}"
                for attr in attrs
            )
            is_selected = current_sku_id == selected_sku_id
            sku_cards.append(
                f"""
                <button class="sku-card {'selected' if is_selected else ''}" type="button" data-sku-id="{escape(current_sku_id, quote=True)}">
                    <span class="sku-card__top">
                        <strong>规格编号：{escape(current_sku_id)}</strong>
                        <span>{_format_price(sku.get("price"))}</span>
                    </span>
                    <span class="sku-card__attrs">{escape(attr_text) if attr_text else '无属性信息'}</span>
                </button>
                """
            )

        faq_items = "\n".join(
            f"""
            <details class="qa-item">
                <summary>{escape(str(item.get("question") or ""))}</summary>
                <p>{escape(str(item.get("answer") or ""))}</p>
            </details>
            """
            for item in product.get("faqs") or []
        )
        review_items = "\n".join(
            f"""
            <article class="review-item">
                <div class="review-meta">
                    <strong>{escape(str(item.get("nickname") or "匿名用户"))}</strong>
                    <span>{'★' * int(item.get("rating") or 0)}{'☆' * max(0, 5 - int(item.get("rating") or 0))}</span>
                </div>
                <p>{escape(str(item.get("content") or ""))}</p>
            </article>
            """
            for item in product.get("reviews") or []
        )
        selected_attrs = _render_kv_table(selected_sku or {}, skip_keys={"attributes"}) if selected_sku else ""
        selected_attr_rows = "\n".join(
            "<tr>"
            f"<th>{_format_value(attr.get('attr_key_raw') or attr.get('attr_key_norm'))}</th>"
            f"<td>{_format_value(attr.get('attr_value_raw') or attr.get('attr_value_norm'))}</td>"
            "</tr>"
            for attr in ((selected_sku or {}).get("attributes") or [])
        )

        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        :root {{ color-scheme: light; }}
        * {{ box-sizing: border-box; }}
        body {{ margin: 0; background: #f4f6f8; color: #1f2933; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; }}
        html, body {{ width: 100%; overflow-x: hidden; }}
        .page {{ width: 100%; max-width: 1040px; margin: 0 auto; padding: 18px; }}
        .hero {{ display: grid; grid-template-columns: minmax(220px, 360px) minmax(0, 1fr); gap: 20px; align-items: start; }}
        .media {{ min-height: 320px; background: #e8edf2; border-radius: 8px; overflow: hidden; display: flex; align-items: center; justify-content: center; color: #7b8794; }}
        .media img {{ width: 100%; height: 100%; min-height: 320px; object-fit: cover; }}
        .title {{ margin: 0 0 10px; font-size: 26px; line-height: 1.25; overflow-wrap: anywhere; }}
        .meta-line {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 14px; }}
        .pill {{ max-width: 100%; border: 1px solid #d8dee6; border-radius: 999px; padding: 5px 10px; background: #fff; color: #53606f; font-size: 13px; overflow-wrap: anywhere; }}
        .price {{ color: #d92d20; font-size: 28px; font-weight: 750; margin: 12px 0; }}
        .section {{ min-width: 0; margin-top: 18px; padding: 16px; background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; }}
        .section h2 {{ margin: 0 0 12px; font-size: 18px; }}
        .desc {{ line-height: 1.75; color: #3e4c59; overflow-wrap: anywhere; }}
        table {{ width: 100%; max-width: 100%; table-layout: fixed; border-collapse: collapse; font-size: 14px; }}
        th, td {{ border-bottom: 1px solid #edf2f7; padding: 9px 8px; text-align: left; vertical-align: top; }}
        th {{ width: 160px; color: #64748b; font-weight: 600; background: #fafbfc; }}
        td {{ overflow-wrap: anywhere; word-break: break-word; }}
        .sku-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(min(220px, 100%), 1fr)); gap: 10px; }}
        .sku-card {{ min-width: 0; text-align: left; border: 1px solid #d8dee6; background: #fff; border-radius: 8px; padding: 12px; cursor: pointer; }}
        .sku-card.selected {{ border-color: #2563eb; box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.14); background: #f8fbff; }}
        .sku-card__top {{ display: flex; justify-content: space-between; gap: 8px; margin-bottom: 8px; }}
        .sku-card__top strong, .sku-card__top span {{ min-width: 0; overflow-wrap: anywhere; }}
        .sku-card__attrs {{ color: #53606f; font-size: 13px; line-height: 1.5; overflow-wrap: anywhere; }}
        .qa-item {{ border-top: 1px solid #edf2f7; padding: 10px 0; }}
        .qa-item:first-child {{ border-top: 0; }}
        .qa-item summary {{ cursor: pointer; font-weight: 650; overflow-wrap: anywhere; }}
        .qa-item p, .review-item p {{ line-height: 1.65; color: #3e4c59; overflow-wrap: anywhere; }}
        .review-item {{ border-top: 1px solid #edf2f7; padding: 12px 0; }}
        .review-item:first-child {{ border-top: 0; }}
        .review-meta {{ display: flex; flex-wrap: wrap; justify-content: space-between; gap: 10px; color: #53606f; overflow-wrap: anywhere; }}
        @media (max-width: 720px) {{
            .page {{ padding: 12px; }}
            .hero {{ grid-template-columns: 1fr; }}
            .title {{ font-size: 21px; }}
            th {{ width: 104px; }}
            th, td {{ padding: 8px 6px; }}
        }}
    </style>
</head>
<body>
    <main class="page">
        <section class="hero section">
            <div class="media">
                {f'<img src="{image_url}" alt="{title}">' if image_url else '无图'}
            </div>
            <div>
                <h1 class="title">{title}</h1>
                <div class="meta-line">
                    <span class="pill">品牌：{brand}</span>
                    <span class="pill">分类：{category}{' / ' + sub_category if sub_category else ''}</span>
                    <span class="pill">商品编号：{escape(str(product.get('product_id') or ''))}</span>
                    {f'<span class="pill">当前规格：{escape(selected_sku_id)}</span>' if selected_sku_id else ''}
                </div>
                <div class="price">{_format_price((selected_sku or {}).get("price") if selected_sku else product.get("base_price"))}</div>
                {f'<p class="desc">{marketing_desc}</p>' if marketing_desc else ''}
            </div>
        </section>

        <section class="section">
            <h2>基础信息</h2>
            <table>{_render_kv_table(product, skip_keys={"skus", "faqs", "reviews"})}</table>
        </section>

        <section class="section">
            <h2>规格</h2>
            <div class="sku-grid">{''.join(sku_cards) or '<p>暂无规格信息</p>'}</div>
        </section>

        {f'''
        <section class="section" id="selected-sku">
            <h2>当前选中规格</h2>
            <table>{selected_attrs}{selected_attr_rows}</table>
        </section>
        ''' if selected_sku else ''}

        <section class="section">
            <h2>FAQ</h2>
            {faq_items or '<p>暂无 FAQ 信息</p>'}
        </section>

        <section class="section">
            <h2>用户评价</h2>
            {review_items or '<p>暂无评价信息</p>'}
        </section>
    </main>
    <script>
        var scrollKey = 'product-page-scroll:' + window.location.pathname;
        var savedScroll = sessionStorage.getItem(scrollKey);
        if (savedScroll !== null) {{
            window.requestAnimationFrame(function() {{
                window.scrollTo(0, Number(savedScroll) || 0);
                sessionStorage.removeItem(scrollKey);
            }});
        }}
        document.querySelectorAll('.sku-card').forEach(function(card) {{
            card.addEventListener('click', function() {{
                var skuId = card.getAttribute('data-sku-id');
                var url = new URL(window.location.href);
                url.searchParams.set('sku_id', skuId);
                sessionStorage.setItem(scrollKey, String(window.scrollY || window.pageYOffset || 0));
                window.location.href = url.toString();
            }});
        }});
    </script>
</body>
</html>
"""
        return HTMLResponse(content=html_content)
        
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
        from fastapi.responses import FileResponse
        image_file = _resolve_product_image_file(image_path)
        
        return FileResponse(image_file)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("图片访问接口错误: %s", e)
        raise HTTPException(status_code=500, detail="服务器内部错误")
