const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "webapp", "index.html"), "utf8");
const script = fs.readFileSync(path.join(root, "webapp", "app.js"), "utf8");

const frontendExecutionPlan = {
  status: "in_progress",
  target_text: "무탠다드 청바지",
  parallel_groups: [
    {
      id: "candidate_analysis",
      status: "pending",
      tools: ["fetch_product_page", "classify_product_assets", "extract_size_material_info", "normalize_product_spec"],
    },
  ],
  steps: [
    {
      id: "context",
      title: "맥락 정리",
      status: "completed",
      tools: ["plan_context_and_sources"],
      agent_role: "planner",
      execution_policy: "sequential",
      summary: "입력과 출처를 정리합니다.",
    },
    {
      id: "skill_selection",
      title: "스킬 선택",
      status: "completed",
      tools: ["load_agent_skill"],
      selected_skills: ["outfit-pairing"],
      agent_role: "coordinator",
      execution_policy: "progressive_disclosure",
      summary: "필요한 SOP를 선택합니다.",
    },
    {
      id: "candidate_search",
      title: "후보 검색",
      status: "in_progress",
      tools: ["search_web_candidates"],
      agent_role: "researcher",
      execution_policy: "open_world_search",
      summary: "웹 후보를 검색합니다.",
    },
    {
      id: "candidate_enrichment",
      title: "후보별 상세 분석",
      status: "pending",
      parallelizable: true,
      parallel_group: "candidate_analysis",
      tools: ["fetch_product_page", "classify_product_assets", "extract_size_material_info", "normalize_product_spec"],
      agent_role: "product_inspector",
      execution_policy: "parallel_candidate_batch",
      summary: "후보별 상세페이지와 OCR을 분석합니다.",
    },
    {
      id: "matching",
      title: "비교 및 점수화",
      status: "pending",
      tools: ["score_candidate_match"],
      agent_role: "matcher",
      execution_policy: "grounded_scoring",
      summary: "후보를 비교합니다.",
    },
    {
      id: "report",
      title: "근거 기반 리포트",
      status: "pending",
      tools: ["write_purchase_report"],
      agent_role: "reporter",
      execution_policy: "trace_grounded_only",
      summary: "근거 기반으로 보고합니다.",
    },
  ],
};

const agentRunPayload = {
  status: "configuration_required",
  run_id: "run_frontend_smoke",
  trace: [
    {
      step_index: 1,
      step_type: "tool_output",
      name: "search_web_candidates",
      status: "configuration_required",
      input: {},
      output: { call_id: "call_frontend_search" },
      created_at: "2026-06-08T00:00:00Z",
    },
  ],
  state: {
    observations: [
      {
        tool: "search_web_candidates",
        call_id: "call_frontend_search",
        result_status: "configuration_required",
        provider: "google_cse_search",
        provider_error_code: "GOOGLE_CSE_CONFIGURATION_REQUIRED",
        provider_error_category: "configuration",
        retry_badges: [
          {
            id: "R0",
            label: "not configured",
            status: "not_started",
            provider: "google_cse_search",
            provider_error_code: "GOOGLE_CSE_CONFIGURATION_REQUIRED",
            provider_error_category: "configuration",
          },
        ],
        result: {
          status: "configuration_required",
          provider: "google_cse_search",
          error: {
            code: "GOOGLE_CSE_CONFIGURATION_REQUIRED",
            message: "Set Google CSE configuration.",
          },
          provider_error_code: "GOOGLE_CSE_CONFIGURATION_REQUIRED",
          provider_error_category: "configuration",
          query_quality: {
            quality: "needs_query_expansion",
            score: 35,
            has_clothing_keyword: true,
            has_product_intent_signal: false,
          },
          domain_policy: {
            allowed_domains_configured: false,
            preferred_domains: ["musinsa.com", "29cm.co.kr"],
            blocked_domains: ["instagram.com"],
            policy: "allowlist_then_blocklist_then_preference",
          },
          search_attempt: {
            attempt_index: 2,
            repeated_query: true,
            previous_query_count: 1,
          },
        },
      },
    ],
    missing_info: [],
    execution_plan: frontendExecutionPlan,
  },
  error: {
    code: "GOOGLE_CSE_CONFIGURATION_REQUIRED",
    provider_error_code: "GOOGLE_CSE_CONFIGURATION_REQUIRED",
    provider_error_category: "configuration",
  },
  llm: {
    status: "connected",
    model: "fake-tool-calling-model",
    api_style: "responses",
    tool_execution: "backend_owned_functions",
  },
};

