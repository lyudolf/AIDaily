"""
main.py — 전체 파이프라인 오케스트레이션 엔트리포인트

하루 1회 실행으로 두 가지 작업을 순차 처리합니다:
  Phase A: 새 URL 수집 → 인사이트 추출 → Notion 적재 → 한글 브리핑 생성
  Phase B: 코멘트 작성 완료된 글의 영문 블로그 초안 자동 생성

Usage:
    # urls.yaml 파일 사용
    python main.py

    # CLI 인자로 URL 직접 전달
    python main.py --urls "https://example.com/article1"

    # Phase B만 실행 (초안 생성만)
    python main.py --draft-only
"""

import argparse
import asyncio
import time
from pathlib import Path

import yaml

from config import validate_env, REQUEST_DELAY_SECONDS
from schemas import ArticleInsight
from src.scraper import fetch_markdown
from src.extractor import extract_insights
from src.briefing_generator import generate_briefing
from src.draft_composer import compose_draft
from src.notion_publisher import (
    publish_to_notion,
    check_duplicate,
    update_briefing,
    fetch_ready_pages,
    fetch_page_blocks,
    save_draft_to_page,
)
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


# ──────────────────────────────────────────────
# Phase A: 새 글 수집 (Step 1 → 2 → 3 → 3.5)
# ──────────────────────────────────────────────
async def process_single_url(url: str) -> str:
    """단일 URL에 대해 수집 파이프라인을 수행합니다."""
    logger.info(f"{'='*60}")
    logger.info(f"처리 시작: {url}")
    logger.info(f"{'='*60}")

    # ── Step 0: 중복 체크 ──
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

    # ── Step 3.5: 한글 브리핑 생성 ──
    try:
        briefing = await generate_briefing(insight)
        if briefing:
            await update_briefing(page_id, briefing)
            logger.info(f"[Step 3.5] [OK] 한글 브리핑 업데이트 완료")
        else:
            logger.warning(f"[Step 3.5] 한글 브리핑 생성 실패. 건너뜀.")
    except Exception as e:
        logger.warning(f"[Step 3.5] 한글 브리핑 실패 (치명적 아님): {e}")

    return "success"


# ──────────────────────────────────────────────
# Phase B: 코멘트 작성된 글 → 영문 초안 생성
# ──────────────────────────────────────────────
async def process_ready_drafts() -> dict[str, int]:
    """Notion에서 ready 상태 페이지를 찾아 영문 초안을 생성합니다."""
    logger.info(f"\n{'='*60}")
    logger.info("Phase B: 코멘트 작성 완료 글 → 영문 초안 생성")
    logger.info(f"{'='*60}")

    results = {"draft_success": 0, "draft_failed": 0}

    ready_pages = await fetch_ready_pages()
    if not ready_pages:
        logger.info("[Phase B] ready 상태 글이 없습니다.")
        return results

    for page in ready_pages:
        title = page["title"]
        logger.info(f"\n[Draft] 초안 생성 시작: '{title}'")

        # 본문에서 인사이트 정보 읽기
        body_text = await fetch_page_blocks(page["page_id"])

        # ArticleInsight 재구성 (속성 + 본문 텍스트)
        insight = ArticleInsight(
            title=page["title"],
            author=page["author"],
            benchmarks=[line.strip() for line in body_text.split("\n") if line.strip()],
            business_impact=body_text[-500:] if body_text else "",
        )

        try:
            draft = await compose_draft(insight, page["my_take_kr"])
        except Exception as e:
            logger.error(f"[Draft] 초안 생성 실패: {e}")
            results["draft_failed"] += 1
            continue

        if not draft:
            results["draft_failed"] += 1
            continue

        # Notion 본문에 초안 저장
        saved = await save_draft_to_page(page["page_id"], draft)
        if saved:
            logger.info(f"[Draft] [OK] 초안 저장 완료: '{draft.title}' ({len(draft.body_markdown.split())} words)")
            results["draft_success"] += 1
        else:
            results["draft_failed"] += 1

    return results


# ──────────────────────────────────────────────
# 파이프라인 실행
# ──────────────────────────────────────────────
async def run_pipeline(urls: list[str], draft_only: bool = False) -> None:
    """Phase A + Phase B를 순차 실행합니다."""

    phase_a_results: dict[str, int] = {
        "success": 0,
        "skipped_duplicate": 0,
        "failed_scraping": 0,
        "failed_extraction": 0,
        "failed_publishing": 0,
    }

    # ── Phase A: 새 글 수집 ──
    if not draft_only:
        if not urls:
            logger.info("Phase A: 처리할 URL이 없습니다. Phase B로 진행합니다.")
        else:
            logger.info(f">> Phase A: 새 글 수집 시작 -- 총 {len(urls)}개 URL")
            logger.info("")

            for i, url in enumerate(urls, 1):
                logger.info(f"\n[{i}/{len(urls)}] 처리 중...")
                status = await process_single_url(url)
                phase_a_results[status] = phase_a_results.get(status, 0) + 1

                if i < len(urls):
                    logger.info(f"[WAIT] Rate Limit 방지 대기: {REQUEST_DELAY_SECONDS}초...")
                    await asyncio.sleep(REQUEST_DELAY_SECONDS)

    # ── Phase B: 초안 생성 ──
    phase_b_results = await process_ready_drafts()

    # ── 결과 요약 ──
    logger.info(f"\n{'='*60}")
    logger.info("파이프라인 실행 결과 요약")
    logger.info(f"{'='*60}")

    if not draft_only:
        logger.info(f"  [Phase A] 새 글 수집:")
        logger.info(f"    [OK]   성공:        {phase_a_results['success']}건")
        logger.info(f"    [SKIP] 중복 스킵:   {phase_a_results['skipped_duplicate']}건")
        logger.info(f"    [FAIL] 스크래핑:    {phase_a_results['failed_scraping']}건")
        logger.info(f"    [FAIL] Gemini:      {phase_a_results['failed_extraction']}건")
        logger.info(f"    [FAIL] Notion:      {phase_a_results['failed_publishing']}건")

    logger.info(f"  [Phase B] 초안 생성:")
    logger.info(f"    [OK]   성공:        {phase_b_results['draft_success']}건")
    logger.info(f"    [FAIL] 실패:        {phase_b_results['draft_failed']}건")
    logger.info(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(
        description="aidaily — AI 뉴스 수집 + 영문 초안 생성 파이프라인"
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
    parser.add_argument(
        "--draft-only",
        action="store_true",
        help="Phase B만 실행 (코멘트된 글 초안 생성만)",
    )
    args = parser.parse_args()

    validate_env()

    urls: list[str] = []
    if not args.draft_only:
        if args.urls:
            urls = args.urls
            logger.info(f"CLI 인자에서 {len(urls)}개 URL 로드")
        else:
            urls = load_urls_from_yaml(args.file)
            if urls:
                logger.info(f"{args.file}에서 {len(urls)}개 URL 로드")

    asyncio.run(run_pipeline(urls, draft_only=args.draft_only))


if __name__ == "__main__":
    main()
