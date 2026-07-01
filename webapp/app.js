const chatForm = document.querySelector("#chatForm");
const chatInput = document.querySelector("#chatInput");
const attachButton = document.querySelector("#attachButton");
const imageInput = document.querySelector("#imageInput");
const attachmentTray = document.querySelector("#attachmentTray");
const sendButton = document.querySelector("#sendButton");
const newChatButton = document.querySelector("#newChatButton");
const messages = document.querySelector("#messages");
const agentStatus = document.querySelector("#agentStatus");
const llmState = document.querySelector("#llmState");
const modelState = document.querySelector("#modelState");
const apiStyleState = document.querySelector("#apiStyleState");
const toolState = document.querySelector("#toolState");
const searchProviderState = document.querySelector("#searchProviderState");
const ocrProviderState = document.querySelector("#ocrProviderState");
const pageFetchProviderState = document.querySelector("#pageFetchProviderState");
const runIdState = document.querySelector("#runIdState");
const streamStatus = document.querySelector("#streamStatus");
const streamCursorState = document.querySelector("#streamCursorState");
const streamSequenceState = document.querySelector("#streamSequenceState");
const planStatus = document.querySelector("#planStatus");
const planList = document.querySelector("#planList");
const toolFlowCount = document.querySelector("#toolFlowCount");
const toolFlowList = document.querySelector("#toolFlowList");
const evidenceStatus = document.querySelector("#evidenceStatus");
const evidenceSummary = document.querySelector("#evidenceSummary");
const evidenceList = document.querySelector("#evidenceList");
const traceCount = document.querySelector("#traceCount");
const traceList = document.querySelector("#traceList");
const observationStatus = document.querySelector("#observationStatus");
const observationPanel = document.querySelector("#observationPanel");
const questionCount = document.querySelector("#questionCount");
const questionList = document.querySelector("#questionList");

let currentRunId = null;
let pendingQuestions = [];
let selectedAttachments = [];
let runEvents = null;
let currentTrace = [];
let currentExecutionPlan = null;
let renderedTerminalEventId = null;
let lastRunEventId = "";
let lastRunEventRunId = "";
let lastRunEventSequence = null;

window.addEventListener("DOMContentLoaded", async () => {
  renderTrace([]);
  renderAgentPlan([]);
  renderToolFlow([]);
  renderEvidence(null);
  renderLatestObservation(null);
  renderQuestions([]);
  addAssistantMessage(
    "마음에 드는 옷의 사진이나 설명을 보내고, 핏·재질·사이즈·디자인 중 어떤 점이 좋은지 알려주세요. Agent가 웹검색과 OCR로 여러 후보를 찾아 비교합니다."
  );
  await refreshStatus();
  chatInput.focus();
});

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = chatInput.value.trim();
  if (!text && !selectedAttachments.length) {
    return;
  }
  const attachments = selectedAttachments.slice();
  selectedAttachments = [];
  renderAttachmentTray();
  chatInput.value = "";
  resizeComposer();
  await sendMessage(text, attachments);
});

chatInput.addEventListener("input", resizeComposer);

attachButton.addEventListener("click", () => {
  imageInput.click();
});

imageInput.addEventListener("change", async () => {
  const files = Array.from(imageInput.files || []);
  imageInput.value = "";
  if (!files.length) {
    return;
  }
  await uploadSelectedImages(files);
});

newChatButton.addEventListener("click", () => {
  closeRunEvents();
  currentRunId = null;
  renderedTerminalEventId = null;
  pendingQuestions = [];
  selectedAttachments = [];
  currentExecutionPlan = null;
  lastRunEventId = "";
  lastRunEventRunId = "";
  lastRunEventSequence = null;
  messages.innerHTML = "";
  renderTrace([]);
  renderAgentPlan([]);
  renderToolFlow([]);
  renderEvidence(null);
  renderLatestObservation(null);
  renderQuestions([]);
  renderAttachmentTray();
  runIdState.textContent = "-";
  renderStreamState("idle");
  addAssistantMessage("새 탐색을 시작합니다. 좋아하는 옷과 마음에 드는 요소를 알려주세요.");
  chatInput.focus();
});

async function sendMessage(text, attachments = []) {
  addUserMessage(text || "사진 첨부", attachments);
  renderedTerminalEventId = null;
  setBusy(true);
  closeRunEvents();
  let keepBusy = false;
  try {
    const payload = currentRunId
      ? await replyToRun(text, attachments)
      : await createRun(text, attachments);
    renderRun(payload);
    keepBusy = payload.status === "running";
  } catch (error) {
    addAssistantMessage(`요청 실패: ${error.message}`, "error");
  } finally {
    if (!keepBusy) {
      setBusy(false);
    }
  }
}

async function createRun(text, attachments = []) {
  const response = await fetch("/api/agent/runs?background=true", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      goal: text || "좋아하는 옷의 요소 기반 유사 의류 탐색",
      free_text: text,
      attachments,
    }),
  });
  return parseJsonResponse(response);
}

async function replyToRun(text, attachments = []) {
  const targetQuestion = selectReplyTargetQuestion(text, pendingQuestions);
  const answers = targetQuestion
    ? [targetQuestion].map((question) => ({
        field: question.field,
        candidate_key: question.candidate_key,
        closet_item_id: question.closet_item_id,
        missing_fields: question.missing_fields,
        value: text || "사진 첨부",
      }))
    : [{ field: "user_reply", value: text || "사진 첨부" }];

  const response = await fetch(`/api/agent/runs/${encodeURIComponent(currentRunId)}/reply?background=true`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answers, message: text, attachments }),
  });
  return parseJsonResponse(response);
}

function selectReplyTargetQuestion(text, questions) {
  if (!questions.length) {
    return null;
  }
  if (questions.length === 1) {
    return questions[0];
  }
  const normalized = String(text || "").toLowerCase();
  const baselineQuestion = questions.find((question) => question.field === "baseline_evidence");
  const candidateQuestion = questions.find((question) => question.field === "unresolved_evidence" && question.candidate_key);
  if (baselineQuestion && /(baseline|기준|내\s*옷|예전|구매|샀|owned|closet)/i.test(normalized)) {
    return baselineQuestion;
  }
  if (candidateQuestion && /(candidate|후보|상품|url|http|https|상세)/i.test(normalized)) {
    return candidateQuestion;
  }
  return baselineQuestion || questions[0];
}