const eventPayload = {
  event_id: "run_frontend_smoke:trace:7",
  event_sequence: 7,
  status: "configuration_required",
  run_id: "run_frontend_smoke",
  trace_count: 1,
  latest_trace: agentRunPayload.trace,
  trace_delta: agentRunPayload.trace,
  latest_observation: {
    tool: "write_purchase_report",
    result_status: "completed",
    result: {
      status: "completed",
      candidate_key: "all",
      grounded: true,
      final_report: "coverage report",
      evidence_coverage: {
        completed_tools: [
          "fetch_product_page",
          "classify_product_assets",
          "extract_size_material_info",
          "normalize_product_spec",
        ],
        missing_tools: [],
        observed_fields: ["material", "price"],
        critical_missing_fields: ["measurements.length", "measurements.shoulder"],
        recommendation_policy: "critical_missing_fields_force_need_more_info",
      },
    },
  },
  error: agentRunPayload.error,
  execution_plan: frontendExecutionPlan,
  missing_info: [
    {
      field: "baseline_evidence",
      closet_item_id: "current_liked_item",
      missing_fields: "measurements.length, measurements.shoulder, material",
      question: "기준 옷의 실측, 소재 근거가 부족합니다.",
    },
  ],
};

const evidencePayload = {
  run_id: "run_frontend_smoke",
  status: "configuration_required",
  counts: {
    trace_steps: 7,
    observations: 3,
  },
  context_budget: [
    {
      estimated_tokens: 8120,
      compaction_threshold: 8000,
      remaining_tokens: 2048,
    },
  ],
  context_compactions: [
    {
      reason: "estimated_token_threshold",
      latest_observations: [
        { tool: "search_web_candidates", status: "configuration_required" },
      ],
      pending_tool_output_count: 1,
      loaded_skills: ["outfit-pairing"],
    },
  ],
  hook_lifecycle: [
    {
      event: "PostToolUse",
      tool: "search_web_candidates",
      status: "recorded",
      mode: "record_only",
      call_id: "call_frontend_search",
    },
  ],
  sdk_like: {
    tool_result_pairing: {
      status: "paired",
      completed_call_count: 1,
      pending_output_count: 0,
    },
    raw_response_ids: ["resp_frontend_1"],
  },
};

const completedExecutionPlan = {
  ...frontendExecutionPlan,
  status: "completed",
  parallel_groups: [
    {
      id: "candidate_analysis",
      status: "completed",
      tool_statuses: {
        fetch_product_page: "completed",
        classify_product_assets: "completed",
        extract_size_material_info: "completed",
        normalize_product_spec: "completed",
      },
    },
  ],
  steps: frontendExecutionPlan.steps.map((step) => ({
    ...step,
    status: "completed",
    tool_statuses: step.id === "candidate_enrichment"
      ? {
          fetch_product_page: "completed",
          classify_product_assets: "completed",
          extract_size_material_info: "completed",
          normalize_product_spec: "completed",
        }
      : undefined,
  })),
};

eventPayload.execution_plan = completedExecutionPlan;

