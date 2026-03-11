"""
src/draft_composer.py — Phase B: 영문 Medium 블로그 초안 생성

인사이트 데이터 + 사용자 한글 코멘트를 합쳐서
900~1200 words의 영문 블로그 초안을 생성합니다.
"""

import json

from schemas import ArticleInsight, MediumDraft
from src.extractor import _call_gemini
from src.utils import setup_logger

logger = setup_logger("aidaily.composer")

DRAFT_PROMPT = """\
You are an expert AI industry analyst writing for Medium.
Write an engaging, insightful blog post (900-1200 words) based on the data below.

## Writing Guidelines
1. **Title**: Create a compelling, click-worthy title (not the same as the source title).
2. **Intro**: Hook the reader with why this matters. 2-3 sentences.
3. **Key Findings**: Summarize the technical highlights in accessible language.
   Include benchmarks and data points where available.
4. **"My Take" Section**: This is the MOST IMPORTANT section.
   The author provided their personal commentary in Korean (below).
   Translate and expand their thoughts into natural, professional English.
   This should feel like an authentic personal opinion, NOT a translation.
   Use first person ("I think...", "In my view...").
5. **Conclusion**: 2-3 sentences on what to watch for next.
6. **Tags**: Suggest up to 5 relevant Medium tags.

## Tone & Style
- Professional but conversational (Medium style)
- Use subheadings for readability
- Include specific numbers/metrics when available
- Avoid jargon without explanation

## Source Data
- Title: {title}
- Author/Org: {author}
- Key Benchmarks: {benchmarks}
- Business Impact: {business_impact}
- GitHub: {github_url}
- Demo: {demo_url}
- License: {license}

## Author's Commentary (Korean — translate and integrate naturally)
{my_take_kr}

## JSON Schema
{schema}
"""


async def compose_draft(
    insight: ArticleInsight, my_take_kr: str
) -> MediumDraft | None:
    """
    인사이트 + 한글 코멘트로 영문 Medium 블로그 초안을 생성합니다.

    Args:
        insight: 추출된 인사이트 데이터
        my_take_kr: 사용자가 작성한 한글 코멘트

    Returns:
        MediumDraft, 또는 실패 시 None
    """
    schema_str = json.dumps(
        MediumDraft.model_json_schema(), indent=2, ensure_ascii=False
    )

    prompt = DRAFT_PROMPT.format(
        schema=schema_str,
        title=insight.title,
        author=insight.author,
        benchmarks="\n".join(f"- {b}" for b in insight.benchmarks),
        business_impact=insight.business_impact,
        github_url=insight.github_url or "N/A",
        demo_url=insight.demo_url or "N/A",
        license=insight.license,
        my_take_kr=my_take_kr,
    )

    logger.info("[Composer] 영문 블로그 초안 생성 요청 중...")

    try:
        raw = await _call_gemini(prompt)
        data = json.loads(raw)
        draft = MediumDraft.model_validate(data)
        word_count = len(draft.body_markdown.split())
        logger.info(
            f"[Composer] 초안 생성 완료: '{draft.title}' "
            f"({word_count} words, tags: {draft.tags})"
        )
        return draft
    except Exception as e:
        logger.error(f"[Composer] 초안 생성 실패: {e}")
        return None
