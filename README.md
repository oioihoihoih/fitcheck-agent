# FitCheck Agent

`FitCheck Agent`는 사용자가 평소 만족하며 입는 옷의 핏, 재질, 사이즈, 실루엣, 디자인 요소를 기준으로 온라인에서 비슷한 의류 후보를 찾아 비교하는 AI Agent 설계/구현 프로젝트입니다.

목표는 단순히 "이 옷을 살까 말까"를 판정하는 것이 아닙니다. 사용자가 이미 좋아하는 옷의 요소를 memory로 삼고, Agent가 웹검색, 상세페이지 fetch, 이미지/OCR, spec 정규화, match scoring tool을 사용해 여러 후보를 찾고 근거 있는 shortlist를 만드는 것입니다.

자유 텍스트는 고정 상품명에만 반응하지 않습니다. Production agent에서는 LLM이 원문 대화와 현재 state를 보고 `search_web_candidates` 같은 backend-owned tool을 직접 선택할 수 있고, `parse_user_goal`은 필수 관문이 아니라 선택 가능한 정규화/메모리 보조 tool입니다. `parse_user_goal`은 `goal` 없이 `free_text`만 받아도 동작하며, args가 비어 있으면 현재 run context의 입력을 사용합니다.

`backend_harness/verify_input_variation_matrix.py`는 반팔 티셔츠, 와이드 데님 팬츠, 후드집업처럼 서로 다른 원문 입력이 LLM request에 그대로 전달되고, model-selected 첫 tool이 `parse_user_goal`이 아니라 입력별 `search_web_candidates` query로 달라지는지 확인합니다.

## 핵심 메시지

- 생활 속 문제: 마음에 드는 옷과 비슷한 핏/재질/사이즈/디자인을 가진 새 옷을 찾기 어렵고, 쇼핑몰 정보는 흩어져 있다.
- Agent 접근: LLM이 모든 판단을 대신하지 않고, 웹검색, OCR, 이미지 분류, HTML 파싱, 규칙 기반 비교, 리포트 생성을 tool로 분리한다.
- 실무형 설계: Web UI, FastAPI backend, agent orchestrator, tool layer, storage, evaluation plan을 명확히 나눈다.
- 구현 전략: AI 코딩 도구를 활용하되, 구조 설계, 판단 기준, 테스트, 한계 분석은 직접 통제한다.
- 현재 구현: `/api/agent/runs` production agent route는 LLM 설정 없이는 `configuration_required`를 반환하고, 웹 첫 화면은 이 route에 연결된 GPT식 chat workspace입니다. 기존 fixed-candidate web-candidates route는 `disabled` 응답만 반환합니다.

## 제안 스택

```text
Frontend: Next.js + React + Tailwind CSS
Backend: FastAPI
Core: Python agent pipeline
Storage: SQLite for MVP, PostgreSQL for production
OCR: PaddleOCR or Tesseract, cloud OCR optional
LLM: OpenAI-compatible API for production agent reasoning and tool calling; fallback parser only for deterministic harness/degraded demo routes
Crawler/Parser: Playwright, BeautifulSoup
Queue: BackgroundTasks for MVP, Celery/RQ for production
```

## 문서 목록

- [Product Brief](docs/01-product-brief.md)
- [System Architecture](docs/02-system-architecture.md)
- [Agent Design](docs/03-agent-design.md)
- [Data Model](docs/04-data-model.md)
- [API Design](docs/05-api-design.md)
- [MVP Implementation Plan](docs/06-mvp-implementation-plan.md)
- [Test and Evaluation](docs/07-test-and-evaluation.md)
- [Presentation Outline](docs/08-presentation-outline.md)
- [Closet Onboarding Dry Run](docs/09-closet-onboarding-dry-run.md)
- [Harness to Production Map](docs/10-harness-to-production-map.md)
- [Existing Item Lookup Workflow](docs/11-existing-item-lookup-workflow.md)
- [Candidate Analysis Dry Run](docs/12-candidate-analysis-dry-run.md)
- [Keyword Web OCR Probe](docs/13-keyword-web-ocr-probe.md)
- [Web Candidate Match Dry Run](docs/14-web-candidate-match-dry-run.md)
- [Web Demo MVP](docs/15-web-demo-mvp.md)
- [Production AI Agent Design](docs/16-production-ai-agent-design.md)
- [What Is an AI Agent?](docs/17-what-is-ai-agent.md)
- [Backend Harness Improvement Log](docs/18-backend-harness-improvement-log.md)
- [Cross-Session Development Harness](docs/19-cross-session-development-harness.md)
- [General Agent Loop Engineering](docs/20-general-agent-loop-engineering.md)

