# Production AI Agent Design

이 문서는 현재 FitCheck MVP를 실제 API 제품에서 동작하는 AI agent로 전환하기 위한 전체 설계입니다.

작성일: 2026-06-06

구현 상태:

- `backend_harness/app/agent_runtime.py`: Responses API style tool-calling loop 골격 구현
- `backend_harness/app/tool_registry.py`: FitCheck backend-owned tool registry 구현
- `backend_harness/app/agent_run_store.py`: SQLite run/trace store와 JSON test store 구현
- `backend_harness/app/providers.py`: search/OCR provider boundary와 optional live adapter 구현
- `backend_harness/app/report_grounding.py`: report grounding validator 구현
- `backend_harness/app/schemas.py`: Pydantic request/response schema 구현
- `backend_harness/app/routes/`: FastAPI route modules 분리
- `GET /api/agent/runs/{run_id}/events`: SSE state/trace event endpoint와 `AgentStateEventResponse` payload schema 구현
- `backend_harness/app/tools/llm_client.py`: OpenAI Responses와 local chat-completions tool calling adapter 구현
- `POST /api/agent/runs`: LLM 설정 없으면 `configuration_required` 반환
- `FITCHECK_SEARCH_PROVIDER=jina_search`: Jina Search live provider 선택, `JINA_API_KEY` 또는 `FITCHECK_SEARCH_API_KEY` 필요
- `FITCHECK_SEARCH_PROVIDER=brave_search`: Brave Search API live provider 선택, `BRAVE_SEARCH_API_KEY` 또는 `FITCHECK_SEARCH_API_KEY` 필요
- `FITCHECK_SEARCH_PROVIDER=searxng`: self-hosted SearXNG search provider 선택, `FITCHECK_SEARXNG_URL` 또는 `SEARXNG_URL` 필요
- `FITCHECK_SEARCH_PROVIDER=duckduckgo_html`: no-key best-effort DuckDuckGo HTML adapter 선택
- Jina/Brave/SearXNG/DuckDuckGo live search 후보에 commerce score, risk flag, search priority metadata 추가
- live search는 `product_like_only` filter를 통과한 상품형 결과만 `candidates`로 노출하고, 블로그/기사/코디 글은 `rejected_candidates` 진단으로만 보존
- production agent route의 `search_web_candidates`는 `FITCHECK_ALLOW_CACHED_AGENT_SEARCH=1` 없이는 `cached_harness_search` 후보를 반환하지 않고 `LIVE_SEARCH_PROVIDER_REQUIRED`로 중단
- cached harness opt-in이 없으면 provider 실행 전 중단하여 fixed candidate cache 생성 부작용도 막음
- 예전 web-candidates deterministic demo route는 `disabled` 응답만 반환하며 agent flow로 사용하지 않음
- live search provider 실패는 `SEARCH_PROVIDER_FAILED`로 즉시 중단하고, 후보 0건 검색은 검색어 확장 guidance 후 같은 query 반복 시 `REPEATED_EMPTY_SEARCH`로 중단
- DuckDuckGo HTML adapter가 anomaly/challenge page를 받으면 `DUCKDUCKGO_HTML_CHALLENGE` 실패로 기록
- live page fetch에서 image URL 후보를 수집하고 size/material/detail OCR target 후보를 asset boundary로 노출
- live page fetch에서 OCR target 이미지를 `harness_outputs/live_assets/<candidate_key>/`에 다운로드하고 `asset_path`로 OCR provider에 전달
- `normalize_product_spec`가 `extract_size_material_info`의 OCR evidence를 사용해 비어 있는 실측/소재/가격 field를 보강하고, `ocr_evidence_used`와 `resolved_fields_from_ocr`를 남김
- `FITCHECK_OCR_PROVIDER=tesseract_cli`: 로컬 Tesseract CLI OCR adapter 선택
- `backend_harness/verify_live_provider_smoke.py`: 선택된 live search/OCR provider의 opt-in smoke verifier 구현
- `backend_harness/verify_search_provider_matrix.py`: Jina/Brave/SearXNG/DuckDuckGo provider 설정과 실행 결과를 비교하는 opt-in matrix verifier 구현
- `backend_harness/verify_ocr_provider_matrix.py`: fixture/Tesseract OCR provider 설정과 product/upload OCR 경계 결과를 비교하는 opt-in matrix verifier 구현
- agent trace/observation에 `tool_timeout_seconds`, `tool_risk`, `duration_ms`, provider `timeout_seconds`, `retry_count` metadata 정규화
- live search provider와 live page fetch에 제한적 retry policy 추가, `retry_count`, `retry_errors`, UI/SSE용 `retry_badges`, provider failure grouping용 `provider_error_code`/`provider_error_category` 기록
- production runtime verifier가 raw free text에서 `parse_user_goal` 없이 `search_web_candidates`를 첫 tool로 선택하는 경로를 검증
- runtime instructions와 initial model input이 `parse_user_goal`을 mandatory first step으로 요구하지 않고 raw input direct search를 허용한다고 검증
- `backend_harness/verify_input_variation_matrix.py`: 반팔 티셔츠, 와이드 데님 팬츠, 후드집업 입력이 서로 다른 model-selected `search_web_candidates` query로 이어지는지 검증
- `parse_user_goal`은 optional helper이므로 `goal` required arg를 요구하지 않고, `free_text` 단독 또는 현재 run context fallback으로 동작하는지 검증
- agent create/reply request는 비어 있거나 공백뿐인 turn을 Pydantic 422 validation으로 막아 LLM runtime까지 보내지 않음
- runtime `start_run()` 직접 호출도 빈 payload를 `invalid_request`로 거부해 run 생성과 LLM 호출을 하지 않음
- runtime `reply()` 직접 호출도 빈 payload를 `invalid_request`로 거부해 user reply trace/state mutation과 LLM 호출을 하지 않음
- 존재하지 않는 `candidate_key`로 후보 tool을 호출하면 `TOOL_EXECUTION_FAILED` 예외가 아니라 `CANDIDATE_NOT_FOUND` structured tool output과 교정 가능한 후보 key suggestion을 반환
- agent loop가 `max_turns` 안에 final/waiting/failed 상태로 수렴하지 않으면 `MAX_AGENT_TURNS_EXCEEDED` guardrail trace를 남기고 pending tool output을 정리
- tool output을 다음 LLM request로 전달하는 단계에서 provider timeout/exception이 나면 `LLM_RESPONSE_FAILED`로 종료하고 pending tool output을 정리
- 한 LLM 응답에 여러 `function_call`이 들어오면 각각 실행하고 `call_id`별 `function_call_output`을 다음 LLM 요청에 함께 전달하는지 검증
- local chat-completions adapter가 여러 `tool_calls`를 Responses-like `function_call` item으로 정규화하고, 후속 요청에서 `role=tool`/`tool_call_id` 메시지로 되돌리는지 검증
- runtime extractor가 chat-style nested `function.name`/`function.arguments` item을 직접 받아도 tool arguments를 보존하도록 검증
- deprecated chat-completions `message.function_call` 응답도 synthetic call id로 정규화하고 후속 요청에서 `role=function` 메시지로 되돌리는지 검증
- Responses-style 응답의 `reasoning` context item을 다음 tool output 요청에 함께 보존하고, local chat-completions adapter에서는 unsupported reasoning item을 user message로 변환하지 않는지 검증
- user reply가 들어오면 stale parsed closet state와 missing-info state를 정리하고, reply message/answer/attachment가 다음 LLM 요청과 closet memory/search tool path에 반영되는지 검증
- 사용자가 unconfirmed baseline identity 후보를 명시적으로 확인하면 `confirm_baseline_identity` backend tool 결과만으로 closet memory 저장 완료 리포트를 만들고, 추가 LLM ack 없이 provider response context를 confirmation `call_id` 기준으로 reset하는지 검증
- 최신 user reply 이후 새 `write_purchase_report` tool output이 없으면 과거 final report를 재사용하지 않고 `FINAL_REPORT_TOOL_REQUIRED`로 차단
- runtime `reply()`가 session mismatch를 `not_found`로 막아 route 밖 직접 호출에서도 cross-session state mutation을 방지
- free text별 brand/category/size/material/preference parsing과 `search_query_hint` 변화 검증은 optional parser/helper boundary로 유지
- `report_grounding.py`가 observation에 없는 숫자뿐 아니라 URL claim과 소재 claim도 거부
- `report_grounding.py`가 관측된 숫자/URL/소재 claim별 `grounding_evidence` path와 UI/SSE용 `citation_badges`를 반환
- `report_grounding.py`가 관측 claim이 하나도 없는 final text를 `report_contains_no_grounded_claims`로 거부
- `report_grounding.py`가 `전체/사이즈/소재/가격/신뢰도 N점` 문장을 score claim으로 분리하고 대응되는 `score.<field>` observation이 없으면 `report_contains_unobserved_score_claims`로 거부
- `report_grounding.py`가 관측되지 않은 명시적 상품명/브랜드 identity claim을 `report_contains_unobserved_identity_claims`로 거부
- `report_grounding.py`가 관측되지 않은 구체적 핏/색상/디자인 속성 claim을 `report_contains_unobserved_style_claims`로 거부
- `report_grounding.py`가 실측 근거 없는 similar fit/true-to-size/fits well 같은 확정적 핏 주장을 `report_contains_unobserved_fit_claims`로 거부
- `report_grounding.py`가 실측/소재/가격 근거 확인 같은 evidence coverage claim을 `evidence_coverage.observed_fields` 또는 정규화 spec field에 대조하고, 없으면 `report_contains_unobserved_evidence_claims`로 거부
- 상품명/후보 audit 목록의 pack/color/model 숫자는 치수/가격/점수 claim으로 취급하지 않도록 numeric extraction noise를 줄임
- max-turn fallback이 이미 생성된 all-candidate shortlist를 최종 리포트로 채택할 때 `write_purchase_report_all_from_observed_matches_after_max_turns` provenance를 유지
- `GroundingValidationResponse`, `GroundingEvidenceItemResponse`, `GroundingEvidenceSourceResponse`, `CitationBadgeResponse`: report grounding 결과, suspicious score/identity/style/fit/evidence claims, evidence path, citation badge의 Pydantic/OpenAPI schema 구현
- `ToolResultResponse`, `ToolResultErrorResponse`, `ToolSuggestionResponse`: tool observation result의 공통 status/error/provider retry/next-tool-suggestion 계약을 Pydantic/OpenAPI schema로 노출
- `SearchCandidateResponse`, `RejectedSearchCandidateResponse`: `search_web_candidates` result의 product-like 후보와 rejected content 후보를 OpenAPI schema로 노출
- HTTP smoke가 `/openapi.json`, `/api/agent/runs/{run_id}/report`, `/api/agent/runs/{run_id}/events`에서 grounding suspicious fields와 citation badges가 유지되는지 검증
- enrichment 전 `score_candidate_match`/`write_purchase_report` 조기 호출을 `score_deferred`와 `CANDIDATE_ENRICHMENT_REQUIRED_BEFORE_REPORT`로 차단하고 next tool suggestions 보존
- deferred missing-info/score/report result는 다음 LLM 요청에 `backend_runtime_guidance` input item으로 전달하고 `tool_guidance` trace로 기록
- `ToolDefinition.execute()`가 backend handler 실행 전 JSON-schema subset으로 tool arguments를 검증하고 `TOOL_ARGUMENT_VALIDATION_FAILED` observation을 반환
- `ToolDefinition.to_responses_tool()`이 현재 optional argument schema 보존을 위해 `strict=false`를 명시하고, local chat-completions adapter가 이 flag를 보존
- unknown model-selected tool call은 `UNKNOWN_TOOL` structured observation으로 저장하고, 같은 failed tool call이 2회 연속 반복되면 `REPEATED_FAILED_TOOL_CALL` guardrail로 run 종료
- function call arguments JSON parse 실패는 `TOOL_ARGUMENT_JSON_PARSE_FAILED`로 schema validation 실패와 분리해 observation/trace에 기록
- `backend_harness/verify_backend_infrastructure.py`: schema/store/upload/grounding 검증
- `backend_harness/verify_local_llm_tool_adapter.py`: local LLM tool-call 변환 검증
- `backend_harness/verify_production_agent_runtime.py`: LLM missing gate와 mock tool-calling loop 검증
- `backend_harness/verify_http_api_smoke.py`: opt-in HTTP smoke로 live server의 status/cookie persistence/OpenAPI response/requestBody schema refs/request-body validation/upload/detail-list-report-reply-SSE session isolation/not-found JSON/SSE route contract와 detail/SSE trace `call_id` correlation 검증
- `backend_harness/verify_live_agent_smoke.py`: opt-in HTTP smoke로 live `/api/agent/runs` route의 external LLM call, model-selected tool call, backend observation 저장, `tool_call`/`tool_output`/observation `call_id` correlation을 검증한다. POST 응답뿐 아니라 같은 session의 `GET /api/agent/runs/{run_id}` 재조회에서도 correlation을 다시 확인한다. Missing `call_id`는 trace 양쪽에 같은 빈 값이 있어도 correlation으로 인정하지 않고, `agentic_boundary_status`와 `completion_status`를 분리 보고한다.

