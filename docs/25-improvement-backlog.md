# FitCheck 우선순위 개선 백로그

**작성일: 2026-07-01**

## ✅ 진행 상황 (2026-07-01 자율 개선)

아래 항목이 구현·라이브 검증·커밋 완료됐다. 각 항목은 실제 서버/브라우저 검증을 거쳤고 백엔드 verify 스위트 회귀가 없다.

| 항목 | 커밋 | 비고 |
|------|------|------|
| P0-1 waiting_for_user 질문 UI + 같은 run 답장 | `7d00819` | 데드엔드 해소 |
| P0-2 실패 경로 모델 답변·친화적 에러 | `bc266dd` + `7d00819` | degraded partial_answer + 한국어 user_message |
| P0-3 옷장 HTTP API (list/create/patch/delete, 세션 스코프) | `61d7dd0` | 북극성 1 토대 |
| P0-4 실행 이력 드로어 + URL runId 복원 | `f56ced0` | listRuns 투영 |
| P0-5 스트리밍 도구 카드 | (기존 `AgentActivity`) | 이미 실현 |
| **P1-1 옷장 관리 페이지** (CRUD + 기준 + 이 옷으로 찾기) | `64fd63f` | **북극성 1** |
| **P1-2 후보 저장 favorites** (테이블+API+저장버튼+뷰) | `2126886` | **북극성 2** |
| **P1-3 클레임별 출처 영수증 + 근거 검증 배지** | `662a59e` | **북극성 3** |
| P1-4 이미지 업로드 컴포저 연결 | `6652c4a` | 시각 비교 스토리 |
| P1-5 접근성 보정 (aria-expanded/aria-live/roles) | `a2a69e7` | 순위 16 |
| P1-6 provider/mode 정직 태그 (라이브/캐시/픽스처) | `(P1-6)` | 순위 19 |
| P1-7 SSE 폴백 폴링 + 로딩 스켈레톤 | `(P1-7)` | 순위 17 |
| P1-8 근거 커버리지 미터 + strong-match 가드 | `(P1-8)` | 순위 14 |

**북극성 3개 모두 완성.** 통합 검증: 백엔드 verify(production_runtime/dev_harness) 통과, 전체 UI 스모크(탐색/옷장/저장함/이력/provider태그/이미지첨부) 콘솔 에러 0. 남은 백로그(순위 11~13, 15, 18, 20~32)는 아래 웨이브 표를 따라 계속 진행한다.

## 판단 프레임워크 (우선순위 루브릭)

이 백로그는 외부 리서치 패턴(Cursor 1.4 / Claude Code / Codex / Perplexity / Baymard / True Fit / 디지털 옷장 앱)과 현재 시스템 감사 결과를 병합해 아래 5개 기준으로 순위를 매긴다. 팀은 새 항목이 생길 때마다 동일한 루브릭을 다시 적용한다.

| 기준 | 의미 | 가중 |
|---|---|---|
| **사용자 영향(User Impact)** | 소비자가 실제로 막히거나(dead-end), 제품의 핵심 가치를 아예 만나지 못하는가. `waiting_for_user` 무표시, 옷장 UI 부재처럼 "기능이 존재하지만 도달 불가"는 최상위. | 높음 |
| **제품 전제 정합성(Closet-Premise Fit)** | "내가 좋아하는 옷과 온라인 후보를 비교한다"는 핵심 전제를 직접 실현하는가. 옷장 메모리·기준 아이템(baseline)·저장 후보가 여기에 해당. | 높음 |
| **노력(Effort)** | S/M/L. 이미 저장/스트림/트레이스가 존재하는 데이터를 UI로 투영(projection)하는 작업은 노력 대비 레버리지가 크다. | 중간 |
| **리스크(Risk)** | 에이전트 루프·그라운딩 규칙·세션 정합성을 깨뜨릴 위험. 백엔드 실패 경로 수정은 저리스크지만 회귀 테스트 필수. | 중간 |
| **에이전트 UX 격차(Agent-UX Gap)** | Claude/Codex급 에이전트 대비 격차를 얼마나 좁히는가: 실시간 작업 가시성, 저비용 조종(steer/stop), 클레임별 출처 영수증. | 높음 |

