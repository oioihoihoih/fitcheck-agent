# Closet Onboarding Dry Run

이 문서는 실제 웹앱을 만들기 전에, 사용자가 휴대폰 웹폼으로 본인의 옷 정보를 입력했다고 가정하고 FitCheck Agent의 첫 번째 workflow를 검증한 기록입니다.

## 테스트 목적

현재 단계의 목적은 후보 상품 추천이 아니라, `내 옷장 기준 데이터`가 agent memory로 저장될 수 있는지 확인하는 것입니다.

검증할 흐름:

```text
휴대폰 웹 입력
-> POST /closet/items
-> 입력 schema validation
-> 텍스트 정보 정규화
-> 브랜드/상품명 기반 기존 옷 정보 lookup
-> 이미지 분류
-> OCR 필요 여부 판단
-> closet item DB 저장
-> LLM summary 생성 경계 확인
```

## 사용한 원본 파일

```text
samples/closet/owned_item_info.txt
samples/closet/owned_item_photo.jpg
```

`owned_item_info.txt`는 UTF-8로 읽으면 다음 정보가 복원됩니다.

```text
제품명 : 포켓 게이머 피그먼트 반팔 티셔츠 차콜
브랜드 : 가까이 유니언즈

총장    어깨너비    가슴단면    소매길이
76      57          62          25

옷이 마음에 드는 이유 : 핏이 마음에 듦, 오래 입어도 괜찮음, 디자인이 마음에 듦.
```

## 휴대폰 웹폼 입력 가정

사용자가 모바일에서 다음 값을 입력했다고 가정합니다.

```json
{
  "input_channel": "mobile_web",
  "uploaded_photo": "samples/closet/owned_item_photo.jpg",
  "name": "포켓 게이머 피그먼트 반팔 티셔츠 차콜",
  "brand": "가까이 유니언즈",
  "category_hint": "반팔 티셔츠",
  "measurements": {
    "length": 76,
    "shoulder": 57,
    "chest": 62,
    "sleeve": 25
  },
  "fit_notes": "평소 마음에 들어 자주 입는 기준 티셔츠로 등록",
  "design_notes": "차콜 색상, 가슴 쪽 작은 그래픽, 피그먼트 워싱 느낌",
  "preference_reasons": [
    "핏이 마음에 듦",
    "오래 입어도 괜찮음",
    "디자인이 마음에 듦"
  ],
  "preference_score": 5
}
```

현재 harness에서는 위 입력을 직접 JSON form으로 받는 대신, `owned_item_info.txt`를 모바일 입력 텍스트로 간주해 parsing합니다.

## 실행 명령

```powershell
python backend_harness/run_closet_onboarding.py
```

생성 결과:

```text
harness_outputs\closet_onboarding_db.json
harness_outputs\closet_onboarding_result.json
```

## API 경계

실제 FastAPI 서버를 아직 띄우지 않았기 때문에, `ApiHarness`가 서버 경계를 대체합니다.

```text
Future API:
POST /closet/items

Harness:
ApiHarness.post_closet_items_from_mobile_form(payload)
```

호출 payload:

```json
{
  "user_id": "demo_user_001",
  "input_channel": "mobile_web",
  "text_path": "samples/closet/owned_item_info.txt",
  "uploaded_assets": [
    {
      "path": "samples/closet/owned_item_photo.jpg",
      "asset_type_hint": "item_photo"
    }
  ],
  "fit_notes": "평소 마음에 들어 자주 입는 기준 티셔츠로 등록",
  "design_notes": "차콜 색상, 가슴 쪽 작은 그래픽, 피그먼트 워싱 느낌",
  "finish_notes": "",
  "preference_score": 5
}
```

## Tool Call Log

실행 결과 agent는 다음 tool을 순서대로 호출했습니다.

| Step | Function | Status | 역할 |
|---|---|---|---|
| 1 | `read_mobile_form_text` | completed | UTF-8 텍스트 입력 읽기 |
| 2 | `parse_closet_text` | completed | 제품명, 브랜드, 실측값 추출 |
| 3 | `infer_closet_traits` | completed | 카테고리, 색상, 소매 유형, 핏 추정, 선호 신호 추출 |
| 4 | `lookup_existing_closet_item` | completed | 브랜드/상품명으로 외부 상품 정보 후보 보강 |
| 5 | `classify_closet_asset` | completed | 업로드 이미지를 `item_photo`로 분류 |
| 6 | `extract_ocr_for_asset` | skipped | 텍스트 중심 이미지가 아니므로 OCR 제외 |
| 7 | `db.insert_closet_item` | completed | JSON DB에 기준 옷 저장 |
| 8 | `generate_closet_summary` | completed | LLM API 경계 확인, 현재는 stub |

## 기준 옷 Lookup 결과

현재 상품명/브랜드로 검색했을 때 같은 상품으로 볼 수 있는 후보가 확인되었습니다.

