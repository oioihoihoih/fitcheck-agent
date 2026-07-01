# Backend Harness

This directory is a local workflow harness for FitCheck Agent before building the full web app.

It simulates the practical backend flow:

```text
mobile web form
-> API boundary
-> schema validation
-> agent orchestration
-> ClothingPurchaseAgent tool trace
-> existing item lookup boundary
-> image classification tool
-> OCR tool boundary
-> closet profile normalizer
-> JSON DB persistence
-> OpenAI-compatible LLM parsing/report boundary
```

The harness avoids mandatory external services for deterministic demo flows. OCR is still represented by replaceable fixtures by default, while the production agent boundary can call an OpenAI-compatible endpoint. If LLM configuration is missing, `/api/agent/runs` returns `configuration_required` and does not silently run fallback reasoning. Rule-based fallback parsing is limited to deterministic harness/degraded demo surfaces; the production tool loop can start from raw user text and let the model choose `search_web_candidates` directly.

The production-agent prototype validates model-selected tool arguments before executing backend handlers. Missing required fields, unexpected fields, or wrong types are recorded as `TOOL_ARGUMENT_VALIDATION_FAILED` tool observations instead of being hidden as generic Python exceptions.

Broken JSON argument strings are recorded separately as `TOOL_ARGUMENT_JSON_PARSE_FAILED`, including a short raw argument excerpt and parse location.

Unknown tool names are recorded as `UNKNOWN_TOOL`; repeated identical failed tool calls stop the run with `REPEATED_FAILED_TOOL_CALL` instead of waiting for max turns.

Live search and live page fetch boundaries use limited GET retries. Retry metadata is recorded as `retry_count` and `retry_errors`; cached and fixture providers keep `retry_count=0`. Provider trace output and stored observations also expose UI-ready `retry_badges` with statuses such as `not_retried`, `retried`, `retry_failed`, `not_started`, and `failed_no_retry`. Final provider failures also include normalized `provider_error_code` and `provider_error_category` fields so UI/SSE consumers can group timeout, connection, HTTP, configuration, challenge-page, and OCR provider capability failures without parsing raw messages.

## Run

From `C:\com2us`:

```powershell
python backend_harness/run_closet_onboarding.py
python backend_harness/run_end_to_end.py
python backend_harness/verify_end_to_end.py
python backend_harness/run_keyword_web_probe.py
python backend_harness/run_web_candidates_match.py
python backend_harness/verify_web_candidates_match.py
python backend_harness/verify_agent_runtime.py
python backend_harness/verify_backend_infrastructure.py
python backend_harness/verify_production_agent_runtime.py
python backend_harness/verify_http_api_smoke.py
python backend_harness/verify_live_provider_smoke.py
python backend_harness/verify_search_provider_matrix.py
python backend_harness/verify_ocr_provider_matrix.py
python backend_harness/verify_live_agent_smoke.py
```

`verify_http_api_smoke.py`는 기본적으로 실행 중인 서버를 요구하지 않고 skip 결과를 출력합니다. `FITCHECK_RUN_HTTP_API_SMOKE=1`을 설정하면 서버 HTTP route를 실제로 호출하지만 LLM route는 실행하지 않습니다. `/api/status`, cookie session persistence, `/openapi.json` response/requestBody schema refs, invalid request body 422 validation, `/api/uploads/images`, seeded agent run의 detail/list/report/reply/SSE session isolation, report/SSE `citation_badges`, agent not-found JSON, SSE `id`/`event`/`retry`/`data` frame과 no-buffer headers를 확인합니다.

`verify_live_provider_smoke.py`는 기본적으로 외부 서비스를 호출하지 않고 skip 결과를 출력합니다. 선택한 live search/OCR provider를 실제로 호출하려면 `FITCHECK_RUN_LIVE_PROVIDER_SMOKE=1`을 설정합니다. 이 검증은 Jina/Brave API key 누락, SearXNG base URL 누락, DuckDuckGo challenge page, Tesseract CLI/language data 누락을 빈 성공으로 처리하지 않고 실패로 드러냅니다.