## 실행

의존성 설치:

```powershell
python -m pip install -r requirements.txt
```

CLI 검증:

```powershell
python -B backend_harness/verify_end_to_end.py
python -B backend_harness/verify_web_candidates_match.py
python -B backend_harness/verify_agent_runtime.py
python -B backend_harness/verify_backend_infrastructure.py
python -B backend_harness/verify_local_llm_tool_adapter.py
python -B backend_harness/verify_production_agent_runtime.py
python -B backend_harness/verify_input_variation_matrix.py
python -B backend_harness/verify_http_api_smoke.py
python -B backend_harness/verify_live_provider_smoke.py
python -B backend_harness/verify_search_provider_matrix.py
python -B backend_harness/verify_ocr_provider_matrix.py
python -B backend_harness/verify_live_agent_smoke.py
```

웹 앱:

```powershell
python -m uvicorn backend_harness.web_server:app --host 127.0.0.1 --port 8000
```

브라우저에서 다음 주소를 엽니다.

```text
http://127.0.0.1:8000
http://127.0.0.1:8000/presentation
```

API:

```text
GET  /api/status
POST /api/agent/runs
GET  /api/agent/runs
GET  /api/agent/runs/{run_id}
GET  /api/agent/runs/{run_id}/events
POST /api/agent/runs/{run_id}/reply
GET  /api/agent/runs/{run_id}/report
POST /api/uploads/images
GET  /api/results/web-candidates  (legacy deterministic harness, disabled)
POST /api/run/web-candidates     (legacy deterministic harness, disabled)
GET  /api/harness/v1/capabilities
POST /api/harness/v1/tasks
GET  /api/harness/v1/tasks/{task_id}
GET  /api/harness/v1/tasks/{task_id}/events
POST /api/harness/v1/tasks/{task_id}/reply
POST /api/harness/v1/tasks/{task_id}/subagents
POST /api/harness/v1/loops
GET  /api/harness/v1/llm/providers
POST /api/harness/v1/llm/probe
GET  /presentation
```

`POST /api/agent/runs`는 production AI agent route입니다. LLM 설정이 없으면 run을 생성하지 않고 `configuration_required`를 반환합니다. LLM이 연결되면 Responses API tool-calling loop가 model-selected tool call을 받아 backend tool registry를 실행하고 observation/trace를 저장합니다.

Create/reply payload가 완전히 비어 있으면 agent 작업으로 만들지 않습니다. `POST /api/agent/runs`는 `goal`, `free_text`, `candidate_url`, `attachments` 중 최소 하나가 필요하고, `POST /api/agent/runs/{run_id}/reply`는 `message`, `answers`, `attachments` 중 최소 하나가 필요합니다. 비어 있거나 공백뿐인 route 요청은 Pydantic validation에서 422로 끝나며 LLM route까지 들어가지 않습니다. Runtime `start_run()`을 route 밖에서 직접 호출해도 빈 payload는 `invalid_request` / `INITIAL_USER_INPUT_REQUIRED`로 거부하고 run 생성이나 LLM 호출을 하지 않습니다. Runtime `reply()` 직접 호출도 빈 payload를 `invalid_request` / `REPLY_USER_INPUT_REQUIRED`로 거부하고 기존 run state를 변경하지 않습니다.