## 1. 결론

현재 구현은 `ClothingPurchaseAgent`가 정해진 순서로 tool을 호출하는 deterministic harness입니다. 실제 AI agent 제품으로 만들려면 다음 구조로 바꿔야 합니다.

```text
사용자 목표
-> Agent Runtime
-> LLM Responses API 또는 Agents SDK
-> model이 다음 tool 선택
-> Backend가 tool 실행
-> tool output을 observation으로 저장
-> model에 observation 반환
-> 반복
-> 최종 후보 탐색/비교 리포트
```

OpenAI API는 reasoning, tool calling, built-in tools, stateful response primitive를 제공합니다. 하지만 쇼핑몰 검색, OCR, 상품 spec 정규화, 옷장 memory, match scoring, 사용자 질문, trace 저장은 FitCheck backend가 직접 소유해야 합니다.

컴투스 요구사항상 cloud OpenAI만 전제하지 않습니다. 같은 agent/tool 구조를 유지하되, 로컬 OpenAI-compatible LLM도 붙일 수 있어야 합니다.

```text
OpenAI cloud:
  Responses API
  hosted tools optional
  backend-owned function tools required

Local LLM:
  /v1/chat/completions tool calling
  hosted tools unavailable
  backend-owned function tools required
```

## 2. AI Agent 정의

기본 개념은 [What Is an AI Agent?](17-what-is-ai-agent.md)를 기준으로 합니다.