**우선순위 원칙:** (사용자 영향 × 제품 전제 정합성 × 에이전트 UX 격차)를 노력·리스크로 나눈 레버리지가 높은 순. "존재하지만 도달 불가능한 자산을 사용자에게 노출"하는 항목(옷장 API, 실행 이력 리스트, 실패 경로의 모델 답변)을 최우선으로 둔다 — 신규 역량 개발보다 이미 저장된 데이터를 드러내는 편이 레버리지가 크기 때문이다.

---

## 우선순위 백로그

리서치 패턴과 감사 항목이 중복되는 경우 하나로 병합했다(예: 리서치 "grounded candidate cards + inline citations"와 감사 "실패 경로의 모델 답변/에러 메시지"는 별개; 리서치 "saved items"와 감사 "favorites 테이블 부재"는 병합).

| 순위 | 항목 | 분류 | 영향 | 노력 | 판단근거 |
|---|---|---|---|---|---|
| 1 | `waiting_for_user` 질문 UI 렌더링 + 같은 run에 답장 | UI | 높음 | M | 제품 필수 요구(누락 정보 질문)가 화면에 안 나와 대화가 조용히 죽는다. `missing_info`는 API에 있으나 미사용. 데드엔드 해소, 저리스크. |
| 2 | 실패/터미널 경로에서 모델 답변·친화적 에러 노출 | 에이전트 | 높음 | M | 유효한 `final_text`가 `FINAL_REPORT_TOOL_REQUIRED`/`MAX_TURNS`에서 버려지고 사용자에겐 목표문만 에코됨. 원시 에러코드가 그대로 전달됨. 이미 계산된 결과를 라벨링해 노출. |
| 3 | 옷장 HTTP API + 고유 item_id + delete/patch | 백엔드/저장구조 | 높음 | M | `closet_items` 테이블·스토어 메서드는 있으나 라우트가 없어 도달 불가. 게다가 하드코딩 `current_liked_item`으로 세션당 1개만 저장 가능. 제품 전제의 저장 계층. |
| 4 | 옷장 관리 페이지(목록/추가/편집/삭제 + 기준 선택) | 신규페이지 | 높음 | L | 핵심 전제("내 옷과 비교")의 유일한 사용자 표면인데 전무. 3번 API 위에 얹힘. True Fit식 "내 기준 옷 대비" 비교의 전제. |
| 5 | 후보 저장(favorites) 테이블 + API + 저장 버튼/뷰 | 저장구조/UI | 높음 | L | 저장/비교가 쇼핑-비교 에이전트의 페이오프인데 shortlist가 run 트레이스에만 존재. NN/g "save for later" 분리, 세션 넘어 저장. |
| 6 | 실행 이력 사이드바(listRuns) + URL runId 복원 | UI | 높음 | M | `GET /api/agent/runs`·`listRuns()`는 있으나 프론트가 호출 안 함. 새로고침/"새 대화"에 결과 소실. Codex "run 커맨드센터" 패턴, 데이터 이미 존재. |
| 7 | 스트리밍 도구 카드(툴콜 상태 행 + 확장) | UI | 높음 | M | 8~12 툴콜을 스피너 하나로만 보여주면 멈춘 듯 보임. `/events` SSE 트레이스를 접힌 카드로 투영. 지각 속도·신뢰의 최대 동인, 백엔드 무변경. |
| 8 | 이미지 업로드 컴포저 연결(파일/카메라/드래그) | UI | 높음 | M | `uploadImage`/`fileToDataUrl`이 API엔 있으나 UI 미연결. 시각 비교 스토리 전체가 도달 불가. attachments 필드도 미채움. |
| 9 | 클레임별 인라인 출처 영수증(후보/리포트) | UI | 높음 | M | 프로젝트 하드 가드레일(모든 수치·소재·가격·URL은 관찰 기반)을 UI로 가시화. observation_ref/citation_id를 배지로. Perplexity 레퍼런스. |
| 10 | 정직한 미확인/no-result 상태(제외·시도 감사 노출) | 에이전트/UI | 중간 | M | no_result 리포트가 match_results·제외 후보를 버림. 시도한 쿼리·실패 이유를 보여주는 정직한 비-답변이 가짜 shortlist보다 신뢰↑. |
| 11 | steer-without-stopping(메시지 큐 주입) + Stop | 에이전트/UI | 중간 | M | 발견은 반복적: 사용자가 실행 중 제약을 추가. 툴 경계에서 주입, 관찰 보존. Cursor 1.4 큐 vs 인터럽트. |
| 12 | Stop(취소) + 부분결과 그라운딩 리포트 + resume | 에이전트/저장구조 | 중간 | M | 긴 루프의 하드 오프스위치. 중단 시 기존 관찰로 그라운딩 리포트 생성(report_grounding 재사용). status='stopped' 저장. |
| 13 | 라이브 계획/체크리스트(핀 고정 아티팩트) | UI/에이전트 | 중간 | M | 실행 초입에 순서 계획을 상태에 저장·스트림. 40초 실행을 "무한"이 아닌 "경계 있는" 감독으로 전환. Claude Code TodoWrite. |
| 14 | 매치 신뢰도 = 근거 커버리지 미터(필드 충족률) | UI/에이전트 | 중간 | M | 원시 모델 % 대신 카테고리 필수필드 present/required로 신뢰도 산출. 누락 시 "strong match" 배지 차단 — 가드레일 강제. |
| 15 | "차이만 보기" 토글 + 속성 그룹 비교뷰(≤5) | UI | 중간 | M | Baymard: 동일 속성 숨김이 우수. Fit/Material/Size/Silhouette/Design 축으로 그룹핑, 기준 대비 근접 값 강조. |
| 16 | 접근성 보정(aria-expanded/aria-live/라벨) | UI | 중간 | S | 토글에 aria-expanded 없음, 스트리밍 텍스트에 aria-live 없음, send 버튼 라벨 정적. 저노력·명확한 사용자 영향. |
| 17 | 에러/로딩 상태 강화(스켈레톤/재연결/재시도/no-result) | UI | 중간 | M | 첫 SSE 전 스켈레톤 無, SSE error가 배너 없이 connected=false만, 실패에 재시도 없음. |
| 18 | closet_item 회귀 방지(미확인 web_lookup baseline 유지) | 에이전트 | 중간 | M | 미확인 web_lookup baseline이 다음 턴에 조용히 pop → baseline-unknown 회귀. 거부 시에만 삭제. |
| 19 | provider/mode 출처 태그 + degraded 배너 | UI | 중간 | S | 하드 규칙: 캐시/픽스처를 라이브로 표기 금지. observation provider 라벨을 색상 태그로, run 상태를 상단 배너로. 프론트 매핑. |
| 20 | 옷장 2계층 스키마 + 브랜드-스코프 사이즈 프로필 | 저장구조/에이전트 | 중간 | M | 구조화 필드(category/colors/brand/size/fabric/season) + 자유 tags/notes 계층. 사이즈는 (category,brand) 스코프 — 브랜드 간 비교 불가 문제. |
| 21 | 근거코드 기반 매치(silhouette/fit/fabric 통제어휘) | 에이전트 | 중간 | M | score_candidate_match가 차원별 breakdown + reason string 방출("matched: wide-leg, cotton twill; unresolved: rise"). 리포트가 인용. |
| 22 | "내 좋아하는 [아이템] 대비" fit 라인(기준 상대 표현) | UI/에이전트 | 중간 | M | 추상 S/M/L 대신 사용자 기준 옷 대비 표현. FitCheck 차별화의 가시화. 측정 없으면 "fit 미확인 - OCR 필요". |
| 23 | 미확인 필드 원탭 보강 액션(fetch/OCR 트리거) | UI/에이전트 | 중간 | M | 누락 필드를 "Not found" 셀 + 원탭으로 백엔드 툴 트리거. "OCR로 확인해봐"를 버튼화. enrichment-before-waiting 가시화. |
| 24 | "이거랑 비슷한 거 더" + 매칭 차원 라벨 | UI/에이전트 | 중간 | M | search_web_candidates 재호출 + "matched: silhouette+material" 라벨. 라벨은 관찰 기반(장식 아님). |
| 25 | AI 제안 + 사용자 확인 옷장 추가 플로우 | 에이전트/UI | 중간 | M | 사진/URL → classify+extract가 속성 제안 → 사용자 확인/편집 → 저장. 미확인 추측이 조용히 "선호 fit"이 되지 않게. |
| 26 | 옷장 URL/영수증 인제스천(권위 있는 메타데이터) | 백엔드/에이전트 | 중간 | M | 소유 아이템 URL을 fetch+normalize해 권위 있는 brand/material/size 확보. 신뢰 위계 spec>OCR>vision. |
| 27 | run별 비용/사용량 패널(토큰/툴콜/캐시 히트) | UI/백엔드 | 낮음 | M | run.llm.external_api_called·툴 트레이스 이미 존재. 스트리밍 델타 이중집계 주의. 실제 작업량 가시화. |
| 28 | 소비자 필터로 교체(예산/사이즈/기준 아이템) + 개발 노브 숨김 | UI | 낮음 | M | maxTurns/toolBudget 등 엔지니어 노브가 사용자에 노출, 정작 예산·사이즈·기준 선택은 부재. |
| 29 | 첫 실행 온보딩(옷장/사진 안내 + provider 라벨) | UI | 낮음 | M | 예시 3개 외 온보딩 無. 필요한 큐·데모/픽스처 backing 설명 부재. |
| 30 | 계정/사용자 모델 + 세션→유저 바인딩 | 저장구조 | 중간 | L | 익명 쿠키뿐이라 쿠키 삭제·기기 변경 시 옷장/이력/favorites 소실. 최소한 세션 export/import. |
| 31 | 원샷 아웃핏 사진 분할 온보딩 + 선호 퀴즈 | 에이전트/UI | 낮음 | L | 아웃핏 1장을 여러 아이템으로 분할 + 선언 선호 수집. 콜드스타트 해소. |
| 32 | JSON 스토어 옷장 패리티 or SQLite 강제 | 저장구조 | 낮음 | S | JsonAgentRunStore가 로드 시 closet_items 미초기화 → 비-SQLite 배포서 소실. 프로덕션 SQLite 강제 명시 + capability 체크. |