후보 정보가 부족한 상태에서 LLM이 바로 질문, 점수화, report 생성을 시도하면 backend는 결과를 꾸미지 않습니다. `ask_missing_info`는 필드별 고정 질문을 만들지 않고, 검색 전이면 원문 입력 기반으로 `load_closet_memory`/`search_web_candidates`를 먼저 제안합니다. `parse_user_goal`은 모델이 필요할 때 선택할 수 있지만 production 흐름의 필수 선행 단계가 아닙니다. 검색 이후에도 enrichment 전에는 `waiting_for_user`로 전환하지 않고, `score_candidate_match`는 `score_deferred=true`와 `next_tool_suggestions`를 반환하며, `write_purchase_report`는 `CANDIDATE_ENRICHMENT_REQUIRED_BEFORE_REPORT`로 실패합니다. 이 deferred 결과는 다음 LLM 요청의 `backend_runtime_guidance`와 trace에 같이 남아 검색, 상세페이지 fetch, asset 분류, OCR, spec 정규화가 먼저 실행되도록 유도합니다.

모델이 schema에 맞지 않는 tool arguments를 보내도 handler를 바로 실행하지 않습니다. Backend tool registry가 required field, extra field, type을 먼저 검증하고, 실패 시 `TOOL_ARGUMENT_VALIDATION_FAILED`를 trace/observation에 저장한 뒤 structured tool output으로 모델에 돌려줍니다.

모델이 function call arguments를 깨진 JSON 문자열로 보내면 `TOOL_ARGUMENT_JSON_PARSE_FAILED`로 따로 기록합니다. 이 경우 raw arguments 일부와 parse 위치가 trace에 남아 schema validation 실패와 구분됩니다.

모델이 등록되지 않은 tool name을 호출하면 `UNKNOWN_TOOL` observation으로 기록하고 사용 가능한 tool list를 돌려줍니다. 같은 failed tool call을 동일 인자와 error code로 2회 연속 반복하면 `REPEATED_FAILED_TOOL_CALL` guardrail로 run을 종료합니다. 같은 조기 `ask_missing_info`를 반복하면 `REPEATED_PREMATURE_MISSING_INFO`로 막아 사용자 질문으로 위장되지 않게 합니다.

모델이 존재하지 않는 `candidate_key`로 후보 상세 fetch, asset 분류, OCR, spec 정규화, score를 호출하면 Python 예외로 새지 않습니다. Backend tool은 `CANDIDATE_NOT_FOUND` structured failure와 `available_candidate_keys`, `next_tool_suggestions`를 tool output으로 반환하고, 모델은 다음 turn에서 유효한 후보 key로 회복할 수 있습니다. 같은 잘못된 후보 key를 반복하면 기존 repeated failed tool guardrail이 run을 종료합니다.

모델이 계속 tool call만 반복하고 최종 report나 `waiting_for_user`로 수렴하지 않으면 `MAX_AGENT_TURNS_EXCEEDED`로 종료합니다. 이때 runtime은 남은 `pending_tool_outputs`를 비우고 `stop_max_agent_turns_exceeded` guardrail trace를 남겨, UI/SSE/API 소비자가 단순 정지와 비용/반복 guardrail 종료를 구분할 수 있게 합니다.

도구 실행 후 그 결과를 다음 LLM 요청으로 넘기는 단계에서 provider timeout/exception이 발생하면 `LLM_RESPONSE_FAILED`로 종료합니다. 이때도 runtime은 남은 `pending_tool_outputs`를 비우고, 실패한 `llm_response` trace와 `stop_llm_response_failed` guardrail trace에 `pending_tool_output_count`를 남겨 stale tool output이 다음 resume/retry처럼 보이지 않게 합니다.

