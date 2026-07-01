# Agentic Harness API Checklist

Use this checklist when a task asks for Agent SDK, local LLM, OpenAI Responses API, or agentic loop implementation through `developer_harness`.

## Script Defaults

Use:

```powershell
$env:HARNESS_BASE_URL = "http://127.0.0.1:8000/api/harness/v1"
$env:HARNESS_CLIENT_ID = "your-session-id"
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py health
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py ready
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py capabilities
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py capabilities --summary true
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py capabilities --summary true --require-sdk-event-type agent_eval_case_verification --require-sdk-event-type harness_verification_result --require-transcript-section agent_eval_case_verifications --require-transcript-section harness_verification_results --require-summary-field agent_eval_case_verification_count --require-summary-field harness_verification_result_count
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py sdk-initialize
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py sdk-mcp-status
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py sdk-context-usage
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py diagnostics --limit 20
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py tasks
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py sessions
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py eval-cases
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py eval-case-verify --eval-case-id 61
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py eval-case-verify --eval-case-id 61 --execute --cwd C:\com2us
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py eval-case-verify --eval-case-id 61 --execute --cwd C:\com2us --record-result-task-id task_...
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py loops
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py loop-create --name "Nightly verifier loop" --cwd C:\com2us\developer_harness --sandbox read-only --skill agentic-harness-loop --verification-command "python -B verify.py" --interval-seconds 600 --max-iterations 3 --prompt "Run the harness verifier loop and record gaps."
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py loop-tick --loop-id loop_... --force true --start false
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py loop-evidence --loop-id loop_...
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py events --task-id task_... --after 0
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py sdk-events --task-id task_... --summary true
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py client-transcript --task-id task_... --summary true
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py progress --task-id task_... --items-json "[{\"content\":\"Verify harness evidence\",\"active_form\":\"Verifying harness evidence\",\"status\":\"completed\"}]"
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py queued-message --task-id task_... --prompt "Follow up with the latest evidence." --priority next
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py cancel-queued-message --task-id task_... --message-uuid msg_...
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py annotate --task-id task_... --title "Provider probe session" --tag provider
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py fork --task-id task_... --prompt "Retry this task with the new evidence." --idempotency-key retry-001
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py command --task-id task_... --command pause
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py reply --task-id task_... --prompt "Resume this task with the new evidence." --background true
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py worker-tick
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py subagent --task-id task_... --cwd C:\com2us\developer_harness --sandbox read-only --prompt "Verify the parent task evidence." --background false
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py subagents --task-id task_...
```

Add `--dry-run` before or after the subcommand to inspect a request without sending it.
For complex JSON in PowerShell, write JSON to a file and pass `--input-json @path\to\file.json` or `--expected-json @path\to\file.json`.
The Python entrypoints include POSIX shebangs and executable bits, so Linux/WSL callers may run
`./.agents/skills/agentic-harness-loop/scripts/harness_api.py health` directly. Keep the explicit
`python ...\harness_api.py` form in portable or Windows examples.

Create a reusable workspace scaffold when starting from scratch:

```powershell
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\scaffold_agent_harness.py --output-dir C:\com2us\harness_outputs\agentic_scaffold --provider ollama --model qwen3-coder --client-id your-session-id --cwd C:\com2us\developer_harness
```

The scaffold writes:

- `provider_profile.json`: explicit OpenAI/compatible profile.
- `harness_task_payload.json`: delegated task body with client-scoped idempotency key, loop metadata, and verifier.
- `evidence_holder_payload.json`: non-running task body for external live LLM or SDK smoke evidence.
- `evidence_templates.json`: tool call, observation, and artifact shapes.
- `eval_case_seed.json`: repeatable failure seed.
- `run_probe.ps1`, `run_task.ps1`, `run_evidence_holder.ps1`, `ingest_trace.ps1`: executable harness commands.
- `agent_loop_contract.md`: guardrails and reporting contract.

Use `plan` when the next API is unclear:

```powershell
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py plan --goal provider
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py plan --goal loop
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py plan --goal trace
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py plan --goal failure --status 409
```

`plan --goal provider` includes a compact capabilities summary command, while
`plan --goal task`, `plan --goal trace`, and `plan --goal eval` include the
fail-closed read-model capabilities gate before task or trace readback commands.

`harness_api.py probe` uses provider-aware defaults. `probe --provider openai`
defaults to `api_style=responses`, `base_url=https://api.openai.com/v1`,
`api_key_env=OPENAI_API_KEY`, and `supports_stateful_responses=true`; local
providers such as Ollama still default to OpenAI-compatible chat-completions
profiles. Override `--api-style`, `--base-url`, or `--api-key-env` only when
testing a nonstandard profile.
Probe responses include non-secret controller fields such as `provider_name`,
`api_style`, `model`, `provider_request_attempted`,
`provider_response_received`, and `external_api_called`. In this probe context,
`external_api_called=true` means the provider `/models` endpoint returned an
HTTP response; it is not proof that a product route made a model call.

Use `trace_ingest.py` when an agent run already produced JSON:

```powershell
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\agents_sdk_smoke.py --output-json C:\com2us\harness_outputs\agents-sdk-smoke.json
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\agents_sdk_smoke.py --scenario handoff --output-json C:\com2us\harness_outputs\agents-sdk-handoff-smoke.json
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\agents_sdk_smoke.py --scenario approval --output-json C:\com2us\harness_outputs\agents-sdk-approval-smoke.json
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\agents_sdk_smoke.py --scenario guardrail --output-json C:\com2us\harness_outputs\agents-sdk-guardrail-smoke.json
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\agents_sdk_smoke.py --scenario guardrail_tripwire --output-json C:\com2us\harness_outputs\agents-sdk-guardrail-tripwire-smoke.json
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\agents_sdk_smoke.py --scenario session --output-json C:\com2us\harness_outputs\agents-sdk-session-smoke.json
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\agents_sdk_smoke.py --scenario stream --output-json C:\com2us\harness_outputs\agents-sdk-stream-smoke.json
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\agents_sdk_smoke.py --scenario stream_handoff --output-json C:\com2us\harness_outputs\agents-sdk-stream-handoff-smoke.json
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\agents_sdk_smoke_matrix.py --scenario approval --scenario stream_handoff --probe-provider --publish-readback --client-id your-session-id
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\trace_ingest.py --dry-run --client-id your-session-id --task-id task_... --trace-json .\agent-result.json
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\trace_ingest.py --client-id your-session-id --task-id task_... --trace-json .\agent-result.json
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\trace_ingest.py --client-id your-session-id --task-id task_... --trace-json .\agent-result.json --readback --fail-on-readback-invalid
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\trace_ingest.py --client-id your-session-id --task-id task_... --trace-json .\agent-result.json --readback --fail-on-readback-invalid --compact-output --output-json .\trace-ingest-full.json
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\trace_ingest.py --client-id your-session-id --task-id task_... --trace-json .\agent-result.json --readback-only --fail-on-readback-invalid --compact-output --output-json .\trace-readback-only-full.json
```