async function uploadSelectedImages(files) {
  const imageFiles = files.filter((file) => file.type.startsWith("image/"));
  if (!imageFiles.length) {
    addAssistantMessage("이미지 파일만 첨부할 수 있습니다.", "error");
    return;
  }
  const remainingSlots = Math.max(0, 4 - selectedAttachments.length);
  const filesToUpload = imageFiles.slice(0, remainingSlots);
  if (!filesToUpload.length) {
    addAssistantMessage("이미지는 최대 4장까지 첨부할 수 있습니다.", "error");
    return;
  }

  setBusy(true);
  try {
    for (const file of filesToUpload) {
      const dataUrl = await readFileAsDataUrl(file);
      const response = await fetch("/api/uploads/images", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename: file.name,
          media_type: file.type,
          data_url: dataUrl,
          role: "current_clothing_photo",
        }),
      });
      const payload = await parseJsonResponse(response);
      if (payload.status !== "completed") {
        throw new Error(payload.error?.message || "이미지 업로드 실패");
      }
      selectedAttachments.push(payload.attachment);
    }
    renderAttachmentTray();
  } catch (error) {
    addAssistantMessage(`이미지 업로드 실패: ${error.message}`, "error");
  } finally {
    setBusy(false);
    chatInput.focus();
  }
}

async function refreshStatus() {
  try {
    const response = await fetch("/api/status");
    const payload = await parseJsonResponse(response);
    renderStatus(payload);
  } catch (error) {
    agentStatus.textContent = "offline";
    llmState.textContent = "unknown";
    modelState.textContent = "-";
    apiStyleState.textContent = "-";
    toolState.textContent = "-";
    searchProviderState.textContent = "-";
    ocrProviderState.textContent = "-";
    pageFetchProviderState.textContent = "-";
  }
}

function renderStatus(payload) {
  const llm = payload.llm || {};
  const providers = payload.providers || {};
  agentStatus.textContent = payload.status || "unknown";
  llmState.textContent = llm.status || "unknown";
  modelState.textContent = llm.model || "-";
  apiStyleState.textContent = llm.api_style || "-";
  toolState.textContent = llm.tool_execution || "-";
  searchProviderState.textContent = formatSearchProviderStatus(providers.search || {});
  ocrProviderState.textContent = formatOcrProviderStatus(providers.ocr || {});
  pageFetchProviderState.textContent = formatPageFetchProviderStatus(providers.page_fetch || {});
  agentStatus.className = statusClass(payload.status);
  llmState.className = statusClass(llm.status);
  searchProviderState.className = providerStatusClass(providers.search || {});
  ocrProviderState.className = providerStatusClass(providers.ocr || {});
  pageFetchProviderState.className = providerStatusClass(providers.page_fetch || {});
}

function renderRun(payload) {
  if (payload.status === "configuration_required") {
    if (payload.run_id) {
      updateCurrentRunId(payload.run_id);
      runIdState.textContent = payload.run_id;
    }
    renderConfigurationRequired(payload);
    return;
  }

  if (payload.run_id) {
    updateCurrentRunId(payload.run_id);
    runIdState.textContent = payload.run_id;
    subscribeRunEvents(payload.run_id);
  }

  agentStatus.textContent = payload.status || "unknown";
  agentStatus.className = statusClass(payload.status);
  if (payload.llm) {
    llmState.textContent = payload.llm.status || "-";
    llmState.className = statusClass(payload.llm.status);
    modelState.textContent = payload.llm.model || "-";
    apiStyleState.textContent = payload.llm.api_style || "-";
    toolState.textContent = payload.llm.tool_execution || "-";
  }

  renderTrace(payload.trace || []);
  renderExecutionPlanFromPayload(payload);
  renderLatestObservation(displayObservation(payload));
  const questions = payload.state?.missing_info || [];
  renderQuestions(questions);
  fetchAndRenderEvidence(payload.run_id, payload.status);

  if (payload.status === "waiting_for_user") {
    addAssistantMessage(formatQuestionsMessage(questions), "question");
    return;
  }

  if (payload.status === "completed") {
    const report = payload.final_report?.summary || "완료됐지만 final_report가 없습니다.";
    addAssistantMessage(report);
    return;
  }

  if (payload.status === "failed") {
    addAssistantMessage(formatFailureMessage(payload), "error", true);
    return;
  }

  if (payload.status !== "running") {
    addAssistantMessage(`Agent 상태: ${payload.status || "unknown"}`);
  }
}

function renderConfigurationRequired(payload) {
  updateCurrentRunId(payload.run_id || null);
  runIdState.textContent = payload.run_id || "-";
  if (payload.run_id) {
    subscribeRunEvents(payload.run_id);
  }
  const llm = payload.llm || {};
  const error = payload.error || {};
  agentStatus.textContent = "configuration_required";
  agentStatus.className = statusClass("configuration_required");
  llmState.textContent = llm.status || "configuration_required";
  llmState.className = statusClass(llm.status || "configuration_required");
  modelState.textContent = llm.model || "-";
  apiStyleState.textContent = llm.api_style || "-";
  toolState.textContent = llm.tool_execution || "-";
  renderTrace(payload.trace || []);
  renderExecutionPlanFromPayload(payload);
  renderLatestObservation(displayObservation(payload));
  renderQuestions([]);
  fetchAndRenderEvidence(payload.run_id, payload.status);

  if (isProviderConfigurationError(error)) {
    const required = (payload.state?.observations || [])
      .flatMap((item) => item.result?.required_configuration || [])
      .map((item) => `- ${escapeHtml(item)}`)
      .join("<br>");
    const code = error.provider_error_code || error.code || "PROVIDER_CONFIGURATION_REQUIRED";
    addAssistantMessage(
      `
        <strong>실제 검색 provider 설정이 필요합니다.</strong>
        <span>Agent는 fallback 후보를 production 결과처럼 사용하지 않습니다. 검색 provider 설정을 완료한 뒤 실제 후보 탐색을 다시 실행해야 합니다.</span>
        <code>${escapeHtml(code)}</code>
        <code>FITCHECK_SEARCH_PROVIDER=brave_search</code>
        <code>BRAVE_SEARCH_API_KEY</code>
        <code>FITCHECK_SEARCH_PROVIDER=tavily_search</code>
        <code>TAVILY_API_KEY</code>
        <code>FITCHECK_SEARCH_PROVIDER=searxng</code>
        <code>FITCHECK_SEARXNG_URL</code>
        <code>FITCHECK_SEARCH_PROVIDER=jina_search</code>
        <code>FITCHECK_SEARCH_PROVIDER=duckduckgo_html</code>
        <code>FITCHECK_SEARCH_PROVIDER=google_cse legacy only</code>
        ${required ? `<span class="missing-block">${required}</span>` : ""}
      `,
      "warning",
      true
    );
    return;
  }

  const missing = (llm.missing || [])
    .map((item) => `- ${escapeHtml(item)}`)
    .join("<br>");
  addAssistantMessage(
    `
      <strong>LLM 설정이 필요합니다.</strong>
      <span>이 route는 production AI agent라서 LLM 없이 fallback으로 실행하지 않습니다.</span>
      <code>OPENAI_API_KEY + FITCHECK_LLM_MODEL</code>
      <span>또는</span>
      <code>FITCHECK_LLM_BASE_URL + FITCHECK_LLM_API_KEY + FITCHECK_LLM_MODEL</code>
      ${missing ? `<span class="missing-block">${missing}</span>` : ""}
    `,
    "warning",
    true
  );
}

