# What Is an AI Agent?

이 문서는 FitCheck 프로젝트에서 "AI agent"라는 말을 어떤 의미로 쓰는지 정의합니다.

작성일: 2026-06-06

## 1. 한 줄 정의

AI agent는 목표를 받고, 현재 상태를 해석하고, 필요한 도구를 선택해 실행하고, 실행 결과를 관찰하면서 목표가 끝날 때까지 행동을 이어가는 AI 시스템입니다.

```text
AI agent = LLM + state + tools + planning loop + execution + observation + guardrails
```

LLM 자체는 agent가 아닙니다. LLM은 agent 안의 reasoning engine 또는 policy에 가깝습니다. Agent는 LLM이 tool을 쓰고 상태를 바꾸며 작업을 끝낼 수 있도록 둘러싼 전체 실행 시스템입니다.

## 2. 챗봇, workflow, agent의 차이

| 구분 | 동작 방식 | 예시 | FitCheck에서의 의미 |
| --- | --- | --- | --- |
| 챗봇 | 입력에 답변 | "이 옷 괜찮아 보여요" | 근거 없이 조언하면 부족함 |
| 고정 workflow | 정해진 순서대로 실행 | parse -> match -> report | 현재 MVP harness에 가까움 |
| AI agent | 상태를 보고 다음 행동을 선택 | 정보 부족 시 질문, tool 실패 시 대체 경로 | production 목표 |

핵심 차이는 "답변을 잘하느냐"가 아니라 "다음 행동을 선택하고 실행하느냐"입니다.

## 3. FitCheck 예시

사용자 입력:

```text
이 후드티 사도 될까? 내가 좋아하는 회색 티랑 핏이 비슷했으면 좋겠어.
```

챗봇 답변:

```text
사진상으로는 괜찮아 보입니다.
```

고정 workflow:

```text
1. 기준 옷 불러오기
2. 후보 상품 cache 불러오기
3. 실측 비교
4. 리포트 생성
```

AI agent:

```text
1. 사용자 목표를 분석한다.
2. 기준 옷 memory가 있는지 확인한다.
3. 후보 상품 URL이 없으면 검색 tool을 선택한다.
4. 상품 페이지를 가져온다.
5. 이미지 중 사이즈표/소재표를 분류한다.
6. OCR 또는 vision tool로 정보를 추출한다.
7. 실측/소재/가격을 정규화한다.
8. 기준 옷과 비교한다.
9. 정보가 부족하면 사용자에게 질문한다.
10. 충분한 근거가 있을 때만 최종 판단을 작성한다.
```

## 4. AI Agent의 핵심 구성요소

### Goal

Agent가 달성해야 하는 목표입니다.

FitCheck 예:

```text
사용자가 좋아하는 기존 옷의 핏, 재질, 사이즈, 실루엣, 디자인 요소를 기준으로 온라인에서 비슷한 후보 옷을 찾아 비교한다.
```

### State

현재까지 알고 있는 정보입니다.

```json
{
  "goal": "좋아하는 옷과 비슷한 후보 탐색",
  "candidate_url": "https://...",
  "closet_item": null,
  "product_spec": null,
  "missing_info": [],
  "trace": []
}
```

### Memory

사용자나 과거 작업에서 가져오는 지속 정보입니다.

FitCheck 예:

- 사용자가 자주 입는 옷
- 선호하는 핏
- 기존 옷 실측
- 싫어하는 소재
- 가격 민감도

### Tools

Agent가 직접 실행할 수 있는 기능입니다. LLM이 tool call을 선택하고 backend가 실제 실행합니다.

FitCheck 필수 tools:

```text
load_closet_memory
search_web_candidates
fetch_product_page
extract_size_material_info
normalize_product_spec
score_candidate_match
ask_missing_info
write_purchase_report
```

### Planning Loop

Agent가 다음 행동을 고르는 반복 구조입니다.

```text
state 확인
-> 다음 tool 선택
-> tool 실행
-> observation 저장
-> state 갱신
-> 다음 행동 선택
```

### Observation

Tool 실행 결과입니다. Agent의 다음 판단은 observation을 근거로 해야 합니다.

예:

```json
{
  "tool": "score_candidate_match",
  "output": {
    "recommendation": "avoid",
    "size_delta": {
      "length": 12,
      "shoulder": 8
    },
    "risks": ["기준 옷보다 총장이 12cm 깁니다."]
  }
}
```

### Guardrails

Agent가 하면 안 되는 일을 막는 정책입니다.

FitCheck guardrails:

- 실측이 없으면 `buy` 금지
- 소재를 모르면 추정하지 않기
- OCR 결과 없는 수치를 만들지 않기
- LLM API 설정이 없으면 실제 agent 실행으로 표시하지 않기
- 최종 리포트는 tool observation에 있는 근거만 사용하기

## 5. Agent인지 확인하는 질문

다음 질문에 "예"라고 답할 수 있어야 production AI agent에 가깝습니다.

```text
1. LLM이 다음 tool을 선택하는가?
2. Backend가 그 tool을 실제로 실행하는가?
3. Tool 결과가 observation으로 저장되는가?
4. Observation을 보고 다음 행동이 달라지는가?
5. 정보 부족 시 사용자에게 질문하는가?
6. 실패 시 재시도하거나 대체 경로를 택하는가?
7. 최종 답변이 tool trace에 근거하는가?
8. LLM 설정이 없으면 실행 불가 상태를 명확히 보여주는가?
```

이 중 대부분이 아니면 보통 agent가 아니라 챗봇, 고정 workflow, 또는 demo harness입니다.

## 6. FitCheck에서 쓰지 말아야 할 표현

다음은 실제 production agent가 구현되기 전에는 피해야 합니다.

```text
AI agent가 실제로 판단한다
LLM이 연결되어 있다
자동 검색/OCR이 된다
사용자 입력을 이해해서 tool을 고른다
```

실제 상태가 그렇지 않다면 이렇게 말해야 합니다.

```text
현재는 deterministic harness다
LLM 설정 경계만 있다
OCR은 fixture다
검색은 cache 또는 고정 URL probe다
fallback parser로 처리 중이다
```

## 7. FitCheck의 목표 기준

FitCheck가 "AI agent"라고 말할 수 있으려면 다음이 필요합니다.

```text
LLM 설정 필수
Responses API 또는 Agents SDK 기반 tool-calling loop
Backend-owned tool registry
Run state 저장
Tool observation 저장
Missing-info question flow
Trace-grounded final report
```

최소 acceptance criteria:

```text
1. LLM 설정이 없으면 configuration_required가 나온다.
2. LLM 설정이 있으면 external_api_called = true가 trace에 남는다.
3. LLM이 최소 2개 이상의 tool을 직접 선택한다.
4. Backend가 tool을 실행하고 output을 observation으로 저장한다.
5. 필수 정보가 없으면 waiting_for_user 또는 need_more_info가 나온다.
6. 최종 리포트는 observation에 있는 수치와 근거만 사용한다.
```

## 8. 관련 문서

- [Production AI Agent Design](16-production-ai-agent-design.md)
- [System Architecture](02-system-architecture.md)
- [Agent Design](03-agent-design.md)
- [Harness to Production Map](10-harness-to-production-map.md)
