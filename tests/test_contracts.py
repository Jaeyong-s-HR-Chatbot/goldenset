"""실제 DB 행(tests/fixtures/db_sample.json)을 형식대로 끝까지 흘려본다.

실행: uv run --with pydantic python tests/test_contracts.py
"""

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pydantic import ValidationError  # noqa: E402

from contracts.pipeline import (  # noqa: E402
    REFUSE_MARK, build_messages, chunk_hit_from_row, me_from_profile, parse_llm_answer,
    profile_from_row, to_chat_response,
)
from contracts.schemas import Calculation, ChatRequest, ChatResponse, HrContext, LlmRequest  # noqa: E402

db = json.loads((ROOT / "tests/fixtures/db_sample.json").read_text(encoding="utf-8"))


def expect_error(fn, why):
    try:
        fn()
    except (ValidationError, ValueError):
        return
    raise AssertionError("막혀야 하는데 통과함: " + why)


# 1) DB(rag) → ChunkHit : 규정 조각엔 doc_kind 가 없어도 채워져야 함
hits = [chunk_hit_from_row(r, score=0.8 - i * 0.05, rank=i + 1) for i, r in enumerate(db["chunks"])]
assert [h.doc_kind for h in hits] == ["규정", "규정", "규정", "법령"]
assert hits[0].title == "취업규칙 제23조(연차휴가)", hits[0].title
assert hits[0].effective_date == date(2026, 7, 28)
expect_error(lambda: chunk_hit_from_row({**db["chunks"][0], "chunk_id": "R3-24"}, 0.5, 1), "잘못된 chunk_id")

# 2) DB(hr) → HrContext : 내 사번 것만, 허용 테이블만
emp = db["employee"]
profile = profile_from_row(emp, db["dept_name"])
hr = HrContext(emp_id=emp["emp_id"], as_of=date(2026, 11, 12), profile=profile, records=db["records"])
assert profile.grade_label == "4급"
expect_error(lambda: HrContext(emp_id=emp["emp_id"], as_of=date(2026, 11, 12), profile=profile,
                               records={"leave_balance": [db["other_emp_leave"]]}), "다른 사람 행")
expect_error(lambda: HrContext(emp_id=emp["emp_id"], as_of=date(2026, 11, 12), profile=profile,
                               records={"employee_pii": [{"emp_id": emp["emp_id"]}]}), "개인정보 테이블")

# 3) /me 응답은 프론트 Me 타입 모양
me = me_from_profile(profile, "employee").model_dump()
assert set(me) == {"employee_id", "name", "dept", "grade", "role"} and me["grade"] == "4급"

# 4) RAG → LLM 메시지
req = LlmRequest(
    question=ChatRequest(message="나 올해 연차 몇 개야?").message,
    chunks=hits[:2],
    hr=hr,
    calculations=[Calculation(name="올해 연차", expr="근속 1년 이상 3년 미만 → 15", value=15, unit="일",
                              basis_chunk_ids=["r03-0024", "r03-0067"])],
)
msgs = build_messages(req)
user = msgs[1]["content"]
assert msgs[0]["role"] == "system" and "[1] 취업규칙 제23조(연차휴가)" in user
assert "[내 정보]" in user and "4급" in user and "[계산 결과]" in user
assert "rrn" not in user and "account_no" not in user

# 5) LLM 출력 → 프론트 응답
ans = parse_llm_answer("올해 연차는 15일이에요 [1][2]. 근속 3년이 되면 16일로 늘어나요 [2].", n_chunks=2)
assert ans.used_refs == [1, 2] and "[1]" not in ans.answer and not ans.refused
resp = to_chat_response(ans, req.chunks)
out = json.loads(resp.model_dump_json(exclude_none=True))
assert set(out) == {"answer", "citations", "refused"}               # frontend ChatResponse 와 같은 키
assert all("title" in c for c in out["citations"])
assert [c["chunk_id"] for c in out["citations"]] == ["r03-0024", "r03-0067"]

refused = to_chat_response(parse_llm_answer(f"{REFUSE_MARK}\n규정에서 찾지 못했어요. 인사팀에 확인해 주세요.", 2), req.chunks)
assert refused.refused and refused.citations == []
ChatResponse.model_validate(out)

print("형식 검사 통과: DB → ChunkHit/HrContext → LLM 메시지 → LLM 출력 → ChatResponse")
print("\n--- LLM에 들어가는 user 메시지 예시 ---\n" + user[:900])
print("\n--- 프론트로 가는 응답 ---\n" + json.dumps(out, ensure_ascii=False, indent=1))