---

## 웨이브 그룹핑

### 즉시 실행 (P0) — 데드엔드 해소 + 존재하지만 도달 불가한 핵심 자산 노출
- **P0-1.** `waiting_for_user` 질문 UI + 같은 run 답장 (순위 1)
- **P0-2.** 실패/터미널 경로 모델 답변·친화적 에러 노출 (순위 2)
- **P0-3.** 옷장 HTTP API + 고유 item_id + delete/patch (순위 3)
- **P0-4.** 실행 이력 사이드바 + URL runId 복원 (순위 6)
- **P0-5.** 스트리밍 도구 카드 (순위 7)

근거: 모두 (a) 소비자가 실제로 막히거나, (b) 이미 백엔드에 존재하는 데이터/기능이 사용자에게 도달하지 못하는 경우이며, 대부분 M 이하 노력·저리스크다. 신규 역량이 아니라 "투영"이다.

### 다음 (P1) — 제품 전제 실현 + 신뢰 코어
- 옷장 관리 페이지 (순위 4)
- 후보 저장(favorites) 테이블+API+UI (순위 5)
- 이미지 업로드 컴포저 연결 (순위 8)
- 클레임별 인라인 출처 영수증 (순위 9)
- 정직한 no-result 상태 (순위 10)
- steer/stop + 부분결과 리포트 (순위 11, 12)
- 라이브 계획 체크리스트 (순위 13)
- 근거 커버리지 신뢰도 미터 (순위 14)
- "차이만 보기" 비교뷰 (순위 15)
- 접근성/에러상태 보정 (순위 16, 17)
- baseline 회귀 방지 (순위 18)
- provider/mode 태그 (순위 19)