```json
{
  "query": "가까이 유니언즈 포켓 게이머 피그먼트 반팔 티셔츠 차콜",
  "match_count": 3,
  "enrichment": {
    "product_code": "G7770106CH",
    "observed_price_range_krw": {
      "min": 34590,
      "max": 44100
    },
    "gender": "men",
    "manufacturer": "피그먼트",
    "registered_month_candidates": ["2025-09", "2026-02"],
    "lookup_confidence": 0.91
  },
  "missing_after_lookup": ["size_label", "material"]
}
```

출처 후보:

```text
- Danawa mobile product page: code=97756184
- Danawa product page: pcode=106165895
- Coupang product page: product=8892552553
```

이 lookup은 기준 옷 정보를 보강하지만, 사용자가 직접 입력한 실측값을 덮어쓰지 않습니다.

## 정규화 결과

최종 저장된 closet item:

```json
{
  "id": "closet_001",
  "user_id": "demo_user_001",
  "category": "t_shirt",
  "brand": "가까이 유니언즈",
  "name": "포켓 게이머 피그먼트 반팔 티셔츠 차콜",
  "color": "charcoal",
  "sleeve_type": "short_sleeve",
  "fit_guess": "relaxed_or_oversized",
  "preference_reasons": [
    "핏이 마음에 듦",
    "오래 입어도 괜찮음",
    "디자인이 마음에 듦"
  ],
  "preference_signals": ["fit", "durability", "design"],
  "measurements": {
    "length": 76.0,
    "shoulder": 57.0,
    "chest": 62.0,
    "sleeve": 25.0
  },
  "lookup_enrichment": {
    "product_code": "G7770106CH",
    "observed_price_range_krw": {
      "min": 34590,
      "max": 44100
    },
    "lookup_confidence": 0.91
  },
  "material": {},
  "missing_after_lookup": ["size_label", "material"],
  "preference_score": 5,
  "source": {
    "input_channel": "mobile_web",
    "measurement_source": "manual_text",
    "measurement_confidence": 1.0
  }
}
```

## OCR 판단

이번 입력 이미지는 옷 단독 사진입니다. 텍스트가 많은 라벨/사이즈표/소재표 이미지가 아니므로 OCR을 수행하지 않는 것이 맞습니다.

실행 결과:

```json
{
  "status": "skipped",
  "reason": "not_text_heavy_image",
  "provider": "ocr_stub",
  "external_api_called": false
}
```

이 판단은 중요합니다. 모든 이미지에 OCR을 걸면 착장 이미지나 상품 사진의 노이즈가 spec 추출 결과를 오염시킬 수 있습니다.

## LLM 판단

이번 단계에서 LLM은 필수 판단을 하지 않습니다. 실측값, 카테고리 추정, lookup 병합은 deterministic function이 처리합니다.

현재 LLM 역할:

```text
저장된 closet item을 사용자가 이해하기 쉬운 summary로 변환
```

Harness 결과:

```json
{
  "provider": "llm_stub",
  "external_api_called": false
}
```

실제 구현에서는 이 경계를 OpenAI/Claude API의 structured output 또는 일반 chat completion으로 교체할 수 있습니다.

## 관찰한 한계

현재 입력과 lookup만으로는 다음 정보가 부족합니다.

```text
- 사이즈 라벨
- 소재 혼용률
- 실제 착용감 세부 메모
- 봉제/마감 만족 이유
- 세탁 후 변형 여부
```

이 항목은 후보 상품 비교 시 confidence를 낮추고, agent가 검색/상세페이지 분석/OCR/정규화로 먼저 보강해야 하는 기준 정보입니다.
툴 보강 후에도 해결되지 않은 항목만 미해결 근거로 남깁니다.

```text
- size_label
- material
- preference_signals
- durability_after_wash
```

## 설계 판단

이번 dry-run에서 확정한 기준:

```text
1. 사용자가 직접 입력한 실측은 confidence 1.0으로 저장한다.
2. 기준 옷 정보가 부족하면 상품명/브랜드 기반 lookup을 먼저 수행한다.
3. lookup 결과는 출처와 confidence를 저장하고 직접 입력값보다 낮은 우선순위로 둔다.
4. 마음에 드는 이유는 preference_reasons로 저장하고 fit/durability/design 신호로 정규화한다.
5. 옷 단독 사진은 OCR 대상이 아니다.
6. 이미지 분류 결과는 OCR 실행 여부를 결정하는 gate로 사용한다.
7. LLM은 저장/계산 로직이 아니라 summary와 질문 생성에 제한한다.
8. material/price/size_label이 비어 있어도 closet item 등록은 허용한다.
9. 부족 정보는 후보 상품 match 단계에서 risk 또는 question으로 사용한다.
```

## 다음 워크플로우

다음 dry-run은 후보 상품 입력입니다.

```text
후보 상품 상세페이지 캡처 또는 URL
-> 이미지 분류
-> 사이즈표/소재표 OCR
-> product spec 정규화
-> closet_001과 비교
-> 구매 적합도 리포트 생성
```