function formatFailureMessage(payload) {
  const error = payload.error || {};
  const providerCode = error.provider_error_code || error.code || "unknown";
  const category = error.provider_error_category || "";
  const message = error.message || error.code || "unknown";
  if (error.resume_available) {
    const reason =
      category === "rate_limit"
        ? "외부 LLM API 사용량 제한으로 일시 중단됐습니다."
        : category === "timeout"
          ? "외부 LLM 응답 시간이 초과되어 일시 중단됐습니다."
          : "외부 LLM 호출이 중단됐지만 이어서 진행할 수 있습니다.";
    const pendingCount = Number(error.pending_tool_output_count || 0);
    return `
      <strong>${escapeHtml(reason)}</strong>
      <span>저장된 tool output은 보존되어 있습니다. 잠시 뒤 메시지를 다시 보내면 같은 run에서 이어서 진행합니다.</span>
      <code>${escapeHtml(providerCode)}</code>
      ${pendingCount ? `<code>pending_tool_outputs=${pendingCount}</code>` : ""}
    `;
  }
  return `
    <strong>실행 실패</strong>
    <span>${escapeHtml(message)}</span>
    <code>${escapeHtml(providerCode)}</code>
  `;
}

function subscribeRunEvents(runId) {
  if (!runId || !window.EventSource) {
    return;
  }
  closeRunEvents();
  const cursor = lastRunEventId && lastRunEventRunId === runId
    ? `?last_event_id=${encodeURIComponent(lastRunEventId)}`
    : "";
  runEvents = new EventSource(`/api/agent/runs/${encodeURIComponent(runId)}/events${cursor}`);
  renderStreamState(cursor ? "replaying" : "connected");
  runEvents.addEventListener("agent_state", (event) => {
    try {
      const payload = JSON.parse(event.data);
      rememberRunEventCursor(event, payload);
      renderRunEvent(payload);
    } catch (error) {
      renderLatestObservation({
        tool: "event_stream",
        result: {
          status: "failed",
          error: { code: "SSE_PARSE_FAILED", message: error.message },
        },
      });
    }
  });
  runEvents.addEventListener("error", () => {
    if (runEvents) {
      runEvents.close();
      runEvents = null;
    }
    renderStreamState("disconnected");
  });
}

function closeRunEvents() {
  if (runEvents) {
    runEvents.close();
    runEvents = null;
  }
}

function rememberRunEventCursor(event, payload) {
  const eventId = event.lastEventId || payload.event_id || "";
  if (eventId) {
    lastRunEventId = eventId;
    lastRunEventRunId = payload.run_id || currentRunId || "";
  }
  if (payload.event_sequence !== undefined && payload.event_sequence !== null) {
    lastRunEventSequence = payload.event_sequence;
  }
  renderStreamState("connected");
}

function updateCurrentRunId(runId) {
  if (currentRunId && runId && currentRunId !== runId) {
    lastRunEventId = "";
    lastRunEventRunId = "";
    lastRunEventSequence = null;
  }
  currentRunId = runId;
}

function renderStreamState(status) {
  if (!streamStatus || !streamCursorState || !streamSequenceState) {
    return;
  }
  streamStatus.textContent = status;
  streamStatus.className = statusClass(status);
  streamCursorState.textContent = lastRunEventId || "-";
  streamSequenceState.textContent = lastRunEventSequence === null ? "-" : String(lastRunEventSequence);
}

function renderRunEvent(payload) {
  agentStatus.textContent = payload.status || "unknown";
  agentStatus.className = statusClass(payload.status);
  renderTraceFromEvent(payload);
  renderExecutionPlanFromPayload(payload);
  renderLatestObservation(payload.latest_observation || null);
  renderQuestions(payload.missing_info || []);
  if (isTerminalStatus(payload.status)) {
    renderTerminalRunEvent(payload);
    fetchAndRenderEvidence(payload.run_id, payload.status);
    setBusy(false);
    closeRunEvents();
    renderStreamState("closed");
  }
}

function renderTraceFromEvent(payload) {
  const latest = payload.trace_delta || payload.latest_trace || [];
  if (!latest.length) {
    traceCount.textContent = String(payload.trace_count || 0);
    renderAgentPlan(currentTrace, payload.status, currentExecutionPlan);
    renderToolFlow(currentTrace);
    return;
  }
  traceCount.textContent = String(payload.trace_count || latest.length);
  const existing = Array.from(traceList.querySelectorAll("[data-step-index]")).map((item) => item.dataset.stepIndex);
  const next = latest.filter((step) => !existing.includes(String(step.step_index)));
  if (!next.length) {
    return;
  }
  if (traceList.querySelector(".empty-line")) {
    traceList.innerHTML = "";
  }
  traceList.insertAdjacentHTML("beforeend", next.map(renderTraceItem).join(""));
  currentTrace = mergeTrace(currentTrace, next);
  renderAgentPlan(currentTrace, payload.status, currentExecutionPlan);
  renderToolFlow(currentTrace);
}

function renderTerminalRunEvent(payload) {
  const eventId = payload.event_id || `${payload.run_id}:${payload.status}:${payload.trace_count}`;
  if (renderedTerminalEventId === eventId) {
    return;
  }
  renderedTerminalEventId = eventId;
  if (payload.status === "completed") {
    addAssistantMessage(payload.final_report?.summary || "탐색이 완료됐습니다.");
    return;
  }
  if (payload.status === "waiting_for_user") {
    addAssistantMessage(formatQuestionsMessage(payload.missing_info || []), "question");
    return;
  }
  if (payload.status === "failed") {
    addAssistantMessage(formatFailureMessage(payload), "error", true);
  }
}

function renderTrace(trace) {
  currentTrace = Array.isArray(trace) ? trace.slice() : [];
  traceCount.textContent = String(trace.length);
  if (!trace.length) {
    traceList.innerHTML = '<li class="empty-line">아직 trace가 없습니다.</li>';
    renderAgentPlan([], "not_started", currentExecutionPlan);
    renderToolFlow([]);
    return;
  }
  traceList.innerHTML = trace
    .map(renderTraceItem)
    .join("");
  renderAgentPlan(currentTrace, "", currentExecutionPlan);
  renderToolFlow(currentTrace);
}