### 탐색 (P2) — 데이터 모델 심화 + 온보딩 + 계정
- 옷장 2계층 스키마 + 브랜드-스코프 사이즈 (순위 20)
- 근거코드 매치 + 기준-상대 fit 표현 (순위 21, 22)
- 원탭 보강 / "비슷한 거 더" (순위 23, 24)
- AI 제안+확인 옷장 추가 / URL·영수증 인제스천 (순위 25, 26)
- 비용 패널 / 소비자 필터 / 온보딩 (순위 27, 28, 29)
- 계정 모델 / 아웃핏 분할 온보딩 / JSON 패리티 (순위 30, 31, 32)

---

## P0 구현 스케치

### P0-1. `waiting_for_user` 질문 렌더링 + 같은 run 답장
- **건드릴 파일:** `webapp-next/src/App.tsx`(finalAgentText L326-333, 터미널 이펙트 L107-112, 컴포저 L291-313), `webapp-next/src/api.ts`(TERMINAL_STATUSES L213-220, missing_info L74, replyToRun).
- **변경:** `finalAgentText()`에 `waiting_for_user` 분기를 추가해 `state.missing_info` 또는 `final_report`에서 질문 텍스트를 렌더. 터미널 이펙트에서 `waiting_for_user`일 때 컴포저를 활성 유지하고 "답변 대기 중" 프롬프트 표시. 전송 시 `replyToRun(runId, ...)`로 같은 run 이어감(새 run 생성 금지).
- **신규:** 없음(엔드포인트 존재). `waiting_for_user`를 완전 터미널 취급에서 "답장 가능 터미널"로 분리.
- **검증:** 미확인 baseline을 유발하는 목표로 run 생성 → 질문이 에이전트 턴으로 보이고 컴포저 활성 → 답장이 같은 run_id를 잇고 상태가 진행됨 확인.

