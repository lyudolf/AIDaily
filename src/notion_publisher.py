"""
src/notion_publisher.py — Notion API를 활용한 DB 속성 및 본문 적재 로직

Phase 1: ArticleInsight를 Notion Database에 새 페이지로 생성 (Step 3)
Phase 2: 한글 브리핑 업데이트 (Step 3.5), 코멘트 기반 초안 처리 (Phase B)
"""

import json

import httpx

from config import NOTION_TOKEN, NOTION_DATABASE_ID, NOTION_API_TIMEOUT_SECONDS
from schemas import ArticleInsight, KoreanBriefing, MediumDraft
from src.utils import api_retry_decorator, RateLimitError, TransientAPIError, setup_logger

logger = setup_logger("aidaily.notion")

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
    """Source URL 기준으로 Notion DB에 이미 동일 페이지가 있는지 확인합니다."""
    url = f"{_NOTION_API_BASE}/databases/{NOTION_DATABASE_ID}/query"
    payload = {
        "filter": {
            "property": "Source URL",
            "url": {"equals": source_url},
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
        return False

    result = response.json()
    if result.get("results"):
        logger.info(f"[Notion] 이미 존재하는 URL입니다. 스킵: {source_url}")
        return True
    return False


# ──────────────────────────────────────────────
# Property 빌더 (Phase 2: 속성 7개로 정리)
# ──────────────────────────────────────────────
def _build_properties(insight: ArticleInsight, source_url: str) -> dict:
    """ArticleInsight를 Notion DB Property 형식으로 변환합니다.

    속성: Title, Author, Source URL, Publish Status,
          Korean Summary, Guide Questions, My Take(KR)
    (License, GitHub URL, Demo URL은 본문(Block)으로 이동)
    """
    return {
        "Title": {
            "title": [{"text": {"content": insight.title[:2000]}}]
        },
        "Author": {
            "rich_text": [{"text": {"content": insight.author[:2000]}}]
        },
        "Source URL": {
            "url": source_url,
        },
        "Publish Status": {
            "select": {"name": "pending"},
        },
    }


# ──────────────────────────────────────────────
# 본문(Block Content) 빌더
# ──────────────────────────────────────────────
def _build_page_blocks(insight: ArticleInsight) -> list[dict]:
    """ArticleInsight의 상세 정보를 Notion Page 본문 블록으로 구성합니다."""
    blocks: list[dict] = []

    # ── Links (GitHub, Demo, License) ──
    links_parts = []
    if insight.github_url:
        links_parts.append(f"GitHub: {insight.github_url}")
    if insight.demo_url:
        links_parts.append(f"Demo: {insight.demo_url}")
    if insight.license and insight.license != "N/A":
        links_parts.append(f"License: {insight.license}")

    if links_parts:
        blocks.append({
            "object": "block",
            "type": "callout",
            "callout": {
                "rich_text": [{"text": {"content": " | ".join(links_parts)}}],
                "icon": {"emoji": "🔗"},
            },
        })

    # ── Key Highlights & Benchmarks ──
    blocks.append({
        "object": "block",
        "type": "heading_2",
        "heading_2": {
            "rich_text": [{"text": {"content": "Key Highlights & Benchmarks"}}]
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

    blocks.append({"object": "block", "type": "divider", "divider": {}})

    # ── Business Impact ──
    blocks.append({
        "object": "block",
        "type": "heading_2",
        "heading_2": {
            "rich_text": [{"text": {"content": "Business Impact Analysis"}}]
        },
    })

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
# Step 3: 메인 적재 함수
# ──────────────────────────────────────────────
@api_retry_decorator()
async def publish_to_notion(
    insight: ArticleInsight, source_url: str
) -> str | None:
    """ArticleInsight를 Notion Database에 새 페이지로 적재합니다."""
    if await check_duplicate(source_url):
        return None

    properties = _build_properties(insight, source_url)
    children = _build_page_blocks(insight)

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
    logger.info(f"[Notion] 페이지 생성 완료: '{insight.title}' (ID: {page_id})")
    return page_id


# ──────────────────────────────────────────────
# Step 3.5: 한글 브리핑 업데이트
# ──────────────────────────────────────────────
@api_retry_decorator()
async def update_briefing(page_id: str, briefing: KoreanBriefing) -> bool:
    """Notion 페이지에 한글 요약 + 가이드 질문을 업데이트합니다."""
    questions_text = "\n".join(
        f"{i+1}. {q}" for i, q in enumerate(briefing.guide_questions)
    )

    payload = {
        "properties": {
            "Korean Summary": {
                "rich_text": [{"text": {"content": briefing.summary_kr[:2000]}}]
            },
            "Guide Questions": {
                "rich_text": [{"text": {"content": questions_text[:2000]}}]
            },
        }
    }

    async with httpx.AsyncClient(timeout=NOTION_API_TIMEOUT_SECONDS) as client:
        response = await client.patch(
            f"{_NOTION_API_BASE}/pages/{page_id}",
            headers=_NOTION_HEADERS,
            json=payload,
        )

    if response.status_code == 429:
        raise RateLimitError("[Notion] Rate Limit 초과 (429)")
    if response.status_code != 200:
        logger.error(f"[Notion] 브리핑 업데이트 실패 ({response.status_code}): {response.text[:300]}")
        return False

    logger.info(f"[Notion] 한글 브리핑 업데이트 완료 (Page ID: {page_id})")
    return True


# ──────────────────────────────────────────────
# Phase B: 코멘트 작성된 글 조회
# ──────────────────────────────────────────────
@api_retry_decorator()
async def fetch_ready_pages() -> list[dict]:
    """
    Notion DB에서 Publish Status = 'ready'인 페이지를 조회합니다.

    Returns:
        [{"page_id", "title", "my_take_kr", "insight_data"}] 형태의 리스트
    """
    url = f"{_NOTION_API_BASE}/databases/{NOTION_DATABASE_ID}/query"
    payload = {
        "filter": {
            "property": "Publish Status",
            "select": {"equals": "ready"},
        },
    }

    async with httpx.AsyncClient(timeout=NOTION_API_TIMEOUT_SECONDS) as client:
        response = await client.post(url, headers=_NOTION_HEADERS, json=payload)

    if response.status_code == 429:
        raise RateLimitError("[Notion] Rate Limit 초과 (429)")
    if response.status_code != 200:
        logger.error(f"[Notion] ready 페이지 조회 실패 ({response.status_code})")
        return []

    results = response.json().get("results", [])
    pages = []

    for page in results:
        props = page.get("properties", {})

        # 제목 추출
        title_parts = props.get("Title", {}).get("title", [])
        title = title_parts[0]["plain_text"] if title_parts else "Untitled"

        # 한글 코멘트 추출
        my_take_parts = props.get("My Take(KR)", {}).get("rich_text", [])
        my_take_kr = my_take_parts[0]["plain_text"] if my_take_parts else ""

        # Author 추출
        author_parts = props.get("Author", {}).get("rich_text", [])
        author = author_parts[0]["plain_text"] if author_parts else "Unknown"

        # Source URL 추출
        source_url = props.get("Source URL", {}).get("url", "")

        if not my_take_kr.strip():
            logger.warning(f"[Notion] '{title}' - My Take(KR)이 비어있습니다. 스킵.")
            continue

        pages.append({
            "page_id": page["id"],
            "title": title,
            "author": author,
            "source_url": source_url,
            "my_take_kr": my_take_kr,
        })

    logger.info(f"[Notion] ready 상태 페이지 {len(pages)}건 발견")
    return pages


# ──────────────────────────────────────────────
# Phase B: 본문에서 인사이트 재구성 (블록 읽기)
# ──────────────────────────────────────────────
@api_retry_decorator()
async def fetch_page_blocks(page_id: str) -> str:
    """페이지 본문 블록들을 텍스트로 추출합니다."""
    url = f"{_NOTION_API_BASE}/blocks/{page_id}/children?page_size=100"

    async with httpx.AsyncClient(timeout=NOTION_API_TIMEOUT_SECONDS) as client:
        response = await client.get(url, headers=_NOTION_HEADERS)

    if response.status_code != 200:
        return ""

    blocks = response.json().get("results", [])
    texts = []
    for block in blocks:
        block_type = block.get("type", "")
        content = block.get(block_type, {})
        rich_texts = content.get("rich_text", [])
        for rt in rich_texts:
            texts.append(rt.get("plain_text", ""))

    return "\n".join(texts)


# ──────────────────────────────────────────────
# Phase B: 초안을 Notion 본문에 저장
# ──────────────────────────────────────────────
@api_retry_decorator()
async def save_draft_to_page(page_id: str, draft: MediumDraft) -> bool:
    """영문 초안을 Notion 페이지 본문에 추가하고 상태를 draft_created로 변경합니다."""

    # 1) 본문에 초안 블록 추가
    draft_blocks = [
        {"object": "block", "type": "divider", "divider": {}},
        {
            "object": "block",
            "type": "heading_1",
            "heading_1": {
                "rich_text": [{"text": {"content": "Medium Draft"}}]
            },
        },
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"text": {"content": draft.title}}]
            },
        },
    ]

    # Notion 블록은 rich_text 최대 2000자이므로 분할
    body = draft.body_markdown
    while body:
        chunk = body[:2000]
        body = body[2000:]
        draft_blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"text": {"content": chunk}}]
            },
        })

    # 태그 추가
    if draft.tags:
        draft_blocks.append({
            "object": "block",
            "type": "callout",
            "callout": {
                "rich_text": [{"text": {"content": f"Tags: {', '.join(draft.tags)}"}}],
                "icon": {"emoji": "🏷️"},
            },
        })

    # 본문 블록 추가
    async with httpx.AsyncClient(timeout=NOTION_API_TIMEOUT_SECONDS) as client:
        resp_blocks = await client.patch(
            f"{_NOTION_API_BASE}/blocks/{page_id}/children",
            headers=_NOTION_HEADERS,
            json={"children": draft_blocks},
        )

    if resp_blocks.status_code not in (200, 201):
        logger.error(f"[Notion] 초안 블록 추가 실패 ({resp_blocks.status_code})")
        return False

    # 2) Publish Status → draft_created
    async with httpx.AsyncClient(timeout=NOTION_API_TIMEOUT_SECONDS) as client:
        resp_status = await client.patch(
            f"{_NOTION_API_BASE}/pages/{page_id}",
            headers=_NOTION_HEADERS,
            json={
                "properties": {
                    "Publish Status": {"select": {"name": "draft_created"}},
                }
            },
        )

    if resp_status.status_code != 200:
        logger.error(f"[Notion] 상태 업데이트 실패 ({resp_status.status_code})")
        return False

    logger.info(f"[Notion] 초안 저장 완료 + 상태 변경: draft_created (Page ID: {page_id})")
    return True