Use `agents_sdk_smoke_matrix.py` when multiple live SDK smoke scenarios should be run as one
repeatable gate. It runs `agents_sdk_smoke.py` per scenario, writes one trace JSON per scenario and
one aggregate matrix JSON, can probe the OpenAI provider through the harness before running, and
can publish each passing scenario into a separate evidence-holder task with trace-ingest readback
validation. The matrix validates scenario-specific evidence such as tool call/output records,
approval request/decision/resume phase records, guardrail tripwire records, SDK session state, and
streamed handoff event/tool-name preservation. If `--publish-readback` is set, the matrix also
checks those scenario-specific requirements against `/sdk-events?summary=true` and
`/client-transcript?summary=true` and validates raw `/events` replay coverage for each scenario
task, including the replay task id, artifact/observation/tool-call event coverage, and expected
`tool_call.status_updated` coverage for queued or otherwise non-terminal tool calls that complete
through a later observation. Terminal tool calls should report
`tool_call_status_update_expected_count=0` and should not require a redundant status-update event;
the per-scenario `scenario_event_replay_validation` payload should also expose
`expected_event_counts` and `observed_event_counts` for artifact, observation, tool-call, and
status-update event coverage so controllers can inspect the exact replay contract in one place;
scenario and aggregate raw event replay fetches should derive `max_events` from readback summaries,
preserve `event_replay_fetch_plan`, and fail validation when the returned replay is shorter than
the expected raw event count reported by that plan; compact live and stored summaries should expose
the aggregate and per-scenario fetch-plan basis, selected max event count, expected raw event count,
and fetch-plan truncation checks;
aggregate summary and scenario-rollup observations should also expose
`scenario_event_replay_expected_event_counts`,
`scenario_event_replay_observed_event_counts`,
`scenario_event_replay_event_counts`,
`scenario_event_replay_count_map_totals`,
`scenario_event_replay_count_map_total_totals`,
`scenario_event_replay_count_total_checks`,
`scenario_event_replay_sse_event_name_counts`,
`scenario_event_replay_event_type_counts`,
`scenario_event_replay_event_status_counts`,
`scenario_event_replay_event_actor_counts`, matching raw replay totals
(`scenario_event_replay_sse_event_name_totals`,
`scenario_event_replay_event_type_totals`,
`scenario_event_replay_event_status_totals`,
`scenario_event_replay_event_actor_totals`), matching coverage event totals, and the narrower
`tool_call_status_update_expected_counts` / `tool_call_status_updated_event_counts` totals so
controllers can explain expected versus observed replay coverage plus raw replay composition from
the aggregate surface; event replay validation should also check that each raw
SSE event-name, event-type, event-status, and event-actor count-map total equals
the replay `event_count`, so stale or partial distribution maps fail readback
with explicit count-total checks;
`--client-id` or `HARNESS_CLIENT_ID` is required. The aggregate matrix summary should expose
provider probe validity, generic readback status counts, projection validation status counts,
scenario event replay status counts, failed projection/replay scenarios, scenario-to-failed-check
maps, provider diagnostic id, and published task ids. Live compact summaries should mirror these
status-count and failed-check maps, not only the full matrix JSON, so controllers can identify the
failed stage from stdout alone. Use
`--publish-matrix-readback` when the aggregate gate itself should be replayable through the harness:
it creates an aggregate evidence-holder task, records an `agents_sdk_smoke_matrix` artifact plus
summary, scenario-rollup, and provider-probe-validation observations, and validates the stored
evidence readback before accepting the matrix task. Use
`--validate-matrix-readback-task-id <task_id> --validate-matrix-json <path>` to revalidate an
already-published aggregate task without making new live SDK/provider calls; the command fetches
stored `/evidence`, `/events`, `/sdk-events?summary=true`, and `/client-transcript?summary=true`,
checks event cursor advancement plus artifact/observation event coverage, preserves
`event_replay_fetch_plan`, rejects fetch-plan truncation, and exits nonzero if validation fails.
Add `--validate-published-scenarios` when the same stored-evidence command should
also refetch every `publish_readback.task_id` in the matrix JSON and rerun generic trace-ingest
readback, scenario projection, and raw event replay validation for each published scenario task.
That mode proves the aggregate matrix task and per-scenario evidence-holder tasks still bind to
their current read-model summaries without live SDK/provider calls. Revalidation output should
include `published_scenario_readback_summary` for fast controller checks and should record
per-scenario fetch/validation failures as `readback_error:*` failed checks with `error_scenarios`
instead of aborting before writing JSON. Validation compact stdout should also mirror
published-scenario readback status, valid flag, scenario count, check count, status counts, failed
scenarios, failed checks, and error scenarios as top-level `published_scenario_readback_*` fields,
so controller gates do not need to open the nested summary before deciding pass/fail. Use
`--compact-output` when stdout should contain only a
controller summary: live and dry-run modes print `matrix_run_summary`, validation mode prints
`readback_validation_summary`. Keep `--output-json` set so the full evidence payload with detailed
per-scenario results is still written. Live compact summaries should include top-level `valid`,
derived from status, failure-reason, stage failure-map, provider-probe, and aggregate-readback
gates; they should also include `matrix_readback_task_id`, matrix and payload readback check counts, failed-check lists, and
task-binding maps so later stored-evidence revalidation can start from stdout alone. Live and
validation compact summaries should also expose aggregate artifact provenance: live compact output
should include `matrix_readback_artifact`, `matrix_readback_artifact_checks`,
`matrix_payload_readback_artifact`, and `matrix_payload_readback_artifact_checks`; stored-evidence
validation compact output should include `aggregate_artifact` and `aggregate_artifact_checks`.
Those fields should show recorded/expected artifact paths, stored and recomputed SHA-256 digests
when files are locally available, path availability, and the matrix artifact path/digest check
results without requiring the controller to open the full payload. Live and
validation compact summaries should also include `revalidation_inputs` with the aggregate readback
task id, matrix JSON path, client id, base URL, and revalidation flags, but never an API key. They
should also expose `revalidation_ready`, `revalidation_missing_fields`, and
`revalidation_required_fields` so dry-run compact output can say exactly why stored-evidence
revalidation is not runnable yet. They should also expose non-secret
`revalidation_cli_args` for this same script, omitting the Python executable, script path, API key,
and caller-chosen `--output-json` path. Use `--revalidate-from-summary-json <path>` when a
previous compact or full summary JSON should be revalidated directly; the script should read
`revalidation_inputs` from the root object, `readback_validation_summary`, or
`matrix_run_summary` and run the same stored-evidence validation path without live SDK/provider
calls. Compact summaries should also expose `summary_revalidation_inputs`,
`summary_revalidation_ready`, `summary_revalidation_missing_fields`, and
`summary_revalidation_cli_args` for that direct summary-file path. When
`--revalidate-from-summary-json` is itself used without a new `--output-json`,
the resulting compact summary should keep the source summary path as the next
summary-file revalidation handle. Live and validation compact summaries should
use `--record-eval-case-on-failure` when failed stored-evidence matrix
validation should become a repeatable `/eval-cases` regression seed. The
recorded eval case should include a source task id when available, failure
label, failed-check input, expected completed status, evidence refs, and a
non-secret verifier command; validation compact stdout should expose
`eval_case_recording`. After recording, the script should also fetch
`GET /eval-cases` for the same client id and expose
`readback_status`, `readback_valid`, `readback_failed_checks`,
`readback_match_strategy`, and `readback_matched_eval_case_id` under
`eval_case_recording`, so controllers can prove the regression seed is visible
through the harness read model. Repeated validation of the same failure should
reuse a matching existing eval case instead of posting another row; compact
stdout should report `eval_case_recording.status=already_recorded`,
`duplicate_prevented=true`, and no readback failed checks when this happens.
It should also expose `readback_matching_eval_case_count` and
`readback_matching_eval_case_ids` so repeated historical duplicates are visible
without a separate eval-case list call. New eval cases should include
`metadata.failure_signature`, and compact stdout should mirror it under
`eval_case_recording.failure_signature` for controller-side grouping. The
signature should be stable across harmless verifier transport/output changes:
canonicalize the verifier command by ignoring `--base-url`, `--client-id`, and
`--compact-output`. Exact verifier command drift from those fields may appear
under `readback_non_blocking_failed_checks`, not `readback_failed_checks`.
Readback output should also expose `readback_signature_metadata_status` plus
stored-signature missing/mismatch id lists so controllers can see whether older
rows are grouped by computed signature while their persisted metadata is absent
or stale. It should also expose `failure_signature_version` and
`failure_signature_material` in compact output, and new eval-case metadata
should store the same fields. It should expose
`failure_signature_material_digest` plus checks for
`eval_case_readback_signature_matches_material` and
`eval_case_readback_signature_version_current`. Recorded eval-case output
should preserve preflight duplicate lookup diagnostics under `dedupe_lookup_*`,
including `lookup_failed` errors when the script posts without a verified
dedupe readback. It should also expose `post_attempted` plus either
`post_skipped_reason` or `post_required_reason`, so compact output can prove
whether the script actually called `POST /eval-cases` or reused an existing
client-scoped regression seed.
Use `harness_api.py eval-case-verify` after recording when a controller needs
to inspect or rerun a stored regression verifier. Without `--execute`, it only
selects the client-scoped eval case, reports the stored verifier command, cwd,
expected status, and evidence refs, and exits without shell execution. Add
`--execute --cwd <absolute-cwd>` only when the stored `verifier_command` should
run locally; the command returns nonzero when the verifier fails and should be
treated as stored harness regression evidence, not as live provider proof unless
the verifier itself performs that provider probe. Add
`--record-result-task-id <task_id>` when the selected eval-case verification
result should be persisted back into harness evidence as an
`eval_case_verification` observation with `eval_case:<id>` and stored evidence
refs.
After recording the result, verify `/sdk-events` and `/client-transcript`:
stored `eval_case_verification` observations should project as
`agent_eval_case_verification` records, and `--summary true` should expose
verification count, failed count, and failure-label counts. Treat these as
stored regression evidence only, not as proof of current provider connectivity
unless the verifier command itself probes and records the provider.
Live and validation compact summaries should
also expose scenario evidence-holder readback counts:
`scenario_publish_readback_count`, `scenario_publish_readback_status_by_scenario`,
`scenario_generic_readback_check_counts`, `scenario_projection_check_counts`,
`scenario_event_replay_check_counts`, `scenario_total_readback_check_counts`, and
`scenario_readback_task_bindings`, `scenario_trace_artifact_digest_matches`,
`scenario_trace_artifact_bindings`, and `scenario_artifact_summaries` for live matrices, plus
`published_scenario_generic_check_counts`, `published_scenario_projection_check_counts`,
`published_scenario_event_replay_check_counts`, `published_scenario_total_check_counts`, and
`published_scenario_task_bindings`, `published_scenario_trace_artifact_digest_matches`,
`published_scenario_trace_artifact_bindings`, and `published_scenario_artifact_summaries` for
stored-evidence validation summaries. They should also expose sanitized SDK session details through
`scenario_session_summaries` and `published_scenario_session_summaries`, including backend, SDK
session id, turn counts, persisted item counts, item type counts, turn phases, status, and evidence
refs without copying raw session item previews or local session database paths. Live and stored
compact output should expose session stored evidence through
`scenario_session_evidence_summaries`, `scenario_readback_session_evidence_summaries`, and
`published_scenario_session_evidence_summaries`, including stored `agent_session` observation
counts, readback session-summary count, backend names, max turn/item counts, turn phases, and
stateful-session booleans without copying raw session item previews, local session database paths,
observation payloads, call id lists, observation id lists, or database ids. Live compact
summaries should additionally expose `scenario_session_state_summaries` from the source SDK smoke
trace, including session id, backend, turn count, phase names, item-count before/after maps,
summary-match checks, session-growth flags, and booleans for whether private DB paths/previews were
present, without copying raw prompts, raw preview text, or local database paths. Live and stored
compact output should expose stream stored evidence through
`scenario_stream_evidence_summaries`, `scenario_readback_stream_evidence_summaries`, and
`published_scenario_stream_evidence_summaries`, including stored `agent_stream_event`
observation counts, expected stream event-type/name/raw-event-type/item-class/tool-name count maps,
expected tool-name and handoff-event booleans for stream handoffs, and SDK/transcript count-map
match booleans without copying raw stream items, raw response events, model output text,
observation payloads, call id lists, observation id lists, or database ids. They should also
expose stream distribution details through `scenario_stream_projection_summaries` and
`published_scenario_stream_projection_summaries`, including SDK-event and client-transcript
stream-event counts plus event-type, event-name, raw-event-type, item-class, and tool-name count
maps, so streamed handoff preservation can be checked from compact stdout. They should also
expose `scenario_event_replay_expected_event_counts`,
`scenario_event_replay_observed_event_counts`,
`scenario_event_replay_event_counts`,
`scenario_event_replay_count_map_totals`,
`scenario_event_replay_count_map_total_totals`,
`scenario_event_replay_count_total_checks`,
`scenario_event_replay_sse_event_name_counts`,
`scenario_event_replay_event_type_counts`,
`scenario_event_replay_event_status_counts`,
`scenario_event_replay_event_actor_counts`,
`scenario_event_replay_sse_event_name_totals`,
`scenario_event_replay_event_type_totals`,
`scenario_event_replay_event_status_totals`,
`scenario_event_replay_event_actor_totals`, matching coverage event totals,
`tool_call_status_update_expected_counts`, `tool_call_status_updated_event_counts`, and their
aggregate totals so compact stdout can explain raw event replay coverage and raw event
name/type/status/actor distribution without opening the full matrix JSON. Live compact summaries
should also expose `scenario_readback_event_replay_event_counts`,
`scenario_readback_event_replay_count_map_totals`,
`scenario_readback_event_replay_count_map_total_totals`, and
`scenario_readback_event_replay_count_total_checks`; stored-evidence validation summaries should
mirror the same data under `published_scenario_event_replay_*` fields. They should also
expose stage-specific failed-check maps such as `scenario_generic_readback_failed_checks`,
`scenario_projection_failed_checks`, `scenario_event_replay_failed_checks`,
`scenario_readback_failed_checks`, `published_scenario_generic_failed_checks`,
`published_scenario_projection_failed_checks`, `published_scenario_event_replay_failed_checks`,
and `published_scenario_stage_failed_checks`. Published scenario compact summaries should also
expose `sdk_events_projection_maps_by_scenario`,
`client_transcript_projection_maps_by_scenario`, `sdk_events_projection_map_totals`, and
`client_transcript_projection_map_totals`; stored-evidence validation compact output should mirror
those as `published_scenario_sdk_events_projection_maps`,
`published_scenario_client_transcript_projection_maps`,
`published_scenario_sdk_events_projection_map_totals`, and
`published_scenario_client_transcript_projection_map_totals`, so controllers can verify
provider/API-style read-model preservation for every published scenario without opening each
scenario result. Live compact summaries should also expose
`scenario_guardrail_tripwire_summaries` from the live SDK result and
`scenario_guardrail_projection_summaries` /
`scenario_readback_guardrail_projection_summaries` from published read-model summaries; stored
revalidation should mirror the read-model side as
`published_scenario_guardrail_projection_summaries`. These fields let controllers see guardrail
names, tripwire counts, run-error projection, error type, and tripwire reasons from compact stdout
without opening the full trace JSON. Live compact output should also expose guardrail stored
evidence summaries through `scenario_guardrail_evidence_summaries` and
`scenario_readback_guardrail_evidence_summaries`; stored revalidation should mirror them as
`published_scenario_guardrail_evidence_summaries`. These summaries should count stored
`agent_guardrail_result` evidence for normal guardrail runs and stored `agent_run_error` evidence
for tripwire runs while omitting raw guardrail results, model output text, run-error payloads,
observation payloads, call ids, observation ids, and database ids.
The aggregate
matrix summary
should also expose
`scenario_api_usage_totals`, `api_usage_totals`, `scenario_sdk_events_projection_counts`,
`scenario_client_transcript_projection_counts`, `scenario_sdk_events_projection_maps`,
`scenario_client_transcript_projection_maps`, `sdk_events_projection_totals`,
`client_transcript_projection_totals`, `sdk_events_projection_map_totals`, and
`client_transcript_projection_map_totals`; check per-scenario provider cost plus guardrail,
run-error, final-output, handoff, session, stream-event, and model-response provider/API-style
counts there before accepting the top-level gate.
Live compact output should expose base tool-call state through
`scenario_tool_state_summaries`: call/output counts, expected tool names, paired
call/output records, phase counts, sanitized call/output summaries, argument
keys, output keys, and marker-presence booleans. It must not copy raw prompts,
final text, tool argument values, or tool output values. Live and stored compact
output should also expose stored harness evidence through
`scenario_tool_evidence_summaries`,
`scenario_readback_tool_evidence_summaries`, and
`published_scenario_tool_evidence_summaries`, including trace-ingest
tool-call counts, observation counts, tool-call name counts, observation-tool
counts, readback evidence count coverage booleans, id counts, and expected
`record_signal` call/output-observation booleans without copying raw
observation payloads, tool arguments, tool outputs, call id lists, observation
id lists, or database primary-key values. Live and stored compact
output should expose tool read-model projection through
`scenario_tool_projection_summaries`,
`scenario_readback_tool_projection_summaries`, and
`published_scenario_tool_projection_summaries`, including SDK-event and
client-transcript tool-progress/phase/model-response/final-output counts plus
the tool-progress delta. Tool projection should remain scoped to the base
`tool` scenario, so approval-required tool calls stay under approval summaries.
Live compact output should also expose approval/interruption state through
`scenario_approval_state_summaries`: request/decision counts, approved decision
counts, `initial` and `after_approval` phase presence, phase interruption
counts, sanitized call/tool/agent/argument-key summaries, and booleans for
observed interruption and approved resume. It must not copy raw approval items,
prompts, or tool outputs. Live and stored compact output should expose approval
stored evidence through `scenario_approval_evidence_summaries`,
`scenario_readback_approval_evidence_summaries`, and
`published_scenario_approval_evidence_summaries`, including the
`approval_required_signal` tool call, approval request/tool-output/decision
observation booleans, trace-ingest counts, readback evidence count coverage,
and id counts without copying raw approval items, tool arguments, tool outputs,
observation payloads, call id lists, observation id lists, or database
primary-key values. Live and stored compact output should expose approval
read-model projection through `scenario_approval_projection_summaries`,
`scenario_readback_approval_projection_summaries`, and
`published_scenario_approval_projection_summaries`, including SDK-event and
client-transcript approval/phase/tool-progress/final-output counts plus the
tool-progress delta.
Live compact output should expose non-stream handoff state through
`scenario_handoff_state_summaries`: handoff call/output/pair counts,
source/target agent names, handoff tool names, expected source/target/tool
booleans, phase counts, sanitized call/output summaries, and paired call/output
records. It must not copy raw prompts, final text, or handoff output payload
values. Live and stored compact output should expose handoff
stored evidence through `scenario_handoff_evidence_summaries`,
`scenario_readback_handoff_evidence_summaries`, and
`published_scenario_handoff_evidence_summaries`, including `agent_handoff`
observation counts/booleans, trace-ingest counts, readback evidence count
coverage, and id counts without copying raw handoff items, handoff output
payloads, observation payloads, call id lists, observation id lists, or
database primary-key values. Live and stored compact output should expose
read-model handoff
projection through `scenario_handoff_projection_summaries`,
`scenario_readback_handoff_projection_summaries`, and
`published_scenario_handoff_projection_summaries`, including SDK-event and
client-transcript handoff/phase/final-output counts. Approval projection must
not appear for handoff-only scenarios just because phase records exist.
Live compact `matrix_run_summary` output should mirror those projection-count maps, totals,
`projection_total_deltas`, `projection_map_deltas`, `projection_total_delta_allowed_keys`, and
`projection_total_delta_assessment` so a controller can make that coverage and delta-allowance
check from compact stdout without reopening the full matrix JSON. It should also expose
`matrix_readback_event_replay_fetch_plan`,
`matrix_payload_readback_event_replay_fetch_plan`,
`matrix_readback_event_replay_event_count`,
`matrix_readback_event_replay_count_map_totals`,
`matrix_readback_event_replay_count_total_checks`,
`matrix_readback_event_replay_expected_event_counts`,
`matrix_readback_event_replay_observed_event_counts`,
`matrix_payload_readback_event_replay_event_count`,
`matrix_payload_readback_event_replay_count_map_totals`,
`matrix_payload_readback_event_replay_count_total_checks`,
`matrix_payload_readback_event_replay_expected_event_counts`,
`matrix_payload_readback_event_replay_observed_event_counts`,
`scenario_event_replay_fetch_plans`, matching `*_fetch_plan_checks` fields,
`scenario_readback_event_replay_expected_event_counts`,
`scenario_readback_event_replay_observed_event_counts`,
`scenario_readback_event_replay_expected_event_totals`, and
`scenario_readback_event_replay_observed_event_totals`, plus
`scenario_readback_event_replay_sse_event_name_counts`,
`scenario_readback_event_replay_event_type_counts`,
`scenario_readback_event_replay_event_status_counts`,
`scenario_readback_event_replay_event_actor_counts`, and matching
`scenario_readback_event_replay_*_totals` so controllers can verify replay sizing, coverage,
raw composition, and truncation checks from compact stdout alone.
Stored-evidence validation compact `readback_validation_summary` output should also mirror source
matrix `scenario_api_usage_totals`, `api_usage_totals`, projection-count maps, projection-map
summaries, projection totals, projection-map totals, projection-delta assessment fields, and
projection-map delta assessment fields so revalidation stdout can support the same coverage
checks. It should also expose `aggregate_event_replay_fetch_plan`,
`aggregate_event_replay_event_count`, `aggregate_event_replay_count_map_totals`,
`aggregate_event_replay_count_total_checks`,
`aggregate_event_replay_expected_event_counts`,
`aggregate_event_replay_observed_event_counts`,
`aggregate_event_replay_fetch_plan_checks`,
`published_scenario_event_replay_fetch_plans`, and
`published_scenario_event_replay_fetch_plan_checks`, plus
`published_scenario_event_replay_expected_event_counts`,
`published_scenario_event_replay_observed_event_counts`,
`published_scenario_event_replay_expected_event_totals`,
`published_scenario_event_replay_observed_event_totals`,
`published_scenario_event_replay_sse_event_name_counts`,
`published_scenario_event_replay_event_type_counts`,
`published_scenario_event_replay_event_status_counts`, and
`published_scenario_event_replay_event_actor_counts`, plus matching
`published_scenario_event_replay_sse_event_name_totals`,
`published_scenario_event_replay_event_type_totals`,
`published_scenario_event_replay_event_status_totals`, and
`published_scenario_event_replay_event_actor_totals` so controllers can make stored-evidence
replay sizing and raw composition checks before accepting the revalidation decision. Live and validation compact
summaries should also expose
`provider_probe_valid`, `provider_probe_diagnostic_id`, `provider_probe_failed_checks`,
`provider_probe_check_count`, `provider_probe_checks`, `provider_probe_status`,
`provider_probe_http_status`, `provider_probe_endpoint`, `provider_probe_models_preview_count`,
`provider_probe_profile`, and `provider_probe_agent_sdk_fit` where a provider probe exists, so the
compact controller output stays tied to the provider diagnostic record without exposing API-key
values. Live compact output should expose `external_api_called`,
`scenario_external_api_called`, and `scenario_readback_external_api_called`; stored-evidence
validation compact output should expose `external_api_called`, `scenario_external_api_called`, and
`published_scenario_external_api_called`, so controllers can confirm live SDK/API evidence from
compact stdout without opening trace artifacts.
Live publish-time compact output and stored-evidence matrix revalidation should also refetch
client-scoped `/llm/diagnostics` and fail when the source matrix provider diagnostic id is no
longer visible. Compact output should expose `provider_diagnostic_readback_status`,
`provider_diagnostic_readback_valid`,
`provider_diagnostic_readback_failed_checks`, `provider_diagnostic_readback_expected_id`,
`provider_diagnostic_readback_matched_id`,
`provider_diagnostic_readback_diagnostic_count`,
`provider_diagnostic_readback_matched_provider_response_received`,
`provider_diagnostic_readback_matched_external_api_called`, and the compact readback checks. This
proves the matrix output still binds to the provider diagnostic read model without running a new
provider probe.
If those totals intentionally differ because `/sdk-events` is lifecycle-oriented and `/client-transcript` is a
compact controller read model, inspect `projection_total_deltas` for the SDK-event count,
client-transcript count, and signed delta instead of treating any difference as hidden drift.
The matrix also reports `projection_total_delta_status`,
`projection_total_delta_allowed_keys`, `projection_total_delta_unexpected_keys`, and
`projection_total_delta_assessment`; only registered lifecycle-vs-compact differences such as a
higher SDK-event `tool_progress_count` are allowed. Unexpected deltas add
`unexpected_projection_total_deltas` to `aggregate_failed_reasons` and fail the aggregate gate.
Nested projection-map deltas, such as mismatched
`agent_model_response_provider_counts` or `agent_model_response_api_style_counts`, are always
unexpected and add `unexpected_projection_map_deltas`.
Aggregate readback validation compares the stored `agents_sdk_smoke_matrix_summary` payload against
the matrix JSON summary, including SDK projection totals, client-transcript projection totals, and
`projection_total_deltas` plus the delta assessment, so corrupted controller-facing summary evidence
should fail revalidation. It should also expose dedicated checks for tool-call status-update
expected/observed maps and totals, such as
`matrix_summary_tool_call_status_update_expected_counts_match`,
`matrix_summary_tool_call_status_updated_event_counts_match`,
`matrix_rollup_tool_call_status_update_expected_counts_match`, and
`matrix_rollup_tool_call_status_updated_event_counts_match`, so controllers do not have to infer
status-update drift only from broad payload mismatch checks.
It should also expose dedicated checks for base tool state/projection drift, such as
`matrix_summary_scenario_tool_state_summaries_match`,
`matrix_summary_scenario_tool_evidence_summaries_match`,
`matrix_summary_scenario_approval_evidence_summaries_match`,
`matrix_summary_scenario_handoff_evidence_summaries_match`,
`matrix_summary_scenario_guardrail_evidence_summaries_match`,
`matrix_summary_scenario_session_evidence_summaries_match`,
`matrix_summary_scenario_stream_evidence_summaries_match`,
`matrix_summary_scenario_tool_projection_summaries_match`,
`matrix_rollup_scenario_tool_state_summaries_match`,
`matrix_rollup_scenario_tool_evidence_summaries_match`, and
`matrix_rollup_scenario_approval_evidence_summaries_match`,
`matrix_rollup_scenario_handoff_evidence_summaries_match`,
`matrix_rollup_scenario_guardrail_evidence_summaries_match`,
`matrix_rollup_scenario_session_evidence_summaries_match`, and
`matrix_rollup_scenario_stream_evidence_summaries_match`, and
`matrix_rollup_scenario_tool_projection_summaries_match`, so stale compact tool-call
or approval/handoff/guardrail/session/stream evidence fails revalidation before a controller trusts it.
It should also include `provider_validation_payload_matches`, comparing the stored
`agents_sdk_smoke_matrix_provider_probe_validation` observation against the source matrix
`provider_probe_validation` payload, so provider detail evidence cannot drift while only the
diagnostic id still matches.
It intentionally ignores the self-referential `matrix_readback_status` and `matrix_readback_valid`
summary fields because those are added after the aggregate readback evidence has already been
recorded. It also compares the stored `agents_sdk_smoke_matrix_scenario_rollup` observation against
a fresh rollup derived from the matrix JSON. The stored rollup should preserve scenario failed
checks, generic readback failed checks and task binding, projection failed checks, scenario
event-replay validity, scenario event-replay failed checks, and replay task binding. It also
validates the recorded `agents_sdk_smoke_matrix` artifact path against the aggregate
evidence-source JSON. Publish-time aggregate validation should include
`matrix_evidence_json` in the evidence-source payload before storing the artifact so `expected_path`
and both recorded/expected SHA-256 digests are available immediately. The final matrix payload should
also include `matrix_payload_readback_validation` after `matrix_readback.task_id` is attached; this
publish-time validation refetches evidence, read-model summaries, raw event replay, and
client-scoped `/llm/diagnostics` so the final output itself proves task-id binding and provider
diagnostic read-model binding without a separate revalidation command. The matrix summary
should mirror aggregate readback `status`, `valid`, `failed_checks`, `task_binding`, and any `error`
for both `matrix_readback` and `matrix_payload_readback`, and should append
`matrix_readback_failed` or `matrix_payload_readback_failed` to `aggregate_failed_reasons` when
either gate fails. When the recorded or
expected artifact file is available locally, it recomputes the SHA-256 digest and compares it with
the stored digest; when files are unavailable, the validation reports availability and keeps the
stored path plus digest format checks as the portable evidence boundary. Existing aggregate
revalidation also binds the supplied task id to the stored evidence, SDK-events summary,
client-transcript summary, event replay, and
`matrix_readback.task_id` in the matrix JSON when present, so controllers do not accidentally
validate one aggregate task with another task's output JSON.