`verify_search_provider_matrix.py`도 기본적으로 외부 서비스를 호출하지 않고 skip 결과를 출력합니다. `FITCHECK_RUN_SEARCH_PROVIDER_MATRIX=1`을 설정하면 `FITCHECK_SEARCH_PROVIDER_MATRIX` 목록의 search provider를 순서대로 확인하고, key/base URL 누락, request 실패, 후보 0건, 후보 수, rejected diagnostics를 한 JSON payload로 비교합니다. `FITCHECK_SEARCH_PROVIDER_MATRIX_REQUIRE_SUCCESS=1`을 설정하면 최소 하나의 provider가 상품형 후보를 반환해야 통과합니다.

`verify_ocr_provider_matrix.py`도 기본적으로 OCR provider를 호출하지 않고 skip 결과를 출력합니다. `FITCHECK_RUN_OCR_PROVIDER_MATRIX=1`을 설정하면 `fixture_boundary,tesseract_cli`를 기본 비교하고, product OCR target과 uploaded image OCR path를 모두 확인합니다. Fixture OCR은 `not_connected`, Tesseract CLI 누락은 `configuration_required`로 남기며, `FITCHECK_OCR_PROVIDER_MATRIX_REQUIRE_SUCCESS=1` 또는 `FITCHECK_OCR_PROVIDER_MATRIX_REQUIRE_TEXT=1`로 failure gate를 강화할 수 있습니다.

`verify_live_agent_smoke.py`도 기본적으로 외부 LLM을 호출하지 않고 skip 결과를 출력합니다. 실제 production route와 LLM API를 함께 확인하려면 서버를 실행한 뒤 `FITCHECK_RUN_LIVE_AGENT_SMOKE=1`을 설정합니다. 이 검증은 `/api/status`, `POST /api/agent/runs`, `GET /api/agent/runs/{run_id}`를 HTTP로 호출하고 `external_api_called`, model-selected tool call, backend observation 저장 여부를 확인합니다.

Production agent route의 `search_web_candidates`는 cached harness 후보를 기본으로 사용하지 않습니다. 실제 후보 탐색을 실행하려면 `FITCHECK_SEARCH_PROVIDER`를 live provider로 설정해야 합니다. cached 후보로 demo/test를 돌릴 때만 `FITCHECK_ALLOW_CACHED_AGENT_SEARCH=1`을 명시합니다.

신규 live search 설정은 `FITCHECK_SEARCH_PROVIDER=brave_search`, `tavily_search`, 또는 self-hosted `searxng`를 우선 사용합니다. Google Programmable Search의 Custom Search JSON API는 2026-01-20 공식 전환 공지 이후 신규/전체 웹 검색 용도로 안정적인 기본 provider로 보기 어렵기 때문에, `google_cse`는 기존 접근 권한이 있는 계정의 legacy/transition provider로만 유지합니다.

`brave_search`는 `BRAVE_SEARCH_API_KEY` 또는 `FITCHECK_SEARCH_API_KEY`, `tavily_search`는 `TAVILY_API_KEY` 또는 `FITCHECK_SEARCH_API_KEY`가 필요합니다. 검색 결과 필터는 상품 상세 URL 신호(`/products/`, `/app/goods/`, `/Product/`, `product_no=`, `goodsNo=` 등)를 우선하고, category/search/menu/like/location/account류 URL은 `rejected_candidates`로 보냅니다. 후보 0건이면 `next_tool_suggestions`에 `site:musinsa.com/products`, `site:musinsa.com/app/goods`, `site:wconcept.co.kr/Product`, `site:29cm.co.kr/catalog` 기반 재검색 쿼리를 기록해 다음 model-selected tool call이 반복 검색을 피하도록 합니다.

