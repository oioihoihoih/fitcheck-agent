# FitCheck Agent

평소 만족하며 입는 옷의 **핏·재질·사이즈·실루엣·디자인**을 기준으로, 온라인에서 비슷한 의류 후보를 찾아 근거와 함께 비교해 주는 AI Agent입니다.

> 컴투스 AI Agent 과제로 설계·구현한 프로젝트입니다. 발표 자료는 [`submission/FitCheck_ppt.pdf`](submission/FitCheck_ppt.pdf)에 있습니다.
>
> **상태: 중단.** 프로토타입까지 만든 뒤 사업화를 검토했지만, 비용과 속도 문제가 결국 "쇼핑몰이 직접 하면 사라지는 문제"라는 결론을 내리고 개발을 멈췄습니다. 자세한 이유는 [사업화를 중단한 이유](#사업화를-중단한-이유)에 있습니다.

## 왜 만들었나

마음에 드는 옷과 비슷한 핏·소재·실측을 가진 새 옷을 찾으려면 여러 쇼핑몰의 상세페이지, 사이즈표 이미지, 소재 표기를 직접 뒤져야 합니다. FitCheck는 사용자가 이미 좋아하는 옷을 **기준(baseline) 메모리**로 삼고, Agent가 검색 → 상세페이지 수집 → 이미지 분류/OCR → 스펙 정규화 → 매칭 점수 계산을 도구로 수행해 **근거 있는 후보 shortlist**를 만듭니다.

설계 원칙은 "LLM이 모든 판단을 대신하지 않는다"입니다. LLM은 다음에 쓸 도구를 고르고, 실제 실행과 상태 저장, 점수 계산, 최종 보고서의 근거 검증은 백엔드가 맡습니다.

## 주요 기능

- **자유 입력 대화**: 텍스트, 상품 URL, 이미지 첨부를 그대로 받아 LLM이 tool-calling loop로 필요한 도구를 직접 선택합니다.
- **옷장 메모리**: 좋아하는 옷을 등록·수정하고 비교 기준으로 지정합니다. 웹에서 해당 옷의 실측·소재 정보를 보강할 수 있습니다.
- **근거 기반 리포트**: 최종 보고서는 저장된 관측(observation)만으로 작성되고, 인용 근거(citation)와 근거 충족도를 함께 보여 줍니다.
- **부족한 정보 처리**: 검색·OCR로도 채우지 못한 정보만 사용자에게 마지막에 질문합니다. 억지로 추천하지 않습니다.
- **실행 제어**: SSE 토큰 스트리밍, 실행 취소, 진행 중 메시지 큐잉(steering), 멀티턴 후속 질문("두 번째 거랑 비교해 줘"), 턴/도구 예산을 지원합니다.
- **후보 비교**: 후보 비교 표, 즐겨찾기 저장, 실행 기록 복원을 지원합니다.

## 아키텍처

```text
[Web UI: React + Vite (webapp-next)]
        │  REST + SSE
        ▼
[FastAPI backend (backend_harness/web_server.py)]
        │
        ▼
[Agent runtime]  ── LLM (OpenAI Responses API 또는 OpenAI-compatible 로컬 LLM)
  - tool-calling loop, run state, trace, steering/cancel
        │  model-selected tool call
        ▼
[Backend-owned tool registry]
  검색 · 페이지 수집 · 이미지 분류 · OCR · 스펙 정규화 · 매칭 점수 · 리포트
        │
        ▼
[Storage: SQLite run store, closet/favorites]
```

### Agent 도구

| 도구 | 역할 |
| --- | --- |
| `parse_user_goal` | 사용자 목표, 좋아하는 옷의 단서, 입력 힌트 정규화 (선택) |
| `plan_context_and_sources` | 대화를 출처 참조와 검색 조건으로 정리 |
| `load_closet_memory` | 옷장 기준 아이템과 선호 신호 로드 |
| `enrich_closet_item_from_web` | 기준 옷의 실측·소재를 웹에서 검색·OCR로 보강 |
| `confirm_baseline_identity` | 검색으로 찾은 기준 옷 후보를 확정 메모리로 승격 |
| `inspect_uploaded_images` | 업로드한 의류 이미지 확인 |
| `search_web_candidates` | 기준 옷과 비슷한 후보 상품 웹 검색 |
| `register_user_candidate_url` | 사용자가 준 상품 URL을 후보로 등록 |
| `fetch_product_page` | 후보 상세페이지 수집 |
| `classify_product_assets` | 상품 이미지를 착장컷/상품컷/사이즈표/소재표 등으로 분류 |
| `extract_size_material_info` | OCR·비전으로 사이즈·소재 근거 추출 |
| `normalize_product_spec` | 실측, 소재, 가격, 누락 필드 정규화 |
| `score_candidate_match` | 기준 옷 대비 후보 매칭 점수 계산 |
| `ask_missing_info` | 끝까지 채우지 못한 정보만 사용자에게 질문 |
| `write_purchase_report` | 저장된 관측으로 근거 기반 최종 리포트 작성 |
| `load_agent_skill` | 상황별 Agent skill 지침 로드 (`backend_harness/agent_skills/`) |

## 기술 스택

- **Backend**: Python 3.12, FastAPI, Uvicorn, SQLite
- **LLM**: OpenAI Responses API (기본), Ollama · LM Studio · vLLM 같은 OpenAI-compatible 서버
- **검색**: Brave Search (권장), Tavily, Jina, SearXNG, DuckDuckGo HTML
- **OCR**: Tesseract CLI (`kor+eng`)
- **Frontend**: React 18 + TypeScript + Vite (`webapp-next/`), 레거시 vanilla JS UI (`webapp/`)

## 폴더 구조

```text
backend_harness/      FastAPI 서버, agent runtime, tool registry, 검증 스크립트
  app/                  runtime · routes · tools · providers
  agent_skills/         runtime이 불러 쓰는 Agent skill 지침
developer_harness/    개발용 세션 하네스 (/api/harness/v1)
webapp-next/          React + Vite 웹 앱
webapp/               레거시 웹 UI (/legacy)
docs/                 설계 문서, 개선 로그, 백로그
samples/              데모용 옷장 아이템과 후보 상품 fixture
submission/           과제 발표 자료 (PDF)
tools/                Tesseract WSL wrapper
.agents/, .codex/     개발 하네스가 쓰는 skill, 에이전트 설정
```

## 빠른 시작

```bash
# 1. Python 의존성
python -m pip install -r requirements.txt

# 2. 환경 변수: .env.example을 복사해 OPENAI_API_KEY, BRAVE_SEARCH_API_KEY 등을 채웁니다
cp .env.example .env

# 3. 웹 앱 빌드 (선택, 없으면 레거시 UI가 뜹니다)
cd webapp-next && npm install && npm run build && cd ..

# 4. 서버 실행
python -m uvicorn backend_harness.web_server:app --host 127.0.0.1 --port 8000
```

브라우저에서 `http://127.0.0.1:8000`을 엽니다. LLM이 설정되지 않았으면 Agent는 실행되지 않고 `/api/status`와 화면에 `configuration_required`가 표시됩니다.

프론트엔드 개발 서버(`npm run dev`, 포트 5199)는 `/api`를 `127.0.0.1:8731`로 프록시하므로, 이때는 백엔드를 `--port 8731`로 띄웁니다.

### OCR (선택)

`FITCHECK_OCR_PROVIDER=tesseract_cli`로 실제 OCR을 켤 수 있습니다. Tesseract를 설치하고, 언어 데이터(`kor`, `eng`, `osd`)는 Tesseract 설치본에 포함된 것을 쓰거나 [tesseract-ocr/tessdata](https://github.com/tesseract-ocr/tessdata)에서 받아 `tools/tessdata/`에 둡니다. 기본값 `fixture_boundary`는 실제 OCR을 하지 않습니다.

## 검증

외부 API를 호출하지 않는 기본 검증:

```bash
python -B backend_harness/verify_end_to_end.py
python -B backend_harness/verify_web_candidates_match.py
python -B backend_harness/verify_agent_runtime.py
python -B backend_harness/verify_input_variation_matrix.py
python -B backend_harness/verify_http_api_smoke.py
python -B backend_harness/verify_production_agent_runtime.py
npm install && npm run verify:frontend
```

실제 LLM·검색 provider를 호출하는 live smoke는 환경 변수로 명시적으로 켜야 실행됩니다. 자세한 방법은 [실행·검증 상세 가이드](docs/00-runbook.md)를 참고하세요.

## 사업화를 중단한 이유

과제용 프로토타입까지 만든 뒤 실제 서비스로 키울지 검토했고, 아래 이유로 중단했습니다. 수치는 개발 중 실제 OpenAI·Brave API로 돌린 실행 376건의 기록입니다. 같은 옷으로 반복 테스트한 실행이 많아 실제 사용자 분포와는 다를 수 있습니다.

### 1. 비싸다

- 질문 한 번에 LLM 호출 약 10회, 입력 토큰 약 16만 개, 상세페이지 수집 6회, OCR 5회가 돌아갑니다. API 비용만 **회당 평균 약 100원**입니다(gpt-5.4-mini·Brave Search 단가, 1달러 1,400원 기준, 서버·프록시 제외).
- 수익원으로 가장 현실적인 쇼핑 제휴 수수료는 의류 결제액의 3~5%입니다. 5만 원짜리 옷 한 벌이면 1,500~2,500원이라, **질문 100번 중 4~7번은 실제 구매로 이어져야** API 비용만 겨우 메웁니다.
- 그런데 376번 중 "사도 된다(buy)" 판정은 한 번도 나오지 않았습니다. 대부분 "정보 부족"이나 "비추천"으로 끝났습니다.

### 2. 느리다

- 질문 한 번에 중앙값 **66초**가 걸리고, 4번 중 1번은 1분 30초를 넘깁니다. 바로 결과가 뜨는 쇼핑 앱 검색과는 비교가 되지 않습니다.

### 3. 이 두 문제는 쇼핑몰이 직접 하면 사라진다

결정적인 이유입니다.

- 비싸고 느린 원인은 상품 정보를 **밖에서 구해야 한다**는 데 있습니다. 매번 검색 → 페이지 수집 → 사이즈표 이미지 OCR → LLM 정리 → 점수 계산을 반복합니다.
- 쇼핑몰은 자기 상품의 실측·소재·가격을 이미 DB에 정리해 두고 있습니다. 같은 비교를 DB 조회와 간단한 계산만으로 **즉시, 거의 공짜로** 할 수 있고, LLM도 크롤링도 필요 없습니다.
- 실제로 무신사는 보유한 옷의 실측을 넣으면 상품 사이즈와 비교해 주는 '마이사이즈'를 2021년부터 제공하고 있습니다.
- 결국 FitCheck의 핵심 기능은 독립 서비스라기보다 **쇼핑몰의 기능 하나**에 가깝습니다. 밖에서 같은 일을 하면 플랫폼보다 비싸고 느리고 부정확할 수밖에 없습니다.

### 4. 쇼핑몰에 파는 길도 막혀 있다

- 남은 방향은 이 기능을 쇼핑몰에 파는 것(B2B)입니다. 하지만 큰 플랫폼은 이미 비슷한 기능이 있거나 직접 만들 수 있습니다. 작은 쇼핑몰은 실측 데이터가 정리돼 있지 않은 경우가 많아, 결국 같은 수집·OCR 문제로 돌아갑니다.
- 독립 서비스로 남으려면 쇼핑몰 크롤링에 계속 기대야 하는데, 이 방식은 차단과 약관 문제를 피하기 어렵습니다.

### 5. 쓸 사람이 적다

- 비교하려면 좋아하는 옷의 총장·어깨너비·가슴단면을 직접 재서 넣어야 합니다. 대부분의 소비자는 이 수고를 하지 않습니다. 기꺼이 하는 사람은 이미 실측표를 스스로 비교할 줄 압니다.

## 문서

- [실행·검증 상세 가이드](docs/00-runbook.md): API 목록, provider 설정, live smoke
- [Product Brief](docs/01-product-brief.md) · [System Architecture](docs/02-system-architecture.md) · [Agent Design](docs/03-agent-design.md)
- [Data Model](docs/04-data-model.md) · [API Design](docs/05-api-design.md) · [Test and Evaluation](docs/07-test-and-evaluation.md)
- [Production AI Agent Design](docs/16-production-ai-agent-design.md) · [What Is an AI Agent?](docs/17-what-is-ai-agent.md)
- [Agent Runtime Capability Gaps](docs/24-agent-runtime-capability-gaps.md) · [Improvement Backlog](docs/25-improvement-backlog.md)