function renderTraceItem(step) {
  return `
    <li data-step-index="${escapeHtml(step.step_index)}">
      <span>${String(step.step_index).padStart(2, "0")}</span>
      <div>
        <strong>${escapeHtml(step.name)}</strong>
        <small>${escapeHtml(step.step_type)} · ${escapeHtml(step.status)}</small>
      </div>
    </li>
  `;
}

function mergeTrace(existing, next) {
  const byIndex = new Map(existing.map((step) => [String(step.step_index), step]));
  for (const step of next) {
    byIndex.set(String(step.step_index), step);
  }
  return Array.from(byIndex.values()).sort((a, b) => Number(a.step_index || 0) - Number(b.step_index || 0));
}

function renderExecutionPlanFromPayload(payload) {
  const plan = payload?.state?.execution_plan || payload?.execution_plan || null;
  if (plan && Array.isArray(plan.steps)) {
    currentExecutionPlan = plan;
  }
  renderAgentPlan(currentTrace, payload?.status || "", currentExecutionPlan);
}

function renderAgentPlan(trace, runStatus = "", executionPlan = null) {
  const plan = executionPlan?.steps?.length
    ? buildAgentPlanFromExecutionPlan(executionPlan)
    : buildAgentPlan(trace, runStatus);
  const completed = plan.filter((item) => item.status === "done" || item.status === "skipped").length;
  const current = plan.find((item) => item.status === "current");
  const planState = executionPlan?.status;
  planStatus.textContent = planState || (current ? "running" : completed === plan.length && plan.length ? "done" : "-");
  planStatus.className = statusClass(planState || (current ? "running" : completed === plan.length && plan.length ? "completed" : ""));
  if (!plan.length) {
    planList.innerHTML = '<li class="empty-line">아직 실행 계획이 없습니다.</li>';
    return;
  }
  planList.innerHTML = plan
    .map((item, index) => `
      <li class="${escapeHtml(item.status)}">
        <span>${String(index + 1).padStart(2, "0")}</span>
        <div>
          <strong>${escapeHtml(item.title)}</strong>
          <small>${escapeHtml(item.detail || item.status)}${item.parallel ? " · 병렬 가능" : ""}</small>
        </div>
      </li>
    `)
    .join("");
}

function buildAgentPlanFromExecutionPlan(executionPlan) {
  return (executionPlan.steps || []).map((step) => ({
    key: step.id,
    title: step.title || step.id || "step",
    detail: executionPlanStepDetail(step),
    status: normalizePlanItemStatus(step.status),
    parallel: Boolean(step.parallelizable),
  }));
}

function executionPlanStepDetail(step) {
  const tools = Array.isArray(step.tools) ? step.tools.join(", ") : "";
  const selectedSkills = Array.isArray(step.selected_skills)
    ? step.selected_skills.filter(Boolean).join(", ")
    : "";
  const parallelGroup = step.parallel_group ? `그룹: ${step.parallel_group}` : "";
  const agentRole = step.agent_role ? `역할: ${step.agent_role}` : "";
  const executionPolicy = step.execution_policy ? `정책: ${step.execution_policy}` : "";
  const statuses = step.tool_statuses && typeof step.tool_statuses === "object"
    ? Object.entries(step.tool_statuses).map(([tool, status]) => `${tool}:${status}`).join(", ")
    : "";
  return [
    step.summary,
    agentRole,
    executionPolicy,
    selectedSkills ? `스킬: ${selectedSkills}` : "",
    parallelGroup,
    statuses || tools,
  ].filter(Boolean).join(" · ");
}

function normalizePlanItemStatus(status) {
  if (status === "completed") {
    return "done";
  }
  if (status === "in_progress" || status === "running") {
    return "current";
  }
  if (status === "skipped") {
    return "skipped";
  }
  if (status === "failed") {
    return "failed";
  }
  return "pending";
}

function buildAgentPlan(trace, runStatus = "") {
  const completedTools = new Set(
    trace
      .filter((step) => ["tool_output", "tool_observation"].includes(step.step_type) && step.status !== "started")
      .map((step) => step.name)
  );
  const activeTool = findActiveToolCall(trace);
  const failed = trace.some((step) => step.status === "failed") || runStatus === "failed";
  const steps = [
    {
      key: "skill",
      title: "스킬/SOP 선택",
      detail: skillContextDetail(trace),
      tools: ["load_agent_skill"],
    },
    {
      key: "context",
      title: "맥락과 출처 정리",
      detail: planContextDetail(trace),
      tools: ["plan_context_and_sources", "parse_user_goal"],
    },
    {
      key: "baseline",
      title: "기준 아이템 근거 확보",
      detail: "URL, 구매 힌트, 사진/OCR, 옷장 메모리 확인",
      tools: ["load_closet_memory", "enrich_closet_item_from_web", "inspect_uploaded_images"],
    },
    {
      key: "search",
      title: "웹 후보 검색",
      detail: "검색 provider로 여러 상품 후보 수집",
      tools: ["search_web_candidates", "register_user_candidate_url"],
    },
    {
      key: "enrich",
      title: "상세페이지/OCR/정규화",
      detail: "상세 fetch, 이미지 분류, OCR, 사이즈/소재/가격 정규화",
      tools: ["fetch_product_page", "classify_product_assets", "extract_size_material_info", "normalize_product_spec"],
    },
    {
      key: "score",
      title: "비교와 점수화",
      detail: "관찰된 근거만 사용해 후보별 적합도 계산",
      tools: ["score_candidate_match"],
    },
    {
      key: "report",
      title: "근거 기반 리포트",
      detail: "최종 추천/보류 사유와 필요한 추가 정보 정리",
      tools: ["write_purchase_report", "ask_missing_info"],
    },
  ];
  const doneIndexes = steps
    .map((step, index) => step.tools.some((tool) => completedTools.has(tool)) ? index : -1)
    .filter((index) => index >= 0);
  const furthestDone = doneIndexes.length ? Math.max(...doneIndexes) : -1;
  const activeIndex = activeTool
    ? steps.findIndex((step) => step.tools.includes(activeTool.name))
    : -1;
  const terminal = ["completed", "waiting_for_user", "configuration_required"].includes(runStatus);

  return steps.map((step, index) => {
    const done = step.tools.some((tool) => completedTools.has(tool))
      || (step.key === "report" && terminal);
    if (done) {
      return { ...step, status: "done" };
    }
    if (activeIndex === index) {
      return { ...step, status: "current" };
    }
    if (index < Math.max(activeIndex, furthestDone)) {
      return { ...step, status: "skipped" };
    }
    if (!failed && runStatus === "running" && activeIndex < 0 && index === furthestDone + 1) {
      return { ...step, status: "current" };
    }
    return { ...step, status: "pending" };
  });
}

