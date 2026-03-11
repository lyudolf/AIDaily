"""
src/briefing_generator.py — Step 3.5: 한글 브리핑 생성

ArticleInsight에서 한글 요약 + 기획자 관점 가이드 질문 3개를 생성합니다.
"""

import json

from schemas import ArticleInsight, KoreanBriefing
from src.extractor import _call_gemini
from src.utils import setup_logger

logger = setup_logger("aidaily.briefing")

BRIEFING_PROMPT = """\
당신은 AI 업계 전문 기획자입니다.
아래 제공된 기사 인사이트 데이터를 분석하여, 한국어로 브리핑 자료를 작성하십시오.

## 작성 규칙
1. `summary_kr`: 기사의 핵심 내용을 한국어 3~4줄로 간결하게 요약하세요.
   - 기술적 세부사항보다 "무엇이 왜 중요한지"에 초점
   - 비전공자도 이해할 수 있는 수준으로 작성
2. `guide_questions`: 이 기사에 대해 기획자/비즈니스 관점에서 의견을 작성할 수 있도록
   돕는 한국어 질문을 정확히 3개 생성하세요.
   - 단순 팩트 질문(X) → 관점/의견을 유도하는 질문(O)
   - 예시: "이 기술이 현업에 도입된다면 가장 먼저 영향받을 산업은?"

## JSON 스키마
{schema}

## 인사이트 데이터
- 제목: {title}
- 저자: {author}
- 벤치마크/핵심 내용: {benchmarks}
- 비즈니스 임팩트: {business_impact}
"""


async def generate_briefing(insight: ArticleInsight) -> KoreanBriefing | None:
    """
    ArticleInsight에서 한글 브리핑(요약 + 질문 3개)을 생성합니다.

    Args:
        insight: 추출된 인사이트 데이터

    Returns:
        KoreanBriefing, 또는 실패 시 None
    """
    schema_str = json.dumps(
        KoreanBriefing.model_json_schema(), indent=2, ensure_ascii=False
    )

    prompt = BRIEFING_PROMPT.format(
        schema=schema_str,
        title=insight.title,
        author=insight.author,
        benchmarks="\n".join(f"- {b}" for b in insight.benchmarks),
        business_impact=insight.business_impact,
    )

    logger.info("[Briefing] 한글 브리핑 생성 요청 중...")

    try:
        raw = await _call_gemini(prompt)
        data = json.loads(raw)
        briefing = KoreanBriefing.model_validate(data)
        logger.info(f"[Briefing] 한글 브리핑 생성 완료 (질문 {len(briefing.guide_questions)}개)")
        return briefing
    except Exception as e:
        logger.error(f"[Briefing] 한글 브리핑 생성 실패: {e}")
        return None
