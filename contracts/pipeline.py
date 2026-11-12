"""형식 사이를 잇는 변환 함수들. DB 행 → 모델, 모델 → LLM 메시지, LLM 출력 → 프론트 응답."""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Iterable, Mapping

from .schemas import (
    ChatResponse, ChunkHit, Citation, EmployeeProfile, HrContext, LlmAnswer, LlmRequest, Me, Role,
)

REFUSE_MARK = "[근거 없음]"
_REF = re.compile(r"\[(\d{1,2})\]")
# 프롬프트에 너무 많이 넣지 않기 위한 테이블별 최대 행 수 (최근 것부터)
MAX_ROWS = {"payroll": 3, "leave_history": 10, "requests": 10, "training": 10}

SYSTEM_PROMPT = f"""너는 누리철도 HR 챗봇이다. 아래 규칙을 지켜 한국어 존댓말로 짧게 답한다.
1. [근거]와 [내 정보], [계산 결과]에 있는 내용만 쓴다. 없는 숫자나 제도는 지어내지 않는다.
2. 근거를 쓴 문장 끝에 그 번호를 [1]처럼 붙인다.
3. 계산이 필요한 숫자는 [계산 결과]의 값을 그대로 쓴다. 직접 계산하지 않는다.
4. 근거로 답할 수 없으면 첫 줄에 {REFUSE_MARK} 라고 쓰고, 인사팀 확인을 안내한다.
5. 질문 속 정보가 근거와 다르면 바로잡는다."""


# ---------------------------------------------------------------- DB 행 → 모델
def chunk_hit_from_row(row: Mapping[str, Any], score: float, rank: int) -> ChunkHit:
    """rag.chunks 한 줄(dict) → ChunkHit. 옛 조각 파일처럼 doc_kind 가 비어 있으면 ID로 채운다."""
    doc_id = row["document_id"]
    return ChunkHit(
        chunk_id=row["chunk_id"],
        document_id=doc_id,
        doc_kind=row.get("doc_kind") or ("법령" if doc_id.startswith("L") else "내부규정"),
        doc_name=row["doc_name"],
        article=row.get("article") or "",
        article_title=row.get("article_title") or "",
        part=row.get("part") or 1,
        text=row["text"],
        effective_date=row.get("effective_date") or None,
        category=row.get("category") or "",
        score=float(score),
        rank=rank,
    )


def profile_from_row(emp: Mapping[str, Any], dept_name: str) -> EmployeeProfile:
    """hr.employees 한 줄 → EmployeeProfile (개인정보 컬럼은 버림)."""
    return EmployeeProfile(
        emp_id=emp["emp_id"], name=emp["name"], dept_name=dept_name, grade=int(emp["grade"]),
        position=emp["position"], hire_date=emp["hire_date"], birth_date=emp["birth_date"],
        work_pattern=emp["work_pattern"], status=emp["status"],
    )


def me_from_profile(p: EmployeeProfile, role: Role) -> Me:
    return Me(employee_id=p.emp_id, name=p.name, dept=p.dept_name, grade=p.grade_label, role=role)


# ---------------------------------------------------------------- 모델 → LLM 메시지
def _fmt_row(row: Mapping[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in row.items() if k != "emp_id" and v not in ("", None))


def format_hr(hr: HrContext) -> str:
    p = hr.profile
    years = (hr.as_of - p.hire_date).days // 365
    lines = [f"- {p.name} / {p.dept_name} / {p.grade_label} {p.position} / 입사 {p.hire_date} (근속 {years}년) "
             f"/ 생년월일 {p.birth_date} / {p.work_pattern} (기준일 {hr.as_of})"]
    for table, rows in hr.records.items():
        for row in rows[: MAX_ROWS.get(table, 20)]:
            lines.append(f"- {table}: {_fmt_row(row)}")
    return "\n".join(lines)


def build_messages(req: LlmRequest) -> list[dict[str, str]]:
    """OpenAI 호환 chat messages (vLLM, Ollama 둘 다 이 형식을 받음)."""
    parts = [f"[질문]\n{req.question}", "[근거]"]
    if req.chunks:
        for i, c in enumerate(req.chunks, 1):
            when = f" · 시행 {c.effective_date}" if c.effective_date else ""
            parts.append(f"[{i}] {c.title} ({c.doc_kind}{when})\n{c.text}")
    else:
        parts.append("(찾은 근거 없음)")
    if req.hr:
        parts.append("[내 정보]\n" + format_hr(req.hr))
    if req.calculations:
        parts.append("[계산 결과]\n" + "\n".join(
            f"- {c.name}: {c.expr} = {c.value:,.0f}{c.unit}" for c in req.calculations))
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "\n\n".join(parts)}]


# ---------------------------------------------------------------- LLM 출력 → 프론트
def parse_llm_answer(raw: str, n_chunks: int) -> LlmAnswer:
    text = raw.strip()
    refused = text.startswith(REFUSE_MARK)
    if refused:
        text = text[len(REFUSE_MARK):].strip()
    refs = sorted({int(m) for m in _REF.findall(text) if 1 <= int(m) <= n_chunks})
    clean = re.sub(r"\s*\[\d{1,2}\]", "", text).strip()
    return LlmAnswer(raw=raw, answer=clean, used_refs=refs, refused=refused)


def to_chat_response(ans: LlmAnswer, chunks: Iterable[ChunkHit]) -> ChatResponse:
    """쓴 근거만 citations 로. 같은 조문의 여러 조각은 하나로 합친다."""
    chunks = list(chunks)
    seen: set[str] = set()
    cites: list[Citation] = []
    for i in ans.used_refs:
        c = chunks[i - 1]
        if c.title in seen:
            continue
        seen.add(c.title)
        cites.append(Citation(title=c.title, chunk_id=c.chunk_id, document_id=c.document_id, doc_kind=c.doc_kind))
    return ChatResponse(answer=ans.answer, citations=[] if ans.refused else cites, refused=ans.refused)


def years_between(a: date, b: date) -> int:
    return (b - a).days // 365
