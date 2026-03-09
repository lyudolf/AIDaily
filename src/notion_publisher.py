"""
src/notion_publisher.py — Step 3: Notion API를 활용한 DB 속성 및 본문 적재 로직

추출된 ArticleInsight를 Notion Database에 새 페이지로 생성합니다.
중복 적재 방지를 위해 Source URL 기반 쿼리를 선행합니다.
"""

import httpx
from notion_client import AsyncClient as NotionAsyncClient
from notion_client.errors import APIResponseError

from config import NOTION_TOKEN, NOTION_DATABASE_ID, NOTION_API_TIMEOUT_SECONDS
from schemas import ArticleInsight
from src.utils import api_retry_decorator, RateLimitError, TransientAPIError, setup_logger

logger = setup_logger("aidaily.notion")

# Notion 비동기 클라이언트 (페이지 생성용)
notion = NotionAsyncClient(auth=NOTION_TOKEN)

# Notion API 직접 호출용 상수
_NOTION_API_BASE = "https://api.notion.com/v1"
_NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}


# ──────────────────────────────────────────────
# 중복 방지
# ──────────────────────────────────────────────
@api_retry_decorator()
async def check_duplicate(source_url: str) -> bool:
    """
    Source URL 기준으로 Notion DB에 이미 동일 페이지가 있는지 확인합니다.

    notion-client v2.7+에서 databases.query가 제거되어
    httpx로 Notion REST API를 직접 호출합니다.

    Returns:
        True면 중복 (이미 존재), False면 신규
    """
    url = f"{_NOTION_API_BASE}/databases/{NOTION_DATABASE_ID}/query"
    payload = {
        "filter": {
            "property": "Source URL",
            "url": {
                "equals": source_url,
            },
        },
        "page_size": 1,
    }

    async with httpx.AsyncClient(timeout=NOTION_API_TIMEOUT_SECONDS) as client:
        response = await client.post(url, headers=_NOTION_HEADERS, json=payload)

    if response.status_code == 429:
        raise RateLimitError("[Notion] Rate Limit 초과 (429)")
    if response.status_code >= 500:
        raise TransientAPIError(f"[Notion] 서버 오류 ({response.status_code})")
    if response.status_code != 200:
        logger.error(f"[Notion] DB 쿼리 실패 ({response.status_code}): {response.text[:300]}")
        return False  # 쿼리 실패 시 중복 아님으로 처리 (적재 시도)

    result = response.json()
    if result.get("results"):
        logger.info(f"[Notion] 이미 존재하는 URL입니다. 스킵: {source_url}")
        return True
    return False


# ──────────────────────────────────────────────
# Property 빌더
# ──────────────────────────────────────────────
def _build_properties(insight: ArticleInsight, source_url: str) -> dict:
    """ArticleInsight를 Notion DB Property 형식으로 변환합니다."""
    properties: dict = {
        "Title": {
            "title": [{"text": {"content": insight.title[:2000]}}]
        },
        "Author": {
            "rich_text": [{"text": {"content": insight.author[:2000]}}]
        },
        "License": {
            "select": {"name": insight.license[:100]}
        },
        "Source URL": {
            "url": source_url,
        },
    }

    # URL 필드는 값이 있을 때만 설정 (null이면 Notion이 빈 값으로 처리)
    if insight.github_url:
        properties["GitHub URL"] = {"url": insight.github_url}
    if insight.demo_url:
        properties["Demo URL"] = {"url": insight.demo_url}

    return properties


# ──────────────────────────────────────────────
# 본문(Block Content) 빌더
# ──────────────────────────────────────────────
def _build_page_blocks(insight: ArticleInsight) -> list[dict]:
    """
    ArticleInsight의 benchmarks와 business_impact를
    Notion Page 본문 블록으로 구성합니다.
    """
    blocks: list[dict] = []

    # ── 📊 Benchmarks / Key Highlights ──
    blocks.append({
        "object": "block",
        "type": "heading_2",
        "heading_2": {
            "rich_text": [{"text": {"content": "📊 Key Highlights & Benchmarks"}}]
        },
    })

    for item in insight.benchmarks:
        blocks.append({
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"text": {"content": item[:2000]}}]
            },
        })

    # 빈 줄 구분
    blocks.append({
        "object": "block",
        "type": "divider",
        "divider": {},
    })

    # ── 💡 Business Impact ──
    blocks.append({
        "object": "block",
        "type": "heading_2",
        "heading_2": {
            "rich_text": [{"text": {"content": "💡 Business Impact Analysis"}}]
        },
    })

    # business_impact를 줄 단위로 분리하여 paragraph 블록으로
    for line in insight.business_impact.split("\n"):
        line = line.strip()
        if line:
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"text": {"content": line[:2000]}}]
                },
            })

    return blocks


# ──────────────────────────────────────────────
# 메인 적재 함수
# ──────────────────────────────────────────────
@api_retry_decorator()
async def publish_to_notion(
    insight: ArticleInsight, source_url: str
) -> str | None:
    """
    ArticleInsight를 Notion Database에 새 페이지로 적재합니다.

    notion-client 라이브러리 대신 httpx로 Notion REST API를 직접 호출합니다.

    Args:
        insight: 추출된 인사이트 데이터
        source_url: 원본 URL (중복 방지 키)

    Returns:
        생성된 페이지 ID, 중복이면 None
    """
    # 1) 중복 체크 (main.py에서 선행하지만, 안전을 위해 이중 체크)
    if await check_duplicate(source_url):
        return None

    # 2) Property + Block 구성
    properties = _build_properties(insight, source_url)
    children = _build_page_blocks(insight)

    # 3) 페이지 생성 — httpx 직접 호출
    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": properties,
        "children": children,
    }

    async with httpx.AsyncClient(timeout=NOTION_API_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{_NOTION_API_BASE}/pages",
            headers=_NOTION_HEADERS,
            json=payload,
        )

    if response.status_code == 429:
        raise RateLimitError("[Notion] Rate Limit 초과 (429)")
    if response.status_code >= 500:
        raise TransientAPIError(f"[Notion] 서버 오류 ({response.status_code})")
    if response.status_code != 200:
        logger.error(
            f"[Notion] 페이지 생성 실패 ({response.status_code}): "
            f"{response.text[:300]}"
        )
        return None

    page_data = response.json()
    page_id = page_data.get("id", "unknown")
    logger.info(
        f"[Notion] 페이지 생성 완료: '{insight.title}' (ID: {page_id})"
    )
    return page_id