function skillContextDetail(trace) {
  const matchStep = [...trace].reverse().find((step) => step.name === "match_agent_skills");
  const output = matchStep?.output_payload || matchStep?.output || {};
  const names = (output.matched_skills || []).map((skill) => skill.name).filter(Boolean);
  if (names.length) {
    return `matched=${names.join(", ")} · 필요한 SOP만 전체 로드`;
  }
  return "스킬 이름·설명으로 관련 SOP 검색";
}

function findActiveToolCall(trace) {
  const completedCallIds = new Set(
    trace
      .filter((step) => step.step_type === "tool_output")
      .map((step) => String((step.output || step.output_payload || {}).call_id || ""))
      .filter(Boolean)
  );
  for (let index = trace.length - 1; index >= 0; index -= 1) {
    const step = trace[index];
    if (!["tool_call", "model_tool_call", "tool_execution"].includes(step.step_type) || step.status !== "started") {
      continue;
    }
    const callId = String((step.input || step.input_payload || {}).call_id || "");
    if (!callId || !completedCallIds.has(callId)) {
      return step;
    }
  }
  return null;
}

function planContextDetail(trace) {
  const planStep = [...trace].reverse().find((step) => step.name === "plan_context_and_sources");
  const output = planStep?.output_payload || planStep?.output || {};
  const constraints = output.search_constraints || {};
  const target = constraints.target_item_query || constraints.target_category_query_label || constraints.query_hint;
  const recipes = (output.applied_recipes || []).map((recipe) => recipe.id).filter(Boolean);
  if (recipes.length && target) {
    return `recipe=${recipes.join(", ")} · 목표: ${target}`;
  }
  if (recipes.length) {
    return `recipe=${recipes.join(", ")}`;
  }
  if (target) {
    return `목표: ${target}`;
  }
  return "사용자 입력, URL, 사진, 이전 답변 분류";
}

function renderToolFlow(trace) {
  const flow = trace.filter((step) =>
    ["context", "runtime", "llm_response", "tool_call", "model_tool_call", "tool_execution", "tool_output", "tool_observation", "tool_guidance", "guardrail"].includes(step.step_type)
  );
  toolFlowCount.textContent = String(flow.length);
  if (!flow.length) {
    toolFlowList.innerHTML = '<li class="empty-line">아직 도구 실행 흐름이 없습니다.</li>';
    return;
  }
  const latestIndex = String(flow[flow.length - 1]?.step_index ?? "");
  const pinnedFlow = flow.filter((step) => step.step_type === "runtime" && step.name === "execute_parallel_tool_batch");
  const visibleFlow = uniqueByStepIndex([...pinnedFlow, ...flow.slice(-18)])
    .sort((left, right) => Number(left.step_index || 0) - Number(right.step_index || 0));
  toolFlowList.innerHTML = visibleFlow
    .map((step) => {
      const css = String(step.step_index) === latestIndex && !["completed", "failed", "waiting_for_user"].includes(step.status)
        ? "current"
        : step.status === "completed"
          ? "done"
          : "";
      return `
        <li class="${escapeHtml(css)}">
          <span>${String(step.step_index).padStart(2, "0")}</span>
          <div>
            <strong>${escapeHtml(flowTitle(step))}</strong>
            <small>${escapeHtml(flowDetail(step))}</small>
          </div>
        </li>
      `;
    })
    .join("");
}

async function fetchAndRenderEvidence(runId, runStatus = "") {
  if (!runId || !evidenceList) {
    return;
  }
  try {
    const response = await fetch(`/api/agent/runs/${encodeURIComponent(runId)}/evidence`);
    const payload = await parseJsonResponse(response);
    renderEvidence(payload, runStatus);
  } catch (error) {
    renderEvidence({
      error: {
        code: "EVIDENCE_FETCH_FAILED",
        message: error.message,
      },
    }, runStatus);
  }
}

function renderEvidence(payload, runStatus = "") {
  if (!evidenceStatus || !evidenceSummary || !evidenceList) {
    return;
  }
  if (!payload) {
    evidenceStatus.textContent = "-";
    evidenceStatus.className = "";
    evidenceSummary.innerHTML = "";
    evidenceList.innerHTML = '<li class="empty-line">아직 run evidence가 없습니다.</li>';
    return;
  }
  if (payload.error) {
    evidenceStatus.textContent = "unavailable";
    evidenceStatus.className = statusClass("configuration_required");
    evidenceSummary.innerHTML = `<span>${escapeHtml(payload.error.code || "EVIDENCE_FETCH_FAILED")}</span>`;
    evidenceList.innerHTML = `<li class="empty-line">${escapeHtml(payload.error.message || "evidence를 불러오지 못했습니다.")}</li>`;
    return;
  }

  const counts = payload.counts || {};
  const contextBudget = latestItem(payload.context_budget);
  const compactions = Array.isArray(payload.context_compactions) ? payload.context_compactions : [];
  const hookRecords = Array.isArray(payload.hook_lifecycle) ? payload.hook_lifecycle : [];
  const rawResponseIds = payload.sdk_like?.raw_response_ids || [];
  const toolPairing = payload.sdk_like?.tool_result_pairing || {};
  const status = runStatus || payload.status || "evidence";
  evidenceStatus.textContent = status;
  evidenceStatus.className = statusClass(status);
  evidenceSummary.innerHTML = [
    evidenceChip("trace", counts.trace_steps),
    evidenceChip("observations", counts.observations),
    evidenceChip("hooks", hookRecords.length),
    evidenceChip("context", Array.isArray(payload.context_budget) ? payload.context_budget.length : 0),
    evidenceChip("compactions", compactions.length),
  ].join("");

  const rows = [
    contextBudget ? {
      type: "Context",
      title: "Context budget",
      detail: formatContextBudget(contextBudget),
    } : null,
    latestItem(compactions) ? {
      type: "Compact",
      title: "Conversation compacted",
      detail: formatCompaction(latestItem(compactions)),
    } : null,
    latestItem(hookRecords) ? {
      type: "Hook",
      title: formatHookTitle(latestItem(hookRecords)),
      detail: formatHookDetail(latestItem(hookRecords)),
    } : null,
    toolPairing.status ? {
      type: "Pairing",
      title: "Tool result pairing",
      detail: formatToolPairing(toolPairing),
    } : null,
    rawResponseIds.length ? {
      type: "Response",
      title: "Provider responses",
      detail: rawResponseIds.slice(-3).join(", "),
    } : null,
  ].filter(Boolean);

  if (!rows.length) {
    evidenceList.innerHTML = '<li class="empty-line">기록된 SDK-like evidence가 없습니다.</li>';
    return;
  }
  evidenceList.innerHTML = rows
    .map((row) => `
      <li>
        <span>${escapeHtml(row.type)}</span>
        <div>
          <strong>${escapeHtml(row.title)}</strong>
          <small>${escapeHtml(row.detail)}</small>
        </div>
      </li>
    `)
    .join("");
}