`search_web_candidates`는 첫 live search 결과가 상품형 후보 0건이면 `FITCHECK_SEARCH_EXPANSION_LIMIT` 개수만큼 상세 URL 패턴 기반 확장 쿼리를 같은 backend-owned tool execution 안에서 시도하고, `search_expansion_attempts`에 각 시도를 남깁니다. 블로그/커뮤니티/카테고리/보조 사이즈표 페이지나 상품 상세 URL 신호가 없는 결과는 점수 상한을 적용해 shortlist 후보에서 제외합니다.

Production agent runtime은 grounded `write_purchase_report` tool output을 최종 보고서로 채택합니다. 마지막 LLM 문장은 `llm_final_text`로만 보관해, 모델이 도구 밖에서 문장을 장식하다가 grounding validation을 깨는 문제를 줄입니다. 후보 여러 개를 enrich하는 live run을 위해 기본 `FITCHECK_AGENT_MAX_TURNS`는 18입니다.

OCR live provider는 `FITCHECK_OCR_PROVIDER=tesseract_cli`로 선택합니다. Windows에서는 `UB-Mannheim.TesseractOCR` winget 패키지와 `kor.traineddata`가 필요합니다. 현재 WSL 로컬 검증은 `tools/tesseract-wsl-wrapper`를 `FITCHECK_TESSERACT_CMD`로 지정하고, `TESSDATA_PREFIX=C:\com2us\tools\tessdata`에 `kor`, `eng`, `osd` language data를 두고 통과했습니다.

Windows PATH가 새 터미널에 반영되지 않는 경우를 위해 Windows 직접 실행 환경에서는 `FITCHECK_TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe`를 설정할 수 있습니다. WSL 실행 환경에서는 Linux 경로와 임시 이미지를 Windows Tesseract가 읽을 수 있게 `FITCHECK_TESSERACT_CMD=/mnt/c/com2us/tools/tesseract-wsl-wrapper`를 사용합니다. Provider는 이 값, `TESSERACT_CMD`, PATH, 기본 설치 경로 순서로 실행 파일을 찾습니다. 현재 `.env`는 `FITCHECK_OCR_PROVIDER=tesseract_cli`로 live OCR을 사용합니다.

Live OCR은 연결되어도 쇼핑몰 이미지 품질에 따라 `completed_empty`가 정상적으로 발생할 수 있습니다. 예를 들어 2026-06-08 live agent run `run_167dca2e9538`에서는 Fruitsfamily 후보 상세 이미지 8개가 OCR target으로 분류되어 Tesseract가 호출됐지만 모두 텍스트 없음으로 종료됐고, 최종 보고서는 이 부족한 실측/소재 근거를 `need_more_info`로 반영했습니다.

Tesseract provider는 원본 이미지, Pillow 전처리 이미지(`preprocessed_threshold`), 세로형 이미지 crop(`preprocessed_crop_top`, `preprocessed_crop_middle`, `preprocessed_crop_bottom`)을 시도하고, OCR text quality가 더 높은 variant를 evidence에 기록합니다. 정규화 단계는 짧거나 상품 spec 키워드가 없는 OCR 노이즈를 `_usable_ocr_evidence`에서 제외하므로, 오인식 텍스트가 실측/소재 근거로 승격되지 않습니다. 2026-06-08 Cent 상세페이지 샘플에서는 live image 40개 중 OCR target 19개, 다운로드 8개, Tesseract attempt 40개가 확인됐고, low-quality text 2개는 confidence 0.23-0.30 및 `ocr_evidence_used_count=0`으로 제외됐습니다. 가격 19900원과 소재 cotton 100은 페이지 text/structured observation에서 확인됐지만 실측 치수는 여전히 missing field로 남았습니다.

