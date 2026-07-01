# API Design

## API 원칙

- Web UI는 FastAPI와 REST로 통신한다.
- 분석 작업은 오래 걸릴 수 있으므로 job 기반으로 설계한다.
- 업로드 파일, OCR 원문, 정규화 결과, 리포트를 분리해서 조회할 수 있게 한다.
- MVP에서는 인증을 생략할 수 있지만, API 형태는 user scope를 고려한다.

## Closet APIs

### POST /closet/items

기존 옷을 등록합니다.

Request:

```json
{
  "category": "shirt",
  "brand": "Uniqlo",
  "name": "Oxford Shirt",
  "size_label": "L",
  "measurements": {
    "shoulder": 48,
    "chest": 56,
    "length": 74,
    "sleeve": 62
  },
  "material": {
    "cotton": 100
  },
  "fit_notes": "어깨가 편하고 총장이 적당함",
  "design_notes": "카라와 단추 간격이 마음에 듦",
  "finish_notes": "세탁 후 변형이 적음",
  "price": 39900,
  "preference_score": 5
}
```

Response:

```json
{
  "id": "closet_001",
  "status": "created"
}
```

### GET /closet/items

등록된 옷 목록을 조회합니다.

Query:

```text
category=shirt
min_preference_score=4
```

### GET /closet/items/{closet_item_id}

기존 옷 상세 정보를 조회합니다.

### POST /closet/items/{closet_item_id}/assets

기존 옷 사진 또는 라벨 사진을 업로드합니다.

Content-Type:

```text
multipart/form-data
```

Fields:

```text
file: image
asset_type: item_photo | label_photo | detail_photo
```

### POST /closet/items/{closet_item_id}/lookup

기존 옷 정보가 부족할 때 브랜드/상품명으로 외부 정보를 검색해 보강합니다.

Request:

```json
{
  "providers": ["danawa", "naver_shopping", "coupang", "general_search"],
  "fields": ["product_code", "price", "material", "size_label", "category"]
}
```

Response:

```json
{
  "closet_item_id": "closet_001",
  "query": "가까이 유니언즈 포켓 게이머 피그먼트 반팔 티셔츠 차콜",
  "matches": [
    {
      "source": "danawa",
      "title": "피그먼트 가까이 유니언즈 GAKKAI UNIONS 포켓 게이머 반팔 티셔츠 차콜 G7770106CH",
      "url": "https://m.danawa.com/product/product.html?code=97756184",
      "confidence": 0.91
    }
  ],
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

검색 결과는 자동으로 확정하지 않고, 출처와 confidence를 보존합니다. 사용자가 직접 입력한 실측값은 외부 검색 결과보다 우선합니다.

## Product Analysis APIs

### POST /products/analyze

후보 상품 분석 job을 생성합니다.

Request:

```json
{
  "source_type": "url",
  "source_url": "https://example.com/product/123",
  "target_size_label": "L"
}
```

Response:

```json
{
  "product_analysis_id": "product_001",
  "status": "queued"
}
```

### POST /products/analyze-with-images

상세페이지 캡처 또는 상품 이미지를 업로드해 분석합니다.

Content-Type:

```text
multipart/form-data
```

Fields:

```text
files: image[]
target_size_label: string
category_hint: string optional
```

Response:

```json
{
  "product_analysis_id": "product_002",
  "status": "queued",
  "asset_count": 4
}
```

### GET /products/{product_analysis_id}

상품 분석 상태와 결과를 조회합니다.

Response:

```json
{
  "id": "product_001",
  "status": "completed",
  "normalized_spec": {
    "category": "shirt",
    "price": 42900,
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
    "overall_confidence": 0.81
  }
}
```

### GET /products/{product_analysis_id}/assets

수집/업로드된 이미지와 OCR 결과를 조회합니다.

## Match APIs

### POST /matches

후보 상품과 기존 옷장 데이터를 비교합니다.

Request:

```json
{
  "product_analysis_id": "product_001",
  "closet_item_ids": ["closet_001"],
  "match_policy": {
    "category_required": true,
    "min_preference_score": 4
  }
}
```

Response:

```json
{
  "match_report_id": "report_001",
  "status": "created"
}
```

### GET /reports/{match_report_id}

구매 판단 리포트를 조회합니다.

Response:

```json
{
  "id": "report_001",
  "score": {
    "overall": 78,
    "size": 82,
    "material": 70,
    "price": 88,
    "confidence": 81
  },
  "recommendation": "caution",
  "preference_alignment": [
    {
      "signal": "fit",
      "status": "matched",
      "evidence": "실측 비교 점수가 100점으로 기준 옷 핏과 가깝습니다."
    },
    {
      "signal": "durability",
      "status": "needs_baseline_material",
      "evidence": "기준 옷 소재 정보가 없어 오래 입어도 괜찮은 이유를 소재 기준으로 확정하기 어렵습니다."
    }
  ],
  "summary": "사이즈는 대체로 유사하지만 총장이 짧고 소재가 달라 착용감 차이가 예상됩니다.",
  "risks": [
    {
      "level": "caution",
      "type": "length",
      "message": "기존 선호 셔츠보다 총장이 4cm 짧습니다."
    }
  ]
}
```

## Internal Tool APIs

초기에는 Python 함수로 구현하고, 나중에 MCP 또는 내부 HTTP tool로 분리할 수 있습니다.

```text
classify_image(asset_id) -> ImageClassification
extract_ocr(asset_id) -> OCRResult
normalize_product(raw_texts, hints) -> NormalizedProductSpec
retrieve_closet_items(category, policy) -> list[ClosetItem]
match_product(product_spec, closet_items) -> MatchResult
generate_report(match_result) -> Report
```

## Error Handling

```json
{
  "error": {
    "code": "INSUFFICIENT_PRODUCT_INFO",
    "message": "사이즈표 또는 실측 정보를 찾지 못했습니다.",
    "recoverable": true,
    "required_fields": ["shoulder", "chest", "length"]
  }
}
```

주요 에러 코드:

```text
UNSUPPORTED_SOURCE
IMAGE_CLASSIFICATION_FAILED
OCR_FAILED
INSUFFICIENT_PRODUCT_INFO
NO_COMPARABLE_CLOSET_ITEM
LOW_CONFIDENCE_RESULT
LLM_REPORT_FAILED
```