function evidenceChip(label, value) {
  return `<span><strong>${escapeHtml(value ?? 0)}</strong>${escapeHtml(label)}</span>`;
}

function latestItem(items) {
  return Array.isArray(items) && items.length ? items[items.length - 1] : null;
}

function formatContextBudget(item) {
  const estimated = item.estimated_tokens ?? item.estimated_input_tokens ?? "-";
  const threshold = item.compaction_threshold ?? item.threshold ?? "-";
  const remaining = item.remaining_tokens ?? "-";
  return `estimated:${estimated} threshold:${threshold} remaining:${remaining}`;
}

function formatCompaction(item) {
  const reason = item.reason || item.trigger || "threshold";
  const pendingCount = item.pending_tool_output_count ?? item.pending_tool_outputs?.length ?? 0;
  const observations = Array.isArray(item.latest_observations) ? item.latest_observations.length : 0;
  const skills = Array.isArray(item.loaded_skills) ? item.loaded_skills.join(",") : "";
  return `reason:${reason} observations:${observations} pending_tool_outputs:${pendingCount}${skills ? ` skills:${skills}` : ""}`;
}

function formatHookTitle(item) {
  const event = item.event || item.hook_event || "hook";
  const tool = item.tool || item.tool_name || "-";
  return `${event}: ${tool}`;
}

function formatHookDetail(item) {
  const mode = item.mode || "record_only";
  const status = item.status || "recorded";
  const callId = item.call_id || item.tool_call_id || "-";
  return `${status} · mode:${mode} · call:${callId}`;
}

function formatToolPairing(pairing) {
  const pending = pairing.pending_output_count ?? pairing.pending_tool_output_count ?? 0;
  const completed = pairing.completed_call_count ?? pairing.completed_tool_call_count ?? 0;
  return `${pairing.status} · completed:${completed} pending:${pending}`;
}

function uniqueByStepIndex(steps) {
  const seen = new Set();
  const unique = [];
  for (const step of steps) {
    const key = String(step.step_index ?? `${step.step_type}:${step.name}:${unique.length}`);
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    unique.push(step);
  }
  return unique;
}

function flowTitle(step) {
  if (step.step_type === "context" && step.name === "match_agent_skills") {
    return "관련 스킬 검색";
  }
  if (step.step_type === "llm_response") {
    return "LLM 판단";
  }
  if (step.step_type === "runtime" && step.name === "execute_parallel_tool_batch") {
    return "병렬 도구 실행";
  }
  if (step.step_type === "runtime") {
    return `런타임 실행: ${step.name}`;
  }
  if (step.step_type === "model_tool_call") {
    return `모델이 도구 선택: ${step.name}`;
  }
  if (step.step_type === "tool_call") {
    return `모델이 도구 선택: ${step.name}`;
  }
  if (step.step_type === "tool_execution") {
    return `백엔드 도구 실행: ${step.name}`;
  }
  if (step.step_type === "tool_observation") {
    return `관찰 저장: ${step.name}`;
  }
  if (step.step_type === "tool_output") {
    return `도구 결과 저장: ${step.name}`;
  }
  if (step.step_type === "tool_guidance") {
    return `백엔드 가이드: ${step.name}`;
  }
  if (step.step_type === "guardrail") {
    return `가드레일: ${step.name}`;
  }
  return step.name || step.step_type || "step";
}

function flowDetail(step) {
  const output = step.output_payload || step.output || {};
  const input = step.input_payload || step.input || {};
  const args = output.arguments || input.arguments || {};
  const query = output.query || args.query || output.search_query_hint || "";
  const count = output.tool_call_count ?? output.call_count ?? input.call_count ?? output.candidate_count ?? output.observation_count;
  const parallelGroup = output.parallel_group || input.parallel_group || "";
  const tool = output.tool || input.tool || "";
  const skills = (output.matched_skills || []).map((skill) => skill.name).filter(Boolean);
  const parts = [step.status, tool ? `tool=${tool}` : "", parallelGroup ? `group=${parallelGroup}` : "", query ? `query=${query}` : "", count !== undefined ? `count=${count}` : ""];
  if (skills.length) {
    parts.push(`skills=${skills.join(",")}`);
  }
  return parts.filter(Boolean).join(" · ");
}

function renderLatestObservation(observation) {
  if (!observation) {
    observationStatus.textContent = "-";
    observationStatus.className = "";
    observationPanel.className = "observation-panel empty-line";
    observationPanel.textContent = "아직 tool observation이 없습니다.";
    return;
  }
  const result = observation.result || {};
  const error = result.error || {};
  const retryBadge = (observation.retry_badges || result.retry_badges || [])[0] || {};
  const providerErrorCode = observation.provider_error_code || result.provider_error_code || error.code || "";
  const providerErrorCategory = observation.provider_error_category || result.provider_error_category || "";
  const provider = observation.provider || result.provider || result.provider_mode || "-";
  const status = result.status || observation.result_status || "unknown";
  const queryQuality = formatQueryQuality(result.query_quality || observation.query_quality || {});
  const domainPolicy = formatDomainPolicy(result.domain_policy || observation.domain_policy || {});
  const searchAttempt = formatSearchAttempt(result.search_attempt || observation.search_attempt || {});
  const coverage = formatEvidenceCoverage(result);
  const fallback = formatFetchFallback(result);
  const assets = formatAssetSummary(result);
  const ocr = formatOcrSummary(result);
  const spec = formatSpecSummary(result);
  const memory = formatMemorySummary(result);
  observationStatus.textContent = status;
  observationStatus.className = statusClass(status);
  observationPanel.className = `observation-panel ${statusClass(status)}`.trim();
  observationPanel.innerHTML = `
    <dl>
      <div><dt>Tool</dt><dd>${escapeHtml(observation.tool || "-")}</dd></div>
      <div><dt>Status</dt><dd>${escapeHtml(status)}</dd></div>
      <div><dt>Provider</dt><dd>${escapeHtml(provider)}</dd></div>
      <div><dt>Error</dt><dd>${escapeHtml(providerErrorCode || "-")}</dd></div>
      <div><dt>Category</dt><dd>${escapeHtml(providerErrorCategory || "-")}</dd></div>
      <div><dt>Retry</dt><dd>${escapeHtml(retryBadge.status || "-")}</dd></div>
      <div><dt>Attempt</dt><dd>${escapeHtml(searchAttempt)}</dd></div>
      <div><dt>Fallback</dt><dd>${escapeHtml(fallback)}</dd></div>
      <div><dt>Assets</dt><dd>${escapeHtml(assets)}</dd></div>
      <div><dt>OCR</dt><dd>${escapeHtml(ocr)}</dd></div>
      <div><dt>Spec</dt><dd>${escapeHtml(spec)}</dd></div>
      <div><dt>Memory</dt><dd>${escapeHtml(memory)}</dd></div>
      <div><dt>Query</dt><dd>${escapeHtml(queryQuality)}</dd></div>
      <div><dt>Domain</dt><dd>${escapeHtml(domainPolicy)}</dd></div>
      <div><dt>Coverage</dt><dd>${escapeHtml(coverage)}</dd></div>
    </dl>
  `;
}