Live page fetch는 HTML table/dl의 상품 spec도 `spec_table_text`로 변환합니다. `총장/어깨/가슴단면/소매` 같은 header row와 다음 numeric row는 `총장: 71 어깨: 51 ...` 형태로 정규화되고, raw body text보다 뒤에 넣어 label-only table이 `소매 71`처럼 오인식되는 문제를 줄입니다. Backend verifier는 fake live page table에서 `measurements={length:71, shoulder:51, chest:56, sleeve:23}`을 확인합니다. Size option selector처럼 `Size: M/L/XL`만 있는 table은 detailed spec signal이 없으면 제외됩니다.

`score_candidate_match` 결과에는 `evidence_coverage`가 포함됩니다. 이 필드는 후보별 completed/missing tool, fetch status, OCR evidence status counts, `spec_table_present`, observed fields, critical missing fields, field confidence, recommendation policy를 기록합니다. 2026-06-08 Cent 샘플은 search/fetch/classify/OCR/normalize가 모두 끝났지만 observed fields가 `material`, `price`뿐이고 실측 치수 4개가 critical missing으로 남아 `recommendation=need_more_info`, `recommendation_policy=critical_missing_fields_force_need_more_info`가 됐습니다.

2026-06-08 live agent route `run_5519566dce18`에서 OpenAI Responses API, Brave search, Tesseract OCR을 함께 호출해 completion까지 확인했습니다. Model-selected sequence는 `parse_user_goal -> load_closet_memory -> search_web_candidates -> fetch/classify/OCR/normalize -> score_candidate_match -> write_purchase_report`였고, `ranked_matches` 3개 모두 `evidence_coverage`를 포함했습니다. 최종 report는 grounded 상태였으며, 각 후보는 enrichment tools가 완료됐지만 실측/소재/가격 중 critical missing field가 남아 `need_more_info`로 보류됐습니다.

2026-06-08 live agent route `run_33f4182d8fb0`에서는 final report 문장에도 coverage summary가 포함되는지 확인했습니다. Report는 `검색, 상세페이지 확인, OCR, 정규화는 완료됐지만 ... 근거가 부족합니다` 형태로 사용자에게 보류 이유를 설명했고 `final_report_grounded=True`를 유지했습니다. 일부 실측만 관찰된 후보는 `일부 실측`과 `나머지 실측`을 구분해 표시합니다.

2026-06-08 live agent route `run_ef7efd9c95c0`에서 과거 구매 기준 옷도 후보 상품과 같은 방식으로 보강되는지 확인했습니다. 입력은 "예전에 무탠다드 청바지 사이즈 L을 산 적이 있는데, 비슷한 거 추천해줘" 유형이었고, model-selected sequence에 `enrich_closet_item_from_web`가 포함됐습니다. 이 도구는 Brave search query `무탠다드 청바지 L`을 실행하고, 선택된 기준 상품 페이지를 fetch한 뒤 Tesseract OCR과 normalize를 수행해 closet memory에 병합했습니다. 해당 live page에서는 가격만 확인되고 기준 옷 실측/소재는 여전히 부족했기 때문에 final report는 후보 부족 질문과 별도로 `baseline_evidence` 질문을 남겼습니다. 이 검증에서 과거 `score_deferred` observation이 최신 completed score 이후에도 report를 막는 버그를 수정했습니다.

2026-06-09 live agent route `run_297498278703`에서 `baseline_evidence` 사용자 답변을 실제 API로 검증했습니다. 최초 run은 기준 옷 web/OCR enrichment 이후에도 기준 옷 실측/소재가 부족해 `baseline_evidence` 질문을 남겼습니다. 사용자가 `baseline length 70 shoulder 50 chest 55 sleeve 22 cotton 100% KRW 59000`을 답하자 model-selected sequence가 `load_closet_memory -> score_candidate_match -> write_purchase_report`로 재개됐고, closet memory는 `memory_source=user_supplied_baseline_evidence`, `missing_after_lookup=[]`, `measurements={length:70, shoulder:50, chest:55, sleeve:22}`, `material={cotton:100}`, `price=59000`으로 갱신됐습니다. 이후 `missing_info`에는 후보 상품 부족 질문만 남아 기준 옷 부족 질문이 해소됐습니다.