FitCheck에서 AI agent는 단순 챗봇이 아닙니다.

```text
챗봇:
사용자 입력 -> 답변

AI agent:
사용자 목표 -> 상태 파악 -> tool 선택 -> tool 실행 -> 결과 관찰 -> 다음 행동 결정 -> 반복 -> 완료
```

FitCheck agent의 최소 조건:

- LLM 설정이 없으면 실행하지 않고 `configuration_required`를 반환한다.
- LLM이 적어도 일부 단계에서 다음 tool을 직접 선택한다.
- tool 실행은 backend가 수행한다.
- tool result를 observation으로 저장하고 다음 LLM 판단에 반영한다.
- 핵심 정보가 부족하면 먼저 검색/fetch/OCR/정규화 도구로 보강하고, 그래도 해결되지 않을 때만 사용자 입력을 요청한다.
- 최종 리포트는 trace와 tool result를 근거로 작성한다.

## 3. 현재 구현과 목표 구현의 차이

| 항목 | 현재 MVP | 목표 agent |
| --- | --- | --- |
| Runtime | 고정 workflow | LLM tool-calling loop |
| LLM | 설정 경계와 fallback parser | 필수 설정, 실제 external API call |
| Tool 선택 | 코드가 순서를 결정 | LLM이 state를 보고 선택 |
| Search | provider boundary, cached harness는 explicit demo/test opt-in, product-like 결과만 후보로 노출 | live 검색 provider 또는 Playwright provider 추가 |
| OCR | provider boundary + fixture OCR provider, Tesseract adapter, OCR provider matrix verifier | live OCR provider 또는 vision model adapter 추가 |
| State | SQLite run store, observations, trace, user replies | multi-user production DB와 migration |
| Auth/session | 익명 session cookie/header | 사용자 auth와 계정별 closet memory |
| Streaming | SSE state/trace endpoint | token/step live streaming 고도화 |
| Schema | 핵심 route, trace entry, observation, SSE event, 공통 tool result, search candidate의 Pydantic schema와 OpenAPI 검증 | fetch/OCR/score/report별 세부 response model 강화 |
| Routes | `app/routes/` 분리 | domain별 service layer 추가 |
| Report grounding | 독립 validator | 더 강한 citation/span validator |
| 정보 부족 | need_more_info 계산 | agent가 먼저 검색/fetch/OCR/정규화 tool을 실행하고, 그래도 부족할 때만 `waiting_for_user`로 전환 |
| 검증 | 점수/추천 expectation | tool path, external call, trace, 질문, final report 검증 |

## 4. OpenAI 제품 선택

### 권장 기본 경로: Responses API

Responses API는 agent-like application을 위한 통합 인터페이스이며, custom functions와 built-in tools를 함께 사용할 수 있습니다. FitCheck v1은 단일 agent와 명확한 tool set이므로 Responses API로 시작하는 것이 가장 단순합니다.

사용 기준:

- 단일 FitCheck agent
- backend가 tool 실행과 state 저장을 소유
- custom function tools 중심
- 필요 시 built-in web search 또는 file search 사용

### 로컬 LLM 경로: Chat Completions tool calling

로컬 Ollama, LM Studio, vLLM 등 OpenAI-compatible server는 대부분 `/v1/chat/completions`를 먼저 지원합니다. 따라서 FitCheck runtime은 Responses API tool schema를 chat-completions tool schema로 변환하고, local model의 `tool_calls`를 Responses-like `function_call` item으로 정규화합니다.

`verify_local_llm_tool_adapter.py`는 local chat-completions 응답의 여러 `tool_calls`가 `call_id`를 보존한 `function_call` item으로 변환되고, tool output follow-up 요청에서는 각각 `role=tool`, `tool_call_id=<원래 id>` 메시지로 제출되는지 확인합니다.

같은 verifier는 구형 호환 서버가 deprecated `message.function_call`을 반환할 때도 synthetic call id를 가진 Responses-like `function_call`으로 정규화하고, 후속 tool output을 deprecated `role=function`, `name=<function_call.name>` 메시지로 제출하는지 확인합니다.

호환 구현이 adapter를 거치지 않고 chat-style nested `function.name`/`function.arguments` item을 runtime에 넘기는 경우도 `verify_production_agent_runtime.py`에서 확인합니다. 이 경계는 검색 query 같은 tool arguments가 `{}`로 손실되는 것을 막기 위한 것입니다.

Responses-style reasoning model이 tool call과 함께 `reasoning` context item을 반환하면 runtime은 해당 item을 다음 LLM 요청의 `function_call_output`과 함께 보존합니다. local chat-completions 경로는 이런 Responses 전용 context item을 지원하지 않으므로 adapter가 user JSON message로 변환하지 않고 건너뜁니다.

로컬 LLM 모드의 원칙:

- `FITCHECK_LLM_API=chat_completions`
- hosted `web_search`, `file_search`, `code_interpreter`, hosted shell은 사용하지 않음
- 모든 도구 실행은 `backend_owned_functions`
- local model이 tool call 없이 최종 답변을 내면 `NO_TOOL_CALLS_BEFORE_FINAL`로 거부
- local model이 tool calling을 안정적으로 지원하지 않으면 production agent로 인정하지 않음

### 확장 경로: Agents SDK

Agents SDK는 orchestration, handoff, guardrail, tracing, multi-agent 구성이 필요해질 때 사용합니다. OpenAI 공식 Codex manual도 Codex MCP를 Agents SDK와 연결해 multi-agent team, handoff, guardrail, trace workflow를 구성하는 사례를 설명합니다. 이 사례는 개발 자동화에는 적합하지만, FitCheck 제품 런타임에는 backend-owned commerce/search/OCR/scoring/report boundary를 유지해야 합니다.

사용 기준:

- SearchAgent, OcrAgent, MatchAgent처럼 specialist를 분리해야 함
- agent handoff가 필요함
- SDK trace와 eval workflow를 적극적으로 쓰고 싶음
- production agent workflow를 더 체계적으로 관리해야 함

도입 gate:

- local OpenAI-compatible provider에서도 같은 tool-calling 계약을 유지할 수 있어야 함
- backend가 tool execution, SQLite run state, observations, SSE trace, report grounding을 계속 소유해야 함
- `/api/status.orchestration.agents_sdk.fitcheck_fit == "conditional"`이고 `FITCHECK_ENABLE_AGENTS_SDK_RUNTIME=1` 같은 명시 opt-in 전에는 product runtime으로 켜지지 않아야 함
- `verify_production_agent_runtime.py`, `verify_backend_infrastructure.py`, live smoke가 기존 Responses runtime과 동등한 증거를 보여야 함

### 비권장 경로: Codex 자체를 제품 agent로 사용

Codex SDK, Codex app-server, Codex MCP server는 존재하지만, FitCheck 사용자의 의류 탐색/비교 agent를 만들기 위한 1차 선택지는 아닙니다.

OpenAI Codex SDK 문서는 Codex SDK를 CI/CD pipeline, 내부 개발 workflow, Codex coding session 제어에 쓰는 표면으로 설명합니다. Python SDK도 local Codex app-server를 JSON-RPC로 제어합니다. 따라서 Codex SDK는 FitCheck 개발/검증 orchestration에는 쓸 수 있지만, end-user product runtime orchestrator로 쓰면 안 됩니다.

현재 구현 상태:

