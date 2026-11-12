"""DB → RAG → LLM → 프론트 사이에서 주고받는 데이터 형식.

흐름:
  ChatRequest ─▶ Route(router) ─▶ ChunkHit[] (rag.chunks)  ─┐
                                 └▶ HrContext (hr 스키마)  ─┼▶ LlmRequest ─▶ (LLM) ─▶ LlmAnswer ─▶ ChatResponse
                                    Calculation[] (코드 계산) ┘

규칙
- ID는 DB 그대로 쓴다: chunk_id "r03-0024" / "L01-0086", document_id "r03" / "L01", 사번 "E1001".
- 직급은 DB에서 숫자(1~5, 사원=5급)지만, 사람에게 보이는 곳(프론트, LLM 프롬프트)에서는 "4급" 같은 문자열로 바꾼다.
- 날짜는 ISO 문자열(YYYY-MM-DD)로 주고받는다.
- 프론트 응답(ChatResponse)은 frontend/src/types.ts 와 같은 모양을 유지한다. 필드를 더해도 되지만 빼거나 이름을 바꾸면 안 된다.
"""

from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

DocKind = Literal["내부규정", "법령"]   # rag.chunks.doc_kind 값 그대로
Role = Literal["employee", "hr_admin"]

# HR 조회에서 LLM에 넘겨도 되는 테이블. employee_pii, users, audit_log 는 절대 안 넘긴다.
HR_TABLES = (
    "leave_balance", "leave_history", "payroll", "family", "training",
    "business_trips", "welfare_points", "requests", "hr_history", "absence_history",
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- 프론트 ↔ API
class ChatRequest(_Model):
    """POST /chat 요청. 사번은 보내지 않는다 (서버가 토큰에서 꺼냄)."""
    message: str = Field(min_length=1, max_length=1000)


class Me(_Model):
    """GET /me 응답 (frontend Me 타입)."""
    employee_id: str
    name: str
    dept: str
    grade: str          # "4급"
    role: Role


class Citation(BaseModel):
    """답변에 붙는 근거. 프론트는 title 만 쓰고, 나머지는 평가·디버그용."""
    model_config = ConfigDict(extra="forbid")
    title: str                          # "취업규칙 제23조(연차휴가)"
    chunk_id: Optional[str] = None      # "r03-0024"
    document_id: Optional[str] = None   # "r03"
    doc_kind: Optional[DocKind] = None


class ChatResponse(_Model):
    """POST /chat 응답 (frontend ChatResponse 타입)."""
    answer: str
    citations: list[Citation]
    refused: bool                       # True 면 화면에 "근거 조문 없음 · 인사팀 확인 필요"


# ---------------------------------------------------------------- DB(rag) → RAG
class ChunkHit(_Model):
    """rag.chunks 검색 결과 한 건."""
    chunk_id: str = Field(pattern=r"^[rL]\d{2}-\d{4}$")
    document_id: str = Field(pattern=r"^[rL]\d{2}$")
    doc_kind: DocKind
    doc_name: str
    article: str = ""                   # "제23조", "별표 2", "" (제목·목차)
    article_title: str = ""
    part: int = 1                       # 긴 조문을 나눈 순서
    text: str
    effective_date: Optional[date] = None
    category: str = ""
    score: float = Field(ge=-1.0, le=1.0)   # 코사인 유사도 (1 - 거리)
    rank: int = Field(ge=1)

    @property
    def title(self) -> str:
        head = f"{self.doc_name} {self.article}".strip()
        return f"{head}({self.article_title})" if self.article and self.article_title else head


# ---------------------------------------------------------------- DB(hr) → RAG
class EmployeeProfile(_Model):
    """employees 한 줄에서 LLM에 넘길 부분만. 주민번호·계좌·주소 같은 건 없음."""
    emp_id: str = Field(pattern=r"^E\d{4}$")
    name: str
    dept_name: str
    grade: int = Field(ge=1, le=5)    # DB 직급 1(부장)~5(사원)
    position: str
    hire_date: date
    birth_date: date
    work_pattern: str
    status: str

    @property
    def grade_label(self) -> str:
        return f"{self.grade}급"


class HrContext(_Model):
    """내 기록 조회 결과. 반드시 토큰의 사번(emp_id) 한 사람 것만."""
    emp_id: str = Field(pattern=r"^E\d{4}$")
    as_of: date
    profile: EmployeeProfile
    records: dict[str, list[dict]] = Field(default_factory=dict)   # 테이블 이름 → 행 목록

    @field_validator("records")
    @classmethod
    def _only_allowed_tables(cls, v: dict[str, list[dict]]) -> dict[str, list[dict]]:
        bad = set(v) - set(HR_TABLES)
        if bad:
            raise ValueError(f"LLM에 넘기면 안 되는 테이블: {sorted(bad)}")
        return v

    def model_post_init(self, _ctx) -> None:
        if self.profile.emp_id != self.emp_id:
            raise ValueError("profile 사번이 조회 사번과 다름")
        for table, rows in self.records.items():
            for row in rows:
                if row.get("emp_id", self.emp_id) != self.emp_id:
                    raise ValueError(f"{table}에 다른 사람 행이 섞임: {row.get('emp_id')}")


# ---------------------------------------------------------------- 코드 계산 → LLM
class Calculation(_Model):
    """수당·출장비 같은 계산은 코드가 하고, LLM은 결과만 문장으로 쓴다."""
    name: str                   # "야간수당"
    expr: str                   # "4180000 / 209 * 0.5 * 4"
    value: float
    unit: str                   # "원", "일"
    basis_chunk_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- RAG → LLM
class LlmRequest(_Model):
    """LLM 한 번 부를 때 들어가는 모든 것. build_messages()로 OpenAI 호환 messages 로 바꾼다."""
    question: str
    chunks: list[ChunkHit] = Field(max_length=8)
    hr: Optional[HrContext] = None
    calculations: list[Calculation] = Field(default_factory=list)


class LlmAnswer(_Model):
    """LLM 출력(글자)을 파싱한 결과."""
    raw: str
    answer: str                 # 화면에 보일 문장 ([1] 같은 번호 표시 뺀 것)
    used_refs: list[int]        # 답에 쓴 근거 번호 (1부터)
    refused: bool