2026-06-09 live agent route `run_b987c6dc8b22`에서 다중 질문 reply contamination 방어를 검증했습니다. UI는 여러 pending question이 있을 때 한 답변을 모든 질문에 복사하지 않고, baseline/candidate cue를 기준으로 하나의 target question에만 답변을 보냅니다. 또한 backend는 같은 reply 안에 `baseline_evidence`와 `unresolved_evidence`가 같은 텍스트로 들어와도 baseline 텍스트를 candidate evidence로 병합하지 않습니다. 실제 API에서 동일한 `baseline length 70 shoulder 50 chest 55 sleeve 22 cotton 100% KRW 59000` 값을 baseline과 candidate answer 양쪽에 보냈지만, 후보 `normalize_product_spec`는 `user_evidence_used=false`를 유지했고 기준 옷 `load_closet_memory`만 `user_evidence_used=true`로 갱신됐습니다.

2026-06-09 live agent route `run_cfa5e101a231`에서 후보 URL reply 경로를 검증했습니다. 사용자가 `https://www.musinsa.com/products/1666442`를 제공하자 model-selected tool call로 `register_user_candidate_url`이 실행됐고, backend는 URL을 `user_supplied_candidate` 후보로 등록했습니다. 이후 fetch/classify/normalize/score/OCR/normalize/score가 이어졌지만 live page fetch는 `HTTP_403_FORBIDDEN`으로 막혔고 OCR evidence도 비어 있었습니다. 새 guardrail은 이 부족한 상태를 completed report로 포장하지 않고 `status=waiting_for_user`, `final_report=None`으로 멈추며 후보 `unresolved_evidence`와 기준 옷 `baseline_evidence` 질문을 남깁니다.

2026-06-09 live agent route `run_c714d85eff75`에서 후보 근거 reply와 기준 옷 근거 reply를 연속 검증했습니다. 후보 URL 등록 후 후보 근거 `length 71 shoulder 51 chest 56 sleeve 23 cotton 100% price KRW 49900`를 보내면 `normalize_product_spec.user_evidence_used=true`로 후보 spec은 채워지지만, 기준 옷 실측이 없어 `status=waiting_for_user`, `final_report=None`이 유지됩니다. 이후 기준 옷 근거 `length 70 shoulder 50 chest 55 sleeve 22 cotton 100% price KRW 59000`를 보내면 `load_closet_memory.user_evidence_used=true`, `missing_info=[]`, `score_candidate_match.recommendation=buy`, grounded final report 완료 상태로 전환됩니다.

`FITCHECK_PAGE_FETCH_FALLBACK=jina_reader`를 설정하면 직접 상품 페이지 fetch가 403 등으로 실패했을 때 backend-owned `fetch_product_page` 안에서 Jina Reader fallback을 시도합니다. 성공 시 `provider=live_page_fetch+jina_reader`, `fetch_fallback_status=completed`, `direct_fetch_status=failed`처럼 직접 fetch 실패와 fallback 성공을 분리해 trace에 남깁니다. Backend verifier는 direct 403 이후 Reader text에서 실측/소재/가격을 normalize하는 경로와 markdown 이미지 asset 생성 경로를 검증합니다.

2026-06-09 live agent route `run_c17d87ad4c5c`에서 무신사 URL `https://www.musinsa.com/products/1666442`의 403 fallback을 검증했습니다. 직접 fetch는 `HTTP_403_FORBIDDEN`으로 실패했지만 Jina Reader fallback은 completed였고, Reader markdown 이미지에서 asset 40개와 OCR target 10개가 생성됐습니다. 실제 Tesseract OCR evidence는 비어 있어 후보 실측/소재/가격은 unresolved로 남았고, agent는 final report를 만들지 않고 `waiting_for_user` 상태에서 추가 근거를 요청했습니다.