- `/api/status.orchestration.current_runtime == "fitcheck_backend_owned_tool_loop"`
- `/api/status.orchestration.recommended_path == "responses_api_primary_agents_sdk_optional_codex_sdk_development_only"`
- `/api/status.orchestration.codex_sdk.enabled_for_product_runtime == false`
- `backend_harness/app/orchestration_capabilities.py`가 Responses API, Agents SDK, Codex SDK의 역할과 설치 상태를 제품 상태로 노출
- `/api/agent/runs/{run_id}/sdk-events`는 Codex/하네스식 read-model projection을 제품 런타임에 적용한 표면이다. raw trace를 `model_response`, `tool_progress`, `agent_guardrail`, `session_state_changed` 같은 SDK-like event로 변환하고 `after` cursor를 지원한다.
- 완료된 run의 `/sdk-events`는 `final_output` 이벤트를 포함한다. 이 이벤트는 grounded 여부, report type, grounding validation 요약, citation badges를 노출해 SDK-like client가 이벤트 스트림만으로 최종 리포트의 근거 상태를 확인할 수 있게 한다.
- `/sdk-events.after`는 raw source event sequence 기준의 exclusive cursor이다. 응답은 `cursor_policy`, `projected_event_count`, `terminal_event_seq`, `terminal_event_included`, `final_output_included`를 노출한다. Terminal raw event만 포함하는 delta에서도 `final_output`이 재생되어야 하며, `after=next_after` caught-up 요청은 raw/projected event 0개와 `final_output_included=false`를 반환해야 한다.
- 이 endpoint는 제품 실행을 Codex SDK에 위임하지 않는다. FitCheck backend-owned tool loop의 SQLite trace/evidence를 클라이언트가 재개 가능한 형태로 읽게 하는 projection이다.
- `verify_backend_infrastructure.py`와 `verify_http_api_smoke.py`가 이 정책을 검증

## 5. Target Architecture

```text
[Web UI]
  - 기준 옷 입력
  - 후보 URL/자유 텍스트 입력
  - agent 진행 상태
  - 추가 질문 답변
  - 최종 리포트
        |
        v
[FastAPI Backend]
  - auth/session
  - run creation
  - streaming/status endpoint
  - user reply endpoint
  - report endpoint
        |
        v
[Agent Runtime]
  - run state load/save
  - LLM request construction
  - tool schema injection
  - tool call dispatch
  - observation append
  - stop condition
  - guardrails
        |
        v
[Tool Registry]
  - closet tools
  - search/fetch tools
  - OCR/vision tools
  - normalize tools
  - matcher tools
  - question/report tools
        |
        v
[Storage]
  - users
  - closet_items
  - agent_runs
  - agent_steps
  - tool_observations
  - products
  - match_reports
  - uploaded_assets
```

## 6. Runtime Files

목표 파일 구조:

```text
backend_harness/app/
  agent_runtime.py
  agent_state.py
  tool_registry.py
  trace_store.py
  memory_store.py
  llm_runtime_client.py
  routes/
    agent_routes.py
  tools/
    closet_tools.py
    search_tools.py
    fetch_tools.py
    ocr_tools.py
    normalize_tools.py
    matcher_tools.py
    question_tools.py
    report_tools.py
```

현재 파일에서의 전환:

| 현재 파일 | 전환 방향 |
| --- | --- |
| `app/clothing_purchase_agent.py` | 고정 workflow 제거, `agent_runtime.py`가 tool loop 담당 |
| `app/tools/llm_client.py` | Responses API client와 tool call handling으로 확장 |
| `web_server.py` | `/api/agent/runs` 계열 route로 분리 |
| `webapp/app.js` | run 생성, status polling, question reply, trace rendering으로 변경 |
| `verify_agent_runtime.py` | LLM external call, tool path, waiting state 검증 추가 |

## 7. Agent State Model

```json
{
  "run_id": "run_01",
  "user_id": "user_01",
  "status": "running",
  "goal": "좋아하는 기준 옷과 비슷한 후보를 찾아 비교",
  "input": {
    "free_text": "",
    "candidate_url": "",
    "uploaded_asset_ids": []
  },
  "closet_memory": {
    "selected_item_ids": [],
    "baseline_item": null
  },
  "candidate_products": [],
  "observations": [],
  "missing_info": [],
  "match_results": [],
  "final_report": null,
  "llm": {
    "provider": "openai",
    "model": "configured_model",
    "external_api_called": true
  },
  "trace": []
}
```

상태 전이:

```text
created
-> running
-> waiting_for_user
-> running
-> completed

created
-> running
-> failed

created
-> configuration_required
```

## 8. Tool Registry

각 tool은 JSON schema, Python handler, timeout, retry policy, risk class를 가집니다.

```python
ToolDefinition(
    name="score_candidate_match",
    description="Compare a candidate product against a closet baseline.",
    schema={
        "type": "object",
        "properties": {
            "candidate_product_id": {"type": "string"},
            "closet_item_id": {"type": "string"}
        },
        "required": ["candidate_product_id", "closet_item_id"]
    },
    handler=score_candidate_match,
    timeout_seconds=10,
    risk="low"
)
```

Tool 실행 원칙:

- LLM은 tool name과 arguments만 제안한다.
- function call arguments JSON string parse 실패는 handler 실행 전에 `TOOL_ARGUMENT_JSON_PARSE_FAILED`로 기록한다.
- backend는 schema validation 후 tool을 실행한다. validation 실패는 Python 예외가 아니라 `TOOL_ARGUMENT_VALIDATION_FAILED` structured tool output으로 기록한다.
- registry에 없는 tool name은 `UNKNOWN_TOOL` structured tool output으로 기록한다.
- tool output은 항상 structured observation으로 저장한다.
- destructive action은 FitCheck v1에서 허용하지 않는다.
- external network tool은 timeout과 retry limit을 둔다.
- 현재 tool schema는 OpenAI Responses와 local chat-completions 양쪽에서 optional argument 의미를 유지하기 위해 `strict=false`를 명시한다. OpenAI Responses는 `strict`를 생략하면 schema를 strict 쪽으로 정규화할 수 있고, strict mode는 모든 property를 required로 요구하므로 `parse_user_goal`, `load_closet_memory`, `inspect_uploaded_images` 같은 optional-helper tool의 의미가 깨질 수 있다. strict-mode 전환은 optional field를 nullable required field로 재작성한 뒤 별도 migration으로 진행한다.
- 같은 failed tool call을 동일 arguments와 error code로 2회 연속 반복하면 `REPEATED_FAILED_TOOL_CALL`로 run을 종료한다.

## 9. Backend-Owned Tools

아래 tool은 production agent가 선택할 수 있는 backend-owned function tool이다. 목록에 있다고 해서 모든 run에서 고정 순서로 실행되는 것은 아니다. 특히 `parse_user_goal`은 필수 parser gate가 아니라 선택 가능한 정규화/메모리 보조 tool이며, 모델은 원문 사용자 입력만 보고 바로 `search_web_candidates`를 호출할 수 있어야 한다.

### `parse_user_goal`

사용자 입력을 goal, candidate source, baseline hints로 나누는 optional helper다. Production control plane은 이 tool을 필수 선행 단계로 요구하지 않는다.

`goal`은 required argument가 아니다. 모델이 원문 사용자 입력을 이미 보고 있으므로 `free_text`만 넘기거나 빈 `{}`를 넘겨도 runtime은 현재 run context에서 `goal`, `free_text`, `candidate_url`, attachment metadata를 읽어 정규화할 수 있어야 한다.