function lastObservation(payload) {
  const observations = payload.state?.observations || [];
  return observations.length ? observations[observations.length - 1] : null;
}

function displayObservation(payload) {
  const latest = lastObservation(payload);
  if (!latest || latest.tool !== "write_purchase_report") {
    return latest;
  }
  const observations = payload.state?.observations || [];
  for (let index = observations.length - 1; index >= 0; index -= 1) {
    if (observations[index]?.tool === "score_candidate_match") {
      return observations[index];
    }
  }
  return latest;
}

function isTerminalStatus(status) {
  return ["completed", "failed", "configuration_required", "invalid_request", "not_found", "waiting_for_user"].includes(status);
}

function isProviderConfigurationError(error) {
  const code = String(error?.provider_error_code || error?.code || "");
  const category = String(error?.provider_error_category || "");
  return (
    category === "configuration" &&
    !code.startsWith("LLM_")
  ) || [
    "LIVE_SEARCH_PROVIDER_REQUIRED",
    "GOOGLE_CSE_CONFIGURATION_REQUIRED",
    "SEARCH_PROVIDER_API_KEY_REQUIRED",
    "SEARCH_PROVIDER_BASE_URL_REQUIRED",
    "OCR_PROVIDER_CONFIGURATION_REQUIRED",
    "OCR_TESSERACT_CLI_NOT_FOUND",
  ].includes(code);
}

function formatSearchProviderStatus(search) {
  const provider = search.provider || search.selected || "-";
  const gaps = [];
  if (search.requires_api_key && !search.api_key_configured) {
    gaps.push("key");
  }
  if (search.requires_search_engine_id && !search.search_engine_id_configured) {
    gaps.push("cx");
  }
  if (search.requires_base_url && !search.base_url_configured) {
    gaps.push("url");
  }
  const mode = search.mode ? ` ${search.mode}` : "";
  return gaps.length ? `${provider}${mode} missing ${gaps.join("/")}` : `${provider}${mode}`;
}

function formatQueryQuality(queryQuality) {
  if (!queryQuality || !queryQuality.quality) {
    return "-";
  }
  const score = typeof queryQuality.score === "number" ? ` ${queryQuality.score}` : "";
  return `${queryQuality.quality}${score}`;
}

function formatSearchAttempt(searchAttempt) {
  if (!searchAttempt || !searchAttempt.attempt_index) {
    return "-";
  }
  const repeated = searchAttempt.repeated_query ? " repeated" : "";
  const previous = typeof searchAttempt.previous_query_count === "number"
    ? ` prev:${searchAttempt.previous_query_count}`
    : "";
  return `#${searchAttempt.attempt_index}${repeated}${previous}`;
}

function formatEvidenceCoverage(result) {
  const coverage = result.evidence_coverage || firstRankedCoverage(result);
  if (!coverage) {
    return "-";
  }
  const observed = (coverage.observed_fields || []).join(", ") || "none";
  const missing = (coverage.critical_missing_fields || []).join(", ") || "none";
  const missingTools = (coverage.missing_tools || []).join(", ") || "none";
  const policy = coverage.recommendation_policy || "-";
  return `observed:${observed} missing:${missing} tools:${missingTools} policy:${policy}`;
}

function formatMemorySummary(result) {
  const status = result.durable_memory_status;
  const match = result.durable_memory_match || {};
  const closet = result.closet_item || {};
  const source = closet.memory_source || "";
  const identity = closet.baseline_identity?.status || result.baseline_identity?.status || "";
  if (!status && !source && !identity) {
    return "-";
  }
  const parts = [];
  if (status) {
    parts.push(`durable:${status}`);
  }
  if (typeof match.score === "number") {
    parts.push(`match:${match.score}`);
  }
  if (Array.isArray(match.reasons) && match.reasons.length) {
    parts.push(`reasons:${match.reasons.slice(0, 3).join(",")}`);
  }
  if (source) {
    parts.push(`source:${source}`);
  }
  if (identity) {
    parts.push(`identity:${identity}`);
  }
  return parts.join(" ");
}

function formatFetchFallback(result) {
  const provider = result.fetch_fallback_provider;
  const status = result.fetch_fallback_status;
  if (!provider && !status && !result.direct_fetch_status) {
    return "-";
  }
  const direct = result.direct_fetch_status ? ` direct:${result.direct_fetch_status}` : "";
  const error = result.direct_fetch_error ? ` error:${truncateText(result.direct_fetch_error, 36)}` : "";
  return `${provider || "fallback"}:${status || "-"}${direct}${error}`;
}

function formatAssetSummary(result) {
  const assetCount = numberOrNull(result.asset_count);
  const ocrTargets = numberOrNull(result.ocr_target_asset_count);
  if (assetCount === null && ocrTargets === null) {
    return "-";
  }
  return `assets:${assetCount ?? 0} ocr_targets:${ocrTargets ?? 0}`;
}

function formatOcrSummary(result) {
  const evidence = Array.isArray(result.evidence) ? result.evidence : null;
  if (!evidence) {
    return "-";
  }
  const textCount = evidence.filter((item) => item.status === "completed" && item.text).length;
  const emptyCount = evidence.filter((item) => item.status === "completed_empty").length;
  const failedCount = evidence.filter((item) => ["failed", "configuration_required", "not_connected", "skipped"].includes(item.status)).length;
  const imageTypes = [...new Set(evidence.map((item) => item.image_type).filter(Boolean))].join(",") || "none";
  return `evidence:${evidence.length} text:${textCount} empty:${emptyCount} failed:${failedCount} types:${imageTypes}`;
}

