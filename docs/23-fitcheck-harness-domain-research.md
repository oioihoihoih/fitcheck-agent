# FitCheck Harness Domain Research

This note records research-backed harness rules for FitCheck's product agent and verification harness.

## Sources Checked

- OpenAI Function Calling guide: function calling connects models to external data and systems.
  Source: https://developers.openai.com/api/docs/guides/function-calling
- OpenAI Structured Outputs guide: schema-constrained outputs reduce malformed tool/report payloads,
  but FitCheck still needs source-specific grounding checks for product facts.
  Source: https://platform.openai.com/docs/guides/structured-outputs
- OpenAI Agents SDK tracing guide: agent runs can be inspected as trace/span style execution
  records.
  Source: https://openai.github.io/openai-agents-python/tracing/
- OpenAI Graders guide: evals can use deterministic and model-based graders, but factual product
  claims still need source-specific checks.
  Source: https://developers.openai.com/api/docs/guides/graders
- OpenAI Cookbook, "Build an Agent Improvement Loop with Traces, Evals, and Codex": real traces,
  feedback, evals, and Codex-driven repairs form the improvement loop.
  Source: https://developers.openai.com/cookbook/examples/agents_sdk/agent_improvement_loop
- OpenAI Codex SDK manual: Codex SDK/MCP are useful development orchestration surfaces, but FitCheck
  product behavior should still be evaluated from backend-owned product traces and observations.
  Source: https://developers.openai.com/codex/sdk
- Anthropic, "Building effective agents": successful agentic systems use simple, composable patterns;
  workflows follow predefined paths, while agents dynamically direct tool use.
  Source: https://www.anthropic.com/engineering/building-effective-agents
- ISO 8559-1:2017: clothing size designation depends on anthropometric definitions for body measurement.
  Source: https://www.iso.org/standard/61686.html
- Coresight Research apparel returns survey: size/fit was the top cited reason for online apparel
  returns, at 53% of respondents.
  Source: https://coresight.com/research/the-true-cost-of-apparel-returns-alarming-return-rates-require-loss-minimization-solutions/
- Baymard apparel sizing research: apparel product pages need findable size guides and size charts
  with measurements that match the product type.
  Sources: https://baymard.com/blog/apparel-size-information and
  https://baymard.com/blog/apparel-ecommerce-ux-research-launch
- Nielsen Norman Group size guide research: international apparel shoppers need size guides and
  product measurements that are localized and understandable.
  Source: https://www.nngroup.com/articles/sizes-measurements-ecommerce/
- Fit Analytics describes size/fit recommendation as relying on garment, style, fit, purchase,
  return, and preference data rather than a single generic size label.
  Source: https://fitanalytics.com/resources/retail-problems-size-inconsistency

## FitCheck Harness Implications

- Size labels such as S/M/L/free/one-size are weak search hints, not comparable fit evidence.
- When a product page contains a size chart with multiple rows, each row is separate evidence.
  A candidate must be compared from the selected size row, not from the first or arbitrary
  measurements on the page.
- Close-fit claims require garment measurement evidence from product tables, size-chart OCR, fetched
  page text, or user-supplied measurements.
- Affirmative fit claims such as similar fit, close measurements, true-to-size, or fits well must
  be separately grounded. A size label or a generic size score is not enough.
- Reports may say fit evidence is missing or uncertain; that uncertainty statement should not be
  treated as a hallucinated affirmative fit claim.
- Body/model measurements, rankings, pack counts, and size-guide instruction numbers must not become
  garment measurement facts.
- Score statements such as overall, size, material, price, and confidence points must cite the
  matching `score.<field>` observation, not unrelated counts, timeouts, OCR text lengths, or product
  name numbers.
- Evidence coverage statements are product claims too. If a report says measurements, material, or
  price evidence was confirmed, the claim must be grounded by `evidence_coverage.observed_fields`
  or an equivalent normalized spec field. Missing-evidence wording is allowed only as uncertainty,
  not as a positive confirmation.
- Search queries, model-selected tool arguments, and planning context are not product evidence.
  Material claims such as cotton, wool, nylon, or polyester must come from observed product text,
  OCR/fetch output, normalized specs, or user-provided baseline facts, not from the query used to
  find candidates.
- Candidate-audit product names may contain model numbers, pack counts, or color counts. These
  should not become measurement, price, or score facts unless the report separately states them as
  facts.
- The harness should verify behavior from traces and observations, not from model prose.
- Prompt and skill instructions must preserve the boundary: the LLM chooses tools, but backend-owned
  tools execute search/fetch/OCR/normalize/score/report and persist evidence.
- Repeated failures should become verifier or eval cases. Passing only cached/demo flows is not proof
  that live product behavior is safe.

## Prompt And Context Audit Findings

