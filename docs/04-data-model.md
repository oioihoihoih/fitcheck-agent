# Data Model

## 설계 원칙

- OCR 원문과 정규화 결과를 모두 저장한다.
- 각 추출 필드에 confidence를 둔다.
- 사용자의 선호 데이터와 후보 상품 분석 데이터를 분리한다.
- match report는 특정 시점의 분석 결과로 보존한다.

## Entity Overview

```text
User
  has many ClosetItem

ClosetItem
  has many ClosetAsset

ProductAnalysis
  has many ProductAsset
  has one NormalizedProductSpec

MatchReport
  belongs to ProductAnalysis
  compares multiple ClosetItem
```

## ClosetItem

사용자가 이미 가지고 있고 만족하는 옷입니다.

```json
{
  "id": "closet_001",
  "user_id": "user_001",
  "category": "shirt",
  "brand": "Uniqlo",
  "name": "Oxford Shirt",
  "size_label": "L",
  "measurements": {
    "shoulder": 48.0,
    "chest": 56.0,
    "length": 74.0,
    "sleeve": 62.0,
    "waist": null,
    "rise": null,
    "thigh": null,
    "hem": null
  },
  "material": {
    "cotton": 100
  },
  "preference_reasons": [
    "핏이 마음에 듦",
    "오래 입어도 괜찮음",
    "디자인이 마음에 듦"
  ],
  "preference_signals": ["fit", "durability", "design"],
  "lookup_enrichment": {
    "product_code": "G7770106CH",
    "observed_price_range_krw": {
      "min": 34590,
      "max": 44100
    },
    "lookup_confidence": 0.91,
    "source_urls": []
  },
  "missing_after_lookup": ["size_label", "material"],
  "fit_notes": "어깨가 편하고 총장이 적당함",
  "design_notes": "카라 크기와 단추 간격이 마음에 듦",
  "finish_notes": "봉제가 깔끔하고 세탁 후 변형이 적음",
  "price": 39900,
  "preference_score": 5,
  "created_at": "2026-06-04T00:00:00+09:00"
}
```

## ClosetAsset

기존 옷과 연결된 사진입니다.

```json
{
  "id": "asset_001",
  "closet_item_id": "closet_001",
  "asset_type": "item_photo",
  "file_path": "uploads/closet/asset_001.jpg",
  "ocr_text": null,
  "created_at": "2026-06-04T00:00:00+09:00"
}
```

## ProductAnalysis

후보 상품 분석 작업입니다.

```json
{
  "id": "product_001",
  "user_id": "user_001",
  "source_type": "url",
  "source_url": "https://example.com/product/123",
  "status": "completed",
  "raw_title": "Oxford Regular Shirt",
  "raw_price": "42,900원",
  "normalized_spec_id": "spec_001",
  "created_at": "2026-06-04T00:00:00+09:00",
  "completed_at": "2026-06-04T00:02:12+09:00"
}
```

## ProductAsset

후보 상품에서 수집한 이미지입니다.

```json
{
  "id": "product_asset_001",
  "product_analysis_id": "product_001",
  "source": "uploaded",
  "file_path": "uploads/products/product_asset_001.png",
  "image_type": "size_chart",
  "classification_confidence": 0.91,
  "ocr_text": "SIZE L 어깨 47 가슴 55 총장 70 소매 61",
  "ocr_confidence": 0.83
}
```

## NormalizedProductSpec

분석된 상품 정보를 비교 가능한 형태로 정규화한 결과입니다.

```json
{
  "id": "spec_001",
  "product_analysis_id": "product_001",
  "category": "shirt",
  "brand": "Unknown",
  "name": "Oxford Regular Shirt",
  "price": 42900,
  "size_label": "L",
  "measurements": {
    "shoulder": 47.0,
    "chest": 55.0,
    "length": 70.0,
    "sleeve": 61.0
  },
  "material": {
    "cotton": 60,
    "polyester": 40
  },
  "fit_keywords": ["regular fit"],
  "review_keywords": ["정사이즈", "얇음"],
  "missing_fields": ["model_height", "model_weight"],
  "field_confidence": {
    "measurements": 0.83,
    "material": 0.76,
    "price": 0.95,
    "category": 0.89
  },
  "overall_confidence": 0.81
}
```

## MatchReport

후보 상품과 기존 옷장 기준을 비교한 결과입니다.

```json
{
  "id": "report_001",
  "product_analysis_id": "product_001",
  "closet_item_ids": ["closet_001"],
  "score": {
    "overall": 78,
    "size": 82,
    "material": 70,
    "price": 88,
    "design": 75,
    "confidence": 81
  },
  "measurement_deltas": {
    "shoulder": -1.0,
    "chest": -1.0,
    "length": -4.0,
    "sleeve": -1.0
  },
  "risks": [
    {
      "level": "caution",
      "type": "length",
      "message": "기존 선호 셔츠보다 총장이 4cm 짧습니다."
    },
    {
      "level": "caution",
      "type": "material",
      "message": "폴리에스터 혼방 비율이 있어 촉감이 다를 수 있습니다."
    }
  ],
  "missing_fields": ["model_height", "model_weight"],
  "recommendation": "caution",
  "summary": "사이즈는 대체로 유사하지만 총장이 짧고 소재가 달라 착용감 차이가 예상됩니다."
}
```

## 권장 DB 테이블

MVP에서는 JSON column을 적극적으로 사용해 빠르게 구현합니다.

```text
users
closet_items
closet_assets
product_analyses
product_assets
normalized_product_specs
match_reports
```