const completedRunPayload = {
  status: "completed",
  run_id: "run_frontend_completed",
  trace: [
    {
      step_index: 0,
      step_type: "context",
      name: "match_agent_skills",
      status: "completed",
      output: {
        matched_skills: [{ name: "outfit-pairing" }],
        selection_policy: "metadata_first_progressive_disclosure",
      },
    },
    {
      step_index: 1,
      step_type: "llm_response",
      name: "responses.create",
      status: "completed",
      output: { tool_call_count: 1 },
    },
    {
      step_index: 2,
      step_type: "tool_call",
      name: "plan_context_and_sources",
      status: "started",
      input: { arguments: { free_text: "같이 입을 청바지 추천" } },
    },
    {
      step_index: 3,
      step_type: "tool_output",
      name: "plan_context_and_sources",
      status: "completed",
      output: {
        search_constraints: {
          goal_mode: "outfit_pairing",
          target_item_query: "청바지",
          query_hint: "청바지 코디 어울리는",
        },
        applied_recipes: [{ id: "outfit_pairing_free_target" }],
      },
    },
    { step_index: 4, step_type: "tool_output", name: "search_web_candidates", status: "completed", output: { candidate_count: 5 } },
    {
      step_index: 5,
      step_type: "runtime",
      name: "execute_parallel_tool_batch",
      status: "started",
      input: { tool: "fetch_product_page", call_count: 4, parallel_group: "candidate_analysis" },
      output: {},
    },
    {
      step_index: 6,
      step_type: "runtime",
      name: "execute_parallel_tool_batch",
      status: "completed",
      input: {},
      output: { tool: "fetch_product_page", call_count: 4, parallel_group: "candidate_analysis" },
    },
    { step_index: 7, step_type: "tool_output", name: "write_purchase_report", status: "completed" },
  ],
  state: {
    observations: [
      {
        tool: "score_candidate_match",
        result_status: "completed",
        result: {
          status: "completed",
          candidate_key: "all",
          ranked_matches: [
            {
              candidate_key: "live_frontend_candidate",
              recommendation: "need_more_info",
              score: { overall: 41, size: 28, material: 60, price: 70, confidence: 29 },
              missing_fields: ["measurements.length", "measurements.shoulder"],
              evidence_coverage: eventPayload.latest_observation.result.evidence_coverage,
            },
          ],
        },
      },
      {
        tool: "write_purchase_report",
        result_status: "completed",
        result: {
          status: "completed",
          candidate_key: "all",
          grounded: true,
          final_report: "coverage fallback report",
          next_questions: [],
          evidence_coverage: eventPayload.latest_observation.result.evidence_coverage,
        },
      },
    ],
    missing_info: [
      {
        field: "baseline_evidence",
        closet_item_id: "current_liked_item",
        missing_fields: "measurements.length, measurements.shoulder, material",
        question: "기준 옷의 실측, 소재 근거가 부족합니다.",
      },
    ],
    execution_plan: completedExecutionPlan,
  },
  final_report: {
    summary: "coverage fallback report",
    grounded: true,
  },
  llm: agentRunPayload.llm,
};

const resumableFailedRunPayload = {
  status: "failed",
  run_id: "run_frontend_resumable_failed",
  trace: [
    { step_index: 1, step_type: "tool_output", name: "load_closet_memory", status: "completed" },
    { step_index: 2, step_type: "guardrail", name: "stop_llm_response_failed", status: "failed" },
  ],
  state: {
    pending_tool_outputs: [
      {
        type: "function_call_output",
        call_id: "call_frontend_pending",
        output: "{\"status\":\"completed\"}",
      },
    ],
    observations: [
      {
        tool: "load_closet_memory",
        call_id: "call_frontend_pending",
        result_status: "completed",
        result: {
          status: "completed",
          durable_memory_status: "loaded",
          durable_memory_match: {
            score: 82,
            reasons: ["memory_reference", "single_confirmed_memory_reference"],
          },
          closet_item: {
            id: "current_liked_item",
            memory_source: "durable_closet_memory",
            baseline_identity: { status: "memory_identity_available" },
          },
        },
      },
    ],
    missing_info: [],
    execution_plan: frontendExecutionPlan,
  },
  error: {
    code: "LLM_RESPONSE_FAILED",
    message: "429 Client Error: Too Many Requests for url: https://api.openai.com/v1/responses",
    provider_error_code: "HTTP_429_RATE_LIMITED",
    provider_error_category: "rate_limit",
    retryable: true,
    resume_available: true,
    pending_tool_output_count: 1,
  },
  llm: agentRunPayload.llm,
};

const resumableFailedEventPayload = {
  status: "failed",
  run_id: "run_frontend_resumable_failed",
  trace_count: 2,
  latest_trace: resumableFailedRunPayload.trace,
  latest_observation: resumableFailedRunPayload.state.observations[0],
  error: resumableFailedRunPayload.error,
  missing_info: [],
};

