const path = require("node:path");
const { chromium } = require("playwright");

const baseUrl = process.env.FITCHECK_BROWSER_BASE_URL || "http://127.0.0.1:8000";
const screenshotPath = path.resolve(__dirname, "..", "harness_outputs", "browser_ui_smoke.png");

const executionPlan = {
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
  steps: [
    {
      id: "context",
      title: "맥락 정리",
      status: "completed",
      tools: ["plan_context_and_sources"],
      agent_role: "planner",
      execution_policy: "sequential",
      summary: "사용자 입력과 출처를 정리합니다.",
    },
    {
      id: "candidate_search",
      title: "후보 검색",
      status: "completed",
      tools: ["search_web_candidates"],
      agent_role: "researcher",
      execution_policy: "open_world_search",
      summary: "웹 후보를 검색합니다.",
    },
    {
      id: "candidate_enrichment",
      title: "후보별 상세 분석",
      status: "completed",
      parallelizable: true,
      parallel_group: "candidate_analysis",
      tools: ["fetch_product_page", "classify_product_assets", "extract_size_material_info", "normalize_product_spec"],
      tool_statuses: {
        fetch_product_page: "completed",
        classify_product_assets: "completed",
        extract_size_material_info: "completed",
        normalize_product_spec: "completed",
      },
      agent_role: "product_inspector",
      execution_policy: "parallel_candidate_batch",
      summary: "상세페이지와 OCR 근거를 분석합니다.",
    },
    {
      id: "report",
      title: "근거 기반 리포트",
      status: "completed",
      tools: ["write_purchase_report"],
      agent_role: "reporter",
      execution_policy: "trace_grounded_only",
      summary: "관찰된 근거만 보고합니다.",
    },
  ],
};

