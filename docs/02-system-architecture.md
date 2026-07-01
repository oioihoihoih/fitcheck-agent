# System Architecture

## 전체 구조

```text
[Next.js Web App]
  - closet item registration
  - product URL/image upload
  - analysis progress
  - match report viewer
        |
        v
[FastAPI Backend]
  - REST API
  - auth/session boundary for future
  - file upload handling
  - background job trigger
        |
        v
[Agent Orchestrator]
  - plan analysis steps
  - call tools
  - validate intermediate outputs
  - surface unresolved evidence only after tool enrichment
        |
        v
[Tool Layer]
  - product page parser
  - image classifier
  - OCR extractor
  - spec normalizer
  - closet retriever
  - fit matcher
  - risk scorer
  - report generator
        |
        v
[Storage]
  - closet items
  - uploaded assets
  - OCR raw text
  - normalized product specs
  - match reports
```

## 컴포넌트 책임

### Web App

사용자가 직접 보는 화면입니다.

- 기존 옷 등록
- 후보 상품 입력
- 이미지 업로드
- 분석 진행 상태 표시
- 최종 리포트 확인
- 부족 정보 입력

### FastAPI Backend

외부 인터페이스와 작업 실행 경계를 담당합니다.

- API request validation
- file upload
- job creation
- agent pipeline 호출
- 결과 저장/조회
- OpenAPI 문서 자동 제공

### Agent Orchestrator

이 프로젝트의 핵심입니다. 사용자의 입력을 보고 어떤 tool을 어떤 순서로 호출할지 결정합니다.

예:

```text
상품 URL 입력
-> page parser 실행
-> 이미지 목록 수집
-> image classifier 실행
-> 사이즈표/소재표만 OCR 실행
-> spec normalizer 실행
-> closet retriever 실행
-> fit matcher 실행
-> risk scorer 실행
-> report generator 실행
```

### Tool Layer

각 tool은 작고 검증 가능한 함수/API로 둡니다.

- `ProductPageParser`: URL에서 title, price, image URL, option text 추출
- `ImageClassifier`: 이미지를 착장컷, 상품컷, 사이즈표, 소재표, 배너로 분류
- `OCRExtractor`: 이미지에서 텍스트 추출
- `SpecNormalizer`: OCR/HTML 텍스트를 정규화된 product spec으로 변환
- `ClosetRetriever`: 비교 기준이 될 기존 옷을 조회
- `FitMatcher`: 실측, 소재, 가격 기준 비교
- `RiskScorer`: 리스크 점수 계산
- `ReportGenerator`: 사용자용 리포트 생성

## 주요 플로우

### 기존 옷 등록

```text
User
-> Web App
-> POST /closet/items
-> Backend validation
-> Store closet item
-> Return closet item profile
```

### 후보 상품 분석

```text
User
-> URL or images upload
-> POST /products/analyze
-> Create analysis job
-> Agent Orchestrator
-> Parser/OCR/Image tools
-> Normalized product spec
-> Store analysis result
```

### 구매 적합도 비교

```text
User selects candidate product
-> POST /matches
-> ClosetRetriever finds comparable closet items
-> FitMatcher compares measurements/material/price
-> RiskScorer calculates risk
-> ReportGenerator creates final report
-> Store and return match report
```

## 실무형 설계 판단

### LLM을 모든 판단에 쓰지 않는다

실측 차이, 소재 비율, 가격 차이처럼 계산 가능한 부분은 규칙 기반으로 처리합니다. LLM은 OCR 텍스트 정리, 리뷰 요약, 리포트 문장화에 사용합니다.

### 정보 신뢰도를 항상 저장한다

OCR 결과나 이미지 분류 결과는 틀릴 수 있습니다. 따라서 각 추출 필드에 confidence를 저장하고, 낮은 confidence는 최종 리포트에서 경고합니다.

### 분석 원문을 보관한다

정규화 결과만 저장하면 디버깅이 어렵습니다. OCR raw text, HTML raw snippet, image classification result를 함께 저장합니다.

### MVP와 production을 분리한다

MVP에서는 SQLite와 synchronous/background task로 충분합니다. production에서는 PostgreSQL, object storage, queue, worker를 붙입니다.

## 배포 관점

### MVP

```text
Next.js dev server
FastAPI uvicorn server
SQLite local DB
local uploads directory
```

### Production

```text
Frontend: Vercel or static hosting
Backend: containerized FastAPI
DB: PostgreSQL
Object storage: S3-compatible storage
Queue: Redis + Celery/RQ
Monitoring: structured logs + tracing
```
