# Candidate Analysis Dry Run

이 문서는 기준 옷 등록 이후, 후보 상품을 분석하고 구매 판단 리포트를 생성하는 end-to-end harness 실행 기록입니다.

## 테스트 목적

검증할 전체 흐름:

```text
기준 옷 등록
-> 후보 상품 fixture 입력
-> 상품 이미지 유형 분류
-> 사이즈표/소재표/리뷰 OCR fixture 처리
-> product spec 정규화
-> 기준 옷 closet_001 선택
-> 실측/소재/가격/신뢰도 비교
-> risk scoring
-> 구매 판단 리포트 생성
```

## 실행 명령

```powershell
python backend_harness/run_end_to_end.py
python backend_harness/verify_end_to_end.py
```

생성 결과:

```text
harness_outputs\end_to_end_db.json
harness_outputs\end_to_end_result.json
```

## 사용한 기준 옷

```text
제품명: 포켓 게이머 피그먼트 반팔 티셔츠 차콜
브랜드: 가까이 유니언즈
실측: 총장 76, 어깨 57, 가슴단면 62, 소매 25
마음에 드는 이유: 핏이 마음에 듦, 오래 입어도 괜찮음, 디자인이 마음에 듦
선호 신호: fit, durability, design
보강 상품 코드 후보: G7770106CH
관측 가격대: 34,590~44,100원
부족 정보: size_label, material
```

## 후보 상품 fixture

```text
samples/product_candidates/candidate_fit_good.json
samples/product_candidates/candidate_risky_short_tight.json
samples/product_candidates/candidate_missing_info.json
```

### candidate_fit_good

기준 옷과 실측이 거의 같은 후보입니다.

```text
총장 75, 어깨 56, 가슴단면 62, 소매 25
소재: 면 100%
가격: 38,900원
리뷰 키워드: 정사이즈, 탄탄
```

### candidate_risky_short_tight

기준 옷보다 작고 짧은 후보입니다.

```text
총장 68, 어깨 51, 가슴단면 55, 소매 22
소재: 폴리에스터 65%, 면 35%
가격: 29,900원
리뷰 키워드: 작아요, 짧아요, 얇아요
```

### candidate_missing_info

상품 사진/착장 사진만 있고 사이즈표와 소재표가 없는 후보입니다.

```text
실측 정보 없음
소재 정보 없음
가격: 42,000원
```

## API 경계

실제 FastAPI 서버 대신 `ApiHarness`가 같은 API 경계를 시뮬레이션합니다.

```text
POST /closet/items
POST /products/analyze-with-images
POST /matches
GET /reports/{id}
```

Harness method:

```text
ApiHarness.post_closet_items_from_mobile_form
ApiHarness.post_products_analyze_from_fixture
ApiHarness.post_matches
ApiHarness.get_report
```

## Tool Call Flow

후보 분석 단계:

| Step | Function | 역할 |
|---|---|---|
| 1 | `read_candidate_fixture` | 후보 상품 fixture 읽기 |
| 2 | `analyze_product_assets` | 상품 이미지 유형 분류 및 OCR fixture 처리 |
| 3 | `normalize_product_spec` | 사이즈/소재/가격/리뷰 정보를 정규화 |
| 4 | `db.insert_product_analysis` | product analysis 저장 |

비교 단계:

| Step | Function | 역할 |
|---|---|---|
| 1 | `choose_closet_targets` | 비교 기준 옷 선택 |
| 2 | `calculate_match` | 실측/소재/가격/신뢰도 점수와 risk 계산 |
| 3 | `generate_match_report_summary` | 사용자용 리포트 summary 생성 |
| 4 | `db.insert_match_report` | match report 저장 |

## 실행 결과

```json
[
  {
    "candidate": "candidate_fit_good",
    "recommendation": "buy",
    "overall": 89,
    "size": 100,
    "material": 60,
    "price": 90,
    "confidence": 91
  },
  {
    "candidate": "candidate_risky_short_tight",
    "recommendation": "avoid",
    "overall": 55,
    "size": 26,
    "material": 60,
    "price": 90,
    "confidence": 89
  },
  {
    "candidate": "candidate_missing_info",
    "recommendation": "need_more_info",
    "overall": 44,
    "size": 28,
    "material": 45,
    "price": 90,
    "confidence": 46
  }
]
```

## 결과 해석

`candidate_fit_good`은 사용자가 중요하게 적은 `핏` 기준에서 실측이 기준 옷과 거의 같아 구매 적합으로 판단했습니다. 다만 기준 옷의 소재 정보가 비어 있어 소재/내구성 판단 점수는 60점으로 제한됩니다.

`candidate_risky_short_tight`는 총장 -8cm, 어깨 -6cm, 가슴단면 -7cm 차이로 size score가 크게 낮아졌고, 리뷰에서도 `작아요`, `짧아요`, `얇아요` 키워드가 발견되어 비추천으로 판단했습니다.

`candidate_missing_info`는 상품 사진과 착장 사진만 있어 사이즈표/소재표 OCR이 불가능합니다. 따라서 억지 추천하지 않고 `need_more_info`로 전환합니다.

## 선호 이유 반영 예시

match report에는 사용자가 적은 마음에 드는 이유가 `preference_alignment`로 들어갑니다.

```json
[
  {
    "signal": "fit",
    "status": "matched",
    "evidence": "실측 비교 점수가 100점으로 기준 옷 핏과 가깝습니다."
  },
  {
    "signal": "durability",
    "status": "needs_baseline_material",
    "evidence": "기준 옷 소재 정보가 없어 오래 입어도 괜찮은 이유를 소재 기준으로 확정하기 어렵습니다."
  },
  {
    "signal": "design",
    "status": "partial_match",
    "evidence": "상품명에서 공통 디자인 키워드가 발견되었습니다: 차콜, 피그먼트, 포켓."
  }
]
```

## 검증 기준

`verify_end_to_end.py`는 다음 expectation을 확인합니다.

```json
{
  "candidate_fit_good": "buy",
  "candidate_risky_short_tight": "avoid",
  "candidate_missing_info": "need_more_info"
}
```

검증 결과:

```text
End-to-end verification passed.
```

## 설계 판단

```text
1. 상품 사진/착장 사진에는 OCR을 걸지 않는다.
2. 사이즈표/소재표/리뷰 텍스트에만 OCR boundary를 적용한다.
3. 실측 비교는 deterministic scoring으로 처리한다.
4. LLM은 최종 summary 문장 생성 boundary로만 사용한다.
5. 실측 정보가 없으면 추천하지 않고 추가 정보 요청으로 전환한다.
6. 기준 옷 소재가 부족하면 후보 상품 소재가 있어도 소재 점수를 제한한다.
7. 기준 옷의 마음에 드는 이유는 fit/durability/design 신호로 저장해 비교 근거에 반영한다.
```

## 다음 구현 단계

이 dry-run은 실제 웹앱 구현 전에 agent workflow가 끝까지 동작함을 확인한 것입니다. 다음 단계에서는 fixture를 실제 입력으로 교체합니다.

```text
Next.js mobile UI
-> FastAPI route
-> SQLite DB
-> 실제 이미지 upload
-> PaddleOCR/Tesseract 또는 cloud OCR 연결
-> LLM API 연결
```