한 번의 LLM 응답에 여러 `function_call`이 들어와도 backend runtime은 각 tool을 실행하고, 각 `call_id`에 대응하는 `function_call_output`을 다음 LLM 요청에 함께 전달합니다. `verify_production_agent_runtime.py`는 `load_closet_memory`와 `search_web_candidates`가 같은 응답에 들어오는 케이스에서 observation 순서, trace의 `tool_call_count=2`, `tool_call`/`tool_output` trace의 `call_id`, `AgentStateEventResponse.latest_trace` schema의 `call_id`, 다음 LLM 요청의 두 tool output을 확인합니다. 또한 OpenAI-compatible 구현이 chat-style nested `function.arguments` item을 직접 반환해도 search query arguments가 `{}`로 손실되지 않는지 확인합니다. Responses-style 응답에 `reasoning` context item이 함께 들어오면 runtime은 이를 다음 LLM 요청의 tool output과 함께 보존하고 trace에 `reasoning_context_item_count`를 남깁니다. `verify_local_llm_tool_adapter.py`는 local chat-completions `tool_calls` 배열도 같은 방식으로 Responses-like `function_call` item으로 정규화하고, 후속 요청에서는 각 output을 `role=tool` 메시지와 원래 `tool_call_id`로 보내는지 확인합니다. 구형 `message.function_call` 응답은 deprecated 호환 경로로 synthetic call id를 붙여 처리하고, 후속 요청에서는 deprecated `role=function` 메시지로 되돌립니다.

Agent run 저장소는 `harness_outputs/agent_runs.sqlite3` SQLite DB입니다. API는 익명 session cookie 또는 `X-FitCheck-Session-Id` header를 사용해 run을 session 단위로 분리합니다. HTTP smoke는 cookie-only 요청이 같은 `fitcheck_session_id`로 이어지는지와 다른 header session에 run detail/list/report/reply/SSE가 노출되지 않는지 확인합니다. Runtime `reply()`도 run session이 있는데 payload session이 없거나 다르면 `not_found`로 막아 route 밖 직접 호출에서도 cross-session mutation을 방지합니다. `GET /api/agent/runs/{run_id}/events`는 SSE로 run 상태와 최신 trace를 스트리밍하며, frame에는 `id`, `event: agent_state`, `retry`, JSON `data`가 포함됩니다.

`POST /api/agent/runs/{run_id}/reply`는 `waiting_for_user` 이후의 GPT식 후속 대화 경로입니다. Reply message, structured answers, attachments는 다음 LLM input에 포함되고, runtime은 오래된 parsed closet state와 missing-info state를 정리한 뒤 모델이 다시 `inspect_uploaded_images`, `search_web_candidates`, `load_closet_memory` 같은 tool을 선택하게 합니다.

완료된 run에 다시 reply하면 이전 `final_report`와 `completed_at`은 비워지고, 최신 사용자 입력 이후 새 `write_purchase_report` tool output이 있어야 final text가 승인됩니다. 과거 report observation만으로 새 답변을 완료 처리하지 않습니다.

핵심 API route는 Pydantic request/response schema를 사용합니다. `verify_backend_infrastructure.py`는 OpenAPI에 agent create/reply, upload, web-candidate request schema와 핵심 response/trace/SSE/grounding schema가 노출되는지 확인하고, route별 response `$ref`와 requestBody `$ref`도 고정합니다. HTTP smoke는 실행 중인 서버의 `/openapi.json`에서도 같은 route model 계약을 확인하며, 잘못된 upload/create/reply request body와 빈 agent create/reply body가 422 validation error로 막히는지도 확인합니다. SSE endpoint는 `text/event-stream`으로 `agent_state` 이벤트를 보내며, payload는 `AgentStateEventResponse`로 검증되고 transport header는 `Cache-Control: no-cache`, `X-Accel-Buffering: no`를 유지합니다. Report와 SSE payload는 `grounding_evidence` 원본 path와 함께 UI가 바로 표시할 수 있는 `citation_badges`도 노출합니다. Grounding 실패 사유 중 `suspicious_identity_claims`와 `suspicious_style_claims`도 OpenAPI, report response, SSE payload smoke에서 유지되는지 확인합니다. Provider tool observation과 trace output은 raw retry field와 별도로 `retry_badges`를 노출해 `not_retried`, `retried`, `retry_failed`, `not_started`, `failed_no_retry` 상태를 UI가 바로 표시할 수 있습니다. `ToolObservationResponse.result`는 공통 `ToolResultResponse` schema를 참조하며, `status`, `error`, provider error fields, retry fields, `next_tool_suggestions`를 OpenAPI에 노출합니다. 검색 result는 `SearchCandidateResponse`와 `RejectedSearchCandidateResponse`를 통해 `candidate_count`, `candidates`, `candidate_filter_policy`, `rejected_candidates`도 typed schema로 노출합니다. Provider 실패는 `provider_error_code`와 `provider_error_category`로도 정규화되어 HTTP 제한/서버 오류, request timeout/connection 오류, configuration 누락, challenge page, OCR provider 미연결/설정 오류를 필터링할 수 있습니다.

