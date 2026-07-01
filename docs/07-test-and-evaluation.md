# Test and Evaluation

## 평가 목표

FitCheck Agent가 "그럴듯한 추천 문장"을 만드는지보다, 사용자의 구매 판단에 필요한 정보를 안정적으로 추출하고 근거 있는 판단을 내리는지 평가합니다.

## 테스트 데이터

### Closet Samples

최소 3개를 준비합니다.

```text
closet_001: 자주 입는 셔츠
closet_002: 자주 입는 니트 또는 맨투맨
closet_003: 실패했던 옷
```

각 sample에는 다음 정보를 포함합니다.

```text
- 사진
- 카테고리
- 브랜드
- 사이즈 라벨
- 실측
- 소재
- 가격
- 만족/불만족 이유
```

### Product Candidate Samples

최소 3개를 준비합니다.

```text
candidate_001: 기존 옷과 잘 맞을 것으로 예상되는 상품
candidate_002: 실측 차이로 실패 위험이 있는 상품
candidate_003: 정보가 부족하거나 OCR이 어려운 상품
```

## 평가 지표

### Extraction Accuracy

OCR/정규화 결과가 실제 상품 정보와 얼마나 일치하는지 확인합니다.

```text
measurement extraction accuracy
material extraction accuracy
price extraction accuracy
category classification accuracy
```

### Match Correctness

기존 옷과 후보 상품의 차이를 올바르게 계산하는지 확인합니다.

```text
shoulder delta
chest delta
length delta
sleeve delta
material overlap
price delta
```

### Risk Detection

실패 가능성이 높은 상품을 위험으로 표시하는지 확인합니다.

```text
too short
too tight
material mismatch
missing size chart
low OCR confidence
review warning keywords
```

### Report Grounding

최종 리포트가 실제 추출 결과에 근거하는지 확인합니다.

```text
리포트 문장이 score/risk/measurement delta와 일치하는가
없는 정보를 꾸며내지 않는가
confidence가 낮을 때 주의 문구를 표시하는가
```

## 테스트 케이스

### TC-001: 정상 사이즈표 이미지

입력:

```text
셔츠 사이즈표 이미지
기준 closet item: shirt L
```

기대:

```text
shoulder/chest/length/sleeve 추출
size score 계산
구매 적합도 리포트 생성
```

### TC-002: 착장 이미지와 사이즈표가 섞인 입력

입력:

```text
모델 착장 이미지 3장
상품 단독 이미지 1장
사이즈표 이미지 1장
```

기대:

```text
착장 이미지는 OCR 제외
사이즈표 이미지에만 OCR 수행
불필요한 이미지로 spec을 오염시키지 않음
```

### TC-003: 핵심 정보 부족

입력:

```text
상품 사진만 있고 사이즈표/소재표 없음
```

기대:

```text
INSUFFICIENT_PRODUCT_INFO 또는 low confidence
억지 추천 금지
검색/fetch/OCR/정규화 보강 후에도 부족한 항목 표시
```

### TC-004: 실패 위험 상품

입력:

```text
기존 선호 셔츠보다 총장 -7cm, 가슴 -5cm
```

기대:

```text
size risk high
recommendation: avoid 또는 caution
구체적인 차이 수치 표시
```

### TC-005: OCR 오류 포함

입력:

```text
화질 낮은 사이즈표
일부 숫자 오인식
```

기대:

```text
confidence 낮음
리포트에 수동 확인 필요 표시
critical field 누락 시 먼저 검색/fetch/OCR/정규화 보강 시도
```

## 수동 평가 양식

```text
상품명:
입력 유형:
정답 실측:
추출 실측:
추출 성공 여부:
기준 옷:
Agent 판단:
사람 판단:
일치 여부:
오류 원인:
개선 아이디어:
```

## 발표에 넣을 수 있는 테스트 결과 예시

```text
총 3개 후보 상품 테스트
- 2개 상품은 사이즈표 OCR 성공
- 1개 상품은 소재 정보 누락으로 보강 필요 항목 표시
- 실패 위험 상품은 총장/가슴 단면 차이로 caution 판정
```

## 한계

- 쇼핑몰별 상세페이지 구조가 다르다.
- 일부 사이즈표는 이미지 품질이 낮아 OCR 오류가 발생한다.
- 착장 사진만으로 실제 핏을 정확히 판단하기 어렵다.
- 소재명은 추출해도 두께감, 촉감, 탄성은 완전히 알기 어렵다.
- 리뷰는 주관적 표현이 많아 보조 신호로만 사용해야 한다.

## 개선 방향

- 쇼핑몰별 parser adapter 추가
- OCR confidence 개선
- 사람이 수정한 spec을 학습 데이터처럼 재사용
- review parser 고도화
- MCP tool로 외부 agent에서 호출 가능하게 확장
