# Agent Design

## Agent 정의

FitCheck Agent는 사용자의 기존 옷장 데이터를 memory로 사용하고, 구매 후보 상품의 흩어진 정보를 tool로 수집/정규화/검증한 뒤 구매 판단 리포트를 생성하는 task-oriented agent입니다.

이 agent의 핵심은 자유 대화가 아니라, 구매 검증 workflow를 안정적으로 수행하는 것입니다.

## Agent 목표

```text
Given:
- user closet profile
- candidate product source

Do:
- identify available product information
- extract reliable specs
- compare against preferred closet items
- estimate purchase risk
- ask for missing critical information when needed
- produce grounded decision report
```

## Agent 상태

Agent는 한 번의 분석 작업에서 다음 상태를 가집니다.

```json
{
  "job_id": "job_001",
  "user_goal": "check whether this shirt matches my preferred fit",
  "inputs": {
    "urls": [],
    "uploaded_assets": []
  },
  "detected_sources": [],
  "tool_results": [],
  "normalized_product": null,
  "comparison_targets": [],
  "risks": [],
  "missing_fields": [],
  "final_report": null
}
```

## Tool 목록

### 1. ProductPageParser

입력:

```json
{
  "url": "https://example.com/product/123"
}
```

출력:

```json
{
  "title": "Oxford Shirt",
  "price": 42900,
  "image_urls": ["..."],
  "option_text": "S/M/L/XL",
  "raw_text": "..."
}
```

### 2. ImageClassifier

이미지를 분석 목적별로 분류합니다.

```text
outfit_image: 모델 착장 이미지
product_image: 상품 단독 이미지
size_chart: 사이즈표
material_chart: 소재/세탁 정보
detail_text: 상세 설명 이미지
banner: 무관한 광고/배너
unknown: 판단 불가
```

### 3. OCRExtractor

사이즈표, 소재표, 상세 설명 이미지에만 OCR을 수행합니다.

중요 정책:

- 모든 이미지에 무조건 OCR을 걸지 않는다.
- 착장 이미지는 OCR 대상이 아니다.
- OCR 결과는 raw text와 confidence를 함께 저장한다.

### 4. SpecNormalizer

OCR/HTML 텍스트를 정규화된 product spec으로 변환합니다.

예:

```json
{
  "category": "shirt",
  "measurements": {
    "shoulder": 47,
    "chest": 55,
    "length": 70,
    "sleeve": 61
  },
  "material": {
    "cotton": 60,
    "polyester": 40
  },
  "fit_keywords": ["regular fit"],
  "confidence": 0.78
}
```

### 5. ExistingItemLookup

사용자가 입력한 기준 옷 정보가 부족할 때 브랜드/상품명으로 외부 상품 정보를 검색해 보강합니다.

입력:

```json
{
  "brand": "가까이 유니언즈",
  "name": "포켓 게이머 피그먼트 반팔 티셔츠 차콜",
  "known_measurements": {
    "length": 76,
    "shoulder": 57,
    "chest": 62,
    "sleeve": 25
  }
}
```

출력:

```json
{
  "query": "가까이 유니언즈 포켓 게이머 피그먼트 반팔 티셔츠 차콜",
  "matches": [],
  "enrichment": {
    "product_code": "G7770106CH",
    "observed_price_range_krw": {
      "min": 34590,
      "max": 44100
    }
  },
  "missing_after_lookup": ["size_label", "material"]
}
```

중요 정책:

- 외부 검색 결과는 사용자의 직접 입력보다 낮은 우선순위를 가진다.
- 출처 URL, title, confidence를 함께 저장한다.
- 여러 출처가 충돌하면 자동 확정하지 않고 후보로 둔다.
- 소재/사이즈처럼 찾지 못한 핵심 정보는 추가 질문으로 남긴다.

### 6. ClosetRetriever

후보 상품과 비교할 기존 옷을 고릅니다.

우선순위:

1. 같은 카테고리
2. 높은 preference score
3. 최근 착용/등록
4. 같은 계절감 또는 소재

### 7. FitMatcher

규칙 기반 비교를 수행합니다.

```text
measurement_delta = candidate_measurement - closet_measurement
material_similarity = overlap of material composition
price_delta = candidate_price - preferred_price_range
fit_risk = keyword/review/measurement based risk
```

### 8. RiskScorer

구매 위험도를 계산합니다.

주요 위험:

- 실측 차이가 허용 범위를 초과함
- 소재가 선호 소재와 다름
- 리뷰에서 작다/크다/얇다/비친다/구김 같은 표현이 반복됨
- OCR confidence가 낮음
- 핵심 정보가 없음

### 9. ReportGenerator

구조화된 결과를 사용자 친화적 문장으로 변환합니다.

LLM은 이 단계에서 사용합니다. 단, LLM에게 최종 판단을 전부 맡기지 않고, 이미 계산된 점수와 근거를 입력으로 제공합니다.

## Orchestration Logic

```text
1. validate input
2. detect source type
3. parse URL if present
4. classify images
5. OCR only information-heavy images
6. lookup existing closet item when baseline fields are missing
7. normalize product spec
8. retrieve comparable closet items
9. calculate match score
10. calculate risk score
11. if critical fields are missing, run search/fetch/OCR/normalize enrichment before any user question
12. generate report
13. persist artifacts and result
```

## LLM 사용 경계

LLM을 쓰는 부분:

- OCR 텍스트 정리
- 상품 설명에서 key-value 추출 보조
- 리뷰 요약
- 사용자용 리포트 문장 생성
- 자동 보강 후에도 남은 근거 부족 항목 정리

LLM을 쓰지 않는 부분:

- 실측 차이 계산
- 가격 차이 계산
- 소재 비율 비교
- confidence threshold 판단
- API validation
- DB persistence

## 판단 기준 예시

셔츠 기준:

```text
shoulder:
  abs(delta) <= 1.5cm: good
  <= 3cm: caution
  > 3cm: risk

chest:
  abs(delta) <= 2cm: good
  <= 4cm: caution
  > 4cm: risk

length:
  abs(delta) <= 2cm: good
  <= 5cm: caution
  > 5cm: risk
```

## 정보 부족 대응

Agent는 정보가 부족할 때 억지로 추천하지 않습니다.

예:

```text
사이즈표를 찾지 못했습니다.
구매 판단에 필요한 핵심 정보가 부족합니다.

추가로 확인하면 좋은 정보:
- 어깨 단면
- 가슴 단면
- 총장
- 소재 혼용률
```

## Agentic AI로 보이는 지점

- 입력을 보고 tool 선택이 달라진다.
- 중간 결과의 신뢰도를 평가한다.
- 부족한 정보를 질문으로 되돌린다.
- 단일 LLM 답변이 아니라 다단계 workflow를 수행한다.
- 계산 가능한 판단은 deterministic logic으로 처리한다.