채팅 UI는 텍스트와 이미지 첨부를 함께 보낼 수 있습니다. 모바일에서는 composer의 `+` 입력이 카메라 캡처를 열 수 있고, 데스크톱에서는 이미지 파일 선택으로 동작합니다. 업로드된 이미지는 `/api/uploads/images`에 저장되고 agent payload의 `attachments`로 전달됩니다. Vision provider는 아직 연결 전입니다. OCR은 기본 `fixture_ocr_boundary`이며, `FITCHECK_OCR_PROVIDER=tesseract_cli`로 전환하면 업로드 이미지에 대해 로컬 Tesseract CLI를 호출합니다. agent는 `inspect_uploaded_images` observation에 없는 이미지 내용을 사실로 주장하지 않습니다. OCR evidence가 usable하지 않으면 `OCR_PROVIDER_NOT_CONNECTED`, `OCR_TESSERACT_CLI_NOT_FOUND`, `OCR_TESSERACT_TIMEOUT`, `OCR_TESSERACT_PROCESS_FAILED`, `OCR_UPLOAD_FILE_NOT_FOUND`, `OCR_LOCAL_ASSET_PATH_REQUIRED` 같은 provider error code가 trace/observation에 남습니다.

후보 상품 OCR은 `extract_size_material_info` observation에 저장되고, `normalize_product_spec`는 최신 OCR evidence의 텍스트를 사용해 비어 있는 실측, 소재, 가격 필드를 보강합니다. OCR evidence는 기존 HTML/page 관측값을 무조건 덮어쓰지 않고, 부족한 핵심 필드를 채운 뒤 `ocr_evidence_used`, `resolved_fields_from_ocr`, `missing_fields` 갱신 결과를 trace-grounded spec으로 남깁니다.

`/api/run/web-candidates`와 `/api/results/web-candidates`는 예전 deterministic demo surface였고 현재는 `disabled` 응답만 반환합니다. 이 경로는 fixed 후보를 재랭킹하는 harness였기 때문에 production AI agent 동작으로 사용하지 않습니다. 웹 UI와 실제 agent flow는 `/api/agent/runs`만 사용합니다.

검색과 OCR은 provider boundary로 분리되어 있습니다. 기본 provider 상태는 `cached_harness_search`와 `fixture_ocr_boundary`를 보여줄 수 있지만, production agent route의 `search_web_candidates`는 cached harness 후보를 기본으로 사용하지 않습니다. live 검색 provider가 없으면 같은 3개 fixture 후보로 답하지 않고 `LIVE_SEARCH_PROVIDER_REQUIRED` / `configuration_required`로 멈춥니다. cached search는 `FITCHECK_ALLOW_CACHED_AGENT_SEARCH=1`을 명시한 데모/테스트에서만 허용합니다. live 검색은 `FITCHECK_SEARCH_PROVIDER=jina_search`와 `JINA_API_KEY` 또는 `FITCHECK_SEARCH_API_KEY`, `FITCHECK_SEARCH_PROVIDER=brave_search`와 `BRAVE_SEARCH_API_KEY` 또는 `FITCHECK_SEARCH_API_KEY`, 또는 `FITCHECK_SEARCH_PROVIDER=searxng`와 `FITCHECK_SEARXNG_URL` 또는 `SEARXNG_URL`로 전환합니다. API key 없는 best-effort 검색은 `FITCHECK_SEARCH_PROVIDER=duckduckgo_html`도 지원하지만 외부 HTML 구조와 차단 정책에 의존합니다. live search 결과는 `product_like_only` 정책을 통과한 상품형 결과만 `candidates`로 노출합니다. 블로그, 기사, 코디 글, 커뮤니티처럼 콘텐츠성 결과는 `rejected_candidates` 진단으로만 남기고 추천 후보나 shortlist 근거로 쓰지 않습니다. live search/page fetch는 제한적 retry를 수행하고 `retry_count`, `retry_errors`, `retry_badges`, `provider_retry_error_codes`를 trace/observation에 남깁니다. 최종 provider 실패나 configuration gate는 `provider_error_code`와 `provider_error_category`도 남깁니다. search provider가 실패하면 run은 `SEARCH_PROVIDER_FAILED`로 멈추고, 상품형 후보 0건 검색을 같은 query로 반복하면 `REPEATED_EMPTY_SEARCH` guardrail이 실행됩니다. 첫 0건 검색은 `search_query_expansion_required` guidance를 LLM에 돌려 검색어를 넓히게 합니다. live OCR은 `FITCHECK_OCR_PROVIDER=tesseract_cli`와 로컬 `tesseract` 실행 파일이 필요합니다. `/api/status`의 `providers` 필드에서 현재 선택과 사용 가능 여부를 확인할 수 있습니다. Report는 `report_grounding.py` validator를 통해 observation에 없는 숫자, URL, 소재, 명시적 상품명/브랜드, 구체적인 핏/색상/디자인 속성 claim을 포함하지 못하게 검증하고, 관측 claim이 하나도 없는 빈 final도 `report_contains_no_grounded_claims`로 거부합니다. 관측된 claim은 `grounding_evidence` path와 `citation_badges`로 근거 field를 반환합니다.

