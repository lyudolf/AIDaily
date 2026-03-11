"""
src/utils.py — tenacity 재시도 데코레이터, 로깅, 예외 처리 헬퍼 함수

모든 모듈에서 공통으로 사용하는 유틸리티를 제공합니다.
"""

import logging
import sys

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)
import httpx

# Google API 예외 — Gemini 라이브러리가 직접 던지는 예외를 tenacity가 잡도록
try:
    from google.api_core.exceptions import (
        ResourceExhausted,
        ServiceUnavailable,
        DeadlineExceeded,
        InternalServerError,
    )
    _GOOGLE_RETRY_EXCEPTIONS = (
        ResourceExhausted,
        ServiceUnavailable,
        DeadlineExceeded,
        InternalServerError,
    )
except ImportError:
    _GOOGLE_RETRY_EXCEPTIONS = ()

from config import (
    RETRY_MAX_ATTEMPTS,
    RETRY_WAIT_MIN_SECONDS,
    RETRY_WAIT_MAX_SECONDS,
)


# ──────────────────────────────────────────────
# 로깅 설정 (Windows cp949 안전 처리)
# ──────────────────────────────────────────────
class _SafeStreamHandler(logging.StreamHandler):
    """Windows cp949 인코딩에서 UnicodeEncodeError를 방지하는 핸들러."""

    def emit(self, record):
        try:
            msg = self.format(record)
            stream = self.stream
            try:
                stream.write(msg + self.terminator)
            except UnicodeEncodeError:
                # cp949로 인코딩 불가능한 문자(en-dash, 특수기호 등)를 ?로 대체
                safe_msg = msg.encode("cp949", errors="replace").decode("cp949")
                stream.write(safe_msg + self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)


def setup_logger(name: str = "aidaily") -> logging.Logger:
    """포맷 통일된 공용 로거를 생성합니다."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler = _SafeStreamHandler(sys.stdout)
        handler.setLevel(logging.INFO)
        formatter = logging.Formatter(
            "[%(asctime)s] %(levelname)-7s | %(name)-18s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


logger = setup_logger()


# ──────────────────────────────────────────────
# 재시도 데코레이터
# ──────────────────────────────────────────────
class RateLimitError(Exception):
    """API Rate Limit(429) 전용 예외."""
    pass


class TransientAPIError(Exception):
    """재시도 가능한 일시적 API 오류 (5xx, timeout 등)."""
    pass


def api_retry_decorator(max_attempts: int = RETRY_MAX_ATTEMPTS):
    """
    API 호출용 tenacity 재시도 데코레이터 팩토리.
    - RateLimitError, TransientAPIError, httpx 타임아웃에 대해 지수 백오프 재시도
    - google.api_core 예외 (ResourceExhausted, ServiceUnavailable 등) 직접 캐치
    - 재시도 전 로그 출력
    """
    retry_exceptions = (
        RateLimitError,
        TransientAPIError,
        httpx.TimeoutException,
    ) + _GOOGLE_RETRY_EXCEPTIONS

    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(
            multiplier=2,   # 무료 티어 대응: 더 넉넉한 대기
            min=RETRY_WAIT_MIN_SECONDS,
            max=RETRY_WAIT_MAX_SECONDS,
        ),
        retry=retry_if_exception_type(retry_exceptions),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )


def check_http_response(response: httpx.Response, service_name: str) -> None:
    """
    HTTP 응답 상태를 검사하고, 재시도 가능한 오류는 적절한 예외로 변환합니다.

    Args:
        response: httpx 응답 객체
        service_name: 로깅용 서비스 이름 (예: "Jina", "Notion")
    """
    if response.status_code == 429:
        raise RateLimitError(
            f"[{service_name}] Rate Limit 초과 (429). 재시도 대기 중..."
        )
    if response.status_code >= 500:
        raise TransientAPIError(
            f"[{service_name}] 서버 오류 ({response.status_code}). 재시도 대기 중..."
        )
    if response.status_code == 404:
        logger.warning(f"[{service_name}] 페이지를 찾을 수 없습니다 (404). 스킵합니다.")
    elif response.status_code >= 400:
        logger.error(
            f"[{service_name}] 클라이언트 오류 ({response.status_code}): "
            f"{response.text[:200]}"
        )
