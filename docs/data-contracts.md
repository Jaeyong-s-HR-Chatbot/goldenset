# 데이터 형식 (DB ↔ RAG ↔ LLM ↔ 프론트)

코드는 `contracts/schemas.py`(형식)와 `contracts/pipeline.py`(변환)에 있어요.
실제 DB 행으로 끝까지 흘려보는 검사: `uv run --with pydantic python tests/test_contracts.py`

```
프론트 ──ChatRequest──▶ API ──▶ 라우터(Route)
                              ├─▶ rag.chunks ──▶ ChunkHit[]  ─┐
                              ├─▶ hr 스키마  ──▶ HrContext    ─┼─▶ LlmRequest ─▶ messages ─▶ 로컬 LLM
                              └─▶ 코드 계산  ──▶ Calculation[] ┘                                │
프론트 ◀──ChatResponse── to_chat_response ◀── LlmAnswer ◀── parse_llm_answer ◀───────────────┘
```

## 공통 규칙

| 항목 | 형식 | 예 |
| --- | --- | --- |
| 조각 ID | `r`/`L` + 두 자리 + `-` + 네 자리 | `r03-0024`, `L01-0086` |
| 문서 ID | `r01`~`r33` 규정, `L01`~`L13` 법령 | `r08` |
| 사번 | `E` + 네 자리 (DB 그대로) | `E1584` |
| 직급 | DB 체계 1급(부장)~5급(사원). 내부는 숫자, 사람에게 보일 때만 `"4급"` | `grade=4` → `"4급"` |
| 문서 종류 | DB `rag.chunks.doc_kind` 값 그대로 | `내부규정`, `법령` |
| 날짜 | ISO 문자열 | `2026-11-12` |
| 근거 제목 | `문서명 조문(조문 제목)` | `취업규칙 제23조(연차휴가)` |

## 1. 프론트 → API: `ChatRequest`

```json
{ "message": "나 올해 연차 몇 개야?" }
```

사번은 보내지 않아요. 서버가 로그인 토큰에서 꺼내요.

## 2. DB(rag) → RAG: `ChunkHit`

`rag.chunks` 검색 결과 한 건. 검색 순위(`rank`)와 점수(`score`, 코사인 유사도)를 붙여요.

```json
{
  "chunk_id": "r03-0024", "document_id": "r03", "doc_kind": "내부규정",
  "doc_name": "취업규칙", "article": "제23조", "article_title": "연차휴가", "part": 1,
  "text": "제23조(연차휴가) ① 1년간 80퍼센트 이상 출근한 직원에게 15일의 …",
  "effective_date": "2026-07-28", "category": "근태관리",
  "score": 0.80, "rank": 1
}
```

- `doc_kind`는 DB 값(`내부규정`/`법령`) 그대로예요. 혹시 비어 있는 옛 파일이면 ID 첫 글자로 채워요.
- `embedding`, `tsv`, `source_file` 같은 검색용·관리용 칸은 넘기지 않아요.

## 3. DB(hr) → RAG: `HrContext`

라우터가 HR 조회를 켰을 때만 만들어요. **토큰의 사번 한 사람 것만** 들어가요.

```json
{
  "emp_id": "E1584", "as_of": "2026-11-12",
  "profile": { "emp_id": "E1584", "name": "이서준", "dept_name": "신호팀", "grade": 4, "position": "대리",
               "hire_date": "…", "birth_date": "…", "work_pattern": "일근", "status": "재직" },
  "records": {
    "leave_balance": [ { "emp_id": "E1584", "year": "2025", "granted_days": "…", "remaining_days": "…" } ],
    "family": [ … ], "payroll": [ … ]
  }
}
```

- `records`에 넣을 수 있는 테이블: `leave_balance, leave_history, payroll, family, training, business_trips, welfare_points, requests, hr_history, absence_history`
- `employee_pii`(주민번호·계좌·주소), `users`(비밀번호), `audit_log`는 넣으면 오류가 나요.
- 다른 사번의 행이 섞여도 오류가 나요.