### P0-2. 실패/터미널 경로에서 모델 답변·친화적 에러 노출
- **건드릴 파일:** `backend_harness/app/agent_runtime.py`(FINAL_REPORT_TOOL_REQUIRED L1321-1336, MAX_AGENT_TURNS L2463-2477, rejected_final_text L2656/2675, GROUNDING_FAILED L1497-1509), `backend_harness/app/routes/agent.py`(summary fallback L528/546, error 반환 L577), `webapp-next/src/App.tsx`(실패 텍스트 L331).
- **변경:** 각 터미널-실패 분기에서 거부된 `final_text`를 `run['final_report'] = {summary: final_text, grounded: False, degraded: True, reason: 'report_tool_not_run'}`(또는 `run['partial_answer']`)로 저장. 에러코드→한국어 사용자 메시지 매핑 프레젠테이션 레이어 추가, 원시 code/grounding은 `error.detail` 하위로. `run['error']['user_message']` 각 실패 지점 채움. 프론트는 degraded 라벨 붙여 표시(라이브 아닌 partial임을 명시).
- **신규:** `error_messages` 매핑 유틸(코드→ko 문구). schemas에 `partial_answer`/`degraded` 필드.
- **검증:** 유효 final_text가 있는데 report 툴 미완료인 케이스 → 목표 에코가 아닌 모델 답변이 degraded 라벨과 함께 노출. 그라운딩 실패 시 사용자에겐 짧은 한국어, detail에 원시 dict.

### P0-3. 옷장 HTTP API + 고유 item_id + delete/patch
- **건드릴 파일:** 신규 `backend_harness/app/routes/closet.py`(prefix `/api/closet`), `backend_harness/app/schemas.py`(ClosetItem req/resp 모델), `backend_harness/app/agent_run_store.py`(delete_closet_item/update_closet_item를 Json·Sqlite 양쪽에 추가; L459-509 참고), `backend_harness/app/tool_registry.py`(promote L2143 하드코딩 `current_liked_item` 제거 → 기본 uuid item_id L461 사용).
- **신규 엔드포인트:** `GET /api/closet`(세션 list_closet_items), `POST /api/closet`(upsert, 고유 item_id 생성), `PATCH /api/closet/{item_id}`, `DELETE /api/closet/{item_id}`. 세션은 `get_or_create_session_id`(agent.py:48 패턴). WHERE에 session_id 스코프 강제.
- **신규 테이블:** 없음(closet_items 존재). item_id를 uuid로 바꿔 다중 아이템 가능하게.
- **검증:** 한 세션에서 POST 2회 → 2개 행 존재(덮어쓰기 없음). PATCH/DELETE가 세션 스코프 안에서만 동작. 타 세션 아이템 접근 불가.