`agents_sdk_smoke.py` live result JSON includes a quick `summary` for controller-side inspection
before ingestion. It should expose new item type/class counts, raw response count/output types,
raw response usage totals, phase names and per-phase counts, approval decision counts, guardrail
tripwire count, SDK session item type counts when present, and stream event distribution maps for
event type, event name, raw event type, item class, and tool name. Treat this as live-smoke trace
metadata; task evidence plus `/sdk-events` and `/client-transcript` remain authoritative after
ingestion.

`trace_ingest.py` records the full trace/result JSON as an `agent_trace` artifact with a
SHA-256 digest, then records separate observations for run summary, raw model response
metadata/usage, tool calls, tool outputs, handoff call/output summaries, guardrails, and
approval/interruption requests and decisions, typed guardrail results, run phases such as
`initial` or `after_approval`, missing-information questions, SDK session summaries, Agents SDK
streaming events, and final output when those fields are present.
Its dry-run and execution responses include `summary.source=trace_ingest_action_summary` with
action, endpoint, artifact-kind, tool-call, tool-call status-update expected, observation-tool,
status, and call-id counts. Execution
responses also include `result_summary.source=trace_ingest_result_summary` with stored artifact
ids, observation ids, tool-call primary keys, tool-call status-update expected counts, status
counts, and call ids returned by the API. Use
these summaries as quick controller checks only; task evidence plus `/sdk-events` and
`/client-transcript` remain the authoritative readback after ingestion.
With `--readback`, execution output should also include `readback.source=trace_ingest_readback`,
`evidence_counts`, `artifact_summaries`, `agent_session_summaries`, `sdk_events_summary`,
`client_transcript_summary`, raw event replay summary, and
`validation.source=trace_ingest_readback_validation`. `artifact_summaries` should include artifact
id, kind, path, digest, status, and only compact non-secret metadata such as trace id, provider,
SDK, model, status, and item type; it should not copy raw approval/interruption item payloads into
compact output. `agent_session_summaries` should include backend, SDK session id, turn counts,
persisted item counts, item type counts, turn phases, status, and evidence refs while omitting raw
session item previews and local session database paths. Treat that validation as a consistency check
over stored harness evidence, raw event replay, and read-model summaries, not proof that a provider
is currently connected. The validation compares ingested model responses, stream events, phases, sessions,
handoffs, approvals, questions, guidance, guardrails, run errors, and SDK final-output coverage
against the read-model summaries. It also binds the expected task id to the readback envelope,
stored evidence, SDK-events summary, client-transcript summary, and raw event replay when present.
The raw event replay gate checks replay completion, timeout status, cursor advancement, and
artifact/observation/tool-call/status-update event coverage against trace-ingest result counts.
The replay fetch should size its `max_events` from the SDK-event summary `raw_event_count` when
available, with stored evidence counts as fallback, and preserve a compact `fetch_plan` so
controllers can see the selected max event count, basis, buffer, expected raw event count, and
evidence event floor. Validation should fail when a fetch plan says an expected raw event count
exists but the replay returns fewer events.
For stream events it also compares event-type, event-name,
raw-event type, item-class, and tool-name distribution maps. For raw model responses it should
also derive provider and API-style count maps from stored `agent_model_response` observations, with
`agent_trace` artifact metadata as fallback for older observations, and compare those maps against
both read-model summaries. If model responses exist but the derived provider/API-style map is empty
or only `none`, validation should fail rather than accepting unattributed model-call evidence. Use
`--fail-on-readback-invalid` in CI/controller smoke tests when projection drift should fail the
command.
Use `--compact-output` when stdout should contain only `trace_ingest_compact_summary`, including
action/result counts, stored artifact/tool/observation ids, readback validity, failed checks, task
binding, provider/API-style maps, evidence/SDK-events/client-transcript stream distribution maps,
artifact/session summaries, raw event replay fetch-plan, expected/observed event counts, and
raw replay name/type/status/actor count maps.
Pair it with `--output-json` when the full action/result/readback payload should be kept as a file.
Use `--readback-only` to revalidate an already ingested trace task without posting new artifacts,
observations, or tool calls. It derives expected result counts from the trace file, performs only
readback requests, compares the expected trace digest with stored `agent_trace` artifact digests,
and supports the same compact-output and fail-on-invalid controller gates.
Run phase observations use `tool_name=agent_run_phase` and are projected by `/sdk-events` and
`/client-transcript` as `agent_phase` records, so approval/resume boundaries remain visible in
SDK-like read models.
Missing-information observations use `tool_name=agent_question` and should preserve question
fields, reason, evidence status, source observation, agent metadata, waiting status, and evidence
refs in both read models.
Backend/runtime guidance observations use `tool_name=agent_guidance` and should preserve guidance
type, reason, required action, suggested backend tools, close-fit search budget, recovery policy,
agent metadata, status, and evidence refs in both read models. If the same guidance appears as both
top-level `agent_guidance` and a guidance-like trace item, ingest it once by semantic content rather
than duplicating observations.
Approval-required SDK tool calls should be recorded as tool-call evidence with
`metadata.approval_required=true` and queued status until the approved tool output observation
arrives. Keep `agent_approval_request` and `agent_approval_decision` observations separate from the
tool output.
SDK session observations use `tool_name=agent_session` and should project backend, SDK session id,
turn counts, persisted item counts, item types, and evidence refs in both read models. Treat this
as stored session trace evidence, not as the harness task session source of truth.
Streaming observations use `tool_name=agent_stream_event` and should project stream index, stream
event type/name, raw response event type, run item/tool metadata, agent, and evidence refs in both
read models. Streamed handoff call/output events should preserve the handoff tool name through the
shared call id. Treat these as stored `Runner.run_streamed` trace evidence, not proof that a
provider is currently connected.
Raw model response observations use `tool_name=agent_model_response` and are projected by
`/sdk-events` as `agent_model_response` records and by `/client-transcript` as
`agent_model_responses`, with response id, model, provider, API style, usage, output summary, and
evidence refs. Older observations that lack provider/API-style fields may recover them from
matching `agent_trace` artifact metadata.
When an Agents SDK raw response omits a per-response model, `trace_ingest.py` falls back to
the trace-level `model` and records `model_source` so the projection remains inspectable.
Handoff call/output observations should project item/raw type, arguments or output when present,
agent metadata, and evidence refs in both `/sdk-events` and `/client-transcript`, so controllers
can reconstruct the handoff boundary without parsing raw trace artifacts.
Approval request/decision observations should project approval arguments, agent/item metadata,
and evidence refs in both `/sdk-events` and `/client-transcript`, so controllers can link an
approval message back to the trace item or decision record without parsing raw observations.
Treat them as stored trace evidence, not proof of current provider connectivity.
If an Agents SDK guardrail tripwire stops the run, preserve it as a policy outcome:
`agent_run_summary`, `agent_run_error`, and the tripwire `agent_guardrail_result` should use
failed status while the raw model response and captured final output remain separate evidence.
Guardrail projections should expose output info, agent output, reason, and evidence refs in
both `/sdk-events` and `/client-transcript`; tripwire run errors should also project as
`agent_run_error` records instead of requiring controllers to inspect raw observations.