## 4. 코드 계산 → LLM: `Calculation`

수당·출장비·연차 일수는 코드가 계산하고, LLM은 그 값을 문장으로만 써요.

```json
{ "name": "야간수당", "expr": "4180000 / 209 * 0.5 * 4", "value": 40000, "unit": "원",
  "basis_chunk_ids": ["r07-0053"] }
```

## 5. RAG → LLM: `LlmRequest` → messages

`build_messages()`가 OpenAI 호환 형식으로 바꿔요. vLLM이든 Ollama든 그대로 받아요.

```text
[system] 너는 누리철도 HR 챗봇이다 … (근거만 쓰기, [번호] 붙이기, 계산 결과 그대로, 근거 없으면 "[근거 없음]")

[user]
[질문]
나 올해 연차 몇 개야?

[근거]
[1] 취업규칙 제23조(연차휴가) (내부규정 · 시행 2026-07-28)
제23조(연차휴가) ① 1년간 80퍼센트 이상 출근한 직원에게 …
[2] 취업규칙 별표 2(근속년수별 연차휴가 산정례) (내부규정 · 시행 2026-07-28)
…

[내 정보]
- 이서준 / 신호팀 / 4급 대리 / 입사 … (근속 N년) / …
- leave_balance: year=2025, granted_days=…, remaining_days=…

[계산 결과]
- 올해 연차: 근속 1년 이상 3년 미만 → 15 = 15일
```

## 6. LLM → API: `LlmAnswer`

LLM은 그냥 글자로 답해요. JSON으로 답하라고 하면 작은 모델이 자주 깨지기 때문이에요.

- 근거를 쓴 문장 끝에 `[1]`, `[2]` → `used_refs` 로 뽑고, 화면용 문장에서는 지워요.
- 첫 줄이 `[근거 없음]`이면 `refused=true`.

## 7. API → 프론트: `ChatResponse`

프론트 `src/types.ts`의 `ChatResponse`와 같은 모양이에요. `citations`에 `chunk_id` 등을 더 붙였지만 프론트는 `title`만 써서 그대로 동작해요.

```json
{
  "answer": "올해 연차는 15일이에요. 근속 3년이 되면 16일로 늘어나요.",
  "citations": [
    { "title": "취업규칙 제23조(연차휴가)", "chunk_id": "r03-0024", "document_id": "r03", "doc_kind": "내부규정" },
    { "title": "취업규칙 별표 2(근속년수별 연차휴가 산정례)", "chunk_id": "r03-0067", "document_id": "r03", "doc_kind": "내부규정" }
  ],
  "refused": false
}
```

골든셋 채점은 `citations[].chunk_id`를 골든셋의 `gold_chunks.primary`와 비교하면 돼요.

## 통일한 것 (2026-11-12)

| 무엇 | 전에 | 지금 |
| --- | --- | --- |
| 문서 종류 `doc_kind` | DB는 `내부규정`/`법령`인데 규정 조각 파일엔 칸이 없었음 | DB 저장소 `rag_build.py`와 `data/regs` 파일에 `내부규정` 추가, 이 형식도 DB 값 그대로 씀 |
| 사번 | 프론트 예시 계정이 `E2023041`, `H2019007` | 프론트 예시 계정을 DB 데모 계정으로: 직원 `E1584` 이서준(P1), 인사팀 `E1307` 김수아(P8) |
| 직급 | DB 1~5급, 골든셋 페르소나엔 6급 | DB 체계(1급 부장 ~ 5급 사원)로 통일. 골든셋 P1·P3 페르소나는 5급 사원, P2는 3급 과장 |

남은 것: 질문셋 페르소나와 똑같은 직원은 DB에 없어요. 본인 데이터가 필요한 골든셋 문항을 실제 DB로 평가하려면 페르소나에 맞는 직원을 DB에 넣어야 해요. 규정에 나오는 6급(예: "6급에서 5급 승진 최소 2년")은 규정 질문이라 그대로 둬요.
