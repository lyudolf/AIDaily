"""
config.py — 전역 설정 및 환경변수 로더

모든 API 키, 타임아웃, 재시도 설정을 중앙 관리합니다.
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()


# ──────────────────────────────────────────────
# API Keys
# ──────────────────────────────────────────────
JINA_API_KEY: str = os.getenv("JINA_API_KEY", "")
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
NOTION_TOKEN: str = os.getenv("NOTION_TOKEN", "")
NOTION_DATABASE_ID: str = os.getenv("NOTION_DATABASE_ID", "")


def validate_env() -> None:
    """필수 환경변수가 모두 설정되었는지 검증합니다."""
    missing: list[str] = []
    if not GEMINI_API_KEY:
        missing.append("GEMINI_API_KEY")
    if not NOTION_TOKEN:
        missing.append("NOTION_TOKEN")
    if not NOTION_DATABASE_ID:
        missing.append("NOTION_DATABASE_ID")
    # JINA_API_KEY는 선택사항 (없으면 키 없이 호출)
    if missing:
        print(f"[ERROR] 다음 환경변수가 .env에 설정되지 않았습니다: {', '.join(missing)}")
        sys.exit(1)


# ──────────────────────────────────────────────
# Jina Reader API
# ──────────────────────────────────────────────
JINA_BASE_URL: str = "https://r.jina.ai"
JINA_TIMEOUT_SECONDS: int = 30

# ──────────────────────────────────────────────
# Gemini API
# ──────────────────────────────────────────────
GEMINI_MODEL: str = "gemini-2.0-flash"
GEMINI_TIMEOUT_SECONDS: int = 60  # 무료 티어 지연 대응: 60초
GEMINI_MAX_CORRECTION_RETRIES: int = 2  # JSON 파싱 실패 시 교정 재시도 횟수

# ──────────────────────────────────────────────
# Notion API
# ──────────────────────────────────────────────
NOTION_API_TIMEOUT_SECONDS: int = 30

# ──────────────────────────────────────────────
# Retry (tenacity) 설정
# ──────────────────────────────────────────────
RETRY_MAX_ATTEMPTS: int = 6
RETRY_WAIT_MIN_SECONDS: int = 4
RETRY_WAIT_MAX_SECONDS: int = 60

# ──────────────────────────────────────────────
# Pipeline 설정
# ──────────────────────────────────────────────
REQUEST_DELAY_SECONDS: float = 3.0  # 요청 간 딜레이 (Rate Limit 회피)
