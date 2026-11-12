# goldenset

누리철도 HR 챗봇의 규정 검색(RAG) 쪽 코드와 평가 데이터.
규정·법령 조각과 HR 데이터는 [DB 저장소](https://github.com/Jaeyong-s-HR-Chatbot/DB)에 있어요.

## 구성

| 경로 | 내용 |
| --- | --- |
| `router/router.py` | 질문 라우터. 규정 검색만 할지, HR 조회도 같이 할지 정함 (LLM 안 씀) |
| `tests/test_router.py` | 라우터 채점 |
| `contracts/` | DB ↔ RAG ↔ LLM ↔ 프론트 사이 데이터 형식과 변환 함수 ([설명](docs/data-contracts.md)) |
| `tests/test_contracts.py` | 실제 DB 행으로 형식을 끝까지 흘려보는 검사 |
| `eval/golden_set.json` | 골든셋 279문항 (정답, 핵심 사실, 근거 조각 ID 등) |
| `eval/routing_questions.json` | 라우터 채점용 질문 366개 (규정 / 개인 / 둘다 라벨) |

## 라우터 규칙

- 규정 검색은 항상 한다.
- 아래 중 하나가 있으면 HR 조회도 같이 한다.
  - 1인칭: 내 / 나 / 제 / 저 (단어 단위로 찾음. "나와요", "제23조"에는 안 걸림). '우리 부모님'처럼 가족 앞의 '우리'도 포함
  - 내 기록 상태를 묻는 말: 남았, 잔여, 승인됐, 지급됐, 들어왔, 보여줘 등 (마지막 절에서만 봄)
- '우리 팀', '저희 부서'가 있으면 `team_scope=True` (다른 사람 데이터 → 권한 확인 필요)

규정/개인/둘다를 키워드로 정확히 나누는 건 처음 보는 문장에서 잘 안 맞아서 하지 않아요.
HR을 한 번 더 조회하는 비용은 작고, 필요한 걸 안 찾으면 답이 틀려요.

```python
from router import route

route("나 지금 남은 연차 며칠이야?")
# Route(rag=True, hr=True, team_scope=False, reason="1인칭 '나'")
```

## 채점 결과 (2026-11-12)

```bash
python tests/test_router.py
```

| 질문 세트 | HR 필요한 질문 잡음 | 규정인데 HR도 조회 |
| --- | --- | --- |
| 팀원이 쓴 30문항 (규칙을 안 본 사람이 쓴 문장) | 20/20 | 0/10 |
| 새로 쓴 57문항 | 41/42 | 6/15 |
| 골든셋 279문항 | 6/14 | 8/265 |

골든셋에서 놓치는 건 "부모님 두 분 다 가족수당 받을 수 있나요?"처럼 1인칭 없이 내 상황을 묻는 질문이에요.

## 골든셋 필드

| 필드 | 뜻 |
| --- | --- |
| `question` | 챗봇에 그대로 넣는 질문 ([틀린 전제] 같은 표시는 정답 힌트라서 뺌) |
| `reference_answer`, `basis` | 정답 문장, 근거규정 |
| `type` | 일반 / 틀린 전제 / 포괄 질문 / 규정 없음 |
| `key_facts` | 답에 꼭 들어가야 하는 핵심 사실 (채점 기준) |
| `must_not_include` | 이 주장을 하면 틀림 (의미로 판정) |
| `gold_chunks` | `primary` = 꼭 필요한 조각, `supporting` = 있으면 좋은 조각 (규정 r01~r33, 법령 L01~L13) |
| `needs_hr_data`, `expected_hr_tables` | 본인 데이터 조회가 필요한 문항과 볼 hr 테이블 |
| `calculation` | 계산 문항의 식과 정답 |
| `uncertain` | 정답에 ※ / 확인 필요가 붙은 문항 |
| `multi_hop` | 근거 문서 2개 이상 또는 포괄 질문 |

골든셋 페르소나 3명은 DB 직원과 같아요: P1 = `E1634`, P2 = `E1033`, P3 = `E1768` (`personas[].emp_id`). 본인 데이터가 필요한 문항은 이 사번으로 로그인해 평가하면 돼요.