function jsonResponse(payload) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve(payload),
  });
}

async function main() {
  const dom = new JSDOM(html, {
    url: "http://127.0.0.1/",
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const { window } = dom;
  const eventSources = [];
  const fetchCalls = [];
  const replyPayloads = [];

  window.fetch = (url, options = {}) => {
    fetchCalls.push({ url: String(url), method: options.method || "GET" });
    if (String(url) === "/api/status") {
      return jsonResponse({
        status: "ready",
        llm: {
          status: "connected",
          model: "fake-tool-calling-model",
          api_style: "responses",
          tool_execution: "backend_owned_functions",
        },
        providers: {
          search: {
            selected: "google_cse",
            provider: "google_cse_search",
            mode: "live_provider",
            requires_api_key: true,
            api_key_configured: false,
            requires_search_engine_id: true,
            search_engine_id_configured: false,
            requires_base_url: false,
          },
          ocr: {
            selected: "fixture_boundary",
            provider: "fixture_ocr_boundary",
            mode: "fixture_result_boundary",
            external_provider_configured: false,
          },
          page_fetch: {
            selected: "jina_reader",
            provider: "live_page_fetch",
            mode: "live_provider",
            external_provider_configured: true,
            fallbacks: ["jina_reader"],
            fallback_enabled: true,
            jina_reader_enabled: true,
            jina_reader_api_key_configured: false,
          },
        },
      });
    }
    if (String(url) === "/api/agent/runs?background=true") {
      return jsonResponse(agentRunPayload);
    }
    if (String(url).includes("/api/agent/runs/") && String(url).endsWith("/reply?background=true")) {
      replyPayloads.push(JSON.parse(options.body || "{}"));
      return jsonResponse(completedRunPayload);
    }
    if (String(url).includes("/api/agent/runs/") && String(url).endsWith("/evidence")) {
      return jsonResponse(evidencePayload);
    }
    throw new Error(`unexpected fetch: ${url}`);
  };

  window.EventSource = class FakeEventSource {
    constructor(url) {
      this.url = url;
      this.listeners = {};
      this.closed = false;
      eventSources.push(this);
      window.setTimeout(() => {
        const payload = String(url).includes("run_frontend_resumable_failed")
          ? resumableFailedEventPayload
          : eventPayload;
        this.dispatch("agent_state", { data: JSON.stringify(payload) });
      }, 0);
    }

    addEventListener(name, callback) {
      this.listeners[name] = callback;
    }

    dispatch(name, event) {
      if (!this.closed && this.listeners[name]) {
        this.listeners[name]({
          lastEventId: payloadEventId(event),
          ...event,
        });
      }
    }

    close() {
      this.closed = true;
    }
  };

  window.eval(script);
  window.document.dispatchEvent(new window.Event("DOMContentLoaded", { bubbles: true }));
  await delay(0);

  const input = window.document.querySelector("#chatInput");
  input.value = "무탠다드 청바지 추천";
  window.document.querySelector("#chatForm").dispatchEvent(
    new window.Event("submit", { bubbles: true, cancelable: true })
  );
  await delay(20);

  const observationStatus = text("#observationStatus", window);
  const observationPanel = text("#observationPanel", window);
  const traceCount = text("#traceCount", window);
  const messagesText = text("#messages", window);
  const headingText = text("h1", window);
  const placeholderText = input.getAttribute("placeholder") || "";
  const searchProviderText = text("#searchProviderState", window);
  const ocrProviderText = text("#ocrProviderState", window);
  const pageFetchProviderText = text("#pageFetchProviderState", window);
  const streamStatusText = text("#streamStatus", window);
  const streamCursorText = text("#streamCursorState", window);
  const streamSequenceText = text("#streamSequenceState", window);
  const evidenceText = text("#evidenceList", window);
  const evidenceSummaryText = text("#evidenceSummary", window);

  const failures = [];
  if (observationStatus !== "completed") {
    failures.push(`unexpected observation status: ${observationStatus}`);
  }
  if (!headingText.includes("마음에 드는 옷을 기준으로")) {
    failures.push(`heading text is not readable Korean: ${headingText}`);
  }
  if (!placeholderText.includes("무탠다드 청바지")) {
    failures.push(`placeholder text is not readable Korean: ${placeholderText}`);
  }
  if (!messagesText.includes("마음에 드는 옷의 사진이나 설명")) {
    failures.push("initial assistant guidance is not readable Korean");
  }
  for (const expected of [
    "write_purchase_report",
    "observed:material, price",
    "missing:measurements.length, measurements.shoulder",
    "tools:none",
    "critical_missing_fields_force_need_more_info",
  ]) {
    if (!observationPanel.includes(expected)) {
      failures.push(`observation panel missing: ${expected}`);
    }
  }
  for (const expected of [
    "실제 검색 provider 설정이 필요합니다.",
    "FITCHECK_SEARCH_PROVIDER=brave_search",
    "BRAVE_SEARCH_API_KEY",
    "FITCHECK_SEARCH_PROVIDER=tavily_search",
    "TAVILY_API_KEY",
    "FITCHECK_SEARCH_PROVIDER=google_cse legacy only",
  ]) {
    if (!messagesText.includes(expected)) {
      failures.push(`provider configuration message missing: ${expected}`);
    }
  }
  if (messagesText.includes("LLM 설정이 필요합니다.")) {
    failures.push("Google CSE configuration was incorrectly shown as LLM configuration");
  }
  if (!searchProviderText.includes("google_cse_search live_provider missing key/cx")) {
    failures.push(`search provider runtime state missing key/cx: ${searchProviderText}`);
  }
  if (!ocrProviderText.includes("fixture_ocr_boundary fixture_result_boundary")) {
    failures.push(`OCR provider runtime state missing: ${ocrProviderText}`);
  }
  if (!pageFetchProviderText.includes("live_page_fetch live_provider fallback:jina_reader no-key")) {
    failures.push(`page fetch provider runtime state missing: ${pageFetchProviderText}`);
  }
  if (traceCount !== "1") {
    failures.push(`unexpected trace count: ${traceCount}`);
  }
  if (streamStatusText !== "closed") {
    failures.push(`unexpected stream status: ${streamStatusText}`);
  }
  if (!streamCursorText.includes("run_frontend_smoke:trace:7")) {
    failures.push(`stream cursor did not capture SSE event id: ${streamCursorText}`);
  }
  if (streamSequenceText !== "7") {
    failures.push(`stream sequence did not render event_sequence: ${streamSequenceText}`);
  }
  for (const expected of [
    "Context budget",
    "estimated:8120 threshold:8000 remaining:2048",
    "Conversation compacted",
    "reason:estimated_token_threshold observations:1 pending_tool_outputs:1 skills:outfit-pairing",
    "PostToolUse: search_web_candidates",
    "paired · completed:1 pending:0",
    "resp_frontend_1",
  ]) {
    if (!evidenceText.includes(expected)) {
      failures.push(`run evidence panel missing: ${expected}`);
    }
  }
  for (const expected of ["7trace", "3observations", "1hooks", "1context", "1compactions"]) {
    if (!evidenceSummaryText.includes(expected)) {
      failures.push(`run evidence summary missing: ${expected}`);
    }
  }
  if (eventSources.length !== 1) {
    failures.push(`unexpected EventSource count: ${eventSources.length}`);
  }
  if (!eventSources[0] || !eventSources[0].closed) {
    failures.push("terminal SSE event did not close EventSource");
  }
  if (!fetchCalls.some((item) => item.url === "/api/agent/runs?background=true" && item.method === "POST")) {
    failures.push("agent run POST was not called");
  }
  if (typeof window.subscribeRunEvents === "function") {
    window.subscribeRunEvents("run_frontend_smoke");
    await delay(20);
    const sameRunReplaySource = eventSources.find((item) => item.url.includes("last_event_id=run_frontend_smoke%3Atrace%3A7"));
    if (!sameRunReplaySource) {
      failures.push("same-run EventSource reconnect did not use Last-Event-ID cursor");
    }
  } else {
    failures.push("subscribeRunEvents is not available for SSE replay check");
  }
  if (typeof window.renderRun === "function") {
    window.renderRun(completedRunPayload);
    await delay(0);
    const completedObservationPanel = text("#observationPanel", window);
    if (!completedObservationPanel.includes("critical_missing_fields_force_need_more_info")) {
      failures.push("completed run coverage policy missing after terminal report event");
    }
    const questionPanel = text("#questionList", window);
    if (!questionPanel.includes("기준 옷의 실측")) {
      failures.push("completed run did not render baseline follow-up question");
    }
    if (questionPanel.includes("?곹뭹 ?곸꽭 URL")) {
      failures.push("completed run should not ask user for public candidate URL after tool recovery is exhausted");
    }
    const executionPlanText = text("#planList", window);
    const executionPlanStatus = text("#planStatus", window);
    for (const expected of [
      "후보 검색",
      "후보별 상세 분석",
      "병렬 가능",
      "스킬: outfit-pairing",
      "역할: researcher",
      "역할: product_inspector",
      "정책: parallel_candidate_batch",
      "그룹: candidate_analysis",
      "fetch_product_page:completed",
    ]) {
      if (!executionPlanText.includes(expected)) {
        failures.push(`execution plan panel missing: ${expected}`);
      }
    }
    if (executionPlanStatus !== "completed") {
      failures.push(`execution plan status should be completed: ${executionPlanStatus}`);
    }
    window.renderLatestObservation({
      tool: "fetch_product_page",
      result: {
        status: "completed",
        provider: "live_page_fetch+jina_reader",
        fetch_fallback_provider: "jina_reader",
        fetch_fallback_status: "completed",
        direct_fetch_status: "failed",
        direct_fetch_error: "403 Client Error: Forbidden for url: https://www.musinsa.com/products/1666442",
        asset_count: 40,
        ocr_target_asset_count: 10,
      },
    });
    const fetchObservationPanel = text("#observationPanel", window);
    for (const expected of ["jina_reader:completed", "direct:failed", "assets:40", "ocr_targets:10"]) {
      if (!fetchObservationPanel.includes(expected)) {
        failures.push(`fetch fallback observation panel missing: ${expected}`);
      }
    }
    window.renderLatestObservation({
      tool: "extract_size_material_info",
      result: {
        status: "completed",
        provider: "tesseract_cli_ocr",
        evidence: [
          { status: "completed", image_type: "size_chart", text: "珥앹옣 ?덈쾮吏?⑤㈃" },
          { status: "completed_empty", image_type: "detail_text" },
          { status: "skipped", reason: "no_local_asset_path_for_live_ocr" },
        ],
      },
    });
    const ocrObservationPanel = text("#observationPanel", window);
    for (const expected of ["evidence:3", "text:1", "empty:1", "failed:1", "types:size_chart,detail_text"]) {
      if (!ocrObservationPanel.includes(expected)) {
        failures.push(`OCR observation panel missing: ${expected}`);
      }
    }
    window.renderLatestObservation({
      tool: "normalize_product_spec",
      result: {
        status: "completed",
        ocr_evidence_used_count: 1,
        user_evidence_used: true,
        normalized_spec: {
          measurements: { length: 71, shoulder: 51 },
          material: { cotton: 100 },
          price: 49900,
          missing_fields: ["measurements.chest"],
        },
      },
    });
    const specObservationPanel = text("#observationPanel", window);
    for (const expected of ["measurements:2", "material:cotton", "price:49900", "missing:1", "ocr:1", "user"]) {
      if (!specObservationPanel.includes(expected)) {
        failures.push(`spec observation panel missing: ${expected}`);
      }
    }
    const completedPlanItems = Array.from(window.document.querySelectorAll("#planList li"));
    if (!completedPlanItems[2]?.classList.contains("done")) {
      failures.push(`search plan step should be done from server execution_plan: ${completedPlanItems[2]?.className || "missing"}`);
    }
    const planText = text("#planList", window);
    const toolFlowText = text("#toolFlowList", window);
    const toolFlowCount = text("#toolFlowCount", window);
    for (const expected of ["맥락 정리", "후보 검색", "후보별 상세 분석", "근거 기반 리포트"]) {
      if (!planText.includes(expected)) {
        failures.push(`agent plan panel missing: ${expected}`);
      }
    }
    for (const expected of ["관련 스킬 검색", "skills=outfit-pairing", "LLM 판단", "모델이 도구 선택: plan_context_and_sources", "도구 결과 저장: search_web_candidates", "병렬 도구 실행", "group=candidate_analysis"]) {
      if (!toolFlowText.includes(expected)) {
        failures.push(`tool flow panel missing: ${expected}`);
      }
    }
    if (toolFlowCount !== "8") {
      failures.push(`unexpected tool flow count: ${toolFlowCount}`);
    }
    window.renderRun(resumableFailedRunPayload);
    await delay(0);
    const memoryObservationPanel = text("#observationPanel", window);
    for (const expected of ["durable:loaded", "match:82", "source:durable_closet_memory", "identity:memory_identity_available"]) {
      if (!memoryObservationPanel.includes(expected)) {
        failures.push(`memory observation panel missing: ${expected}`);
      }
    }
    const failedMessagesText = text("#messages", window);
    for (const expected of [
      "외부 LLM API 사용량 제한으로 일시 중단됐습니다.",
      "저장된 tool output은 보존되어 있습니다.",
      "HTTP_429_RATE_LIMITED",
      "pending_tool_outputs=1",
    ]) {
      if (!failedMessagesText.includes(expected)) {
        failures.push(`resumable failure message missing: ${expected}`);
      }
    }
    input.value = "다시 진행해";
    window.document.querySelector("#chatForm").dispatchEvent(
      new window.Event("submit", { bubbles: true, cancelable: true })
    );
    await delay(20);
    const resumeReply = replyPayloads[0] || {};
    const resumeCall = fetchCalls.find((item) => item.url === "/api/agent/runs/run_frontend_resumable_failed/reply?background=true");
    if (!resumeCall) {
      failures.push("resumable failed run did not send reply to the same run id");
    }
    if (resumeReply.message !== "다시 진행해") {
      failures.push("resumable failed run reply did not preserve retry message");
    }
    if ((resumeReply.answers || [])[0]?.field !== "user_reply") {
      failures.push("resumable failed run retry should be sent as a general user_reply");
    }
    const resumedEventSource = eventSources.find((item) => item.url.includes("run_frontend_resumable_failed"));
    if (!resumedEventSource) {
      failures.push("resumable failed run did not open an EventSource");
    }
    if (resumedEventSource?.url.includes("last_event_id=run_frontend_smoke%3Atrace%3A7")) {
      failures.push(`different run EventSource incorrectly reused previous cursor: ${resumedEventSource.url}`);
    }
    input.value = "length 70 shoulder 50 chest 55 sleeve 22 cotton 100% KRW 59000";
    window.document.querySelector("#chatForm").dispatchEvent(
      new window.Event("submit", { bubbles: true, cancelable: true })
    );
    await delay(20);
    const reply = replyPayloads[1] || {};
    const baselineAnswer = (reply.answers || []).find((item) => item.field === "baseline_evidence");
    if (!baselineAnswer || baselineAnswer.closet_item_id !== "current_liked_item") {
      failures.push("baseline reply did not preserve closet_item_id");
    }
  } else {
    failures.push("renderRun is not available for completed run fallback check");
  }

  const result = {
    status: failures.length ? "failed" : "completed",
    observationStatus,
    traceCount,
    eventSourceCount: 1,
    totalEventSourceCountAfterCompletedFallback: eventSources.length,
    eventSourceClosed: Boolean(eventSources[0] && eventSources[0].closed),
    fetchCalls,
    replyPayloads,
    providerConfigurationMessageShown: messagesText.includes("FITCHECK_SEARCH_PROVIDER=brave_search"),
    searchProviderText,
    ocrProviderText,
    pageFetchProviderText,
    failures,
  };
  console.log(JSON.stringify(result, null, 2));
  if (failures.length) {
    process.exit(1);
  }
}

function payloadEventId(event) {
  try {
    const payload = JSON.parse(event.data || "{}");
    return payload.event_id || "";
  } catch {
    return "";
  }
}

function text(selector, window) {
  const node = window.document.querySelector(selector);
  return node ? node.textContent.trim() : "";
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