2026-06-09 live agent route `run_19231f82b5ab`에서 Jina Reader fallback 이미지가 OCR provider까지 전달되는지 재검증했습니다. `raw_input.assets`만 채워지고 `asset_results`가 갱신되지 않아 OCR evidence가 비어 있던 버그를 수정했고, 이후 `extract_size_material_info`에서 Tesseract evidence 10개가 생성됐습니다. Provider evidence에는 `image_type=size_chart/detail_text`도 보존됩니다. 실제 OCR 텍스트는 모델 착용 정보와 치수 라벨 수준이라 상품 실측값은 여전히 부족했고, agent는 `waiting_for_user`로 남아 사용자 근거를 요청했습니다.

Web UI observation panel은 score/report observation의 `evidence_coverage`도 표시합니다. `score_candidate_match(all)`처럼 shortlist 결과인 경우 첫 ranked match의 coverage를 요약해 `observed`, `missing`, `tools`, `policy`를 보여줍니다. `write_purchase_report` result도 top match coverage를 포함하므로 SSE terminal event가 report observation을 보내도 coverage 표시가 유지됩니다. Frontend smoke는 SSE 최신 observation으로 report coverage를 주입하고 `observed:material, price`, `missing:measurements.length...`, `critical_missing_fields_force_need_more_info`가 표시되는지 확인합니다.

Web UI Latest Tool panel은 fetch fallback과 OCR 상태도 요약합니다. `fetch_product_page` observation에서는 `Fallback`에 `jina_reader:completed direct:failed`, `Assets`에 asset count/OCR target count를 표시합니다. `extract_size_material_info` observation에서는 OCR evidence/text/empty/failed count와 image type을 표시하고, `normalize_product_spec` observation에서는 측정값 개수, 소재, 가격, missing field 수, OCR/user evidence 사용 여부를 표시합니다.

`/api/status`와 Web UI Runtime panel은 `page_fetch` provider 상태도 표시합니다. 현재 live 설정에서는 `provider=live_page_fetch`, `fallbacks=["jina_reader"]`가 노출되며, HTTP API smoke는 이 필드가 schema에 포함되는지 검증합니다.

Self-hosted 검색을 쓰려면 `FITCHECK_SEARCH_PROVIDER=searxng`와 `FITCHECK_SEARXNG_URL` 또는 `SEARXNG_URL`을 설정합니다. SearXNG instance에서 JSON output이 비활성화되어 있으면 provider request failure로 드러나며, 후보 성공으로 위장하지 않습니다.

Live search provider 결과는 상품형 결과만 후보로 통과시킵니다. `product_like_only` filter를 통과하지 못한 블로그, 기사, 코디 글, 커뮤니티 결과는 `rejected_candidates` 진단으로 남지만 `candidates`나 추천 shortlist 근거로 쓰지 않습니다.

`run_keyword_web_probe.py`와 `run_web_candidates_match.py`는 더 이상 특정 상품 URL/후보를 기본값으로 만들지 않습니다. source를 넣지 않고 실행하면 후보 0개의 deterministic boundary check만 수행합니다. 예전 `/api/run/web-candidates`와 `/api/results/web-candidates` route는 fixed 후보 데모 surface였기 때문에 현재 `disabled` 응답을 반환합니다.

Web demo:

```powershell
python -m uvicorn backend_harness.web_server:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

Outputs:

```text
harness_outputs/closet_onboarding_db.json
harness_outputs/end_to_end_db.json
harness_outputs/web_candidates_match_db.json
harness_outputs/closet_onboarding_result.json
harness_outputs/end_to_end_result.json
harness_outputs/web_keyword_probe/keyword_web_probe_result.json
harness_outputs/web_candidates_match_result.json
```

## Current Test Input

The first dry-run uses:

```text
C:\com2us\현재 내 옷 정보\옷 정보.txt
C:\com2us\현재 내 옷 정보\내 옷 사진.jpg
```

The text file is treated as information entered through a mobile web form, and the image is treated as an uploaded closet item photo.
