"""질문 라우터: 규정 검색만 할지, HR 조회도 같이 할지 정한다 (LLM 안 씀).

규칙
- 규정 검색(RAG)은 항상 한다.
- 질문에 1인칭(내/나/제/저)이 있거나, 내 기록의 상태를 묻는 표현(남았, 승인됐, 지급됐 …)이
  마지막 절에 있으면 HR 조회도 같이 한다.
- '우리 팀'·'저희 팀'처럼 남의 데이터가 걸리면 team_scope=True 로 표시한다 (권한 확인용).

개인/둘다를 키워드로 나누는 건 처음 보는 문장에서 잘 안 맞아서 하지 않는다.
HR 조회를 한 번 더 하는 비용은 작고, 필요한 걸 안 찾으면 답이 틀린다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 1인칭: 글자 포함이 아니라 '단어'로 본다 ("나와요", "제23조", "저녁"에 안 걸리게)
_FIRST_PERSON = re.compile(r"^(내|나|제|저|나의)(가|는|도|를|의|한테|랑|만)?$")
# '우리 부모님', '우리 애'처럼 가족 앞의 우리/저희는 '내'와 같은 뜻
_OUR_FAMILY = re.compile(r"(우리|저희)\s*(부모님|엄마|아빠|아버지|어머니|아이|애|딸|아들|남편|아내|와이프|가족|집)")
# 남의 데이터 범위
_TEAM = re.compile(r"(우리|저희)\s*(팀|부서|본부|사업소)")
# 내 기록의 '상태'를 묻는 표현: 주어가 빠져도 HR 조회 ("연차 몇 개 남았어?")
_STATUS = re.compile(
    r"(남았|잔여|잔액|승인됐|승인 났|처리됐|처리 안 된|지급됐|들어왔|나왔어|받았|썼어|썼지|쓴 거|쓴 게"
    r"|명세|이력|기록 있|조회해|보여줘|보여줄래|보여주세요|확인하고 싶|등록돼 있|몇 번 했|언제였)"
)
# 마지막 절만 본다: "5일 썼는데, 며칠까지 돼요?"의 '썼는데'는 상황 설명이지 조회가 아님
_CLAUSE_SPLIT = re.compile(r"(?:는데|은데|고서|[,.])\s*")
_TOKEN_SPLIT = re.compile(r"[^0-9A-Za-z가-힣]+")


@dataclass(frozen=True)
class Route:
    rag: bool          # 규정 문서 검색
    hr: bool           # HR DB 조회
    team_scope: bool   # 다른 사람 데이터가 걸림 → 권한 확인 필요
    reason: str


def _tokens(q: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT.split(q) if t]


def _last_clause(q: str) -> str:
    parts = [p for p in _CLAUSE_SPLIT.split(q) if p.strip()]
    return parts[-1] if parts else q


def route(question: str) -> Route:
    q = question.strip()
    first = next((t for t in _tokens(q) if _FIRST_PERSON.match(t)), None)
    if not first and (m := _OUR_FAMILY.search(q)):
        first = m.group(0)
    status = _STATUS.search(_last_clause(q))
    team = bool(_TEAM.search(q))
    if first:
        reason = f"1인칭 '{first}'"
    elif status:
        reason = f"상태 표현 '{status.group(0)}'"
    elif team:
        reason = "팀 범위"
    else:
        reason = "규정만"
    return Route(rag=True, hr=bool(first or status or team), team_scope=team, reason=reason)


if __name__ == "__main__":
    import sys

    for line in sys.argv[1:] or ["나 지금 남은 연차 며칠이야?", "결혼하면 휴가 며칠이야?"]:
        print(line, "→", route(line))
