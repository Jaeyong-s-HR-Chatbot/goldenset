"""router 채점: 라벨(규정/개인/둘다) 붙은 질문으로 'HR 조회 여부'가 맞는지 본다.

실행: python tests/test_router.py
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from router import route  # noqa: E402

items = json.loads((ROOT / "eval" / "routing_questions.json").read_text(encoding="utf-8"))

# 기본 동작 확인
assert route("나 지금 남은 연차 며칠이야?").hr
assert route("연차 몇 개 남았어?").hr                      # 주어 생략
assert not route("결혼하면 휴가 며칠이야?").hr
assert not route("휴가 며칠 나와요?").hr                    # '나와요'의 '나'는 1인칭 아님
assert not route("취업규칙 제23조 내용 알려줘").hr          # '제23조'의 '제'는 1인칭 아님
assert not route("돌봄휴가 5일 썼는데, 길게 쉬는 건 며칠까지 돼요?").hr  # 서술 속 '썼는데'
assert route("우리 팀 연차 현황 보여줘").team_scope
assert route("우리 부모님도 가족수당 대상이야?").hr and not route("우리 부모님도 가족수당 대상이야?").team_scope
assert route("잔여 연차 알려줘").hr

stats = defaultdict(lambda: {"tp": 0, "fn": 0, "fp": 0, "tn": 0, "miss": [], "extra": []})
for it in items:
    r = route(it["question"])
    need = it["label"] != "규정"
    s = stats[it["source"]]
    key = ("tp" if r.hr else "fn") if need else ("fp" if r.hr else "tn")
    s[key] += 1
    if key == "fn":
        s["miss"].append(it["question"])
    if key == "fp":
        s["extra"].append(it["question"])

names = {"teammate": "팀원 30문항 (처음 보는 문장)", "new": "새로 쓴 57문항", "golden": "골든셋 279문항"}
for src in ["teammate", "new", "golden"]:
    s = stats[src]
    need = s["tp"] + s["fn"]
    print(f"\n## {names[src]}")
    print(f"  HR 필요한 질문 잡음: {s['tp']}/{need} ({s['tp'] / need * 100:.0f}%)")
    print(f"  규정 질문인데 HR도 조회: {s['fp']}/{s['fp'] + s['tn']}")
    for q in s["miss"]:
        print("    놓침:", q)
    for q in s["extra"]:
        print("    괜히 조회:", q)
