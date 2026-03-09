"""
src/extractor.py — Step 2: Gemini API 심층 정보 JSON 추출 로직

Gemini API에 Markdown을 주입하여 정형화된 JSON(ArticleInsight)을 추출합니다.
JSON 파싱 실패 시 에러 메시지를 포함한 교정 요청을 최대 2회 수행합니다.
"""

import json

import google.generativeai as genai
from pydantic import ValidationError

from config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_MAX_CORRECTION_RETRIES,
)
from schemas import ArticleInsight
from src.utils import api_retry_decorator, RateLimitError, TransientAPIError, setup_logger

logger = setup_logger("aidaily.extractor")

# Gemini 클라이언트 초기화
genai.configure(api_key=GEMINI_API_KEY)

EXTRACTION_PROMPT = """\
당신은 AI 업계의 최신 동향을 분석하는 전문 리서처입니다.
아래 제공된 웹페이지의 Markdown 텍스트를 분석하여, 반드시 지정된 JSON 스키마에 맞는 데이터를 추출하십시오.

## 추출 규칙
1. `title`: 이 페이지의 핵심 주제를 명확하게 나타내는 제목을 작성하세요. 원문의 제목이 있으면 그것을 사용하되, 없거나 불명확하면 내용에 기반하여 생성하세요.
2. `author`: 저자, 발표자, 또는 핵심 인물/기관명. 확인 불가시 "Unknown"으로 기입하세요.
3. `github_url`: 깃허브 저장소 링크. 본문에 명시된 것만 추출하세요. 없으면 null.
4. `demo_url`: 데모/체험 페이지 링크. 본문에 명시된 것만 추출하세요. 없으면 null.
5. `license`: 라이선스 정보. 본문에 언급되지 않으면 "N/A".
6. `benchmarks`: 성능 지표, 주요 수치, 업데이트 내용, 또는 핵심 발언을 리스트로 정리하세요. 최소 1개 이상.
7. `business_impact`: 이 기술/뉴스가 실제 비즈니스에 미치는 영향을 기획자 관점에서 3줄로 심층 분석하세요.

## JSON 스키마
{schema}

## 분석 대상 Markdown
{markdown}
"""

CORRECTION_PROMPT = """\
이전 응답에서 JSON 파싱 오류가 발생했습니다. 아래 오류를 확인하고 올바른 JSON을 다시 생성해 주세요.

## 오류 내용
{error}

## 요구 스키마
{schema}

## 이전 응답 (오류 발생)
{previous_response}
"""


@api_retry_decorator()
async def _call_gemini(prompt: str) -> str:
    """
    Gemini API를 호출하고 텍스트 응답을 반환합니다.
    Rate Limit(429), 서버 과부하(503) 시 재시도 가능한 예외를 발생시킵니다.
    """
    model = genai.GenerativeModel(
        GEMINI_MODEL,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.1,  # 정형 추출이므로 낮은 temperature
        ),
    )
    try:
        response = await model.generate_content_async(prompt)

        if not response or not response.text:
            raise TransientAPIError("[Gemini] 빈 응답이 반환되었습니다.")

        return response.text

    except Exception as e:
        error_str = str(e).lower()
        if "429" in error_str or "resource_exhausted" in error_str:
            raise RateLimitError(f"[Gemini] Rate Limit 초과: {e}")
        if "503" in error_str or "overloaded" in error_str:
            raise TransientAPIError(f"[Gemini] 서버 과부하: {e}")
        if "deadline" in error_str or "timeout" in error_str:
            raise TransientAPIError(f"[Gemini] 타임아웃: {e}")
        raise


def _parse_insight(raw_json: str) -> ArticleInsight:
    """JSON 문자열을 ArticleInsight로 파싱합니다."""
    data = json.loads(raw_json)
    return ArticleInsight.model_validate(data)


async def extract_insights(markdown: str) -> ArticleInsight | None:
    """
    Gemini API를 사용해 Markdown에서 ArticleInsight를 추출합니다.

    JSON 파싱 실패 시 에러 메시지를 프롬프트에 포함하여
    최대 GEMINI_MAX_CORRECTION_RETRIES 회 교정 재시도합니다.

    Args:
        markdown: 정제된 Markdown 텍스트

    Returns:
        추출된 ArticleInsight, 또는 완전 실패 시 None
    """
    schema_str = json.dumps(
        ArticleInsight.model_json_schema(), indent=2, ensure_ascii=False
    )

    # 첫 번째 시도
    prompt = EXTRACTION_PROMPT.format(schema=schema_str, markdown=markdown)
    logger.info("[Gemini] 인사이트 추출 요청 중...")

    try:
        raw_response = await _call_gemini(prompt)
    except Exception as e:
        logger.error(f"[Gemini] API 호출 실패: {e}")
        return None

    # 파싱 시도 + 교정 루프
    for attempt in range(1 + GEMINI_MAX_CORRECTION_RETRIES):
        try:
            insight = _parse_insight(raw_response)
            if attempt > 0:
                logger.info(f"[Gemini] 교정 {attempt}회차에서 파싱 성공!")
            else:
                logger.info(f"[Gemini] 인사이트 추출 성공: {insight.title}")
            return insight

        except (json.JSONDecodeError, ValidationError) as e:
            error_msg = str(e)
            logger.warning(
                f"[Gemini] JSON 파싱 실패 (시도 {attempt + 1}/"
                f"{1 + GEMINI_MAX_CORRECTION_RETRIES}): {error_msg[:150]}"
            )

            if attempt < GEMINI_MAX_CORRECTION_RETRIES:
                # 교정 프롬프트로 재시도
                correction = CORRECTION_PROMPT.format(
                    error=error_msg,
                    schema=schema_str,
                    previous_response=raw_response[:2000],
                )
                try:
                    raw_response = await _call_gemini(correction)
                except Exception as api_err:
                    logger.error(f"[Gemini] 교정 요청 API 호출 실패: {api_err}")
                    return None

    logger.error("[Gemini] 최대 교정 횟수 초과. 인사이트 추출 포기.")
    return None