입력:

```json
{
  "free_text": "이 후드티 사도 돼? 총장 70..."
}
```

출력:

```json
{
  "goal_type": "similar_clothing_discovery",
  "candidate_hints": {},
  "baseline_hints": {},
  "missing_initial_inputs": []
}
```

### `load_closet_memory`

사용자의 기존 기준 옷을 불러온다.

출력:

```json
{
  "closet_items": [
    {
      "id": "closet_01",
      "category": "tshirt",
      "measurements": {
        "length": 76,
        "shoulder": 57,
        "chest": 62,
        "sleeve": 25
      },
      "material": {},
      "preference_reasons": ["핏", "오래 입어도 괜찮음"]
    }
  ]
}
```

### `search_web_candidates`

브랜드, 상품명, 키워드로 후보 상품을 찾는다.

정책:

- 검색어와 source URL을 저장한다.
- 검색 결과는 confidence와 함께 저장한다.
- 같은 상품으로 보이는 결과는 canonical candidate로 묶는다.

### `fetch_product_page`

상품 상세 HTML, 가격, 이미지 URL, 옵션 텍스트를 가져온다.

출력:

```json
{
  "url": "https://...",
  "title": "...",
  "price": 59000,
  "image_urls": [],
  "raw_text": "...",
  "fetch_status": "completed"
}
```

### `classify_product_assets`

이미지를 OCR 대상과 비대상으로 나눈다.

분류:

```text
outfit_image
product_image
size_chart
material_chart
detail_text
banner
unknown
```

### `extract_size_material_info`

사이즈표, 소재표, 상세 설명 이미지만 OCR/vision 처리한다.

출력:

```json
{
  "raw_text": "총장 76 어깨 59...",
  "normalized_candidates": {
    "measurements": {},
    "material": {}
  },
  "confidence": 0.82
}
```

### `normalize_product_spec`

HTML/OCR/review 정보를 `ProductSpec`으로 합친다.

OCR evidence는 `extract_size_material_info` observation에서 가져온다. `normalize_product_spec`는 OCR text에서 실측, 소재, 가격 후보를 다시 파싱하고, 기존 HTML/page 관측값을 무조건 덮지 않고 비어 있는 핵심 field만 채운다. 병합 결과에는 `ocr_evidence_used`, `resolved_fields_from_ocr`, 갱신된 `missing_fields`, `field_confidence`를 포함한다.

```json
{
  "candidate_product_id": "product_01",
  "category": "tshirt",
  "measurements": {
    "length": 76,
    "shoulder": 59,
    "chest": 66,
    "sleeve": 27
  },
  "material": {
    "cotton": 100
  },
  "price": 39000,
  "missing_fields": [],
  "confidence": 0.89
}
```

### `score_candidate_match`

기준 옷과 후보 상품을 deterministic하게 비교한다.

점수 구성:

```text
size: 45%
material: 20%
price: 15%
confidence: 20%
```

LLM은 이 점수 계산을 직접 하지 않는다. 계산 가능한 비교는 backend가 처리한다.

검색 직후 enrichment tool을 건너뛰고 호출되면 점수를 만들지 않는다. 이 경우 backend는 `score_deferred=true`, `reason=candidate_enrichment_required_before_scoring`, `next_tool_suggestions`를 반환한다.

### `ask_missing_info`

필드별 고정 질문 생성기가 아니다. 검색 전이면 원문 입력과 현재 state를 바탕으로 `load_closet_memory`, `inspect_uploaded_images`, `search_web_candidates` 같은 evidence tool을 먼저 제안하고, 검색 이후에는 상세페이지 fetch, 이미지/OCR, 정규화 tool을 먼저 실행한다. `parse_user_goal`은 모델이 필요할 때 선택할 수 있지만 질문 전 필수 선행 단계는 아니다. 이 tool enrichment 이후에도 핵심 정보가 없을 때만 마지막 수단으로 unresolved evidence 질문을 만든다.

enrichment 전에 호출되면 `waiting_for_user`로 전환하지 않는다. Runtime은 반환된 `next_tool_suggestions`를 `backend_runtime_guidance` input item으로 다음 LLM 요청에 포함하고, 같은 내용을 `tool_guidance` trace로 남긴다. 이는 backend가 직접 점수나 리포트를 꾸미는 fallback이 아니라, 모델이 다음 turn에서 필요한 backend-owned tool을 선택하도록 만드는 recovery signal이다. 같은 조기 `ask_missing_info`를 동일 인자로 반복하면 `REPEATED_PREMATURE_MISSING_INFO` guardrail로 종료한다.

예:

```json
{
  "status": "completed",
  "deferred_user_question": true,
  "reason": "candidate_fields_must_be_enriched_with_tools_before_asking_user",
  "next_tool_suggestions": [
    {
      "tool": "fetch_product_page",
      "arguments": {"candidate_key": "candidate_001"}
    }
  ]
}
```

### `write_purchase_report`

최종 tool results를 근거로 리포트를 작성한다.

입력에는 score, deltas, missing fields, risks, sources만 넣는다. LLM에게 원문 전체를 무제한으로 넘기지 않는다.

직전 score가 deferred 상태이면 report 생성을 차단한다. 이때 `CANDIDATE_ENRICHMENT_REQUIRED_BEFORE_REPORT`와 기존 `next_tool_suggestions`를 반환해 모델이 다음 turn에서 `fetch_product_page`, `classify_product_assets`, `extract_size_material_info`, `normalize_product_spec`를 실행하도록 한다.

## 10. Agent Loop

### Context Planner And Source References

사용자 입력에서 URL, 브랜드, 구매 이력, 사이즈, 검색 의도, 기준 상품인지 후보 상품인지를 정리하는 단계는 별도 context planner 성격의 backend-owned tool로 다룬다.

이 planner는 최종 추천을 만들지 않는다. 역할은 다음 run state를 갱신하는 것이다.

- `context_snapshot`: 원문 입력과 reply를 dedupe한 뒤 brand/category/color/size/search intent/source role을 정규화한다.
- `closet_item.source_url`: 사용자가 "이거 L 사이즈 구매했었는데"처럼 기준 상품 URL을 주면 baseline source로 저장한다.
- `user_supplied_candidate_url`: 사용자가 후보 URL을 보낸 경우 candidate source로 등록한다.
- `search_constraints`: 검색 provider가 사용할 brand/category/size/style/material query constraints를 저장한다.
- `source_refs`: URL, attachment id, OCR asset path, product candidate key처럼 나중에 tool이 다시 load할 수 있는 lightweight identifier를 저장한다.

LLM context에는 전체 HTML, 이미지, 긴 대화 원문을 계속 넣지 않는다. 필요한 요약과 source reference만 넣고, 상세 데이터는 backend tool이 source reference를 기준으로 fetch/OCR/normalize한다.

Codex의 subagent 패턴과 유사하게 noisy/read-heavy 작업은 분리할 수 있지만, FitCheck v1에서는 바로 multi-agent로 쪼개기보다 `plan_context_and_sources` 같은 단일 planner tool을 먼저 둔다. 여러 후보를 병렬로 깊게 조사하거나 검색 query 개선을 독립적으로 평가해야 할 때 SearchPlanner/OcrInspector 같은 subagent로 승격한다.