## Source-Backed Design Rules

- OpenAI Agents SDK `new_items` is the rich result surface for agent, tool, handoff, approval, and debugging metadata; ingest those categories into harness evidence. Source: https://openai.github.io/openai-agents-python/results/
- OpenAI Agents SDK tracing records LLM generations, tool calls, handoffs, guardrails, and custom events; mirror those categories into harness task evidence. Source: https://openai.github.io/openai-agents-python/tracing/
- OpenAI Agents SDK agents combine instructions, tools, handoffs, guardrails, and structured outputs; keep each of those visible in task prompts, evidence, or eval cases instead of hiding them in prose. Source: https://openai.github.io/openai-agents-python/agents/
- OpenAI guardrails can trip and stop execution, and tool guardrails apply to function tools rather than handoff calls; record guardrail outcomes separately from ordinary tool observations. Source: https://openai.github.io/openai-agents-python/guardrails/
- OpenAI recommends Responses API for new projects when one model call plus tools and application-owned logic is enough, while Agents SDK fits application-owned orchestration, tool execution, approvals, and state. Source: https://developers.openai.com/api/docs/guides/agents
- Responses API provides built-in tools and stateful interactions, but local OpenAI-compatible endpoints should still be treated as chat-completions style unless stateful Responses support is verified. Sources: https://developers.openai.com/api/docs/guides/migrate-to-responses and https://developers.openai.com/api/reference/responses/overview/
- Anthropic recommends simple, composable agent patterns and comprehensive tool evaluations; prefer small typed tools plus eval cases over hidden monolithic loops. Sources: https://www.anthropic.com/research/building-effective-agents and https://www.anthropic.com/engineering/writing-tools-for-agents
- LangGraph emphasizes durable execution, persistence, streaming, and human-in-the-loop interruption; use harness leases, heartbeat, events, evidence, and eval cases to approximate those runtime guarantees for delegated Codex work. Sources: https://docs.langchain.com/oss/python/langgraph/overview, https://docs.langchain.com/oss/python/langgraph/persistence, and https://docs.langchain.com/oss/python/langchain/human-in-the-loop

