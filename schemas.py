"""
schemas.py — Pydantic을 활용한 추출용 JSON 스키마 모델 정의

Gemini API의 Structured Output과 데이터 검증에 사용됩니다.
"""

from pydantic import BaseModel, Field


class ArticleInsight(BaseModel):
    """Gemini API가 추출해야 하는 정형화된 인사이트 스키마."""

    title: str = Field(
        description="뉴스, 아티클, 또는 프로젝트의 명확한 제목"
    )
    author: str = Field(
        description="저자, 발언을 한 영향력 있는 인물명, 또는 소속 기관명"
    )
    github_url: str | None = Field(
        default=None,
        description="깃허브 링크 (발견되지 않으면 null)"
    )
    demo_url: str | None = Field(
        default=None,
        description="시연 데모 링크 (발견되지 않으면 null)"
    )
    license: str = Field(
        default="N/A",
        description="오픈소스 라이선스 정책 (해당 사항이 없으면 N/A)"
    )
    benchmarks: list[str] = Field(
        default_factory=list,
        description="성능 지표, 주요 업데이트 내용, 혹은 인물의 핵심 발언 요약 목록"
    )
    business_impact: str = Field(
        description=(
            "이 기술, 뉴스, 또는 발언이 실제 비즈니스에 어떻게 적용될 수 있고 "
            "어떤 파급 효과를 주는지 기획자 관점에서 작성한 3줄 심층 분석"
        )
    )