현재 구현 메모:

- `plan_context_and_sources` tool은 run 입력과 reply에서 baseline URL, candidate URL, attachment ref, search constraints를 정리해 `state.source_refs`, `state.search_constraints`, `state.context_plan`에 저장한다.
- baseline URL이 확인되면 `closet_item.source_url`로 승격하고, `enrich_closet_item_from_web`는 검색을 건너뛰고 사용자 제공 URL을 직접 fetch/OCR/normalize한다.
- `parse_user_goal`은 여전히 optional helper이며, 모델은 raw input이 충분하면 바로 검색하거나 planner tool을 선택할 수 있다.

```python
def run_agent(run_id: str) -> AgentRun:
    state = store.load_run(run_id)
    assert_llm_configured()

    while state.status == "running":
        response = llm.responses_create(
            instructions=FITCHECK_AGENT_INSTRUCTIONS,
            input=build_model_input(state),
            tools=tool_registry.schemas(),
            previous_response_id=state.previous_response_id,
        )

        state.record_llm_response(response)

        tool_calls = extract_tool_calls(response)
        if not tool_calls:
            state.final_report = extract_final_report(response)
            state.status = "completed"
            break

        for call in tool_calls:
            tool = tool_registry.get(call.name)
            args = tool.validate(call.arguments)
            result = tool.execute(args)
            state.add_observation(call_id=call.id, result=result)

            if result.status == "waiting_for_user":
                state.status = "waiting_for_user"
                break

        store.save_run(state)

    return state
```

Stop 조건:

- final report 생성
- `waiting_for_user` 상태 진입
- max tool calls 초과
- max runtime 초과
- fatal tool error
- LLM configuration missing

비슷한 핏/실측 후보 탐색에서는 LLM turn limit이 곧 검색 종료를 의미하지 않는다.
`score_candidate_match` 또는 `write_purchase_report`가 no-close-fit 상태와
`search_web_candidates` recovery suggestion을 반환하면, runtime은 먼저 backend-owned recovery
검색과 `score_candidate_match(candidate_key="all")` 재평가를 실행한다. 약한 후보 보고서는
close-fit 검색 예산(`FITCHECK_CLOSE_FIT_SEARCH_ATTEMPTS`)이 소진되었거나 더 이상 recovery
suggestion이 없을 때만 허용한다.

기본 제한:

```text
max_tool_calls_per_run = 12
max_runtime_seconds = 90
max_retries_per_tool = 2
max_candidate_products = 8
close_fit_measurement_search_attempts = 24
```

## 11. Prompt Contract

System instruction 핵심:

```text
You are FitCheckAgent.
Your job is to find and compare clothing candidates using tools.
Do not invent measurements, material, price, or reviews.
Use tools before recommending or ranking candidates.
Do not treat parse_user_goal as a mandatory first step; call search_web_candidates directly from raw user text when cues are sufficient.
If critical fields are missing, first use search/fetch/OCR/normalize tools. Call ask_missing_info only after tool enrichment is exhausted.
Final reports must cite tool observations by step id.
Never claim a product is safe when required fields are missing.
```

LLM이 직접 하면 안 되는 것:

- 실측 차이 수치 계산을 임의로 생성
- 소재 비율 추정
- 가격 추정
- OCR 결과 없는 사이즈표 해석
- source 없는 상품 정보 생성

## 12. API Design

### `POST /api/agent/runs`

새 agent run을 생성한다.

`goal`, `free_text`, `candidate_url`, `attachments` 중 최소 하나가 필요하다. `{}` 또는 공백 문자열뿐인 route 요청은 agent 작업이 아니므로 Pydantic validation에서 422로 거부하고 LLM runtime을 호출하지 않는다. Runtime `start_run()`을 직접 호출해도 빈 payload는 `invalid_request` / `INITIAL_USER_INPUT_REQUIRED`로 거부되고 run store에 저장하지 않는다.

요청:

```json
{
  "user_id": "demo_user_001",
  "goal": "이 옷 사도 되는지 판단해줘",
  "candidate_url": "https://...",
  "free_text": "회색 후드티...",
  "baseline_closet_item_id": "closet_01"
}
```

응답:

```json
{
  "run_id": "run_01",
  "status": "running"
}
```

LLM 설정이 없을 때:

```json
{
  "run_id": null,
  "status": "configuration_required",
  "missing": [
    "OPENAI_API_KEY",
    "FITCHECK_LLM_MODEL"
  ]
}
```

### `GET /api/agent/runs/{run_id}`

현재 상태, trace, 중간 결과를 조회한다.

### `GET /api/agent/runs/{run_id}/events`

Server-Sent Events 또는 streaming endpoint로 step 진행 상황을 보낸다.

현재 endpoint는 `id`, `event: agent_state`, `retry`, JSON `data` frame을 보내며, payload는 `AgentStateEventResponse`로 검증된다. 변경이 없는 running stream에서는 keepalive comment를 보낼 수 있다. trace 항목은 `TraceEntryResponse`, tool observation은 `ToolObservationResponse` 형태로 노출된다. HTTP smoke는 owner session의 seeded run SSE가 completed event를 반환하고 다른 session에서는 `not_found` event를 반환하는지, 그리고 SSE id/retry/no-buffer header가 유지되는지 확인한다.

### `POST /api/agent/runs/{run_id}/reply`

agent가 질문한 정보에 사용자가 답한다.

`message`, `answers`, `attachments` 중 최소 하나가 필요하다. 빈 reply는 대화 turn이 아니므로 route에서는 422로 거부한다. Runtime `reply()`를 직접 호출해도 빈 payload는 `invalid_request` / `REPLY_USER_INPUT_REQUIRED`로 거부되고, `user_reply` trace나 `input.user_replies`를 추가하지 않으며 LLM을 호출하지 않는다. HTTP smoke는 다른 session이 seeded run에 reply를 보내도 `not_found`가 반환되고 owner run이 재개되지 않는지, 빈 reply가 LLM runtime 전에 validation으로 막히는지 확인한다.

요청:

```json
{
  "answers": [
    {
      "field": "candidate.measurements.length",
      "value": 70
    }
  ]
}
```

### `GET /api/agent/runs/{run_id}/report`

최종 후보 탐색/비교 리포트를 조회한다.

## 13. Storage Design

MVP는 SQLite, production은 PostgreSQL을 권장합니다.

### `agent_runs`

```text
id
user_id
status
goal
input_json
state_json
llm_provider
llm_model
created_at
updated_at
completed_at
```

### `agent_steps`

```text
id
run_id
step_index
step_type        # llm_response | tool_call | tool_output | user_reply
tool_name
status
input_json
output_json
duration_ms
created_at
```

`tool_call` trace input과 `tool_output` trace output은 model-selected call과 backend result를 연결할 수 있도록 같은 `call_id`를 포함한다. 같은 LLM 응답에 여러 function call이 들어온 경우에도 observation과 다음 LLM 요청의 `function_call_output.call_id`가 같은 값을 유지해야 한다. Runtime verifier는 이 `tool_output` trace를 `AgentStateEventResponse.latest_trace` payload로도 검증해 API/SSE schema serialization 후에도 correlation id가 빠지지 않는지 확인한다.

### `closet_items`

기존 `docs/04-data-model.md`의 closet item 구조를 유지하되, source와 confidence 필드를 강화합니다.

### `product_specs`