## Provider Evidence

- `GET /api/harness/v1/llm/providers` lists intended profiles.
- `POST /api/harness/v1/llm/probe` records `/models` probe diagnostics when `X-Harness-Client-Id` is present.
- Local Ollama, LM Studio, vLLM, and similar servers default to `api_style=chat_completions`.
- OpenAI cloud may use `api_style=responses` only when configured and verified.

Script examples:

```powershell
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py providers
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py probe --provider openai --model gpt-5.4-mini
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py probe --provider ollama --base-url http://localhost:11434/v1 --model qwen3-coder
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py diagnostics --limit 20
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py diagnostics --limit 20 --expect-diagnostic-id 113
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py diagnostics --limit 20 --expect-diagnostic-id 113 --expect-status completed --expect-provider openai --expect-model gpt-5.4-mini --expect-api-style responses
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py diagnostics --limit 20 --expect-diagnostic-id 113 --expect-status completed --expect-provider openai --expect-model gpt-5.4-mini --expect-api-style responses --expect-provider-request-attempted true --expect-provider-response-received true --expect-external-api-called true
```

`diagnostics --expect-diagnostic-id N` adds `diagnostics_validation` to stdout and exits nonzero
when the current client-scoped diagnostics read model does not contain that id. Add
`--expect-status`, `--expect-provider`, `--expect-model`, `--expect-api-style`,
`--expect-provider-request-attempted`, `--expect-provider-response-received`, and
`--expect-external-api-called` when reproducing the same status/model/API-style and provider-call
binding checked by matrix provider diagnostic readback. It checks stored read-model evidence only;
it does not run a new provider probe.

