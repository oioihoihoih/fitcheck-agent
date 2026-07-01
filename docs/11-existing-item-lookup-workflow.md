# Existing Item Lookup Workflow

기준 옷 등록 단계에서 사용자가 입력한 정보만으로 충분하지 않을 수 있습니다. 따라서 FitCheck Agent는 기존 옷에 대해서도 먼저 검색 보강을 수행해야 합니다.

## 왜 필요한가

기준 옷은 후보 상품 비교의 기준점입니다. 기준 데이터가 부정확하거나 부족하면 이후 추천/비추천 판단도 흔들립니다.

예를 들어 사용자는 다음을 모를 수 있습니다.

```text
- 정확한 사이즈 라벨
- 소재 혼용률
- 구매 당시 가격
- 상품 코드
- 같은 상품의 다른 색상/시즌 정보
- 브랜드 공식 표기명
```

## 현재 dry-run 결과

사용자 입력:

```text
제품명: 포켓 게이머 피그먼트 반팔 티셔츠 차콜
브랜드: 가까이 유니언즈
실측: 총장 76, 어깨 57, 가슴 62, 소매 25
마음에 드는 이유: 핏이 마음에 듦, 오래 입어도 괜찮음, 디자인이 마음에 듦
```

검색 query:

```text
가까이 유니언즈 포켓 게이머 피그먼트 반팔 티셔츠 차콜
```

검색으로 보강된 후보 정보:

```json
{
  "product_code": "G7770106CH",
  "observed_price_range_krw": {
    "min": 34590,
    "max": 44100
  },
  "gender": "men",
  "manufacturer": "피그먼트",
  "registered_month_candidates": ["2025-09", "2026-02"],
  "lookup_confidence": 0.91
}
```

여전히 부족한 정보:

```text
- size_label
- material
```

## Lookup Agent 흐름

```text
1. 사용자가 기준 옷 정보를 입력한다.
2. Agent가 필수 필드 누락 여부를 확인한다.
3. 브랜드 + 상품명 + 색상 키워드로 search query를 만든다.
4. 여러 provider에서 후보를 수집한다.
5. 후보별 confidence를 계산한다.
6. 일치 가능성이 높은 후보에서 product code, price, category 등을 추출한다.
7. 사용자가 직접 입력한 실측과 충돌하는 정보는 덮어쓰지 않는다.
8. 보강된 정보와 아직 부족한 정보를 함께 저장한다.
```

## Provider 후보

MVP:

```text
- 일반 web search API
- 사용자가 붙여넣은 상품 URL
- 사용자가 업로드한 상품 상세 캡처
```

확장:

```text
- Naver Shopping API
- Danawa adapter
- Coupang page parser
- 브랜드 공식몰 parser
- 이미지 유사 검색
```

## Confidence 기준

```text
브랜드 일치: +0.25
상품명 핵심 토큰 일치: +0.30
색상 일치: +0.15
상품 코드 일치 또는 반복 출현: +0.20
카테고리 일치: +0.10
```

자동 확정 기준:

```text
confidence >= 0.85:
  보강 정보로 저장 가능

0.65 <= confidence < 0.85:
  후보로 저장하고 사용자 확인 필요

confidence < 0.65:
  참고 후보로만 저장
```

## 데이터 저장 정책

```json
{
  "lookup_enrichment": {
    "product_code": "G7770106CH",
    "observed_price_range_krw": {
      "min": 34590,
      "max": 44100
    },
    "lookup_confidence": 0.91,
    "source_urls": []
  },
  "lookup_matches": [],
  "missing_after_lookup": ["size_label", "material"]
}
```

## API 설계

```text
POST /closet/items
POST /closet/items/{id}/lookup
GET /closet/items/{id}
```

등록과 lookup을 분리할 수 있어야 합니다. 사용자가 처음에는 사진과 간단한 메모만 넣고, 나중에 agent가 보강 검색을 수행할 수 있기 때문입니다.

## 중요한 설계 원칙

```text
1. 사용자 직접 입력값을 외부 검색 결과로 덮어쓰지 않는다.
2. 검색 결과에는 반드시 출처 URL과 confidence를 남긴다.
3. 가격은 현재가/관측가일 뿐 구매 당시 가격으로 확정하지 않는다.
4. 소재와 사이즈 라벨처럼 찾지 못한 정보는 추가 질문으로 남긴다.
5. 동일 상품 여부가 애매하면 후보로만 저장한다.
```