```text
id
run_id
source_url
title
price
measurements_json
material_json
raw_source_refs_json
confidence
missing_fields_json
created_at
```

### `match_reports`

```text
id
run_id
closet_item_id
product_spec_id
recommendation
score_json
risks_json
questions_json
summary
created_at
```

## 14. UI Design

첫 화면은 marketing page나 분석 대시보드가 아니라 실제 agent와 대화하는 chat-first workspace입니다. 사용자는 GPT식 대화창에 후보 옷 URL, 캡처 설명, 기준 옷 정보를 자연어로 입력하고, agent는 필요한 tool call을 실행하거나 부족한 정보를 다시 질문합니다.

필수 영역:

- 대화 메시지 timeline
- 후보 URL/자유 텍스트 입력 composer
- 이미지 첨부/capture 입력
- 새 대화/run reset
- run status sidebar
- LLM configuration state
- live trace sidebar
- missing info 질문 sidebar와 대화 답변 흐름
- final report message

후보 카드, 기준 옷 memory editor, OCR asset viewer는 대화 흐름을 방해하지 않는 보조 view로 둡니다. 모바일에서는 chat workspace가 먼저 나오고 runtime/trace/missing-info 패널은 아래로 내려갑니다.

이미지 입력 정책:

- 모바일은 composer의 이미지 입력에서 카메라 capture를 허용한다.
- 데스크톱은 같은 `+` 버튼으로 이미지 파일을 첨부한다.
- 업로드 이미지는 `/api/uploads/images`에 저장하고 run payload의 `attachments`로 전달한다.
- 기본 OCR은 `fixture_ocr_boundary`이므로 `inspect_uploaded_images`는 metadata와 `vision_status=not_connected`를 반환한다.
- `FITCHECK_OCR_PROVIDER=tesseract_cli`가 설정되고 로컬 `tesseract` 실행 파일이 있으면 업로드 이미지 OCR evidence를 observation에 저장한다.
- agent는 이미지 metadata만 보고 색상, 핏, 사이즈, 소재, 상태를 사실로 말하면 안 된다. OCR text만 근거로 사용할 수 있고 pixel-level vision claim은 vision provider가 연결된 뒤에만 가능하다.

상태별 UI:

```text
configuration_required:
  LLM API 설정 필요 표시

running:
  현재 호출 중인 tool과 step 표시

waiting_for_user:
  자동 검색/fetch/OCR 이후에도 해결되지 않은 근거에 대해서만 질문 form 표시

completed:
  최종 판정, 근거, trace 표시

failed:
  실패 원인, 마지막 성공 step, 재시도 버튼 표시
```

## 15. Guardrails

### 정보 부족 guardrail

다음 필드가 없으면 `buy`를 금지합니다.

```text
candidate.measurements.length
candidate.measurements.shoulder
candidate.measurements.chest
candidate.material
```

후보 카테고리에 따라 필수 실측은 달라질 수 있습니다.

### Hallucination guardrail

LLM final report에 포함되는 모든 수치와 source는 tool observation에 존재해야 합니다.

검증 방식:

- final report 생성 전 `validate_report_grounding` 실행
- 없는 수치, 없는 URL, 없는 소재가 있으면 재작성 요청
- `grounding_evidence`는 원본 observation path/value를 담고, `citation_badges`는 `[G1]` 같은 UI/SSE 표시용 축약 citation을 담는다.
- Provider retry 상태는 raw `retry_count`/`retry_errors`와 별도로 `retry_badges`에 `not_retried`, `retried`, `retry_failed`, `not_started`, `failed_no_retry` 상태로 노출한다.
- Provider 실패 원인은 `provider_error_code`와 `provider_error_category`로 정규화한다. 예: `HTTP_429_RATE_LIMITED`/`rate_limited`, `HTTP_503_SERVICE_UNAVAILABLE`/`provider_server_error`, `REQUEST_TIMEOUT`/`timeout`, `REQUEST_CONNECTION_ERROR`/`connection_error`, `SEARCH_PROVIDER_API_KEY_REQUIRED`/`configuration`, `DUCKDUCKGO_HTML_CHALLENGE`/`challenge`, `OCR_PROVIDER_NOT_CONNECTED`/`provider_not_connected`, `OCR_TESSERACT_CLI_NOT_FOUND`/`configuration`.

### Privacy guardrail

사용자의 옷장 memory, 업로드 이미지, 탐색 후보 URL은 user_id 단위로 분리합니다.

### Cost guardrail

- 후보 개수 제한
- OCR 대상 이미지 제한
- LLM max turns 제한
- 같은 URL cache
- prompt에 raw HTML 전체를 넣지 않고 extracted snippet만 사용

## 16. Error Handling

| 상황 | 처리 |
| --- | --- |
| LLM API key 없음 | `configuration_required` |
| LLM endpoint timeout | retry 후 `failed` 또는 fallback 금지 |
| search 실패 | 제한적 retry 후 failed observation 저장, 다른 search provider 시도 또는 사용자 URL 요청 |
| OCR 실패 | OCR 대상 이미지와 실패 사유 저장, 질문 생성 |
| 실측 누락 | 검색/fetch/OCR/정규화 보강 후에도 없으면 `waiting_for_user` 또는 `need_more_info` |
| tool arguments JSON parse 실패 | `TOOL_ARGUMENT_JSON_PARSE_FAILED` failed observation/trace 저장, raw argument excerpt 기록 |
| tool schema validation 실패 | `TOOL_ARGUMENT_VALIDATION_FAILED` failed observation/trace 저장, LLM에 structured tool output으로 재시도 요청 |
| unknown tool call | `UNKNOWN_TOOL` failed observation/trace 저장, available tool list 반환 |
| 같은 failed tool call 반복 | `REPEATED_FAILED_TOOL_CALL` guardrail로 run failed |
| max turns 초과 | close-fit/recovery guidance가 남아 있으면 backend recovery와 재스코어링을 먼저 실행한다. recovery가 없거나 모두 소진되면 `MAX_AGENT_TURNS_EXCEEDED` guardrail trace 저장, pending tool outputs 정리, run failed |
| LLM response timeout/exception | `LLM_RESPONSE_FAILED` failed trace와 `stop_llm_response_failed` guardrail trace 저장, pending tool outputs 정리, run failed |

중요: production agent에서는 LLM이 없을 때 후보 탐색/비교를 fallback으로 완료하지 않습니다. fallback parser는 개발/데모 보조 수단으로만 둡니다. LLM이 있는 production route에서도 parser를 고정 선행 단계로 두지 않고, 원문 대화에서 바로 search/fetch/OCR tool을 선택할 수 있어야 합니다.

또한 production agent에서는 live search provider 없이 cached harness 후보를 기본 검색 결과로 사용하지 않습니다. `FITCHECK_SEARCH_PROVIDER`가 live provider로 설정되지 않았고 `FITCHECK_ALLOW_CACHED_AGENT_SEARCH=1`도 없으면 `search_web_candidates`는 `LIVE_SEARCH_PROVIDER_REQUIRED`를 반환하고 run은 `configuration_required`에서 멈춥니다.

이 gate는 cached provider를 실행한 뒤 결과를 버리는 방식이 아니라 provider 실행 전에 멈춥니다. 따라서 특정 상품 후보 cache를 생성하거나 stale fixture를 observation에 노출하는 부작용이 없습니다. legacy `/api/run/web-candidates`와 `/api/results/web-candidates` route는 fixed 후보 demo surface였기 때문에 `disabled` 응답만 반환합니다.