## Task Evidence

Every stateful request must include `X-Harness-Client-Id`.

Use task metadata like:

```json
{
  "metadata": {
    "agentic_loop_template": "agents_sdk_trace_eval_codex",
    "agentic_loop": {
      "trace_sources": ["harness_events", "tool_observations"],
      "eval_targets": ["tool_loop_regression"],
      "verification_commands": ["python -B verify.py"]
    }
  }
}
```

Record evidence with:

- `POST /api/harness/v1/tasks/{task_id}/tool-calls`
- `POST /api/harness/v1/tasks/{task_id}/observations`
- `POST /api/harness/v1/tasks/{task_id}/artifacts`
- `GET /api/harness/v1/tasks/{task_id}/evidence`

Script examples:

```powershell
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py task --cwd C:\com2us\developer_harness --sandbox read-only --idempotency-key inspect-provider-diagnostics-001 --skill agentic-harness-loop --verification-command "python -B verify.py" --prompt "Inspect provider diagnostics and propose the next eval."
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py task --start false --cwd C:\com2us\developer_harness --sandbox read-only --idempotency-key live-llm-evidence-holder-001 --prompt "Evidence holder for live LLM smoke."
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py tool-call --task-id task_... --call-id call_provider_probe_001 --tool-name provider_probe --input-json "{\"provider\":\"ollama\"}" --status running
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py observation --task-id task_... --call-id call_provider_probe_001 --tool-name provider_probe --result-json "{\"status\":\"configuration_required\"}" --evidence-ref diagnostic:missing-base-url
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py permission-decision --task-id task_... --call-id call_provider_probe_001 --tool-name provider_probe --behavior ask --decision-reason-json "{\"type\":\"mode\",\"mode\":\"default\"}"
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py progress --task-id task_... --items-json "[{\"item_id\":\"probe\",\"content\":\"Probe provider\",\"active_form\":\"Probing provider\",\"status\":\"completed\"},{\"item_id\":\"record\",\"content\":\"Record diagnostic evidence\",\"active_form\":\"Recording diagnostic evidence\",\"status\":\"completed\"}]"
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py queued-message --task-id task_... --prompt "Retry after provider configuration changes." --priority later --message-uuid msg_retry_provider
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py cancel-queued-message --task-id task_... --message-uuid msg_retry_provider
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py evidence --task-id task_...
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py events --task-id task_...
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py sdk-initialize
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py sdk-context-usage
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py sdk-events --task-id task_... --summary true
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py client-transcript --task-id task_... --summary true
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py tasks
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py sessions
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py annotate --task-id task_... --title "Harness evidence holder" --summary "Tracks live SDK smoke evidence."
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py fork --task-id task_... --prompt "Retry with updated provider profile." --idempotency-key retry-provider-profile-001
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py command --task-id task_... --command pause
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py reply --task-id task_... --prompt "Continue after updated provider profile." --background true
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py sdk-events --task-id task_...
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py client-transcript --task-id task_...
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py eval-cases
```