`run_keyword_web_probe.py`도 더 이상 특정 쇼핑몰/상품 URL을 기본값으로 갖지 않습니다. deterministic probe를 따로 검증하려면 `FITCHECK_HARNESS_SOURCES_JSON` 또는 `FITCHECK_HARNESS_SOURCES_PATH`로 명시적인 source list를 넣어야 합니다.

실제 live provider smoke는 기본 검증에서 외부 서비스를 호출하지 않습니다. 선택한 live provider를 확인하려면 명시적으로 opt-in합니다.

```powershell
set FITCHECK_RUN_LIVE_PROVIDER_SMOKE=1
set FITCHECK_SEARCH_PROVIDER=jina_search
set JINA_API_KEY=...
python -B backend_harness/verify_live_provider_smoke.py

set FITCHECK_SEARCH_PROVIDER=brave_search
set BRAVE_SEARCH_API_KEY=...
set FITCHECK_SEARCH_COUNTRY=KR
set FITCHECK_SEARCH_LANG=ko
python -B backend_harness/verify_live_provider_smoke.py

set FITCHECK_SEARCH_PROVIDER=searxng
set FITCHECK_SEARXNG_URL=https://your-searxng.example
set FITCHECK_SEARCH_LANG=ko
python -B backend_harness/verify_live_provider_smoke.py

set FITCHECK_OCR_PROVIDER=tesseract_cli
set FITCHECK_OCR_LANGS=kor+eng
python -B backend_harness/verify_live_provider_smoke.py
```

여러 search provider를 한 번에 비교하려면 matrix verifier를 opt-in합니다. 기본값은 Jina, Brave, SearXNG, DuckDuckGo HTML이며, 설정 누락은 `configuration_required`로 기록합니다. `FITCHECK_SEARCH_PROVIDER_MATRIX_REQUIRE_SUCCESS=1`을 켜면 최소 1개 provider가 성공하지 못할 때 실패 gate로 동작합니다.

```powershell
set FITCHECK_RUN_SEARCH_PROVIDER_MATRIX=1
set FITCHECK_SEARCH_PROVIDER_MATRIX=jina_search,brave_search,searxng,duckduckgo_html
set FITCHECK_SEARCH_PROVIDER_MATRIX_QUERY=반팔 티셔츠 소재 사이즈표
python -B backend_harness/verify_search_provider_matrix.py
```

`duckduckgo_html`은 no-key best-effort provider입니다. 현재 환경처럼 DuckDuckGo가 anomaly/challenge page를 반환하면 `DUCKDUCKGO_HTML_CHALLENGE`로 실패를 기록하고, 빈 후보 성공으로 위장하지 않습니다.