### P0-4. 실행 이력 사이드바 + URL runId 복원
- **건드릴 파일:** `webapp-next/src/App.tsx`(newSession L191-196, turns 상태 L44), `webapp-next/src/api.ts`(listRuns L154-156 사용), `webapp-next/src/hooks/useRunStream.ts`(runId 소스 L20-23).
- **변경:** 마운트 시 `listRuns()` 호출해 세션 실행 이력 사이드바 렌더(goal/status/timestamp/요약). 각 항목 클릭 시 해당 run의 `/events` 리플레이 + 최종 리포트 로드. 현재 runId를 URL(query/path)에 반영, 로드 시 URL에서 rehydrate. "새 대화"는 폐기가 아니라 아카이브(리스트에 남김).
- **신규:** 없음(엔드포인트 존재). 프론트 리스트+라우팅 위주.
- **검증:** run 생성 → 새로고침 시 URL runId로 복원. "새 대화" 후에도 이전 run이 사이드바에 남고 클릭 시 리포트 재현.

### P0-5. 스트리밍 도구 카드(툴콜 상태 행 + 확장)
- **건드릴 파일:** `webapp-next/src/components/AgentActivity.tsx`(현재 옷장은 트레이스 라벨만 L97-98), `webapp-next/src/hooks/useRunStream.ts`(SSE 파싱), 신규 `webapp-next/src/components/ToolCard.tsx`.
- **변경:** `/api/agent/runs/{run_id}/events` SSE의 각 트레이스 엔트리(model-selected tool → backend execution → observation)를 call id 키의 `<ToolCard>`로 매핑. 기본 접힘 한 줄 상태 행(스피너→체크, 예 `search_web_candidates: "cropped wool blazer" 검색 중`), 클릭 시 args+persisted observation 확장. 장기 실행 중 완료 카드 자동 접힘.
- **신규:** 프론트 컴포넌트만. 백엔드 로직 무변경(기존 trace/observation의 투영). 원한다면 `tool_call_started`/`tool_call_finished` SSE 이벤트 타입만 추가.
- **검증:** 8~12 툴콜 run에서 각 툴이 개별 행으로 진행 표시 → 확장 시 저장된 observation 노출. 스피너 하나만 보이던 "멈춘 듯한" 화면이 가시적 진행으로 전환.

---

## 북극성 (Working Demo → 사람들이 쓰는 제품)

감사 결과가 가리키는 방향: FitCheck의 핵심 전제("내가 좋아하는 옷과 온라인 후보 비교")를 실현하는 저장·비교 계층이 백엔드엔 부분적으로 있으나 사용자에게 거의 도달하지 못한다. 데모를 제품으로 바꾸는 2~3가지는:

1. **옷장 관리 경험(생성/편집/삭제 + 기준 아이템 선택).** 지금은 옷장이 트레이스 라벨로만 존재하고 HTTP 표면도, UI 페이지도 없다(세션당 아이템 1개 제한까지). 이걸 실물화해야 "매번 자유텍스트로 내 옷을 재설명"하는 대신 저장된 기준 옷 대비 비교가 가능해지고, 제품 전제가 장식이 아닌 실체가 된다. (P0-3 → P1 옷장 페이지)
2. **세션을 넘어 저장·비교되는 후보(favorites).** shortlist가 run 트레이스에만 살아 사라진다. 저장 테이블+버튼+비교뷰가 있어야 쇼핑-비교 에이전트의 페이오프(여러 턴/세션에 걸쳐 후보를 모으고 내 옷과 나란히 비교)가 성립한다. (순위 5)
3. **모든 클레임의 가시적 출처 영수증 + 정직한 미확인 상태.** 프로젝트의 하드 가드레일(수치·소재·가격·URL은 반드시 관찰 기반)을 UI로 드러내고, 근거 없는 필드는 "not found"로, 실패는 시도한 쿼리·이유와 함께 정직하게 보여준다. 이것이 AI 쇼핑의 신뢰 장벽을 넘는 차별점이다. (순위 9, 10, 14)

이 세 가지가 없으면 FitCheck는 "동작하는 데모"에 머물고, 갖추면 사용자가 자신의 옷장을 쌓아 올리며 반복해서 돌아오는 지속형 어시스턴트가 된다.