function formatSpecSummary(result) {
  const spec = result.normalized_spec;
  if (!spec) {
    return "-";
  }
  const measurements = spec.measurements ? Object.keys(spec.measurements).length : 0;
  const material = spec.material ? Object.keys(spec.material).join(",") : "";
  const price = spec.price ?? "-";
  const missing = Array.isArray(spec.missing_fields) ? spec.missing_fields.length : 0;
  const userEvidence = result.user_evidence_used ? " user" : "";
  const ocrCount = typeof result.ocr_evidence_used_count === "number" ? ` ocr:${result.ocr_evidence_used_count}` : "";
  return `measurements:${measurements} material:${material || "-"} price:${price} missing:${missing}${ocrCount}${userEvidence}`;
}

function numberOrNull(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function truncateText(value, maxLength) {
  const text = String(value || "");
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text;
}

function firstRankedCoverage(result) {
  const ranked = result.ranked_matches || [];
  for (const item of ranked) {
    if (item.evidence_coverage) {
      return item.evidence_coverage;
    }
  }
  return null;
}

function formatDomainPolicy(domainPolicy) {
  if (!domainPolicy || !domainPolicy.policy) {
    return "-";
  }
  const allowed = domainPolicy.allowed_domains_configured ? "allowlist" : "open";
  const preferred = Array.isArray(domainPolicy.preferred_domains) ? domainPolicy.preferred_domains.length : 0;
  const blocked = Array.isArray(domainPolicy.blocked_domains) ? domainPolicy.blocked_domains.length : 0;
  return `${allowed} preferred:${preferred} blocked:${blocked}`;
}

function formatOcrProviderStatus(ocr) {
  const provider = ocr.provider || ocr.selected || "-";
  if (ocr.provider === "tesseract_cli_ocr" && !ocr.tesseract_cli_available) {
    return `${provider} missing binary`;
  }
  const mode = ocr.mode ? ` ${ocr.mode}` : "";
  return `${provider}${mode}`;
}

function formatPageFetchProviderStatus(pageFetch) {
  const provider = pageFetch.provider || pageFetch.selected || "-";
  const mode = pageFetch.mode ? ` ${pageFetch.mode}` : "";
  const fallbacks = Array.isArray(pageFetch.fallbacks) && pageFetch.fallbacks.length
    ? ` fallback:${pageFetch.fallbacks.join(",")}`
    : " fallback:none";
  const key = pageFetch.jina_reader_enabled && !pageFetch.jina_reader_api_key_configured ? " no-key" : "";
  return `${provider}${mode}${fallbacks}${key}`;
}

function providerStatusClass(provider) {
  if (!provider || !provider.provider) {
    return "";
  }
  if (
    (provider.requires_api_key && !provider.api_key_configured) ||
    (provider.requires_search_engine_id && !provider.search_engine_id_configured) ||
    (provider.requires_base_url && !provider.base_url_configured) ||
    (provider.provider === "tesseract_cli_ocr" && !provider.tesseract_cli_available)
  ) {
    return "warn";
  }
  if (provider.mode === "live_provider") {
    return "ok";
  }
  return "";
}

function renderQuestions(questions) {
  pendingQuestions = questions || [];
  questionCount.textContent = String(pendingQuestions.length);
  if (!pendingQuestions.length) {
    questionList.innerHTML = '<div class="empty-line">추가 질문 없음</div>';
    return;
  }
  questionList.innerHTML = pendingQuestions
    .map(
      (question) => `
        <div class="question-item">
          <strong>${escapeHtml(question.field || "field")}</strong>
          <span>${escapeHtml(question.question || "")}</span>
        </div>
      `
    )
    .join("");
}

function formatQuestionsMessage(questions) {
  if (!questions.length) {
    return "추가 정보가 필요합니다.";
  }
  return questions.map((question) => question.question).join("\n");
}

function addUserMessage(text, attachments = []) {
  addMessage("user", escapeHtml(text), "", attachments);
}

function addAssistantMessage(content, variant = "", isHtml = false) {
  addMessage("assistant", isHtml ? content : escapeHtml(content), variant);
}

function addMessage(role, content, variant = "", attachments = []) {
  const article = document.createElement("article");
  article.className = `message ${role} ${variant}`.trim();
  article.innerHTML = `
    <div class="avatar">${role === "user" ? "U" : "A"}</div>
    <div class="bubble">
      ${attachments.length ? renderMessageAttachments(attachments) : ""}
      ${content.replaceAll("\n", "<br>")}
    </div>
  `;
  messages.appendChild(article);
  messages.scrollTop = messages.scrollHeight;
}

function setBusy(isBusy) {
  sendButton.disabled = isBusy;
  chatInput.disabled = isBusy;
  attachButton.disabled = isBusy;
  sendButton.textContent = isBusy ? "처리 중" : "보내기";
}

function resizeComposer() {
  chatInput.style.height = "auto";
  chatInput.style.height = `${Math.min(chatInput.scrollHeight, 160)}px`;
}

function renderAttachmentTray() {
  if (!selectedAttachments.length) {
    attachmentTray.hidden = true;
    attachmentTray.innerHTML = "";
    return;
  }
  attachmentTray.hidden = false;
  attachmentTray.innerHTML = selectedAttachments
    .map(
      (attachment, index) => `
        <div class="attachment-chip">
          <img src="${escapeHtml(attachment.url)}" alt="" />
          <span>${escapeHtml(attachment.filename || "image")}</span>
          <button type="button" aria-label="첨부 제거" data-remove-attachment="${index}">×</button>
        </div>
      `
    )
    .join("");
  attachmentTray.querySelectorAll("[data-remove-attachment]").forEach((button) => {
    button.addEventListener("click", () => {
      const index = Number(button.dataset.removeAttachment);
      selectedAttachments.splice(index, 1);
      renderAttachmentTray();
      chatInput.focus();
    });
  });
}

function renderMessageAttachments(attachments) {
  return `
    <div class="message-attachments">
      ${attachments
        .map(
          (attachment) => `
            <img src="${escapeHtml(attachment.url)}" alt="${escapeHtml(attachment.filename || "첨부 이미지")}" />
          `
        )
        .join("")}
    </div>
  `;
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve(String(reader.result)));
    reader.addEventListener("error", () => reject(reader.error || new Error("파일 읽기 실패")));
    reader.readAsDataURL(file);
  });
}

async function parseJsonResponse(response) {
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || payload.message || `HTTP ${response.status}`);
  }
  return payload;
}

function statusClass(status) {
  if (status === "connected" || status === "completed" || status === "ready") {
    return "ok";
  }
  if (status === "configuration_required" || status === "waiting_for_user" || status === "in_progress" || status === "running") {
    return "warn";
  }
  if (status === "failed" || status === "offline") {
    return "bad";
  }
  return "";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