const completedRunPayload = {
  status: "completed",
  run_id: "run_browser_ui_smoke",
  trace: [
    {
      step_index: 0,
      step_type: "context",
      name: "match_agent_skills",
      status: "completed",
      output: {
        matched_skills: [{ name: "outfit-pairing" }],
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
      input: { arguments: { free_text: "무탠다드 청바지 추천" } },
    },
    {
      step_index: 3,
      step_type: "tool_output",
      name: "plan_context_and_sources",
      status: "completed",
      output: {
        call_id: "call_plan",
        search_constraints: {
          target_item_query: "청바지",
        },
        applied_recipes: [{ id: "outfit_pairing_free_target" }],
      },
    },
    {
      step_index: 4,
      step_type: "tool_output",
      name: "search_web_candidates",
      status: "completed",
      output: { call_id: "call_search", candidate_count: 3 },
    },
    {
      step_index: 5,
      step_type: "runtime",
      name: "execute_parallel_tool_batch",
      status: "completed",
      output: {
        tool: "fetch_product_page",
        call_count: 3,
        parallel_group: "candidate_analysis",
      },
    },
    {
      step_index: 6,
      step_type: "tool_output",
      name: "write_purchase_report",
      status: "completed",
      output: { call_id: "call_report" },
    },
  ],
  state: {
    observations: [
      {
        tool: "score_candidate_match",
        call_id: "call_score",
        result_status: "completed",
        result: {
          status: "completed",
          candidate_key: "all",
          ranked_matches: [
            {
              candidate_key: "musinsa_001",
              recommendation: "need_more_info",
              score: { overall: 61, size: 48, material: 70, price: 66, confidence: 55 },
              missing_fields: ["measurements.length", "measurements.shoulder"],
              evidence_coverage: {
                completed_tools: ["fetch_product_page", "extract_size_material_info"],
                missing_tools: [],
                observed_fields: ["material", "price", "url"],
                critical_missing_fields: ["measurements.length", "measurements.shoulder"],
                recommendation_policy: "critical_missing_fields_force_need_more_info",
              },
            },
          ],
        },
      },
      {
        tool: "write_purchase_report",
        call_id: "call_report",
        result_status: "completed",
        result: {
          status: "completed",
          grounded: true,
          final_report: "기준 옷과 비슷한 후보 shortlist입니다.\n- 후보: http://example.test/products/denim-001\n- 부족한 근거: 실측 length, shoulder",
        },
      },
    ],
    missing_info: [
      {
        field: "baseline_evidence",
        closet_item_id: "current_liked_item",
        missing_fields: "measurements.length, measurements.shoulder",
        question: "기준 옷의 실측 근거가 부족합니다.",
      },
    ],
    execution_plan: executionPlan,
  },
  final_report: {
    summary: "기준 옷과 비슷한 후보 shortlist입니다.\n- 후보: http://example.test/products/denim-001\n- 부족한 근거: 실측 length, shoulder",
    grounded: true,
  },
  llm: {
    status: "connected",
    model: "browser-smoke-model",
    api_style: "responses",
    tool_execution: "backend_owned_functions",
  },
};

const evidencePayload = {
  run_id: "run_browser_ui_smoke",
  status: "completed",
  counts: {
    trace_steps: 7,
    observations: 2,
  },
  context_budget: [
    {
      estimated_tokens: 9120,
      compaction_threshold: 8000,
      remaining_tokens: 1600,
    },
  ],
  context_compactions: [
    {
      reason: "estimated_token_threshold",
      latest_observations: [{ tool: "score_candidate_match", status: "completed" }],
      pending_tool_output_count: 0,
      loaded_skills: ["outfit-pairing"],
    },
  ],
  hook_lifecycle: [
    {
      event: "PostToolUse",
      tool: "write_purchase_report",
      status: "recorded",
      mode: "record_only",
      call_id: "call_report",
    },
  ],
  sdk_like: {
    tool_result_pairing: {
      status: "paired",
      completed_call_count: 3,
      pending_output_count: 0,
    },
    raw_response_ids: ["resp_browser_smoke"],
  },
};

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const failures = [];
  const consoleErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => consoleErrors.push(error.message));

  await page.route("**/api/agent/runs/run_browser_ui_smoke/evidence", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(evidencePayload),
    });
  });

  await page.goto(baseUrl, { waitUntil: "networkidle" });
  await page.waitForFunction(() => typeof window.renderRun === "function");
  await page.evaluate(() => {
    window.EventSource = class {
      constructor() {}
      addEventListener() {}
      close() {}
    };
  });
  await page.evaluate((payload) => window.renderRun(payload), completedRunPayload);
  await page.waitForFunction(() => document.querySelector("#evidenceList")?.textContent?.includes("resp_browser_smoke"));
  await page.screenshot({ path: screenshotPath, fullPage: true });

  const text = async (selector) => (await page.locator(selector).innerText()).trim();
  const bodyText = await text("body");
  const planText = await text("#planList");
  const toolFlowText = await text("#toolFlowList");
  const messagesText = await text("#messages");
  const questionsText = await text("#questionList");
  const evidenceText = await text("#evidenceList");
  const evidenceSummaryChips = await page.locator("#evidenceSummary span").evaluateAll((nodes) =>
    nodes.map((node) => node.textContent.trim().replace(/\s+/g, ""))
  );
  const traceCount = await text("#traceCount");
  const planStatus = await text("#planStatus");

  for (const expected of [
    "후보 검색",
    "후보별 상세 분석",
    "병렬 가능",
    "역할: product_inspector",
    "정책: parallel_candidate_batch",
    "그룹: candidate_analysis",
    "fetch_product_page:completed",
  ]) {
    if (!planText.includes(expected)) {
      failures.push(`plan text missing: ${expected}`);
    }
  }
  for (const expected of [
    "관련 스킬 검색",
    "LLM 판단",
    "도구 결과 저장: search_web_candidates",
    "병렬 도구 실행",
    "group=candidate_analysis",
  ]) {
    if (!toolFlowText.includes(expected)) {
      failures.push(`tool flow missing: ${expected}`);
    }
  }
  for (const expected of [
    "기준 옷과 비슷한 후보 shortlist입니다.",
    "http://example.test/products/denim-001",
    "부족한 근거",
  ]) {
    if (!messagesText.includes(expected)) {
      failures.push(`chat message missing: ${expected}`);
    }
  }
  if (!questionsText.includes("기준 옷의 실측 근거가 부족합니다.")) {
    failures.push("follow-up question missing from Missing Info panel");
  }
  for (const expected of [
    "Context budget",
    "estimated:9120 threshold:8000 remaining:1600",
    "Conversation compacted",
    "PostToolUse: write_purchase_report",
    "paired",
    "resp_browser_smoke",
  ]) {
    if (!evidenceText.includes(expected)) {
      failures.push(`run evidence missing: ${expected}`);
    }
  }
  for (const expected of ["7trace", "2observations", "1hooks", "1context", "1compactions"]) {
    if (!evidenceSummaryChips.includes(expected)) {
      failures.push(`run evidence summary missing: ${expected}`);
    }
  }
  if (planStatus !== "completed") {
    failures.push(`plan status should be completed, got ${planStatus}`);
  }
  if (Number(traceCount) !== completedRunPayload.trace.length) {
    failures.push(`trace count should be ${completedRunPayload.trace.length}, got ${traceCount}`);
  }
  for (const marker of ["占", "�", "筌", "疫", "野", "횄", "횂"]) {
    if (bodyText.includes(marker)) {
      failures.push(`visible mojibake marker found: ${marker}`);
    }
  }
  if (consoleErrors.length) {
    failures.push(`browser console errors: ${consoleErrors.join(" | ")}`);
  }

  await browser.close();
  const result = {
    status: failures.length ? "failed" : "completed",
    baseUrl,
    run_id: completedRunPayload.run_id,
    planStatus,
    traceCount,
    screenshotPath,
    failures,
  };
  console.log(JSON.stringify(result, null, 2));
  if (failures.length) {
    process.exit(1);
  }
})();
