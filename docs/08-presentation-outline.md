# Presentation Outline

10장 이내 제출을 기준으로 한 발표/PDF 구성안입니다.

실제 HTML 발표자료는 `submission/fitcheck_agent_presentation.html`에 작성했습니다.

## 1. Title

```text
FitCheck Agent
내 옷장 기준 온라인 의류 구매 검증 AI Agent
```

핵심 문장:

```text
평소 만족하며 입는 옷의 실측, 핏, 소재 데이터를 기준으로 새 상품의 구매 실패 위험을 줄이는 agent입니다.
```

## 2. Problem

온라인 의류 구매 실패 원인을 보여줍니다.

```text
- 같은 L 사이즈라도 브랜드마다 실측이 다름
- 상세 정보가 이미지 안에 묻혀 있음
- 착장 사진과 정보 이미지가 섞여 있음
- 리뷰는 유용하지만 구조화되어 있지 않음
- 사용자는 매번 직접 비교해야 함
```

## 3. User Scenario

```text
1. 내가 자주 입는 셔츠를 등록
2. 구매 후보 상품 URL/캡처 업로드
3. Agent가 상품 정보를 추출
4. 기존 옷과 비교
5. 구매 적합도와 위험 요소 확인
```

## 4. Agent Goal

단순 추천과의 차이를 강조합니다.

```text
일반 추천:
취향에 맞아 보이는 옷을 제안

FitCheck Agent:
내가 이미 만족한 옷의 수치/소재/핏 기준으로 후보 상품을 검증
```

## 5. System Architecture

```text
Web App
-> FastAPI Backend
-> Agent Orchestrator
-> OCR / Image Classifier / Parser / Matcher / Report Generator
-> Storage
```

## 6. Agent Workflow

```text
입력 분석
-> 이미지 분류
-> OCR
-> 상품 spec 정규화
-> 옷장 기준 검색
-> 실측/소재/가격 비교
-> 리스크 계산
-> 리포트 생성
```

## 7. Tool Design

```text
ImageClassifier:
착장컷, 상품컷, 사이즈표, 소재표, 배너 분류

OCRExtractor:
사이즈표/소재표 텍스트 추출

FitMatcher:
기존 옷과 후보 상품 수치 비교

RiskScorer:
구매 위험도 계산

ReportGenerator:
사용자용 판단 리포트 생성
```

## 8. Result Example

```text
구매 적합도: 78점
판정: 주의 필요

근거:
- 기존 선호 셔츠보다 총장이 4cm 짧음
- 어깨/가슴 단면은 유사
- 면 60%, 폴리 40% 혼방으로 촉감 차이 가능
- 소재 정보 OCR confidence 0.76으로 수동 확인 권장

추가 확인:
- 모델 키/몸무게
- 착용 사이즈
```

## 9. Evaluation

```text
테스트 데이터:
- 자주 입는 옷 2개
- 실패했던 옷 1개
- 후보 상품 3개

평가:
- OCR 추출 정확도
- 실측 차이 계산 정확도
- 위험 상품 탐지 여부
- 정보 부족 시 억지 추천 방지
```

## 10. Limits and Expansion

한계를 숨기지 않고 실무적으로 설명합니다.

```text
한계:
- 쇼핑몰별 상세페이지 구조 차이
- OCR 오류
- 착장 사진만으로 실제 핏 판단 어려움
- 소재의 촉감/두께는 불완전 추정

확장:
- 쇼핑몰별 parser adapter
- 리뷰 기반 fit risk 분석
- 개인 옷장 memory 고도화
- MCP tool로 Claude/Codex에서 호출 가능
```

## 발표 핵심 문장

```text
이 프로젝트는 LLM에게 "이 옷 살까요?"라고 묻는 서비스가 아닙니다.
Agent가 상품 정보 수집, 이미지 분류, OCR, 정규화, 비교, 리스크 계산 tool을 순서대로 호출하고,
LLM은 최종 리포트와 비정형 텍스트 정리에 제한적으로 사용합니다.
```

## Demo Backup

```text
실행 가능한 MVP:
- FastAPI 서버
- 브라우저 UI
- 기준 옷 등록 결과
- 키워드 웹 검색 후보 3개
- 후보별 buy/caution/need_more_info 판정
```

검증 명령:

```powershell
python -B backend_harness/verify_web_candidates_match.py
python -m uvicorn backend_harness.web_server:app --host 127.0.0.1 --port 8000
```
