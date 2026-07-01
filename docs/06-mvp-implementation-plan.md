# MVP Implementation Plan

## 목표

실무형 구조를 유지하면서도 과제 제출 전까지 완성 가능한 MVP를 만든다. 핵심은 멋진 UI보다 agent pipeline이 실제로 동작하고, 결과를 검증할 수 있는 것이다.

## MVP 기능

```text
1. 기존 옷 등록
2. 후보 상품 이미지 업로드
3. 이미지 유형 분류
4. OCR 텍스트 추출
5. 상품 spec 정규화
6. 기존 옷과 비교
7. 구매 판단 리포트 출력
```

## 1차 구현 범위

### Frontend

- Next.js app
- closet item form
- product image upload form
- analysis status panel
- match report page

### Backend

- FastAPI app
- SQLite DB
- local file upload
- synchronous or background analysis job
- OpenAPI docs

### Core Agent

- deterministic pipeline
- tool별 Python module 분리
- LLM report generation은 optional fallback 가능

## 권장 디렉터리 구조

```text
fitcheck-agent/
  frontend/
    app/
    components/
    lib/
  backend/
    app/
      main.py
      api/
      core/
      db/
      models/
      schemas/
      services/
      tools/
        image_classifier.py
        ocr_extractor.py
        spec_normalizer.py
        closet_retriever.py
        fit_matcher.py
        risk_scorer.py
        report_generator.py
    tests/
  docs/
  samples/
    closet/
    products/
```

## 구현 단계

### Phase 1: Core Data and API

- `closet_items` CRUD
- product analysis job 생성
- image upload 저장
- SQLite 연결
- 기본 API 테스트

완료 기준:

- 기존 옷을 등록하고 조회할 수 있다.
- 후보 상품 이미지를 업로드할 수 있다.

### Phase 2: Extraction Pipeline

- 이미지 유형 분류 rule-based baseline
- OCR extractor 연결
- OCR raw text 저장
- spec normalizer 구현

완료 기준:

- 사이즈표 이미지에서 shoulder/chest/length/sleeve를 추출한다.
- 소재표 이미지에서 material composition을 추출한다.

### Phase 3: Matching Pipeline

- closet item retrieval
- measurement delta 계산
- material similarity 계산
- risk scoring
- recommendation 생성

완료 기준:

- 후보 상품과 기준 옷의 차이를 JSON으로 반환한다.
- 위험 요소가 리스트로 출력된다.

### Phase 4: Report and UI

- report generator
- 결과 페이지
- 신뢰도, 보강 후에도 남은 누락 정보 표시

완료 기준:

- 사용자가 브라우저에서 최종 구매 판단 리포트를 볼 수 있다.

### Phase 5: Evaluation and Presentation

- 테스트 샘플 3개 준비
- 성공/실패 케이스 비교
- 한계와 개선 방향 정리
- PPT용 아키텍처 이미지/흐름도 정리

완료 기준:

- 과제 제출용 PDF/PPT에 설계, 구현, 테스트 결과가 들어간다.

## 우선순위

Must:

- 기존 옷 등록
- 후보 상품 이미지 업로드
- OCR 추출
- 수치 비교
- 리포트 생성

Should:

- 이미지 분류
- URL 입력
- confidence 표시
- 부족 정보 질문

Could:

- 리뷰 파싱
- 여러 후보 상품 ranking
- MCP tool 노출
- Claude/Codex 연결

Won't for MVP:

- 결제 자동화
- 전체 쇼핑몰 크롤러
- 로그인/회원가입
- 배포 운영

## AI 코딩 도구 활용 전략

AI 코딩 도구는 다음에 활용한다.

- 반복적인 CRUD/API boilerplate 생성
- UI form/component 초안 생성
- OCR parsing helper 구현
- 테스트 샘플 생성
- 리팩터링 보조

직접 통제해야 하는 부분:

- agent workflow 설계
- scoring 기준
- 실패 케이스 판단
- LLM 사용 경계
- 테스트 결과 해석
