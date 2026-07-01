# Harness to Production Map

이 문서는 현재 만든 local harness가 실제 웹앱/백엔드 구현에서 어떤 컴포넌트로 교체될지 정리합니다.

## 현재 Harness 구조

```text
backend_harness/
  run_closet_onboarding.py
  app/
    api.py
    agent.py
    db.py
    schemas.py
    tools/
      image_classifier.py
      input_normalizer.py
      ocr_extractor.py
      llm_client.py
```

## 경계 매핑

| Harness | Production | 설명 |
|---|---|---|
| `ApiHarness` | FastAPI router | 실제 HTTP API 요청/응답 처리 |
| `JsonStore` | PostgreSQL/SQLite + ORM | closet item, product analysis, report 저장 |
| `ToolCall` dataclass | Pydantic schema | tool call log와 API response schema |
| `ClosetOnboardingAgent` | Agent service | tool orchestration 담당 |
| `input_normalizer.py` | Spec normalizer service | 텍스트/폼/OCR 결과 정규화 |
| `closet_lookup.py` | Existing item lookup adapters | 기준 옷의 상품명/브랜드 기반 검색 보강 |
| `image_classifier.py` | VLM or classifier | 이미지 유형 분류 |
| `ocr_extractor.py` | PaddleOCR/Tesseract/cloud OCR | 사이즈표/소재표 OCR |
| `llm_client.py` | OpenAI/Claude API client | 리포트 생성, 질문 생성, 리뷰 요약 |
| `harness_outputs/*.json` | DB rows + logs | 분석 결과와 디버깅 로그 |

## 서버 설계

현재:

```python
ApiHarness.post_closet_items_from_mobile_form(payload)
```

실제 FastAPI:

```text
POST /closet/items
POST /closet/items/{id}/assets
GET /closet/items/{id}
POST /products/analyze
POST /matches
GET /reports/{id}
```

구현 시 `ApiHarness` 내부 로직은 FastAPI route handler에서 그대로 service 호출로 바뀝니다.

## DB 설계

현재:

```text
harness_outputs/closet_onboarding_db.json
harness_outputs/end_to_end_db.json
harness_outputs/web_candidates_match_db.json
```

실제:

```text
SQLite for MVP
PostgreSQL for production
```

첫 구현에서는 SQLite를 권장합니다. 이유는 다음과 같습니다.

```text
- 과제 MVP에서 설치/운영 부담이 낮음
- JSON field로 OCR 결과와 tool log 저장 가능
- 나중에 PostgreSQL로 이동하기 쉬움
```

## Schema 설계

현재는 dict와 작은 dataclass만 사용합니다. 실제 API에서는 Pydantic schema를 사용합니다.

예상 schema:

```text
ClosetItemCreate
ClosetItemRead
UploadedAssetRead
ProductAnalysisCreate
NormalizedProductSpecRead
MatchReportRead
ToolCallLog
```

schema에서 중요한 점:

```text
- measurement 단위는 cm로 고정
- source와 confidence를 field 단위로 저장
- null과 unknown을 구분
- OCR raw text와 normalized spec을 분리
```

## OCR 설계

현재:

```text
item_photo -> OCR skipped
label_photo/size_chart/material_chart -> not_implemented
```

실제:

```text
1. image classifier로 OCR 대상 이미지 필터링
2. OCR 실행
3. raw text 저장
4. spec normalizer로 수치/소재 추출
5. confidence 낮으면 추가 질문 생성
```

OCR을 모든 이미지에 적용하지 않는 이유:

```text
- 착장 이미지와 상품 단독 이미지는 spec 정보가 거의 없음
- 무관한 배너 텍스트가 상품 정보로 잘못 들어갈 수 있음
- 비용과 시간이 증가함
```

## 기준 옷 Lookup 설계

현재:

```text
closet_lookup.py
-> fixture_lookup
-> external_api_called=false
```

실제:

```text
1. 사용자가 입력한 브랜드/상품명/사진으로 query 생성
2. Naver Shopping, Danawa, Coupang, brand site, general search adapter 호출
3. 상품명 유사도, 브랜드 일치, 색상/제품코드 일치로 후보 scoring
4. price, product_code, category, material, size_label 후보 추출
5. 사용자 직접 입력값과 충돌하면 덮어쓰지 않고 conflict로 기록
6. source URL과 confidence 저장
```

우선순위:

```text
사용자 직접 입력 > 라벨/사이즈표 OCR > 브랜드 공식 페이지 > 가격비교/쇼핑몰 > 일반 검색 snippet
```

기준 옷 lookup이 필요한 이유:

```text
- 사용자가 소재/가격/사이즈 라벨을 모를 수 있음
- 기존 옷의 상품 코드를 찾으면 동일/유사 상품 후보 탐색이 쉬움
- 후보 상품과 비교할 때 baseline confidence를 높일 수 있음
```

## LLM 설계

현재:

```text
llm_stub
external_api_called=false
```

실제 LLM 사용 위치:

```text
- OCR raw text에서 key-value 후보 정리
- 리뷰 요약
- 자동 보강 후에도 남은 근거 부족 항목 정리
- 최종 리포트 문장화
```

LLM을 쓰지 않을 위치:

```text
- 실측 차이 계산
- 가격 차이 계산
- threshold 기반 risk 판단
- DB 저장
- API validation
```

이 경계를 지켜야 결과를 테스트하고 설명할 수 있습니다.

## Agent Workflow 확장

현재 완료한 workflow:

```text
closet item onboarding
```

다음에 추가할 workflow:

```text
product candidate analysis
-> match report generation
```

필요 tool:

```text
ProductImageClassifier
ProductOCRExtractor
ProductSpecNormalizer
ClosetRetriever
FitMatcher
RiskScorer
ReportGenerator
```

## 발표에서 설명할 포인트

```text
구현 전 설계만 한 것이 아니라, 실제 사용자 입력을 기준으로 API 경계와 agent tool chain을 dry-run 했습니다.
현재는 외부 OCR/LLM 없이 harness로 검증했고, 나중에 각 stub을 실제 provider로 교체할 수 있게 경계를 분리했습니다.
```