The `events --task-id` command should return a `harness_event_sse_replay` envelope with
`event_count`, `keepalive_count`, `timed_out`, `next_after`, `cursor_policy`,
`sse_event_name_counts`, `event_type_counts`, `event_status_counts`, and `event_actor_counts`,
so controllers can inspect replay coverage and cursor progress without reparsing raw SSE lines.
The `--summary true` read-model commands should include `summary_requested=true`,
`agent_guidance_count`, unique `agent_guidance_reasons`, `agent_guidance_reason_counts`,
missing-information question counts, `agent_eval_case_verification_count`,
`agent_eval_case_verification_failed_count`,
`agent_eval_case_failure_label_counts`, `harness_verification_result_count`,
`harness_verification_failed_count`, `harness_verification_status_counts`,
`agent_model_response_provider_counts`,
`agent_model_response_api_style_counts`, and a historical-evidence claim boundary. The
client-transcript summary should also include `item_type_counts` so controllers can quickly
check whether tool progress, model responses, stream events, questions, guidance, eval-case
verification results, and harness verification results survived projection before fetching the
full read model.
`backend_harness/publish_live_agent_smoke_export.py` should use those summary maps when publishing
live-smoke trace exports: derive expected provider/API-style maps from trace-ingest actions and
fail readback when either SDK-events or client-transcript loses
`agent_model_response_provider_counts` or `agent_model_response_api_style_counts`. It should also
bind the expected trace file digest to the stored `agent_trace` artifact and fail when counts match
but the stored trace artifact digest differs. Its publish readback should also fetch
`/tasks/{task_id}/events?after=0` with a replay size derived from stored evidence and SDK-event
summary counts, then fail when artifact, tool-call, or observation events are not replayable from
the generic harness event log. Use `--readback-only --task-id ...` to rerun the same validation
against an existing evidence-holder task without posting new artifacts, observations, tool calls,
or eval cases; use `--compact-output` when controller logs should show only the validation gate,
artifact binding, projection counts, replay coverage, revalidation readiness, and non-secret
readback-only CLI args. Use `--revalidate-from-summary-json <path>` when a previous compact or
full publisher JSON should be the only input for the same readback-only stored-evidence gate. Add
`--record-result-task-id <task_id>` when the publisher revalidation outcome should be persisted as
an `eval_case_verification` observation and projected through SDK-events/client-transcript read
models. The publisher also performs a post-record readback of evidence, SDK-events, and
client-transcript; compact output should expose `record_result_readback_valid`, failed checks, and
the SDK-event/client-transcript match counts for the recorded observation id. If this record-result
gate fails, compact output should preserve the original stored-evidence status as `publish_status`
while returning top-level `status=failed` and a nonzero CLI exit.
When `--compact-output` is paired with `--output-json`, the publisher JSON file
should preserve the full payload and mirror the compact controller fields at
the root plus `publish_summary` / `compact_summary`, so stored JSON can be used
without scraping stdout.
For product-route live smokes, set `FITCHECK_LIVE_AGENT_PUBLISH_HARNESS=1` and
`FITCHECK_LIVE_AGENT_HARNESS_RECORD_RESULT=1` so `verify_live_agent_smoke.py` exercises the same
publish-result evidence gate during the actual run.
The live-smoke result should expose top-level publish/readback summary fields including
`harness_publish_task_id`, `harness_publish_readback_valid`,
`harness_publish_record_result_readback_valid`, and
`harness_publish_record_result_gate_valid`, so automation can gate the stored-evidence result
without parsing the nested `harness_publish` payload.
It should also expose non-secret replay handles at the top level:
`run_id`, `trace_id`, `harness_trace_json`, `harness_trace_digest`,
`harness_eval_case_json`, `harness_eval_case_digest`,
`harness_publish_trace_json`, `harness_publish_eval_case_json`,
`harness_publish_revalidation_inputs`, and
`harness_publish_revalidation_cli_args`, so automation can rerun stored-evidence
readback directly from the compact result.
`publish_live_agent_smoke_export.py --revalidate-from-summary-json` should read
those live-smoke wrapper fields directly as well as publisher-native
`revalidation_inputs`.
It should also expose top-level agent-loop counts, including `llm_raw_response_count`,
`trace_model_requested_tool_call_count`, `trace_backend_recovery_tool_call_count`,
`trace_tool_output_count`, and `observation_count`, and fail
`model_selected_tool_call_missing` when all tool calls are backend-recovery calls rather than
model-requested calls.
For discovery-style product smokes, require top-level candidate evidence summaries as well:
latest search candidate/product-like/rejected counts, completed fetch count, normalized candidate
and field-coverage counts, ranked match count, top-ranked score fields, report candidate-audit
counts, and close-fit search budget. The default completed-smoke minimums are two searched
candidates, two ranked matches, one normalized candidate, and one reported candidate; use
`FITCHECK_LIVE_AGENT_EXPECT_MIN_SEARCH_CANDIDATES`,
`FITCHECK_LIVE_AGENT_EXPECT_MIN_RANKED_MATCHES`,
`FITCHECK_LIVE_AGENT_EXPECT_MIN_NORMALIZED_CANDIDATES`, and
`FITCHECK_LIVE_AGENT_EXPECT_MIN_REPORTED_CANDIDATES` to override for narrower scenarios.
For completed product-route smokes, require the verifier's user-visible final-report text quality
gate to pass on both the run `final_report` and SDK `final_output`: unresolved missing-info
question phrasing must fail, while grounded confidence-limit phrasing is acceptable.
Set `FITCHECK_LIVE_AGENT_PROBE_PROVIDER=1` when the same live smoke should also POST a
client-scoped `/llm/probe`, read it back from `/llm/diagnostics`, and fail if provider request,
response, external-call, or diagnostic-id readback evidence is missing. Keep this labeled as provider
connectivity evidence, not standalone proof of product-route reasoning.
The same compact summaries are available directly from the server API with
`GET /api/harness/v1/tasks/{task_id}/sdk-events?summary=true` and
`GET /api/harness/v1/tasks/{task_id}/client-transcript?summary=true`; callers still need the
same `X-Harness-Client-Id` isolation header.
`harness_api.py sdk-events --summary true` and `harness_api.py client-transcript --summary true`
should forward `summary=true` to those server endpoints and pass through an already summarized
response. If a local fallback summary is needed, the CLI should use the same
`developer_harness.read_model_summary` functions as the FastAPI runtime to prevent script/server
summary drift.
`GET /api/harness/v1/capabilities` advertises this discovery surface as
`loop_engineering_contract.read_model_summaries` and a structured `read_model_contract`.
The structured contract should list SDK event types, client transcript sections, summary fields,
and cursor semantics, including newer projection surfaces such as
`agent_eval_case_verification` and `harness_verification_result`, so controllers can discover
support before fetching a task read model.
Use `harness_api.py capabilities --summary true` when controller logs need this discovery
without printing the full skills, agents, hooks, provider templates, and catalog payload.
Add `--require-sdk-event-type`, `--require-transcript-section`, and `--require-summary-field`
to make the command exit nonzero when a required projection surface is missing.