OCR provider도 matrix verifier로 비교할 수 있습니다. 기본값은 `fixture_boundary,tesseract_cli`이며, fixture는 `not_connected`, Tesseract CLI/언어 데이터 누락은 `configuration_required`로 기록합니다. `FITCHECK_OCR_PROVIDER_MATRIX_REQUIRE_SUCCESS=1`을 켜면 usable OCR provider가 없을 때 실패 gate로 동작하고, `FITCHECK_OCR_PROVIDER_MATRIX_IMAGE_PATH`로 실제 텍스트가 있는 테스트 이미지를 지정할 수 있습니다.

```powershell
set FITCHECK_RUN_OCR_PROVIDER_MATRIX=1
set FITCHECK_OCR_PROVIDER_MATRIX=fixture_boundary,tesseract_cli
python -B backend_harness/verify_ocr_provider_matrix.py
```

LLM API는 OpenAI-compatible endpoint로 연결됩니다. 기본값은 로컬 Ollama 호환 주소이며, local-first 실행은 `/v1/chat/completions` tool calling을 사용합니다.

Local adapter는 chat-completions `tool_calls` 배열을 FitCheck runtime의 Responses-like `function_call` item으로 변환합니다. 여러 `tool_calls`가 한 응답에 들어온 경우도 `verify_local_llm_tool_adapter.py`에서 검증합니다. Runtime extractor도 nested `function.name`/`function.arguments` 형태를 직접 처리합니다. 일부 구형 local server가 deprecated `message.function_call`을 반환하면 adapter가 이를 단일 Responses-like `function_call`로 정규화합니다. Responses 전용 `reasoning` context item은 local chat-completions message로 변환하지 않고 무시해, local LLM 프롬프트에 unsupported JSON이 섞이지 않게 합니다.

```powershell
set FITCHECK_LLM_BASE_URL=http://127.0.0.1:11434/v1
set FITCHECK_LLM_MODEL=llama3.1
set FITCHECK_LLM_API_KEY=ollama
set FITCHECK_LLM_API=chat_completions
```

OpenAI cloud를 사용할 때는 Responses API가 기본입니다.

```powershell
set OPENAI_API_KEY=...
set OPENAI_BASE_URL=https://api.openai.com/v1
set FITCHECK_LLM_MODEL=gpt-5.4-mini
set FITCHECK_LLM_API=responses
set FITCHECK_LLM_TIMEOUT=60
set FITCHECK_LLM_MAX_RETRIES=1
```

실행 중인 API server의 route contract를 HTTP로 확인하는 smoke도 기본 검증에서는 skip됩니다. 이 검증은 외부 LLM을 호출하지 않고 `/api/status`, cookie session persistence, `/openapi.json` response/requestBody schema refs와 grounding suspicious properties, invalid request body 422 validation, `/api/uploads/images`, seeded agent run의 detail/list/report/reply/SSE session isolation, detail trace와 SSE `latest_trace`의 `call_id` correlation, not-found agent JSON, SSE `id`/`event`/`retry`/`data` frame과 no-buffer headers를 확인합니다.

```powershell
python -m uvicorn backend_harness.web_server:app --host 127.0.0.1 --port 8000
set FITCHECK_RUN_HTTP_API_SMOKE=1
set FITCHECK_API_BASE_URL=http://127.0.0.1:8000
python -B backend_harness/verify_http_api_smoke.py
```

