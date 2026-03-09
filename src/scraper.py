"""
src/scraper.py — Step 1: Jina Reader API 호출 및 텍스트 정제 로직

Jina Reader API를 사용하여 웹페이지를 순수 Markdown으로 변환하고,
불필요한 노이즈를 정규식으로 1차 정제합니다.
"""

import re

import httpx

from config import JINA_API_KEY, JINA_BASE_URL, JINA_TIMEOUT_SECONDS
from src.utils import api_retry_decorator, check_http_response, setup_logger

logger = setup_logger("aidaily.scraper")


def _clean_markdown(raw_md: str) -> str:
    """
    Jina Reader가 반환한 Markdown에서 토큰 낭비를 유발하는 노이즈를 제거합니다.

    - 네비게이션 잔여물 (연속된 짧은 링크 라인)
    - 과도한 빈 줄 (3줄 이상 → 2줄)
    - 이미지 alt 텍스트 내 긴 base64/URL 제거
    - 불필요한 HTML 주석 제거
    """
    # HTML 주석 제거
    text = re.sub(r"<!--.*?-->", "", raw_md, flags=re.DOTALL)

    # base64 인코딩된 이미지 참조 제거 (토큰 낭비 심각)
    text = re.sub(r"!\[([^\]]*)\]\(data:image/[^)]+\)", r"[\1](image)", text)

    # 과도한 빈 줄 정리 (3줄 이상 → 2줄)
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 앞뒤 공백 정리
    text = text.strip()

    return text


@api_retry_decorator()
async def fetch_markdown(url: str) -> str | None:
    """
    Jina Reader API를 통해 URL의 웹페이지를 Markdown으로 변환합니다.

    Args:
        url: 대상 웹페이지 URL

    Returns:
        정제된 Markdown 텍스트, 또는 실패 시 None
    """
    jina_url = f"{JINA_BASE_URL}/{url}"

    headers: dict[str, str] = {
        "Accept": "text/markdown",
    }
    if JINA_API_KEY:
        headers["Authorization"] = f"Bearer {JINA_API_KEY}"
    # JSON 모드로 요청하면 title, content 등 구조화된 응답을 받을 수 있음
    headers["X-Return-Format"] = "markdown"

    async with httpx.AsyncClient(timeout=JINA_TIMEOUT_SECONDS) as client:
        try:
            response = await client.get(jina_url, headers=headers)
        except httpx.TimeoutException:
            logger.error(f"[Jina] 타임아웃 발생: {url}")
            raise
        except httpx.ConnectError:
            logger.error(f"[Jina] 연결 실패: {url}")
            return None

    # HTTP 상태 검사 (429, 5xx는 재시도, 404는 스킵)
    check_http_response(response, "Jina")

    if response.status_code != 200:
        return None

    raw_markdown = response.text
    if not raw_markdown or len(raw_markdown.strip()) < 50:
        logger.warning(f"[Jina] 추출된 콘텐츠가 너무 짧습니다: {url}")
        return None

    cleaned = _clean_markdown(raw_markdown)
    logger.info(
        f"[Jina] 파싱 완료: {url} "
        f"(원본 {len(raw_markdown):,}자 → 정제 {len(cleaned):,}자)"
    )
    return cleaned