- Runtime instructions include the correct no-hallucination clothing policy: preserve unfamiliar
  target words, treat size labels as weak hints, require garment measurement evidence, and separate
  body/model measurements from garment measurements.
- Initial model context separates `goal`, raw `input`, skill catalog metadata, matched recipes, and
  required behavior. This matches the Codex-style progressive disclosure pattern and avoids dumping
  all skill SOPs into every request.
- `plan_context_and_sources` creates a user-visible execution plan and `grounding_policy` so the
  model sees source/evidence constraints before candidate ranking.
- Product-page text is treated as untrusted observation data. Prompt-injection-like fetched lines are
  redacted before normalization, while safe measurement/material/price text remains parseable.
- The remaining harness risk is coverage drift: live and fixture smoke tests must keep checking that
  these instructions remain present in actual model requests, not only in source docs.

## Current Applied Changes

- Runtime instructions now say size labels are weak hints and require garment measurement evidence
  for fit-sensitive claims.
- Similar-product and outfit-pairing skills now carry the same size-label policy.
- Grounding policy exposes `size_label` as `weak_size_hint`.
- Report grounding now exposes `suspicious_fit_claims` and rejects unsupported similar-fit,
  true-to-size, close-measurement, and fits-well claims unless observed garment measurement or
  explicit source text supports the claim.
- Report grounding now exposes `suspicious_score_claims`; score text such as `신뢰도 39점` must be
  grounded by `score.confidence=39`.
- Report grounding now exposes `suspicious_evidence_claims`; coverage text such as `실측, 소재, 가격
  근거를 확인했습니다` must be grounded by actual observed coverage fields.
- Product-name pack numbers such as `3장가격` are excluded from numeric claim extraction so listing a
  candidate name does not create a fake measurement or score claim.
- Product-name color counts and candidate-audit list numbers such as `3Colors` or `Candidate 3 tee`
  are excluded from numeric claim extraction.
- Product run read models expose `/api/agent/runs/{run_id}/session-info` and `/sdk-events`, so
  clients can resume from observed trace/evidence cursors instead of relying on model text.
- `/sdk-events` marks tool events with `backend_owned: true` when the event is paired to a persisted
  backend observation, matching FitCheck's backend-owned tool execution boundary.
- `/sdk-events` emits a terminal `final_output` event with grounded/report metadata, a compact
  grounding validation summary, and citation badges so SDK-like clients can verify final output
  grounding without a separate `/report` fetch.
- `/sdk-events` now exposes replay metadata (`cursor_policy`, `projected_event_count`,
  `terminal_event_seq`, `terminal_event_included`, and `final_output_included`) so SDK-like clients
  can distinguish full history, terminal delta, and already-caught-up cursor responses.
- Input variation verification now includes an unseen free-form target (`바라클라바`/`넥워머`) so
  the harness proves raw target preservation beyond common parser categories such as t-shirt, pants,
  or hoodie.
- Input variation verification now asserts that each real model request still includes the domain
  grounding instructions for unfamiliar words, weak size labels, garment measurements, and
  body/model measurement separation.
- Live agent smoke now treats terminal SDK `final_output` as insufficient unless its suspicious
  claim arrays are present and empty and at least one citation badge is exposed.
- Report grounding now rejects candidate material claims that are supported only by
  `tool.arguments.query` or planning context. `normalized_spec.material` and material-specific
  observations remain valid evidence.
- Product normalization now preserves `size_measurement_rows` and records `measurement_selection`.
  `score_candidate_match` exposes which size-chart row was selected and the per-field score basis.
- Close-fit recovery now enriches more candidates by default and keeps searching for a close
  measurement match for a larger bounded budget before writing a no-close-match report. The
  default budget is 24 measurement-focused search attempts and remains configurable with
  `FITCHECK_CLOSE_FIT_SEARCH_ATTEMPTS`.
- `score_candidate_match`, blocked `write_purchase_report`, and shortlist `candidate_audit`
  surfaces now expose `close_fit_search_budget` so clients can distinguish "still searching for a
  closer measured match" from "search budget exhausted; only weak reference candidates remain."
- Close-fit recovery guidance is backend-recoverable. If the model ignores a no-close-fit
  `score_candidate_match` or blocked `write_purchase_report` result and tries to stop, the runtime
  can execute the suggested `search_web_candidates` recovery itself and continue the loop before
  accepting a grounded final report.
- LLM `max_turns` is not by itself close-fit search exhaustion. If a deferred close-fit score still
  has backend recovery suggestions, the runtime executes the recovery search and immediately
  re-runs `score_candidate_match(candidate_key="all")` before falling back to any weak/no-close
  report. This keeps "find the closest measured item" loops bounded by the close-fit search budget,
  not by an arbitrary conversation turn limit.