`sdk-context-usage` reports a harness estimate from stored tasks, events, and evidence. If
trace ingestion recorded `agent_model_response.usage`, the response also includes an `apiUsage`
summary aggregated from those observations. Treat that as stored trace evidence, not proof that a
live provider is currently connected.
The context usage response should expose `sourceTaskIds`, `eventCount`, `evidenceCounts`, and an
explicit claim boundary. When `apiUsage` is present it should also expose `responseCount`,
`requestCount`, `taskIds`, `models`,
`providers`, `apiStyles`, `taskCount`, per-task `taskUsage`, per-model `modelUsage`, per-status
`statusUsage`, per-provider `providerUsage`, per-API-style `apiStyleUsage`, `modelCounts`,
`statusCounts`, `providerCounts`, `apiStyleCounts`, `evidenceRefCount`, and per-response
`request_count` plus `evidence_refs` so SDK-style controllers can link aggregate, task-level, model-level,
status-level, provider-level, and API-style-level usage back to stored trace evidence.

Subagent examples:

```powershell
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py subagent --task-id task_... --cwd C:\com2us\developer_harness --sandbox read-only --skill agentic-harness-loop --idempotency-key verify-child-001 --background false --prompt "Inspect parent evidence and report gaps."
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py subagents --task-id task_...
```

## Eval Seeds

Create eval cases when a trace or runtime gap is repeatable:

```json
{
  "name": "provider configuration regression",
  "source_task_id": "task_...",
  "failure_label": "configuration_required",
  "input": {"provider": "local_missing"},
  "expected": {"status": "configuration_required"},
  "verifier_command": "python -B verify.py",
  "evidence_refs": ["task:task_..."]
}
```

Script example:

```powershell
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py eval-case --name "provider configuration regression" --source-task-id task_... --failure-label configuration_required --input-json "{\"provider\":\"local_missing\"}" --expected-json "{\"status\":\"configuration_required\"}" --verifier-command "python -B verify.py" --evidence-ref task:task_...
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py eval-case-verify --eval-case-id <eval_case_id>
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py eval-case-verify --eval-case-id <eval_case_id> --execute --cwd C:\com2us
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py eval-case-verify --eval-case-id <eval_case_id> --execute --cwd C:\com2us --record-result-task-id task_...
```

If shell quoting is fragile:

```powershell
Set-Content -Encoding UTF8 .\eval-input.json '{"provider":"local_missing"}'
Set-Content -Encoding UTF8 .\eval-expected.json '{"status":"configuration_required"}'
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py eval-case --name "provider configuration regression" --input-json @.\eval-input.json --expected-json @.\eval-expected.json
```

## Worker Safety

- Use worker leases for delegated execution.
- Renew only the current owner/epoch with `/workers/heartbeat`.
- Treat stale epoch, wrong owner, or cross-client heartbeat failures as intended safety behavior.

Script example:

```powershell
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py worker-tick
python C:\com2us\.agents\skills\agentic-harness-loop\scripts\harness_api.py heartbeat --task-id task_... --lease-epoch 1 --lease-seconds 300
```

## Reporting Rules

Say "harness evidence" for deterministic, cached, fixture, mocked, or fallback results.
Say "connected" only after a provider probe or live verifier proves connectivity.
Include verifier command output status in final claims.

## Failure Recovery

- Missing provider config: return or surface `configuration_required`; do not retry blindly.
- Provider endpoint failure: separate harness API reachability from model provider reachability.
- Tool call failure: record `tool-call` and failed `observation`, then add a retry hint or eval case.
- Guardrail failure: record it as a policy outcome, not a model/tool crash.
- Handoff/subagent failure: preserve child task id, parent event, and final handoff summary before retrying.
- Repeated failure: create an eval case with source task, input, expected result, verifier command, and evidence refs.
