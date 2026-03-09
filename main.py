"""
main.py — 전체 파이프라인 오케스트레이션 엔트리포인트

URL 리스트를 입력받아 Step 1→2→3을 순차 실행합니다.
개별 URL 실패 시 파이프라인을 중단하지 않고 스킵합니다.

Usage:
    # urls.yaml 파일 사용
    python main.py

    # CLI 인자로 URL 직접 전달
    python main.py --urls "https://example.com/article1" "https://example.com/article2"
"""

import argparse
import asyncio
import time
from pathlib import Path

import yaml

from config import validate_env, REQUEST_DELAY_SECONDS
from src.scraper import fetch_markdown
from src.extractor import extract_insights
from src.notion_publisher import publish_to_notion, check_duplicate
from src.utils import setup_logger

logger = setup_logger("aidaily.main")


def load_urls_from_yaml(filepath: str = "urls.yaml") -> list[str]:
    """urls.yaml에서 URL 리스트를 로드합니다."""
    path = Path(filepath)
    if not path.exists():
        logger.error(f"URL 파일을 찾을 수 없습니다: {filepath}")
        logger.info("urls.yaml.example을 참고하여 urls.yaml을 생성해 주세요.")
        return []

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data or "urls" not in data:
        logger.error(f"urls.yaml에 'urls' 키가 없습니다.")
        return []

    urls = data["urls"]
    if not isinstance(urls, list) or not urls:
        logger.error("urls.yaml의 urls 항목이 비어있거나 올바르지 않습니다.")
        return []

    return [str(url).strip() for url in urls if url]


async def process_single_url(url: str) -> str:
    """
    단일 URL에 대해 3-Step 파이프라인을 수행합니다.

    Returns:
        결과 상태 문자열: "success", "skipped_duplicate", "failed_*"
    """
    logger.info(f"{'='*60}")
    logger.info(f"처리 시작: {url}")
    logger.info(f"{'='*60}")

    # ── Step 0: 중복 체크 (Jina/Gemini 호출 전 선행) ──
    try:
        is_dup = await check_duplicate(url)
        if is_dup:
            return "skipped_duplicate"
    except Exception as e:
        logger.warning(f"[Step 0] 중복 체크 실패, 계속 진행: {e}")

    # ── Step 1: Jina Reader API ──
    try:
        markdown = await fetch_markdown(url)
    except Exception as e:
        logger.error(f"[Step 1] Jina 파싱 실패: {e}")
        return "failed_scraping"

    if not markdown:
        logger.warning(f"[Step 1] Markdown 추출 실패. 스킵: {url}")
        return "failed_scraping"

    logger.info(f"[Step 1] [OK] Markdown 추출 완료 ({len(markdown):,}자)")

    # ── Step 2: Gemini API ──
    try:
        insight = await extract_insights(markdown)
    except Exception as e:
        logger.error(f"[Step 2] Gemini 추출 실패: {e}")
        return "failed_extraction"

    if not insight:
        logger.warning(f"[Step 2] 인사이트 추출 실패. 스킵: {url}")
        return "failed_extraction"

    logger.info(f"[Step 2] [OK] 인사이트 추출 완료: {insight.title}")

    # ── Step 3: Notion API ──
    try:
        page_id = await publish_to_notion(insight, source_url=url)
    except Exception as e:
        logger.error(f"[Step 3] Notion 적재 실패: {e}")
        return "failed_publishing"

    if page_id is None:
        return "skipped_duplicate"

    logger.info(f"[Step 3] [OK] Notion 적재 완료 (Page ID: {page_id})")
    return "success"


async def run_pipeline(urls: list[str]) -> None:
    """
    URL 리스트에 대해 순차적으로 파이프라인을 실행하고 결과를 요약합니다.
    """
    if not urls:
        logger.error("처리할 URL이 없습니다. urls.yaml 또는 --urls 인자를 확인해주세요.")
        return

    logger.info(f">> aidaily 파이프라인 시작 -- 총 {len(urls)}개 URL")
    logger.info("")

    results: dict[str, int] = {
        "success": 0,
        "skipped_duplicate": 0,
        "failed_scraping": 0,
        "failed_extraction": 0,
        "failed_publishing": 0,
    }

    for i, url in enumerate(urls, 1):
        logger.info(f"\n[{i}/{len(urls)}] 처리 중...")
        status = await process_single_url(url)
        results[status] = results.get(status, 0) + 1

        # Rate Limit 방지: 다음 URL 전 딜레이
        if i < len(urls):
            logger.info(
                f"[WAIT] Rate Limit 방지 대기: {REQUEST_DELAY_SECONDS}초..."
            )
            await asyncio.sleep(REQUEST_DELAY_SECONDS)

    # ── 결과 요약 ──
    logger.info(f"\n{'='*60}")
    logger.info("파이프라인 실행 결과 요약")
    logger.info(f"{'='*60}")
    logger.info(f"  [OK]   성공 (Notion 적재):  {results['success']}건")
    logger.info(f"  [SKIP] 스킵 (중복):         {results['skipped_duplicate']}건")
    logger.info(f"  [FAIL] 실패 (스크래핑):     {results['failed_scraping']}건")
    logger.info(f"  [FAIL] 실패 (Gemini 추출):  {results['failed_extraction']}건")
    logger.info(f"  [FAIL] 실패 (Notion 적재):  {results['failed_publishing']}건")
    logger.info(f"{'='*60}")
    total = sum(results.values())
    logger.info(f"  총: {total}건 처리 완료")


def main():
    parser = argparse.ArgumentParser(
        description="aidaily — AI 뉴스 파싱 및 Notion 자동 적재 파이프라인"
    )
    parser.add_argument(
        "--urls",
        nargs="+",
        help="처리할 URL 리스트 (직접 입력). 미지정 시 urls.yaml 사용.",
    )
    parser.add_argument(
        "--file",
        default="urls.yaml",
        help="URL 리스트 YAML 파일 경로 (기본값: urls.yaml)",
    )
    args = parser.parse_args()

    # 환경변수 검증
    validate_env()

    # URL 리스트 결정
    if args.urls:
        urls = args.urls
        logger.info(f"CLI 인자에서 {len(urls)}개 URL 로드")
    else:
        urls = load_urls_from_yaml(args.file)
        if urls:
            logger.info(f"{args.file}에서 {len(urls)}개 URL 로드")

    # 파이프라인 실행
    asyncio.run(run_pipeline(urls))


if __name__ == "__main__":
    main()
