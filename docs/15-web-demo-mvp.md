# Web Demo MVP Archive

이전 Web Demo MVP는 deterministic cached 후보를 화면에서 재랭킹하는 데모였다.

현재 웹 UI는 GPT-style chat surface로 바뀌었고 `/api/agent/runs` production agent route만 사용한다. 예전 `/api/run/web-candidates`와 `/api/results/web-candidates` route는 fixed 후보 데모 surface였기 때문에 `disabled` 응답만 반환한다.