Live search provider가 실패하면 run은 `SEARCH_PROVIDER_FAILED`로 멈추고 provider error를 trace에 남깁니다. Trace output과 observation에는 raw retry metadata, `retry_badges`, `provider_error_code`, `provider_error_category`를 함께 남겨 UI/SSE 소비자가 provider retry와 실패 원인을 바로 표시할 수 있습니다. SearXNG provider는 self-hosted instance의 `/search?format=json` endpoint를 사용하며, URL 미설정 또는 instance의 JSON output 비활성화는 configuration/request failure로 처리합니다. Search provider가 반환한 일반 웹 결과 중 블로그, 기사, 코디 글, 커뮤니티처럼 상품 상세페이지 가능성이 낮은 결과는 `rejected_candidates`에만 남기고 `candidates`에는 넣지 않습니다. 따라서 후보 0건은 "검색 결과 없음"이 아니라 "상품형 후보 없음"을 뜻합니다. 후보 0건 검색은 첫 번째에는 `search_query_expansion_required` guidance와 `next_tool_suggestions`를 반환해 모델이 더 넓은 query로 다시 검색하도록 하며, 같은 빈 query를 반복하면 `REPEATED_EMPTY_SEARCH`로 종료합니다. 서로 다른 query라도 연속 빈 검색 attempt가 limit을 넘으면 `EMPTY_SEARCH_ATTEMPT_LIMIT_EXCEEDED`로 종료해 query expansion 비용 루프를 막습니다.

## 17. Verification Plan

### Unit tests

- tool schema validation
- invalid candidate key structured failure
- HTTP request body validation
- material parser
- measurement parser
- match scoring
- report grounding validator

### Integration tests

- LLM 설정 없으면 `configuration_required`
- LLM mock이 `load_closet_memory -> search_web_candidates -> fetch_product_page -> classify_product_assets -> extract_size_material_info -> normalize_product_spec -> score_candidate_match -> write_purchase_report` 순서로 tool call
- tool output이 observation으로 저장됨
- missing field 발생 시 score/report 전에 search/fetch/OCR/normalize 보강이 먼저 발생함
- SSE event stream이 `id`/`event`/`retry`/`data` frame과 no-buffer headers를 유지함
- 사용자 답변 후 run 재개

### Browser tests

- 새 run 생성
- live trace 표시
- 자유 텍스트 변경 시 tool path와 결과 변경
- missing info 질문 제출
- final report 표시

### Acceptance criteria

```text
1. LLM API 설정 없으면 agent run이 시작되지 않는다.
2. LLM API 설정이 있으면 external_api_called = true가 run.llm과 관련 trace/observation metadata에 남는다.
3. LLM이 최소 2개 이상의 custom tool을 선택한다.
4. tool output이 DB에 step별로 저장된다.
5. 후보 실측이 없으면 buy가 나오지 않는다.
6. 정보 부족 시 먼저 backend tool enrichment가 발생하고, 보강 이후에도 부족할 때만 UI가 사용자 질문 상태로 전환된다.
7. 최종 report는 observation에 존재하는 수치만 사용한다.
8. 같은 입력을 다시 실행하면 cache를 활용하되 trace는 새로 남긴다.
```

## 18. Migration Plan

### Phase 1. LLM required mode

- `configuration_required`를 hard gate로 만든다.
- production agent route에서는 fallback final decision을 금지한다.
- `.env.example` 작성:

```text
OPENAI_API_KEY=
FITCHECK_LLM_MODEL=
FITCHECK_LLM_BASE_URL=https://api.openai.com/v1
FITCHECK_LLM_API=responses
FITCHECK_LLM_TIMEOUT=60
FITCHECK_LLM_MAX_RETRIES=1

# Local LLM example
FITCHECK_LLM_BASE_URL=http://127.0.0.1:11434/v1
FITCHECK_LLM_API_KEY=ollama
FITCHECK_LLM_MODEL=llama3.1
FITCHECK_LLM_API=chat_completions
FITCHECK_LLM_TIMEOUT=60
FITCHECK_LLM_MAX_RETRIES=1
```

### Phase 2. Tool registry and local adapter

- 현재 함수들을 tool handler로 감싼다.
- schema, timeout, retry, risk metadata를 추가한다.
- `verify_tool_registry.py`를 만든다.
- Responses function schema를 chat-completions tool schema로 변환한다.
- `verify_local_llm_tool_adapter.py`로 local adapter 변환을 검증한다.

### Phase 3. Responses API agent loop

- `agent_runtime.py` 구현
- tool call parsing
- tool output submission
- run state persistence
- max turn guard
- live route smoke verifier는 기본 skip으로 유지하고, opt-in 시 실행 중인 API server를 HTTP로 호출해 external LLM/tool-call evidence를 확인

### Phase 4. New API routes

- `POST /api/agent/runs`
- `GET /api/agent/runs/{run_id}`
- `POST /api/agent/runs/{run_id}/reply`
- `GET /api/agent/runs/{run_id}/report`

### Phase 5. UI trace and question flow

- 현재 단일 분석 버튼을 run-based UI로 변경
- trace list를 agent step 기반으로 변경
- `waiting_for_user` 질문 form 추가

### Phase 6. Real provider integration

- search provider 또는 Playwright search
- OCR provider
- product page fetcher
- source cache

### Phase 7. Evaluation

- deterministic fixture eval
- LLM mock eval
- no-LLM HTTP route smoke through `FITCHECK_RUN_HTTP_API_SMOKE=1`
- real LLM smoke test through `FITCHECK_RUN_LIVE_AGENT_SMOKE=1`
- browser end-to-end test

## 19. MVP Cut Line

첫 production-agent MVP에서 반드시 포함:

- LLM required gate
- single FitCheckAgent
- custom tool calling loop
- closet memory load
- candidate parsing
- match scoring
- missing info question
- final report
- trace persistence

첫 MVP에서 제외 가능:

- multi-agent handoff
- real-time websocket streaming
- 모든 쇼핑몰 지원
- 리뷰 요약
- 결제/구매 기능
- Codex SDK integration

## 20. Official References

- FitCheck AI agent definition: [What Is an AI Agent?](17-what-is-ai-agent.md)
- OpenAI Agents SDK overview: https://developers.openai.com/api/docs/guides/agents
- OpenAI function/tool calling guide: https://developers.openai.com/api/docs/guides/function-calling
- Responses API migration guide: https://developers.openai.com/api/docs/guides/migrate-to-responses
- Codex SDK: https://developers.openai.com/codex/sdk
- Codex app-server: https://developers.openai.com/codex/app-server
- Codex with Agents SDK: https://developers.openai.com/codex/guides/agents-sdk

공식 문서상 핵심 근거:

- Agents는 plan, tool call, specialist collaboration, state를 포함하는 application이다.
- Function calling은 model이 tool call을 반환하고 application이 해당 tool을 실행한 뒤 output을 다시 model에 제공하는 multi-step flow다.
- Responses API는 built-in tools와 custom functions를 포함한 agentic application을 위한 통합 endpoint다.
- Codex SDK/app-server/MCP server는 Codex를 programmatic하게 제어할 수 있지만, FitCheck 제품 runtime에서는 도메인 tool을 직접 구현하는 OpenAI API/Agents SDK 경로가 더 적합하다.