실제 production route와 OpenAI-compatible LLM 호출을 함께 확인하는 smoke도 기본 검증에서 외부 API를 호출하지 않습니다. 서버를 실행한 뒤 명시적으로 opt-in하면 `/api/status`, `POST /api/agent/runs`, `GET /api/agent/runs/{run_id}`를 HTTP로 호출하고 `external_api_called`, model-selected tool call, backend observation 저장 여부, `tool_call` trace/`tool_output` trace/observation의 `call_id` correlation을 확인합니다. OpenAI Responses function calling 문서도 tool call output이 특정 model tool call을 `call_id`로 참조해야 한다고 설명하므로, 이 smoke는 POST 응답뿐 아니라 같은 session으로 다시 조회한 run에서도 `call_id`가 유지되는지 확인합니다. `call_id`가 비어 있으면 trace 양쪽에 같은 빈 값이 있어도 correlation으로 인정하지 않습니다. 이 smoke는 기본적으로 `agentic_boundary_status`와 `completion_status`를 분리해 보고합니다. 현재 live search provider가 없고 cached boundary로만 LLM/tool loop를 확인하려면 `FITCHECK_LIVE_AGENT_ALLOW_CACHED_SEARCH=1`을 추가해야 하며, end-to-end completion까지 실패 gate로 보려면 `FITCHECK_LIVE_AGENT_REQUIRE_COMPLETION=1`을 켭니다.

```powershell
python -m uvicorn backend_harness.web_server:app --host 127.0.0.1 --port 8000
set FITCHECK_RUN_LIVE_AGENT_SMOKE=1
set FITCHECK_LIVE_AGENT_ALLOW_CACHED_SEARCH=1
set FITCHECK_AGENT_BASE_URL=http://127.0.0.1:8000
python -B backend_harness/verify_live_agent_smoke.py
```

Provider 선택:

```powershell
set FITCHECK_SEARCH_PROVIDER=cached_harness
set FITCHECK_OCR_PROVIDER=fixture_boundary

# Only for demos/tests that intentionally allow cached agent search
set FITCHECK_ALLOW_CACHED_AGENT_SEARCH=1

# Optional live adapters
set FITCHECK_SEARCH_PROVIDER=jina_search
set JINA_API_KEY=...
set FITCHECK_SEARCH_PROVIDER=brave_search
set BRAVE_SEARCH_API_KEY=...
set FITCHECK_SEARCH_COUNTRY=KR
set FITCHECK_SEARCH_LANG=ko
set FITCHECK_SEARCH_PROVIDER=searxng
set FITCHECK_SEARXNG_URL=https://your-searxng.example
set FITCHECK_SEARCH_PROVIDER=duckduckgo_html
set FITCHECK_SEARCH_MAX_RETRIES=1
set FITCHECK_PAGE_FETCH_MAX_RETRIES=1
set FITCHECK_OCR_PROVIDER=tesseract_cli
set FITCHECK_OCR_LANGS=kor+eng
```

LLM 설정이 없거나 로컬 LLM server가 실행 중이 아니면 `/api/status`와 화면에는 `llm.status = configuration_required`가 표시됩니다. production agent route는 이 상태에서 실행되지 않습니다. 자유 텍스트 fallback parser는 기존 deterministic harness/degraded demo route에서만 사용합니다.

Hosted tools는 OpenAI Responses API에서 참고할 수 있지만, FitCheck production route의 핵심 도구는 local/custom function tools입니다. 모델은 tool call을 선택하고, 실제 실행은 FastAPI backend가 `backend_owned_functions`로 수행합니다.

## MVP 범위

MVP는 모든 쇼핑몰을 자동 지원하는 완성형 서비스가 아니라, 핵심 agent 흐름을 검증하는 데 집중합니다.

```text
1. 사용자가 좋아하는 기존 옷 2~3개 등록
2. 후보 상품 상세페이지 캡처 또는 URL 입력
3. 이미지 분류: 착장컷 / 상품컷 / 사이즈표 / 소재표 / 배너
4. OCR로 사이즈와 소재 정보 추출
5. 기존 옷장 데이터와 규칙 기반 비교
6. production agent는 LLM이 원문 자유 텍스트에서 필요한 tool을 선택하고, deterministic harness만 fallback parser를 사용
7. 신뢰도와 부족 정보 표시
```

## 평가 포인트

- Agent가 어떤 tool을 왜 호출하는지 설명 가능한가
- OCR/이미지 분류 실패 시 억지 추천하지 않는가
- LLM이 필요한 부분과 규칙 기반 로직이 필요한 부분을 구분했는가
- 결과 리포트가 사용자의 후보 탐색과 비교 판단에 실제로 도움이 되는가
- 과제 발표에서 설계, 구현, 테스트, 한계를 모두 설명할 수 있는가
