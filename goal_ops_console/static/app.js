const stateClass = (value) => `state-${String(value || "").replaceAll(" ", "_")}`;

const AUTO_REFRESH_MS = 5000;
const SECTION_IDS = [
  "goals-section",
  "tasks-section",
  "operator-section",
  "workflows-section",
  "events-section",
  "trace-section",
  "audit-section",
  "faults-section",
  "health-section",
  "states-section",
];
const IS_DESKTOP_MODE = new URLSearchParams(window.location.search).get("desktop") === "1";
const VISUAL_PRESETS = [
  {
    id: "warm",
    label: "Warm",
    className: "",
  },
  {
    id: "graphite",
    label: "Graphite",
    className: "visual-graphite",
  },
  {
    id: "signal",
    label: "Signal",
    className: "visual-signal",
  },
];

let selectedGoalId = "";
let plannerPreview = null;
let defaultConsumerId = "goal_ops_console";
let autoRefreshEnabled = true;
let autoRefreshHandle = null;
let densityMode = "comfy";
let visualMode = "warm";
let globalFilterTerm = "";
let jumpScrollScheduled = false;
let plannerReviewInboxStatus = "needs_review";
let plannerReviewInboxSort = "needs_review";
const MUTATION_LOCK_FALLBACK_MESSAGE = (
  "Mutating operations are blocked while runtime is in critical protection mode."
);

const viewCache = {
  goals: [],
  plannerReviewInbox: null,
  plannerDeferredFollowups: null,
  tasks: [],
  workflows: [],
  workflowRuns: [],
  events: [],
  auditEntries: [],
  faultSummary: {},
  faultEntries: [],
  queue: [],
  health: null,
  readiness: null,
  slo: null,
  runtimeState: null,
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const isJson = response.headers.get("content-type")?.includes("application/json");
  const payload = isJson ? await response.json() : await response.text();
  if (!response.ok) {
    let message = typeof payload === "object" && payload ? payload.detail || JSON.stringify(payload) : payload;
    if (typeof payload === "object" && payload && typeof payload.retry_after_seconds === "number") {
      message = `${message} Retry after ${payload.retry_after_seconds}s.`;
    }
    const error = new Error(`[${response.status}] ${message || "Request failed"}`);
    error.status = response.status;
    error.retryAfterSeconds = payload?.retry_after_seconds;
    throw error;
  }
  return payload;
}

function showError(id, error) {
  const target = document.getElementById(id);
  if (!target) return;
  target.textContent = error?.message || "";
  target.setAttribute("aria-live", error ? "assertive" : "polite");
  if (id === "system-feedback") {
    if (error) {
      target.classList.add("error-state");
    } else {
      target.classList.remove("error-state");
    }
  }
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function normalizeFilterToken(value) {
  return String(value ?? "").toLowerCase();
}

function matchesGlobalFilter(values) {
  if (!globalFilterTerm) {
    return true;
  }
  return values.some((value) => normalizeFilterToken(value).includes(globalFilterTerm));
}

function filterRows(rows, valueMapper) {
  if (!globalFilterTerm) {
    return rows;
  }
  return rows.filter((row) => matchesGlobalFilter(valueMapper(row)));
}

function filteredEmptyMessage(defaultMessage) {
  if (!globalFilterTerm) {
    return defaultMessage;
  }
  return `No entries match "${escapeHtml(globalFilterTerm)}".`;
}

function updateSelectedGoalLabel() {
  const selectedLabel = selectedGoalId ? `Selected goal: ${selectedGoalId}` : "No goal selected.";
  document.getElementById("selected-goal-label").textContent = selectedLabel;
}

function setActiveJump(sectionId) {
  document.querySelectorAll(".jump-btn").forEach((button) => {
    button.classList.toggle("active", button.dataset.jumpTarget === sectionId);
  });
}

function jumpToSection(sectionId) {
  const target = document.getElementById(sectionId);
  if (!target) {
    return;
  }
  target.scrollIntoView({ behavior: "smooth", block: "start" });
  setActiveJump(sectionId);
}

function updateActiveJumpByScroll() {
  let nearestId = SECTION_IDS[0];
  let nearestDistance = Number.POSITIVE_INFINITY;
  for (const sectionId of SECTION_IDS) {
    const target = document.getElementById(sectionId);
    if (!target) {
      continue;
    }
    const distance = Math.abs(target.getBoundingClientRect().top - 220);
    if (distance < nearestDistance) {
      nearestDistance = distance;
      nearestId = sectionId;
    }
  }
  setActiveJump(nearestId);
}

function updateToolbarStatus() {
  const status = document.getElementById("toolbar-status");
  if (!status) {
    return;
  }
  const filterPart = globalFilterTerm
    ? `Filter active: "${globalFilterTerm}".`
    : "Global filter inactive.";
  const refreshPart = autoRefreshEnabled
    ? `Auto-refresh every ${AUTO_REFRESH_MS / 1000}s.`
    : "Auto-refresh paused.";
  const densityPart = `Density: ${densityMode}.`;
  const activeVisualPreset = VISUAL_PRESETS.find((preset) => preset.id === visualMode) || VISUAL_PRESETS[0];
  const visualPart = `Visual: ${activeVisualPreset.label}.`;
  const runtime = viewCache.runtimeState;
  const runtimePart = runtime
    ? `Runtime: ${runtime.readinessReady ? "ready" : "not-ready"}, SLO ${runtime.sloStatus.toUpperCase()}, ${runtime.severity}.`
    : "Runtime: loading.";
  const desktopPart = IS_DESKTOP_MODE
    ? "Desktop shortcuts: Ctrl+1/2/3 visual, Ctrl+Shift+F filter, Ctrl+Shift+R refresh."
    : "";
  status.textContent = (
    `${filterPart} ${refreshPart} ${densityPart} ${visualPart} ${runtimePart}`
    + `${desktopPart ? ` ${desktopPart}` : ""}`
  );
}

function deriveRuntimeState(health, readiness, slo) {
  if (!health || !readiness || !slo) {
    return {
      severity: "loading",
      safeModeActive: false,
      readinessReady: false,
      sloStatus: "unknown",
      mutationBlocked: false,
      mutationBlockReason: "",
      summary: "Runtime status is loading.",
      recommendations: ["Wait for health/readiness/SLO refresh to complete."],
      alerts: [],
    };
  }

  const backpressure = health.backpressure || {};
  const readinessChecks = (readiness.checks && typeof readiness.checks === "object")
    ? readiness.checks
    : {};
  const safeModeCheck = readinessChecks.safe_mode || health.safe_mode || {};
  const safeModeActive = Boolean(safeModeCheck.active);
  const readinessReady = Boolean(readiness.ready);
  const sloStatus = String(slo.status || "unknown").toLowerCase();
  const rawAlerts = Array.isArray(slo.alerts) ? slo.alerts : [];
  const alerts = rawAlerts.filter((alert) => alert && typeof alert === "object");
  const criticalAlerts = alerts.filter(
    (alert) => String(alert.severity || "").toLowerCase() === "critical",
  );
  const warningAlerts = alerts.filter(
    (alert) => String(alert.severity || "").toLowerCase() === "warning",
  );
  const failingReadinessChecks = Object.entries(readinessChecks)
    .filter(([, check]) => (
      check
      && typeof check === "object"
      && Object.prototype.hasOwnProperty.call(check, "ok")
      && check.ok === false
    ))
    .map(([checkName]) => checkName);

  const isCritical = safeModeActive || !readinessReady || sloStatus === "critical";
  const isDegraded = (
    isCritical
    || sloStatus === "degraded"
    || Boolean(backpressure.is_throttled)
    || warningAlerts.length > 0
  );
  const severity = isCritical ? "critical" : isDegraded ? "degraded" : "ok";
  const mutationBlocked = isCritical;

  let summary = "Runtime healthy. Mutating actions are enabled.";
  let mutationBlockReason = "";
  if (safeModeActive) {
    const reason = String(safeModeCheck.reason || "runtime safe mode active");
    summary = `Safe mode active: ${reason}. Mutating actions are blocked for protection.`;
    mutationBlockReason = "Safe mode is active.";
  } else if (!readinessReady) {
    const checkHint = failingReadinessChecks.length
      ? ` failing check: ${failingReadinessChecks[0]}.`
      : "";
    summary = `Readiness is false.${checkHint} Mutating actions are blocked until recovery.`;
    mutationBlockReason = "Readiness is false.";
  } else if (sloStatus === "critical") {
    const alertHint = criticalAlerts.length
      ? ` Active critical alert: ${criticalAlerts[0].code || "unknown"}.`
      : "";
    summary = `SLO is critical.${alertHint} Mutating actions are blocked to prevent escalation.`;
    mutationBlockReason = "SLO status is critical.";
  } else if (sloStatus === "degraded" || Boolean(backpressure.is_throttled) || warningAlerts.length > 0) {
    summary = "Runtime degraded. Mutating actions remain available with caution.";
  }

  const recommendations = [];
  if (safeModeActive) {
    recommendations.push("Inspect runtime safe mode cause and clear underlying fault before disabling.");
  }
  if (!readinessReady) {
    recommendations.push("Open readiness checks and remediate failing components before normal operations.");
  }
  if (sloStatus !== "ok") {
    recommendations.push("Review /system/slo alerts and stabilize error budget before rollout.");
  }
  if (Boolean(backpressure.is_throttled)) {
    recommendations.push("Drain backpressure and reduce new workflow starts until queue utilization normalizes.");
  }
  if (!recommendations.length) {
    recommendations.push("Continue supervised operations and monitor the runtime rail for regressions.");
  }

  const alertSummaries = alerts.slice(0, 4).map((alert) => ({
    code: String(alert.code || "unknown"),
    severity: String(alert.severity || "warning").toLowerCase(),
    message: String(alert.message || ""),
  }));

  return {
    severity,
    safeModeActive,
    readinessReady,
    sloStatus,
    mutationBlocked,
    mutationBlockReason: mutationBlockReason || MUTATION_LOCK_FALLBACK_MESSAGE,
    summary,
    recommendations,
    alerts: alertSummaries,
  };
}

function renderRuntimeStateRail(runtimeState) {
  const rail = document.getElementById("runtime-state-rail");
  if (!rail) {
    return;
  }

  const severity = ["ok", "degraded", "critical", "loading"].includes(runtimeState?.severity)
    ? runtimeState.severity
    : "loading";
  const summaryTarget = document.getElementById("runtime-state-summary");
  const alertsTarget = document.getElementById("runtime-state-alerts");
  const recommendationsTarget = document.getElementById("runtime-state-recommendations");

  rail.classList.remove("state-ok", "state-degraded", "state-critical", "state-loading");
  rail.classList.add(`state-${severity}`);
  rail.setAttribute("data-runtime-severity", severity);

  document.body.classList.toggle("runtime-critical", severity === "critical");
  document.body.classList.toggle("runtime-degraded", severity === "degraded");
  document.body.classList.toggle("runtime-loading", severity === "loading");

  if (summaryTarget) {
    summaryTarget.textContent = String(runtimeState?.summary || "Runtime state unavailable.");
  }

  if (alertsTarget) {
    const alerts = Array.isArray(runtimeState?.alerts) ? runtimeState.alerts : [];
    alertsTarget.innerHTML = alerts.length
      ? `<ul>${alerts.map((alert) => (
          `<li><span class="pill ${stateClass(alert.severity)}">${escapeHtml(alert.severity)}</span> `
          + `<strong>${escapeHtml(alert.code)}</strong>: ${escapeHtml(alert.message || "-")}</li>`
        )).join("")}</ul>`
      : `<div class="meta">No active SLO alerts.</div>`;
  }

  if (recommendationsTarget) {
    const recommendations = Array.isArray(runtimeState?.recommendations)
      ? runtimeState.recommendations
      : [];
    recommendationsTarget.innerHTML = recommendations.length
      ? `<ul>${recommendations.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
      : `<div class="meta">No operator recommendations.</div>`;
  }
}

function applyMutationControlState() {
  const runtimeState = viewCache.runtimeState;
  const blocked = Boolean(runtimeState?.mutationBlocked);
  const reason = String(runtimeState?.mutationBlockReason || MUTATION_LOCK_FALLBACK_MESSAGE);

  document.querySelectorAll('[data-mutation-control="true"]').forEach((control) => {
    if (!("disabled" in control)) {
      return;
    }
    const isExempt = String(control.dataset.mutationExempt || "").toLowerCase() === "true";
    if (!Object.prototype.hasOwnProperty.call(control.dataset, "mutationBaseDisabled")) {
      control.dataset.mutationBaseDisabled = control.disabled ? "true" : "false";
    }
    if (!Object.prototype.hasOwnProperty.call(control.dataset, "mutationOriginalTitle")) {
      control.dataset.mutationOriginalTitle = control.getAttribute("title") || "";
    }

    const baseDisabled = control.dataset.mutationBaseDisabled === "true";
    const shouldLock = blocked && !isExempt;
    control.disabled = baseDisabled || shouldLock;

    if (shouldLock) {
      control.setAttribute("aria-disabled", "true");
      control.setAttribute("title", reason);
    } else {
      control.removeAttribute("aria-disabled");
      const originalTitle = control.dataset.mutationOriginalTitle || "";
      if (originalTitle) {
        control.setAttribute("title", originalTitle);
      } else {
        control.removeAttribute("title");
      }
    }
  });
}

function ensureMutationAllowed(actionLabel, { allowWhenBlocked = false } = {}) {
  const runtimeState = viewCache.runtimeState;
  if (!runtimeState || !runtimeState.mutationBlocked || allowWhenBlocked) {
    return;
  }
  const reason = String(runtimeState.mutationBlockReason || MUTATION_LOCK_FALLBACK_MESSAGE);
  throw new Error(`${actionLabel} blocked. ${reason}`);
}

function setDensityMode(mode) {
  densityMode = mode === "compact" ? "compact" : "comfy";
  document.body.classList.toggle("density-compact", densityMode === "compact");
  const toggleButton = document.getElementById("toggle-density");
  if (toggleButton) {
    toggleButton.textContent = `Density: ${densityMode === "compact" ? "Compact" : "Comfy"}`;
  }
  try {
    localStorage.setItem("goal_ops_density", densityMode);
  } catch {
    // Ignore localStorage write failures in restricted contexts.
  }
  updateToolbarStatus();
}

function toggleDensityMode() {
  setDensityMode(densityMode === "compact" ? "comfy" : "compact");
}

function setVisualMode(mode) {
  const preset = VISUAL_PRESETS.find((item) => item.id === mode) || VISUAL_PRESETS[0];
  visualMode = preset.id;
  VISUAL_PRESETS.forEach((item) => {
    if (item.className) {
      document.body.classList.remove(item.className);
    }
  });
  if (preset.className) {
    document.body.classList.add(preset.className);
  }
  const toggleButton = document.getElementById("toggle-visual-mode");
  if (toggleButton) {
    toggleButton.textContent = `Visual: ${preset.label}`;
  }
  try {
    localStorage.setItem("goal_ops_visual_mode", visualMode);
  } catch {
    // Ignore localStorage write failures in restricted contexts.
  }
  updateToolbarStatus();
}

function toggleVisualMode() {
  const currentIndex = VISUAL_PRESETS.findIndex((preset) => preset.id === visualMode);
  const nextIndex = currentIndex >= 0
    ? (currentIndex + 1) % VISUAL_PRESETS.length
    : 0;
  setVisualMode(VISUAL_PRESETS[nextIndex].id);
}

function setAutoRefresh(enabled) {
  autoRefreshEnabled = Boolean(enabled);
  const toggleButton = document.getElementById("toggle-refresh");
  if (toggleButton) {
    toggleButton.textContent = `Auto-refresh: ${autoRefreshEnabled ? "On" : "Off"}`;
  }
  updateToolbarStatus();
}

function rerenderFilteredViews() {
  renderGoals(viewCache.goals);
  renderPlannerReviewInbox(viewCache.plannerReviewInbox);
  renderPlannerDeferredFollowups(viewCache.plannerDeferredFollowups);
  renderTasks(viewCache.tasks);
  renderWorkflows(viewCache.workflows, viewCache.workflowRuns);
  renderEvents(viewCache.events);
  renderAudit(viewCache.auditEntries);
  renderFaults(viewCache.faultSummary, viewCache.faultEntries);
  renderQueue(viewCache.queue);
  updateSelectedGoalLabel();
}

function renderGoalButtons(goal) {
  const actionsByState = {
    draft: [
      ["activate", "Activate"],
      ["trace", "Trace"],
    ],
    active: [
      ["block", "Block"],
      ["trace", "Trace"],
    ],
    blocked: [
      ["activate", "Activate"],
      ["archive", "Archive"],
      ["trace", "Trace"],
    ],
    escalation_pending: [
      ["hitl_approve", "HITL Approve"],
      ["archive", "Archive"],
      ["trace", "Trace"],
    ],
    completed: [
      ["archive", "Archive"],
      ["trace", "Trace"],
    ],
    cancelled: [
      ["archive", "Archive"],
      ["trace", "Trace"],
    ],
    archived: [["trace", "Trace"]],
  };
  const actions = actionsByState[goal.state] || [["trace", "Trace"]];
  const actionButtons = actions.map(([action, label]) => {
    if (action === "trace") {
      return `<button class="secondary" data-correlation="${goal.goal_id}">${label}</button>`;
    }
    return (
      `<button class="secondary" data-mutation-control="true" data-goal-action="${action}" `
      + `data-goal-id="${goal.goal_id}">${label}</button>`
    );
  });
  actionButtons.push(
    `<button class="secondary" data-plan-goal="${goal.goal_id}">Plan Preview</button>`,
  );
  return actionButtons.join("");
}

function renderPlannerSuggestionStatus(item) {
  const decision = plannerSuggestionDecision(item);
  if (item.task_exists || decision === "created") {
    const taskId = item.existing_task_id || item.review_task_id;
    const existingTask = taskId ? ` as ${escapeHtml(taskId)}` : "";
    return `<div class="meta">${escapeHtml(item.source)} &middot; Already created${existingTask}</div>`;
  }
  if (decision === "deferred" || decision === "rejected") {
    return (
      `<div class="meta">${escapeHtml(item.source)} &middot; Review: `
      + `${escapeHtml(decision)}</div>`
    );
  }
  return `<div class="meta">${escapeHtml(item.source)} &middot; Ready for review</div>`;
}

function renderPlannerSuggestionAction(item, index) {
  const decision = plannerSuggestionDecision(item);
  if (item.task_exists || decision === "created") {
    return `<button type="button" class="secondary" disabled>Already created</button>`;
  }
  if (decision === "deferred" || decision === "rejected") {
    return `<button type="button" class="secondary" disabled>${escapeHtml(decision)}</button>`;
  }
  return (
    `<button type="button" class="secondary" data-mutation-control="true" `
    + `data-plan-suggestion-index="${index}">Create task</button>`
  );
}

function renderPlannerSuggestionSelection(item, index) {
  const decision = plannerSuggestionDecision(item);
  if (item.task_exists || decision !== "pending") {
    return "";
  }
  return (
    `<label class="meta"><input type="checkbox" data-plan-select-index="${index}"> `
    + "Select for batch actions</label>"
  );
}

function renderPlannerSuggestionEditor(item, index) {
  const decision = plannerSuggestionDecision(item);
  if (item.task_exists || decision !== "pending") {
    return "";
  }
  return `
    <div class="entity-grid" style="margin-top:0.75rem;">
      <label class="entity-metric">
        <span class="meta">Review title</span>
        <input type="text" data-plan-title-index="${index}" value="${escapeHtml(item.title)}">
      </label>
      <label class="entity-metric">
        <span class="meta">Review priority</span>
        <select data-plan-priority-index="${index}">
          ${["low", "medium", "high"].map((priority) => (
            `<option value="${priority}" ${priority === item.priority_hint ? "selected" : ""}>${priority}</option>`
          )).join("")}
        </select>
      </label>
    </div>
    <label class="meta" style="display:block;margin-top:0.75rem;">
      Review description
      <textarea data-plan-description-index="${index}" rows="3">${escapeHtml(item.description)}</textarea>
    </label>
  `;
}

function plannerSuggestionDecision(item) {
  if (item.review_decision) {
    return item.review_decision;
  }
  return item.task_exists ? "created" : "pending";
}

function renderPlannerSuggestionReviewControls(item, index) {
  const decision = plannerSuggestionDecision(item);
  if (decision === "deferred" || decision === "rejected") {
    const comment = item.review_comment
      ? `<div class="meta">Comment: ${escapeHtml(item.review_comment)}</div>`
      : "";
    return `
      <div class="entity-divider"></div>
      <div class="meta">Review decision: ${escapeHtml(decision)}</div>
      ${comment}
      <div class="actions">
        <button type="button" class="secondary" data-mutation-control="true" data-plan-review-reopen-index="${index}">Reopen review</button>
      </div>
    `;
  }
  if (item.task_exists || decision === "created") {
    return "";
  }
  return `
    <label class="meta" style="display:block;margin-top:0.75rem;">
      Review comment (optional)
      <textarea data-plan-review-comment-index="${index}" rows="2" placeholder="Why defer or reject this suggestion?"></textarea>
    </label>
    <div class="actions">
      <button type="button" class="secondary" data-mutation-control="true" data-plan-review-index="${index}" data-plan-review-decision="deferred">Defer</button>
      <button type="button" class="secondary" data-mutation-control="true" data-plan-review-index="${index}" data-plan-review-decision="rejected">Reject</button>
    </div>
  `;
}

function plannerReviewSummaryFromSuggestions(suggestions) {
  return (suggestions || []).reduce((summary, item) => {
    const decision = plannerSuggestionDecision(item);
    summary.total_suggestions += 1;
    if (decision in summary) {
      summary[decision] += 1;
    }
    return summary;
  }, {
    total_suggestions: 0,
    pending: 0,
    created: 0,
    deferred: 0,
    rejected: 0,
  });
}

function plannerReviewQueue(preview) {
  return preview.review_queue || {
    summary: plannerReviewSummaryFromSuggestions(preview.suggestions || []),
    reviews: [],
  };
}

function plannerReviewInboxVisibleItems(payload) {
  return filterRows(payload?.items || [], (item) => [
    item.goal_id,
    item.goal_title,
    item.state,
    item.needs_review ? "needs review" : "reviewed",
    item.next_suggestion?.title || "",
    item.next_suggestion?.description || "",
    item.next_suggestion?.rationale || "",
    item.next_suggestion?.priority_hint || "",
  ]);
}

function nextPlannerReviewGoalId() {
  return plannerReviewInboxVisibleItems(viewCache.plannerReviewInbox)
    .find((item) => item.needs_review)?.goal_id || "";
}

function renderPlannerReviewSummary(preview) {
  const queue = plannerReviewQueue(preview);
  const summary = queue.summary || plannerReviewSummaryFromSuggestions(preview.suggestions || []);
  const reviews = queue.reviews || [];
  const reviewItems = reviews.length
    ? reviews.map((review) => {
      const taskId = review.task_id ? ` | task ${escapeHtml(review.task_id)}` : "";
      const comment = review.comment ? ` | ${escapeHtml(review.comment)}` : "";
      return (
        `<div class="meta">#${Number(review.suggestion_index) + 1} `
        + `${escapeHtml(review.decision)} | ${escapeHtml(review.suggestion_title)}`
        + `${taskId}${comment}</div>`
      );
    }).join("")
    : `<div class="meta">No saved review decisions yet.</div>`;
  return `
    <div class="entity-grid" style="margin-top:0.75rem;">
      <div class="entity-metric"><span class="meta">Pending</span><strong>${summary.pending}</strong></div>
      <div class="entity-metric"><span class="meta">Created</span><strong>${summary.created}</strong></div>
      <div class="entity-metric"><span class="meta">Deferred</span><strong>${summary.deferred}</strong></div>
      <div class="entity-metric"><span class="meta">Rejected</span><strong>${summary.rejected}</strong></div>
    </div>
    <details style="margin-top:0.75rem;">
      <summary class="meta">Saved review decisions (${reviews.length})</summary>
      <div class="stack-list" style="margin-top:0.5rem;">${reviewItems}</div>
    </details>
  `;
}

function plannerReviewAudit(preview) {
  return preview.review_audit || {
    entries: [],
  };
}

function renderPlannerReviewAuditEntry(entry) {
  const suggestionNumber = Number(entry.suggestion_index) + 1;
  const title = entry.suggestion_title || `Suggestion #${suggestionNumber}`;
  const taskId = entry.task_id ? ` | task ${escapeHtml(entry.task_id)}` : "";
  if (entry.action === "reopened") {
    const comment = entry.cleared_comment ? ` | ${escapeHtml(entry.cleared_comment)}` : "";
    return (
      `<div class="meta">#${suggestionNumber} reopened from ${escapeHtml(entry.cleared_decision || "reviewed")} `
      + `| ${escapeHtml(title)} | ${escapeHtml(entry.emitted_at)}${comment}</div>`
    );
  }
  const comment = entry.comment ? ` | ${escapeHtml(entry.comment)}` : "";
  return (
    `<div class="meta">#${suggestionNumber} ${escapeHtml(entry.decision || "reviewed")} `
    + `| ${escapeHtml(title)} | ${escapeHtml(entry.emitted_at)}${taskId}${comment}</div>`
  );
}

function renderPlannerReviewAudit(preview) {
  const audit = plannerReviewAudit(preview);
  const entries = audit.entries || [];
  const auditItems = entries.length
    ? entries.map((entry) => renderPlannerReviewAuditEntry(entry)).join("")
    : `<div class="meta">No planner review history yet.</div>`;
  return `
    <details style="margin-top:0.75rem;">
      <summary class="meta">Review history (${entries.length})</summary>
      <div class="stack-list" style="margin-top:0.5rem;">${auditItems}</div>
    </details>
  `;
}

function renderPlannerReviewInbox(payload) {
  const container = document.getElementById("planner-review-inbox");
  if (!container) return;
  if (!payload) {
    container.innerHTML = `
      <span class="meta">Planner Review Inbox</span>
      <p class="meta">Review coverage is loading.</p>
    `;
    return;
  }
  const summary = payload.summary || {};
  const items = plannerReviewInboxVisibleItems(payload);
  const nextReviewItem = items.find((item) => item.needs_review);
  const statusButtons = [
    ["needs_review", "Needs review"],
    ["reviewed", "Reviewed"],
    ["all", "All"],
  ].map(([status, label]) => (
    `<button type="button" class="secondary ${plannerReviewInboxStatus === status ? "active" : ""}" `
    + `data-plan-inbox-status="${status}">${label}</button>`
  )).join("");
  const itemMarkup = items.length
    ? items.map((item) => {
      const reviewState = item.needs_review ? "Needs review" : "Reviewed";
      const lastReviewed = item.last_reviewed_at || "no saved decisions";
      const itemSummary = item.summary || {};
      const nextSuggestion = item.next_suggestion
        ? `
          <div class="card" style="background:rgba(255,255,255,0.48);">
            <div class="entity-header">
              <div>
                <div class="meta">Next pending suggestion #${Number(item.next_suggestion.suggestion_index) + 1}</div>
                <div class="entity-title">${escapeHtml(item.next_suggestion.title)}</div>
              </div>
              <span class="pill ${stateClass(item.next_suggestion.priority_hint)}">
                ${escapeHtml(item.next_suggestion.priority_hint)}
              </span>
            </div>
            <p class="meta">${escapeHtml(item.next_suggestion.description)}</p>
            <p class="meta"><strong>Why suggested:</strong> ${escapeHtml(item.next_suggestion.rationale)}</p>
          </div>
        `
        : `<div class="meta">No pending planner suggestions.</div>`;
      return `
        <article class="entity-card ${selectedGoalId === item.goal_id ? "selected" : ""}">
          <div class="entity-header">
            <div>
              <div class="entity-title">${escapeHtml(item.goal_title)}</div>
              <div class="meta">${escapeHtml(item.goal_id)} &middot; ${escapeHtml(lastReviewed)}</div>
            </div>
            <span class="pill ${item.needs_review ? "state-degraded" : "state-ok"}">${reviewState}</span>
          </div>
          <div class="entity-grid">
            <div class="entity-metric"><span class="meta">Pending</span><strong>${itemSummary.pending || 0}</strong></div>
            <div class="entity-metric"><span class="meta">Created</span><strong>${itemSummary.created || 0}</strong></div>
            <div class="entity-metric"><span class="meta">Deferred</span><strong>${itemSummary.deferred || 0}</strong></div>
            <div class="entity-metric"><span class="meta">Rejected</span><strong>${itemSummary.rejected || 0}</strong></div>
          </div>
          ${nextSuggestion}
          <div class="actions" style="margin-top:0.75rem;">
            <button type="button" class="secondary" data-plan-goal="${escapeHtml(item.goal_id)}">Open Plan Preview</button>
          </div>
        </article>
      `;
    }).join("")
    : `<div class="meta">${filteredEmptyMessage("No planner review items yet.")}</div>`;
  container.innerHTML = `
    <span class="meta">Planner Review Inbox &middot; ${summary.total_goals || 0} goals</span>
    <div class="actions" style="margin-top:0.75rem;">
      ${statusButtons}
      <label class="meta">
        Sort
        <select data-plan-inbox-sort="true">
          <option value="needs_review" ${plannerReviewInboxSort === "needs_review" ? "selected" : ""}>Needs review first</option>
          <option value="last_reviewed_at" ${plannerReviewInboxSort === "last_reviewed_at" ? "selected" : ""}>Last reviewed</option>
          <option value="goal_title" ${plannerReviewInboxSort === "goal_title" ? "selected" : ""}>Goal title</option>
        </select>
      </label>
      <button type="button" class="secondary" data-plan-next-review="true" ${nextReviewItem ? "" : "disabled"}>
        Open next review
      </button>
    </div>
    <div class="entity-grid" style="margin-top:0.75rem;">
      <div class="entity-metric"><span class="meta">Needs Review</span><strong>${summary.goals_needing_review || 0}</strong></div>
      <div class="entity-metric"><span class="meta">Pending</span><strong>${summary.pending_suggestions || 0}</strong></div>
      <div class="entity-metric"><span class="meta">Created</span><strong>${summary.created || 0}</strong></div>
      <div class="entity-metric"><span class="meta">Deferred</span><strong>${summary.deferred || 0}</strong></div>
    </div>
    <div class="stack-list" style="margin-top:0.75rem;">${itemMarkup}</div>
  `;
}

function plannerDeferredFollowupVisibleItems(payload) {
  return filterRows(payload?.items || [], (item) => [
    item.goal_id,
    item.goal_title,
    item.state,
    item.suggestion_title,
    item.suggestion_description,
    item.suggestion_rationale,
    item.priority_hint,
    item.comment || "",
    item.deferred_at,
  ]);
}

function renderPlannerDeferredFollowups(payload) {
  const container = document.getElementById("planner-deferred-followups");
  if (!container) return;
  if (!payload) {
    container.innerHTML = `
      <span class="meta">Deferred Follow-ups</span>
      <p class="meta">Deferred planner suggestions are loading.</p>
    `;
    return;
  }
  const summary = payload.summary || {};
  const items = plannerDeferredFollowupVisibleItems(payload);
  const itemMarkup = items.length
    ? items.map((item) => {
      const comment = item.comment ? `<div class="meta">Comment: ${escapeHtml(item.comment)}</div>` : "";
      return `
        <article class="entity-card ${selectedGoalId === item.goal_id ? "selected" : ""}">
          <div class="entity-header">
            <div>
              <div class="entity-title">${escapeHtml(item.suggestion_title)}</div>
              <div class="meta">
                ${escapeHtml(item.goal_title)}
                &middot; #${Number(item.suggestion_index) + 1}
                &middot; ${escapeHtml(item.deferred_at)}
              </div>
            </div>
            <span class="pill ${stateClass(item.priority_hint)}">${escapeHtml(item.priority_hint)}</span>
          </div>
          <p class="meta">${escapeHtml(item.suggestion_description)}</p>
          <p class="meta"><strong>Why suggested:</strong> ${escapeHtml(item.suggestion_rationale)}</p>
          ${comment}
          <div class="actions" style="margin-top:0.75rem;">
            <button type="button" class="secondary" data-plan-goal="${escapeHtml(item.goal_id)}">Open Plan Preview</button>
            <button
              type="button"
              class="secondary"
              data-mutation-control="true"
              data-plan-followup-reopen-goal="${escapeHtml(item.goal_id)}"
              data-plan-followup-reopen-index="${escapeHtml(String(item.suggestion_index ?? ""))}"
            >Reopen review</button>
          </div>
        </article>
      `;
    }).join("")
    : `<div class="meta">${filteredEmptyMessage("No deferred planner follow-ups yet.")}</div>`;
  container.innerHTML = `
    <span class="meta">Deferred Follow-ups &middot; ${summary.total_followups || 0} suggestions</span>
    <div class="actions" style="margin-top:0.75rem;">
      <button type="button" class="secondary" data-plan-followups-refresh="true">Refresh follow-ups</button>
    </div>
    <div class="entity-grid" style="margin-top:0.75rem;">
      <div class="entity-metric"><span class="meta">Follow-ups</span><strong>${summary.total_followups || 0}</strong></div>
      <div class="entity-metric"><span class="meta">Goals</span><strong>${summary.goals_with_followups || 0}</strong></div>
    </div>
    <div class="stack-list" style="margin-top:0.75rem;">${itemMarkup}</div>
  `;
  applyMutationControlState();
}

function renderPlannerPreview(preview) {
  const container = document.getElementById("planner-preview");
  if (!container) return;
  if (!preview) {
    container.innerHTML = `
      <span class="meta">Planner Preview</span>
      <p class="meta">Select a goal and use Plan Preview to generate deterministic task suggestions. Use Create task on a suggestion to add exactly one task.</p>
    `;
    return;
  }
  const suggestions = preview.suggestions || [];
  const selectableCount = suggestions.filter((item) => (
    !item.task_exists && plannerSuggestionDecision(item) === "pending"
  )).length;
  container.innerHTML = `
    <span class="meta">Planner Preview &middot; ${escapeHtml(preview.source)}</span>
    <h3>${escapeHtml(preview.goal_title)}</h3>
    <div class="meta">${escapeHtml(preview.goal_id)} &middot; ${suggestions.length} suggested tasks &middot; no tasks created automatically</div>
    ${renderPlannerReviewSummary(preview)}
    ${renderPlannerReviewAudit(preview)}
    <div class="actions" style="margin-top:0.75rem;">
      <button type="button" class="secondary" data-mutation-control="true" data-plan-bulk-create="true" ${selectableCount ? "" : "disabled"}>Create selected tasks</button>
      <button type="button" class="secondary" data-mutation-control="true" data-plan-bulk-review-decision="deferred" ${selectableCount ? "" : "disabled"}>Defer selected</button>
      <button type="button" class="secondary" data-mutation-control="true" data-plan-bulk-review-decision="rejected" ${selectableCount ? "" : "disabled"}>Reject selected</button>
      <span class="meta">${selectableCount} selectable suggestions</span>
    </div>
    <label class="meta" style="display:block;margin-top:0.75rem;">
      Batch review comment (optional)
      <textarea data-plan-bulk-review-comment="true" rows="2" placeholder="Shared note for Defer selected or Reject selected"></textarea>
    </label>
    <div class="stack-list" style="margin-top:0.75rem;">
      ${suggestions.map((item, index) => `
        <article class="entity-card">
          <div class="entity-header">
            <div>
              <div class="entity-title">${escapeHtml(item.title)}</div>
              ${renderPlannerSuggestionStatus(item)}
            </div>
            <span class="pill state-${escapeHtml(item.priority_hint)}">${escapeHtml(item.priority_hint)}</span>
          </div>
          <p class="meta">${escapeHtml(item.description)}</p>
          <p class="meta"><strong>Why suggested:</strong> ${escapeHtml(item.rationale)}</p>
          ${renderPlannerSuggestionEditor(item, index)}
          ${renderPlannerSuggestionReviewControls(item, index)}
          ${renderPlannerSuggestionSelection(item, index)}
          <div class="actions">
            ${renderPlannerSuggestionAction(item, index)}
          </div>
        </article>
      `).join("")}
    </div>
  `;
  applyMutationControlState();
}

function renderTaskButtons(task) {
  const terminalStates = new Set(["poison", "exhausted", "succeeded"]);
  const buttons = [];
  if (!terminalStates.has(task.status)) {
    buttons.push(
      `<button class="secondary" data-mutation-control="true" data-task-action="success" data-task-id="${task.task_id}">Success</button>`,
    );
    buttons.push(
      `<button class="secondary" data-mutation-control="true" data-task-action="skill" data-task-id="${task.task_id}">Skill Fail</button>`,
    );
    buttons.push(
      `<button class="secondary" data-mutation-control="true" data-task-action="execution" data-task-id="${task.task_id}">Exec Fail</button>`,
    );
    buttons.push(
      `<button class="secondary" data-mutation-control="true" data-task-action="external" data-task-id="${task.task_id}">External Fail</button>`,
    );
  }
  buttons.push(`<button class="secondary" data-correlation="${task.goal_id}">Goal Trace</button>`);
  return buttons.join("");
}

function renderTaskPlannerProvenance(task) {
  if (!task.planner_source) {
    return "";
  }
  const parts = [`Planner: ${escapeHtml(task.planner_source)}`];
  if (task.planner_suggestion_index !== null && task.planner_suggestion_index !== undefined) {
    const suggestionIndex = Number(task.planner_suggestion_index);
    if (Number.isFinite(suggestionIndex)) {
      parts.push(`suggestion #${suggestionIndex + 1}`);
    }
  }
  if (task.planner_priority_hint) {
    parts.push(`priority: ${escapeHtml(task.planner_priority_hint)}`);
  }
  if (task.planner_operator_overrides && typeof task.planner_operator_overrides === "object") {
    const editedFields = Object.keys(task.planner_operator_overrides).sort();
    if (editedFields.length) {
      parts.push(`operator edits: ${escapeHtml(editedFields.join(", "))}`);
    }
  }
  const rationale = task.planner_suggestion_rationale
    ? `<div class="meta"><strong>Why suggested:</strong> ${escapeHtml(task.planner_suggestion_rationale)}</div>`
    : "";
  return `<div class="meta">${parts.join(" &middot; ")}</div>${rationale}`;
}

function renderGoals(goals) {
  const filteredGoals = filterRows(goals, (goal) => [
    goal.goal_id,
    goal.title,
    goal.state,
    goal.blocked_reason,
    goal.escalation_reason,
  ]);
  const content = filteredGoals.length
    ? `<div class="stack-list">
        ${filteredGoals.map((goal) => `
          <article class="entity-card ${selectedGoalId === goal.goal_id ? "selected" : ""}">
            <div class="entity-header">
              <div>
                <div><button type="button" class="inline-link-button entity-title" data-select-goal="${goal.goal_id}" aria-label="Select goal ${goal.title}">${goal.title}</button></div>
                <div class="meta">${goal.goal_id}</div>
              </div>
              <span class="pill ${stateClass(goal.state)}">${goal.state}</span>
            </div>
            ${goal.blocked_reason ? `<div class="meta">${goal.blocked_reason}</div>` : ""}
            ${goal.escalation_reason ? `<div class="meta">${goal.escalation_reason}</div>` : ""}
            <div class="entity-grid">
              <div class="entity-metric">
                <span class="meta">Priority</span>
                <div>base ${Number(goal.base_priority || 0).toFixed(2)} / effective ${Number(goal.priority || 0).toFixed(2)}</div>
                <div class="priority-track"><div class="priority-fill" style="width:${Math.round((goal.priority || 0) * 100)}%"></div></div>
              </div>
              <div class="entity-metric">
                <span class="meta">Tasks</span>
                <div>${goal.task_count}</div>
              </div>
            </div>
            <div class="entity-divider"></div>
            <div class="actions">${renderGoalButtons(goal)}</div>
          </article>`).join("")}
      </div>`
    : `<div class="meta">${filteredEmptyMessage("No goals yet.")}</div>`;
  document.getElementById("goals-table").innerHTML = content;
  renderPlannerPreview(plannerPreview);
  applyMutationControlState();
}

function renderTasks(tasks) {
  const container = document.getElementById("tasks-table");
  const filteredTasks = filterRows(tasks, (task) => [
    task.task_id,
    task.goal_id,
    task.title,
    task.status,
    task.failure_type,
    task.error_hash,
    task.correlation_id,
    task.planner_source,
    task.planner_suggestion_index,
    task.planner_priority_hint,
    task.planner_suggestion_description,
    task.planner_suggestion_rationale,
  ]);
  if (!filteredTasks.length) {
    container.innerHTML = `<div class="meta">${filteredEmptyMessage("No tasks for the current selection.")}</div>`;
    applyMutationControlState();
    return;
  }
  container.innerHTML = `<div class="stack-list">
    ${filteredTasks.map((task) => `
      <article class="entity-card">
        <div class="entity-header">
          <div>
            <div class="entity-title">${task.title}</div>
            <div class="meta">${task.task_id}</div>
            ${renderTaskPlannerProvenance(task)}
          </div>
          <span class="pill ${stateClass(task.status)}">${task.status}</span>
        </div>
        <div class="entity-grid">
          <div class="entity-metric">
            <span class="meta">Retry</span>
            <div>${task.retry_count}</div>
            <div class="meta">${task.correlation_id}</div>
          </div>
          <div class="entity-metric">
            <span class="meta">Failure</span>
            <div>${task.failure_type || "-"}</div>
            <div class="meta">${task.error_hash || ""}</div>
          </div>
        </div>
        <div class="entity-divider"></div>
        <div class="actions">${renderTaskButtons(task)}</div>
      </article>`).join("")}
  </div>`;
  applyMutationControlState();
}

function renderEvents(events) {
  const container = document.getElementById("events-table");
  const filteredEvents = filterRows(events, (event) => [
    event.seq,
    event.event_type,
    event.entity_id,
    event.correlation_id,
    event.emitted_at,
    JSON.stringify(event.payload || {}),
  ]);
  if (!filteredEvents.length) {
    container.innerHTML = `<div class="meta">${filteredEmptyMessage("No events match the current filter.")}</div>`;
    return;
  }
  container.innerHTML = `<div class="table-scroll"><table>
    <thead>
      <tr>
        <th>Seq</th>
        <th>Event</th>
        <th>Entity</th>
        <th>Correlation</th>
        <th>Payload</th>
      </tr>
    </thead>
    <tbody>
      ${filteredEvents.map((event) => `
        <tr>
          <td>${event.seq}</td>
          <td>${event.event_type}<div class="meta">${event.emitted_at}</div></td>
          <td>${event.entity_id}</td>
          <td><button type="button" class="inline-link-button" data-correlation="${event.correlation_id}" aria-label="Filter by correlation ${event.correlation_id}">${event.correlation_id}</button></td>
          <td><pre>${JSON.stringify(event.payload || {}, null, 2)}</pre></td>
        </tr>`).join("")}
      </tbody>
  </table></div>`;
}

function renderAudit(entries) {
  const container = document.getElementById("audit-table");
  const filteredEntries = filterRows(entries, (entry) => [
    entry.created_at,
    entry.action,
    entry.actor,
    entry.status,
    entry.entity_type,
    entry.entity_id,
    JSON.stringify(entry.details || {}),
  ]);
  if (!filteredEntries.length) {
    container.innerHTML = `<div class="meta">${filteredEmptyMessage("No audit entries yet.")}</div>`;
    return;
  }
  container.innerHTML = `<div class="table-scroll"><table>
    <thead>
      <tr>
        <th>Time</th>
        <th>Action</th>
        <th>Status</th>
        <th>Entity</th>
        <th>Details</th>
      </tr>
    </thead>
    <tbody>
      ${filteredEntries.map((entry) => `
        <tr>
          <td>${entry.created_at}</td>
          <td>${entry.action}<div class="meta">${entry.actor}</div></td>
          <td>${entry.status}</td>
          <td>
            <div>${entry.entity_type || "-"}</div>
            <div class="meta">${entry.entity_id || "-"}</div>
          </td>
          <td><pre>${JSON.stringify(entry.details || {}, null, 2)}</pre></td>
        </tr>`).join("")}
      </tbody>
    </table></div>`;
}

function renderFaults(summary, entries) {
  const summaryTarget = document.getElementById("fault-summary");
  const tableTarget = document.getElementById("fault-table");
  const top = summary?.top_error_hashes || [];

  summaryTarget.innerHTML = `
    <div class="grid-two" style="margin-bottom:0.75rem;">
      <div class="card"><span class="meta">Failures (filtered)</span><strong>${summary?.total_failures || 0}</strong></div>
      <div class="card"><span class="meta">Dead-Letter Tasks</span><strong>${summary?.dead_letter_tasks || 0}</strong></div>
      <div class="card"><span class="meta">Poison Tasks</span><strong>${summary?.poison_tasks || 0}</strong></div>
      <div class="card"><span class="meta">Exhausted Tasks</span><strong>${summary?.exhausted_tasks || 0}</strong></div>
      <div class="card"><span class="meta">External Failures (window)</span><strong>${summary?.systemic_external_failures_last_window || 0}</strong></div>
      <div class="card"><span class="meta">Top Hash Buckets</span><strong>${top.length}</strong></div>
    </div>
    ${top.length ? `<div class="table-scroll"><table>
      <thead><tr><th>Type</th><th>Error Hash</th><th>Count</th><th>Tasks</th><th>Latest</th></tr></thead>
      <tbody>
        ${top.map((item) => `
          <tr>
            <td>${item.failure_type}</td>
            <td>${item.error_hash || "-"}</td>
            <td>${item.count}</td>
            <td>${item.task_count}</td>
            <td>${item.latest_at || "-"}</td>
          </tr>`).join("")}
      </tbody>
    </table></div>` : `<div class="meta">No fault buckets for current filter.</div>`}
  `;

  const filteredEntries = filterRows(entries, (item) => [
    item.failure_id,
    item.created_at,
    item.failure_type,
    item.failure_status,
    item.task_id,
    item.task_title,
    item.task_status,
    item.goal_id,
    item.goal_title,
    item.goal_state,
    item.correlation_id,
    item.error_hash,
    item.last_error,
  ]);
  if (!filteredEntries.length) {
    tableTarget.innerHTML = `<div class="meta">${filteredEmptyMessage("No fault records for current filter.")}</div>`;
    applyMutationControlState();
    return;
  }
  tableTarget.innerHTML = `<div class="table-scroll"><table>
    <thead>
      <tr>
        <th>Time</th>
        <th>Failure</th>
        <th>Task</th>
        <th>Goal</th>
        <th>Correlation</th>
        <th>Error</th>
        <th>Remediation</th>
      </tr>
    </thead>
    <tbody>
      ${filteredEntries.map((item) => `
        <tr>
          <td>${item.created_at}</td>
          <td>
            <div>${item.failure_type}</div>
            <div class="meta">retry=${item.retry_count} / ${item.failure_status}</div>
          </td>
          <td>
            <div>${item.task_title || "-"}</div>
            <div class="meta">${item.task_id}</div>
            <div><span class="pill ${stateClass(item.task_status)}">${item.task_status || "-"}</span></div>
          </td>
          <td>
            <div>${item.goal_title || "-"}</div>
            <div class="meta">${item.goal_id}</div>
            <div><span class="pill ${stateClass(item.goal_state)}">${item.goal_state || "-"}</span></div>
          </td>
          <td><button type="button" class="inline-link-button" data-correlation="${item.correlation_id}" aria-label="Filter by correlation ${item.correlation_id}">${item.correlation_id}</button></td>
          <td>
            <div>${item.error_hash || "-"}</div>
            <div class="meta">${item.last_error || ""}</div>
          </td>
          <td>${renderFaultRemediationButtons(item)}</td>
        </tr>`).join("")}
      </tbody>
  </table></div>`;
  applyMutationControlState();
}

function renderFaultRemediationButtons(item) {
  const buttons = [];
  if (["failed", "exhausted", "poison"].includes(item.task_status)) {
    buttons.push(
      `<button class="secondary" data-mutation-control="true" data-fault-action="retry" data-failure-id="${item.failure_id}">Retry Task</button>`,
    );
  }
  if (["blocked", "escalation_pending"].includes(item.goal_state)) {
    buttons.push(
      `<button class="secondary" data-mutation-control="true" data-fault-action="requeue_goal" data-failure-id="${item.failure_id}">Requeue Goal</button>`,
    );
  }
  if (item.failure_status !== "resolved") {
    buttons.push(
      `<button class="secondary" data-mutation-control="true" data-fault-action="resolve" data-failure-id="${item.failure_id}">Resolve</button>`,
    );
  }
  if (!buttons.length) {
    return `<div class="meta">No remediation needed</div>`;
  }
  return `<div class="actions">${buttons.join("")}</div>`;
}

function renderFlowTrace(trace) {
  const container = document.getElementById("flow-trace");
  if (!trace || trace.event_count === 0) {
    container.innerHTML = `<div class="meta">No trace events for this goal id.</div>`;
    return;
  }
  const attempts = trace.attempts || [];
  container.innerHTML = `
    <div class="grid-two">
      <div class="card"><span class="meta">Goal ID</span><div>${trace.goal_id}</div></div>
      <div class="card"><span class="meta">Total Events</span><strong>${trace.event_count}</strong></div>
      <div class="card"><span class="meta">Goal-Level Events</span><strong>${trace.goal_level_count}</strong></div>
      <div class="card"><span class="meta">Attempt Groups</span><strong>${trace.attempt_count}</strong></div>
    </div>
    ${attempts.length ? `<div class="table-scroll" style="margin-top:0.75rem;"><table>
      <thead><tr><th>Task</th><th>Attempt</th><th>Seq Range</th><th>Events</th></tr></thead>
      <tbody>
        ${attempts.map((item) => `
          <tr>
            <td><div>${item.task_id}</div></td>
            <td>${item.attempt}</td>
            <td>${item.first_seq} -> ${item.last_seq}</td>
            <td>${item.event_types.join(", ")}</td>
          </tr>`).join("")}
      </tbody>
    </table></div>` : `<div class="meta" style="margin-top:0.75rem;">No attempt-level events detected.</div>`}
  `;
}

function renderTopKpis(health) {
  const target = document.getElementById("top-kpis");
  if (!target) {
    return;
  }
  const totals = health.totals || {};
  const backpressure = health.backpressure || {};
  const faults = health.faults || {};
  const audit = health.audit || {};
  const runtimeState = viewCache.runtimeState;
  const throttled = Boolean(backpressure.is_throttled);
  const deadLetterTasks = faults.dead_letter_tasks || 0;
  const readinessLabel = runtimeState
    ? runtimeState.readinessReady ? "READY" : "NOT READY"
    : "LOADING";
  const readinessClass = runtimeState
    ? runtimeState.readinessReady ? "good" : "alert"
    : "";
  const sloLabel = runtimeState
    ? String(runtimeState.sloStatus || "unknown").toUpperCase()
    : "UNKNOWN";
  const sloClass = runtimeState
    ? runtimeState.sloStatus === "ok" ? "good" : runtimeState.sloStatus === "unknown" ? "" : "alert"
    : "";

  target.innerHTML = `
    <div class="kpi-chip">
      <span class="meta">Goals</span>
      <strong>${totals.goals || 0}</strong>
    </div>
    <div class="kpi-chip ${throttled ? "alert" : "good"}">
      <span class="meta">Backpressure</span>
      <strong>${throttled ? "ON" : "OFF"}</strong>
    </div>
    <div class="kpi-chip ${deadLetterTasks > 0 ? "alert" : ""}">
      <span class="meta">Dead-Letter</span>
      <strong>${deadLetterTasks}</strong>
    </div>
    <div class="kpi-chip">
      <span class="meta">Audit 24h</span>
      <strong>${audit.entries_last_24h || 0}</strong>
    </div>
    <div class="kpi-chip ${readinessClass}">
      <span class="meta">Readiness</span>
      <strong>${readinessLabel}</strong>
    </div>
    <div class="kpi-chip ${sloClass}">
      <span class="meta">SLO</span>
      <strong>${sloLabel}</strong>
    </div>
  `;
}

function renderHealth(health, readiness, slo) {
  viewCache.runtimeState = deriveRuntimeState(health, readiness, slo);
  renderRuntimeStateRail(viewCache.runtimeState);
  renderTopKpis(health);
  defaultConsumerId = health.default_consumer_id || defaultConsumerId;
  const consumerInput = document.getElementById("consumer-id");
  if (consumerInput && !consumerInput.value.trim()) {
    consumerInput.value = defaultConsumerId;
  }
  const totals = health.totals || {};
  const backpressure = health.backpressure || {};
  const retention = health.retention || {};
  const metrics = health.metrics || {};
  const audit = health.audit || {};
  const faults = health.faults || {};
  const consumerStats = Array.isArray(health.consumer_stats) ? health.consumer_stats : [];
  const stuckEvents = Array.isArray(health.stuck_events) ? health.stuck_events : [];
  const invariantViolations = Array.isArray(health.invariant_violations) ? health.invariant_violations : [];
  const sloAlerts = Array.isArray(slo?.alerts) ? slo.alerts : [];
  document.getElementById("health-cards").innerHTML = `
    <div class="card"><span class="meta">Events</span><strong>${totals.events || 0}</strong></div>
    <div class="card"><span class="meta">Goals</span><strong>${totals.goals || 0}</strong></div>
    <div class="card"><span class="meta">Tasks</span><strong>${totals.tasks || 0}</strong></div>
    <div class="card"><span class="meta">Readiness</span><strong>${viewCache.runtimeState.readinessReady ? "READY" : "NOT READY"}</strong></div>
    <div class="card"><span class="meta">SLO Status</span><strong>${String(viewCache.runtimeState.sloStatus).toUpperCase()}</strong></div>
    <div class="card"><span class="meta">SLO Alerts</span><strong>${sloAlerts.length}</strong></div>
    <div class="card"><span class="meta">Safe Mode</span><strong>${viewCache.runtimeState.safeModeActive ? "ON" : "OFF"}</strong></div>
    <div class="card"><span class="meta">Retry Budget</span><strong>${health.retry_budget_per_cycle}</strong></div>
    <div class="card"><span class="meta">Pending Events</span><strong>${backpressure.pending_events || 0}</strong></div>
    <div class="card"><span class="meta">Backpressure</span><strong>${backpressure.is_throttled ? "ON" : "OFF"}</strong></div>
    <div class="card"><span class="meta">429 Count</span><strong>${metrics["http.requests.status.429"] || 0}</strong></div>
    <div class="card"><span class="meta">Audit (24h)</span><strong>${audit.entries_last_24h || 0}</strong></div>
    <div class="card"><span class="meta">Failures</span><strong>${faults.total_failures || 0}</strong></div>
    <div class="card"><span class="meta">Dead-Letter Tasks</span><strong>${faults.dead_letter_tasks || 0}</strong></div>`;

  document.getElementById("backpressure-status").innerHTML = `
    <div class="meta">Consumer: ${backpressure.consumer_id || "-"}</div>
    <div class="meta">Pending: ${backpressure.pending_events || 0} / ${backpressure.max_pending_events || 0}</div>
    <div class="meta">Retry-After: ${backpressure.retry_after_seconds || 0}s</div>`;

  document.getElementById("retention-policy").innerHTML = `
    <div class="meta">Events: ${retention.events_days || 0} days</div>
    <div class="meta">Event Processing: ${retention.event_processing_days || 0} days</div>
    <div class="meta">Failure Log: ${retention.failure_log_days || 0} days</div>`;

  const metricRows = Object.entries(metrics);
  document.getElementById("metrics-hooks").innerHTML = metricRows.length
    ? `<table><thead><tr><th>Metric</th><th>Value</th></tr></thead><tbody>${
        metricRows.map(([name, value]) => `<tr><td>${name}</td><td>${value}</td></tr>`).join("")
      }</tbody></table>`
    : `<div class="meta">No metrics captured yet.</div>`;

  document.getElementById("consumer-stats").innerHTML = consumerStats.length
    ? `<table><thead><tr><th>Consumer</th><th>Status</th><th>Count</th></tr></thead><tbody>${
        consumerStats.map((item) => `<tr><td>${item.consumer_id}</td><td>${item.status}</td><td>${item.count}</td></tr>`).join("")
      }</tbody></table>`
    : `<div class="meta">No consumer activity yet.</div>`;

  document.getElementById("stuck-events").innerHTML = stuckEvents.length
    ? `<table><thead><tr><th>Consumer</th><th>Event</th><th>Started</th></tr></thead><tbody>${
        stuckEvents.map((item) => `<tr><td>${item.consumer_id}</td><td>${item.event_type} (${item.event_id})</td><td>${item.processing_started_at}</td></tr>`).join("")
      }</tbody></table>`
    : `<div class="meta">No stuck events.</div>`;

  document.getElementById("invariant-violations").innerHTML = invariantViolations.length
    ? `<ul>${invariantViolations.map((item) => `<li>${item}</li>`).join("")}</ul>`
    : `<div class="meta">No invariant violations detected.</div>`;

  const topFaults = faults.top_error_hashes || [];
  document.getElementById("fault-snapshot").innerHTML = topFaults.length
    ? `<table><thead><tr><th>Type</th><th>Hash</th><th>Count</th></tr></thead><tbody>${
        topFaults.map((item) => `<tr><td>${item.failure_type}</td><td>${item.error_hash || "-"}</td><td>${item.count}</td></tr>`).join("")
      }</tbody></table>`
    : `<div class="meta">No dead-letter faults in snapshot.</div>`;
  applyMutationControlState();
  updateToolbarStatus();
}

function renderQueue(goals) {
  const container = document.getElementById("queue-table");
  const filteredGoals = filterRows(goals, (goal) => [
    goal.goal_id,
    goal.title,
    goal.state,
    goal.queue_status,
  ]);
  if (!filteredGoals.length) {
    container.innerHTML = `<div class="meta">${filteredEmptyMessage("No queue entries yet.")}</div>`;
    return;
  }
  container.innerHTML = `<div class="table-scroll"><table>
    <thead>
      <tr>
        <th>Goal</th>
        <th>State</th>
        <th>Queue</th>
        <th>Wait</th>
        <th>Priority</th>
      </tr>
    </thead>
    <tbody>
      ${filteredGoals.map((goal) => `
        <tr>
          <td>
            <div><button type="button" class="inline-link-button" data-select-goal="${goal.goal_id}" aria-label="Select goal ${goal.title}">${goal.title}</button></div>
            <div class="meta">${goal.goal_id}</div>
          </td>
          <td><span class="pill ${stateClass(goal.state)}">${goal.state}</span></td>
          <td>${goal.queue_status}</td>
          <td>${goal.wait_cycles}</td>
          <td>
            <div>base ${Number(goal.base_priority || 0).toFixed(2)} / effective ${Number(goal.priority || 0).toFixed(2)}</div>
            <div class="priority-track"><div class="priority-fill" style="width:${Math.round((goal.priority || 0) * 100)}%"></div></div>
          </td>
        </tr>`).join("")}
    </tbody>
  </table></div>`;
}

function renderWorkflows(workflows, runs) {
  const workflowContainer = document.getElementById("workflows-table");
  const runsContainer = document.getElementById("workflow-runs-table");
  const workflowSelect = document.getElementById("workflow-select");
  if (!workflowContainer || !runsContainer || !workflowSelect) {
    return;
  }

  const selectableWorkflows = workflows.filter((item) => item.is_enabled);
  const selectedBefore = workflowSelect.value;
  workflowSelect.innerHTML = `
    <option value="">Select workflow</option>
    ${selectableWorkflows.map((item) => `<option value="${item.workflow_id}">${item.name}</option>`).join("")}
  `;
  if (selectedBefore && selectableWorkflows.some((item) => item.workflow_id === selectedBefore)) {
    workflowSelect.value = selectedBefore;
  } else if (!selectedBefore && selectableWorkflows.length) {
    workflowSelect.value = selectableWorkflows[0].workflow_id;
  }

  const filteredWorkflows = filterRows(workflows, (item) => [
    item.workflow_id,
    item.name,
    item.description,
    item.entrypoint,
    item.last_run_at,
  ]);
  workflowContainer.innerHTML = filteredWorkflows.length
    ? `<div class="table-scroll"><table>
        <thead>
          <tr>
            <th>Workflow</th>
            <th>Description</th>
            <th>Entrypoint</th>
            <th>Runs</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
          ${filteredWorkflows.map((item) => `
            <tr>
              <td>
                <div>${item.name}</div>
                <div class="meta">${item.workflow_id}</div>
              </td>
              <td>${item.description || "-"}</td>
              <td><span class="meta">${item.entrypoint}</span></td>
              <td>
                <div>${item.run_count || 0}</div>
                <div class="meta">${item.last_run_at || "never"}</div>
              </td>
              <td>
                <div class="actions">
                  <button type="button" class="secondary" data-mutation-control="true" data-workflow-start="${item.workflow_id}" ${item.is_enabled ? "" : "disabled"}>Start</button>
                </div>
              </td>
            </tr>`).join("")}
        </tbody>
      </table></div>`
    : `<div class="meta">${filteredEmptyMessage("No workflows available.")}</div>`;

  const filteredRuns = filterRows(runs, (run) => [
    run.run_id,
    run.workflow_id,
    run.workflow_name,
    run.status,
    run.requested_by,
    run.idempotency_key,
    run.correlation_id,
    JSON.stringify(run.result_payload || {}),
  ]);
  runsContainer.innerHTML = filteredRuns.length
    ? `<div class="table-scroll"><table>
        <thead>
          <tr>
            <th>Started</th>
            <th>Workflow</th>
            <th>Status</th>
            <th>Requested By</th>
            <th>Result</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          ${filteredRuns.map((run) => `
            <tr>
              <td>${run.started_at}</td>
              <td>
                <div>${run.workflow_name}</div>
                <div class="meta">${run.workflow_id}</div>
              </td>
              <td><span class="pill ${stateClass(run.status)}">${run.status}</span></td>
              <td>
                <div>${run.requested_by}</div>
                <div class="meta">${run.run_id}</div>
                <div class="meta">${run.idempotency_key || "no idempotency key"}</div>
              </td>
              <td><pre>${JSON.stringify(run.result_payload || {}, null, 2)}</pre></td>
              <td>
                ${["queued", "running"].includes(run.status)
                  ? `<button type="button" class="secondary" data-mutation-control="true" data-mutation-exempt="true" data-workflow-cancel="${run.run_id}">Cancel</button>`
                  : `<span class="meta">-</span>`}
              </td>
            </tr>`).join("")}
        </tbody>
      </table></div>`
    : `<div class="meta">${filteredEmptyMessage("No workflow runs yet.")}</div>`;
  applyMutationControlState();
}

async function refreshGoals() {
  const goals = await api("/goals");
  viewCache.goals = goals;
  renderGoals(viewCache.goals);
  updateSelectedGoalLabel();
}

async function refreshPlannerReviewInbox() {
  const params = new URLSearchParams({
    status: plannerReviewInboxStatus,
    sort: plannerReviewInboxSort,
  });
  const inbox = await api(`/goals/planner/reviews?${params.toString()}`);
  viewCache.plannerReviewInbox = inbox;
  renderPlannerReviewInbox(viewCache.plannerReviewInbox);
}

async function refreshPlannerDeferredFollowups() {
  const payload = await api("/goals/planner/reviews/followups");
  viewCache.plannerDeferredFollowups = payload;
  renderPlannerDeferredFollowups(viewCache.plannerDeferredFollowups);
}

async function refreshWorkflows() {
  const [workflowPayload, runPayload] = await Promise.all([
    api("/workflows"),
    api("/workflows/runs?limit=100"),
  ]);
  viewCache.workflows = workflowPayload.workflows || [];
  viewCache.workflowRuns = runPayload.runs || [];
  renderWorkflows(viewCache.workflows, viewCache.workflowRuns);
}

async function refreshQueue() {
  const queue = await api("/system/queue");
  viewCache.queue = queue;
  renderQueue(viewCache.queue);
}

async function refreshTasks() {
  const goalId = document.getElementById("task-goal-id").value.trim();
  const path = goalId ? `/tasks?goal_id=${encodeURIComponent(goalId)}` : "/tasks";
  const tasks = await api(path);
  viewCache.tasks = tasks;
  renderTasks(viewCache.tasks);
}

async function refreshEvents() {
  const correlationId = document.getElementById("event-correlation-id").value.trim();
  const entityId = document.getElementById("event-entity-id").value.trim();
  const params = new URLSearchParams();
  if (correlationId) params.set("correlation_id", correlationId);
  if (entityId) params.set("entity_id", entityId);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const events = await api(`/events${suffix}`);
  viewCache.events = events;
  renderEvents(viewCache.events);
}

async function refreshAudit() {
  const action = document.getElementById("audit-action").value.trim();
  const status = document.getElementById("audit-status").value.trim();
  const params = new URLSearchParams();
  if (action) params.set("action", action);
  if (status) params.set("status", status);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const payload = await api(`/system/audit${suffix}`);
  viewCache.auditEntries = payload.entries || [];
  renderAudit(viewCache.auditEntries);
}

function collectFaultFilters() {
  const failureType = document.getElementById("fault-failure-type").value.trim();
  const failureStatus = document.getElementById("fault-failure-status").value.trim();
  const taskStatus = document.getElementById("fault-task-status").value.trim();
  const goalId = document.getElementById("fault-goal-id").value.trim();
  const errorHash = document.getElementById("fault-error-hash").value.trim();
  const deadLetterOnly = document.getElementById("fault-dead-letter-only").checked;
  const filters = {
    dead_letter_only: deadLetterOnly,
  };
  if (failureType) filters.failure_type = failureType;
  if (failureStatus) filters.failure_status = failureStatus;
  if (taskStatus) filters.task_status = taskStatus;
  if (goalId) filters.goal_id = goalId;
  if (errorHash) filters.error_hash = errorHash;
  return filters;
}

function renderFaultFilterSummary(filters) {
  const target = document.getElementById("fault-filter-summary");
  if (!target) return;
  const parts = [];
  if (filters.failure_type) parts.push(`type=${filters.failure_type}`);
  if (filters.failure_status) parts.push(`status=${filters.failure_status}`);
  if (filters.task_status) parts.push(`task=${filters.task_status}`);
  if (filters.goal_id) parts.push(`goal=${filters.goal_id}`);
  if (filters.error_hash) parts.push(`hash=${filters.error_hash}`);
  parts.push(`dead_letter_only=${filters.dead_letter_only ? "true" : "false"}`);
  target.textContent = `Current filter: ${parts.join(", ")}`;
}

async function refreshFaults() {
  const filters = collectFaultFilters();
  renderFaultFilterSummary(filters);
  const params = new URLSearchParams();
  if (filters.failure_type) params.set("failure_type", filters.failure_type);
  if (filters.failure_status) params.set("failure_status", filters.failure_status);
  if (filters.task_status) params.set("task_status", filters.task_status);
  if (filters.goal_id) params.set("goal_id", filters.goal_id);
  if (filters.error_hash) params.set("error_hash", filters.error_hash);
  params.set("dead_letter_only", filters.dead_letter_only ? "true" : "false");
  const suffix = `?${params.toString()}`;
  const [entryPayload, summaryPayload] = await Promise.all([
    api(`/system/faults${suffix}`),
    api(`/system/faults/summary${suffix}`),
  ]);
  viewCache.faultSummary = summaryPayload || {};
  viewCache.faultEntries = entryPayload.entries || [];
  renderFaults(viewCache.faultSummary, viewCache.faultEntries);
}

async function refreshFlowTrace() {
  const goalId = document.getElementById("trace-goal-id").value.trim();
  const container = document.getElementById("flow-trace");
  if (!goalId) {
    container.innerHTML = `<div class="meta">Enter a goal id to load flow trace.</div>`;
    return;
  }
  const trace = await api(`/events/trace/${encodeURIComponent(goalId)}`);
  renderFlowTrace(trace);
}

async function refreshHealth() {
  const [health, readiness, slo] = await Promise.all([
    api("/system/health"),
    api("/system/readiness"),
    api("/system/slo"),
  ]);
  viewCache.health = health;
  viewCache.readiness = readiness;
  viewCache.slo = slo;
  renderHealth(viewCache.health, viewCache.readiness, viewCache.slo);
}

async function refreshAll() {
  await Promise.all([
    refreshGoals(),
    refreshPlannerReviewInbox(),
    refreshPlannerDeferredFollowups(),
    refreshTasks(),
    refreshWorkflows(),
    refreshEvents(),
    refreshFlowTrace(),
    refreshAudit(),
    refreshFaults(),
    refreshHealth(),
    refreshQueue(),
  ]);
}

function startAutoRefreshLoop() {
  if (autoRefreshHandle) {
    clearInterval(autoRefreshHandle);
  }
  autoRefreshHandle = setInterval(() => {
    if (!autoRefreshEnabled) {
      return;
    }
    refreshGoals().catch(() => {});
    refreshPlannerReviewInbox().catch(() => {});
    refreshPlannerDeferredFollowups().catch(() => {});
    refreshTasks().catch(() => {});
    refreshWorkflows().catch(() => {});
    refreshEvents().catch(() => {});
    refreshFlowTrace().catch(() => {});
    refreshAudit().catch(() => {});
    refreshFaults().catch(() => {});
    refreshHealth().catch(() => {});
    refreshQueue().catch(() => {});
  }, AUTO_REFRESH_MS);
}

async function runOperatorAction(action) {
  const consumerId = document.getElementById("consumer-id").value.trim() || defaultConsumerId;
  const batchSize = Number(document.getElementById("consumer-batch-size").value || 50);
  let result;
  showError("system-feedback", null);
  if (action !== "diagnostics") {
    ensureMutationAllowed(`Operator action "${action}"`);
  }
  if (action === "age") {
    result = await api("/system/scheduler/age", { method: "POST" });
    document.getElementById("system-feedback").textContent = `Aged ${result.aged_count} queue entries.`;
  } else if (action === "pick") {
    result = await api("/system/scheduler/pick", { method: "POST" });
    const picked = result.picked_goal;
    document.getElementById("system-feedback").textContent = picked
      ? `Activated goal ${picked.goal_id}.`
      : "No queued goal was available to pick.";
  } else if (action === "drain") {
    result = await api(`/system/consumers/${encodeURIComponent(consumerId)}/drain?batch_size=${batchSize}`, {
      method: "POST",
    });
    document.getElementById("system-feedback").textContent = `Consumer ${consumerId} processed ${result.processed_count} events.`;
  } else if (action === "reclaim") {
    result = await api(`/system/consumers/${encodeURIComponent(consumerId)}/reclaim`, { method: "POST" });
    document.getElementById("system-feedback").textContent = `Consumer ${consumerId} reclaimed ${result.reclaimed_count} stuck events.`;
  } else if (action === "retention") {
    result = await api("/system/maintenance/retention", { method: "POST" });
    document.getElementById("system-feedback").textContent = (
      `Retention cleanup deleted events=${result.events_deleted}, `
      + `event_processing=${result.event_processing_deleted}, `
      + `failure_log=${result.failure_log_deleted}.`
    );
  } else if (action === "diagnostics") {
    result = await api("/system/diagnostics", { method: "POST" });
    document.getElementById("system-feedback").textContent = (
      `Diagnostics snapshot exported to ${result.file_path}.`
    );
  }
  await refreshAll();
}

async function runPlannerPreview(goalId) {
  if (!goalId) {
    throw new Error("Select a goal before requesting a plan preview.");
  }
  const preview = await api(`/goals/${encodeURIComponent(goalId)}/plan`, { method: "POST" });
  const reviewQueue = await api(`/goals/${encodeURIComponent(goalId)}/plan/reviews`);
  const reviewAudit = await api(`/goals/${encodeURIComponent(goalId)}/plan/reviews/audit`);
  plannerPreview = { ...preview, review_queue: reviewQueue, review_audit: reviewAudit };
  selectedGoalId = goalId;
  document.getElementById("task-goal-id").value = goalId;
  document.getElementById("event-correlation-id").value = goalId;
  document.getElementById("trace-goal-id").value = goalId;
  document.getElementById("fault-goal-id").value = goalId;
  renderPlannerPreview(plannerPreview);
  renderPlannerReviewInbox(viewCache.plannerReviewInbox);
  renderPlannerDeferredFollowups(viewCache.plannerDeferredFollowups);
  updateSelectedGoalLabel();
}

function parsePlannerSuggestionIndex(indexValue) {
  const index = Number.parseInt(indexValue, 10);
  const suggestions = Array.isArray(plannerPreview?.suggestions) ? plannerPreview.suggestions : [];
  if (!Number.isInteger(index) || index < 0 || index >= suggestions.length) {
    throw new Error("Planner suggestion is no longer available. Run Plan Preview again.");
  }
  return index;
}

function selectedPlannerSuggestionIndexes() {
  return Array.from(document.querySelectorAll("[data-plan-select-index]:checked"))
    .map((input) => parsePlannerSuggestionIndex(input.dataset.planSelectIndex));
}

function collectPlannerSuggestionOverride(index) {
  const suggestion = plannerPreview?.suggestions?.[index];
  if (!suggestion) {
    throw new Error("Planner suggestion is no longer available. Run Plan Preview again.");
  }

  const title = document.querySelector(`[data-plan-title-index="${index}"]`)?.value.trim() || "";
  const description = document.querySelector(`[data-plan-description-index="${index}"]`)?.value.trim() || "";
  const priorityHint = document.querySelector(`[data-plan-priority-index="${index}"]`)?.value || "";
  const override = {};
  if (title !== suggestion.title) {
    override.title = title;
  }
  if (description !== suggestion.description) {
    override.description = description;
  }
  if (priorityHint !== suggestion.priority_hint) {
    override.priority_hint = priorityHint;
  }
  return Object.keys(override).length ? override : null;
}

function collectPlannerSuggestionOverrides(indexes) {
  return indexes.reduce((overrides, index) => {
    const override = collectPlannerSuggestionOverride(index);
    if (override) {
      overrides[index] = override;
    }
    return overrides;
  }, {});
}

function ensurePlannerSuggestionReadyForCreate(suggestion) {
  const decision = plannerSuggestionDecision(suggestion);
  if (suggestion?.task_exists || decision === "created") {
    throw new Error("Planner suggestion was already created. Run Plan Preview again or choose another suggestion.");
  }
  if (decision === "deferred" || decision === "rejected") {
    throw new Error(`Planner suggestion was already ${decision}. Run Plan Preview again or choose another suggestion.`);
  }
}

function ensurePlannerSuggestionReadyForReview(suggestion) {
  const decision = plannerSuggestionDecision(suggestion);
  if (suggestion?.task_exists || decision === "created") {
    throw new Error("Planner suggestion was already created. Run Plan Preview again or choose another suggestion.");
  }
  if (decision === "deferred" || decision === "rejected") {
    throw new Error(`Planner suggestion was already ${decision}. Run Plan Preview again or choose another suggestion.`);
  }
}

function collectPlannerReviewComment(index) {
  return document.querySelector(`[data-plan-review-comment-index="${index}"]`)?.value.trim() || null;
}

function collectPlannerBulkReviewComment() {
  return document.querySelector("[data-plan-bulk-review-comment]")?.value.trim() || null;
}

async function reviewPlannerSuggestion(indexValue, decision) {
  if (!plannerPreview?.goal_id) {
    throw new Error("Run Plan Preview before reviewing a suggested task.");
  }
  const suggestionIndex = parsePlannerSuggestionIndex(indexValue);
  const goalId = plannerPreview.goal_id;
  ensureMutationAllowed(`Planner review ${decision}`);
  const comment = collectPlannerReviewComment(suggestionIndex);
  const response = await api(`/goals/${encodeURIComponent(goalId)}/plan/reviews`, {
    method: "POST",
    body: JSON.stringify({
      suggestion_index: suggestionIndex,
      decision,
      ...(comment ? { comment } : {}),
    }),
  });
  document.getElementById("system-feedback").textContent = (
    `Planner suggestion #${response.suggestion_index + 1} marked ${response.review.decision}.`
  );
  selectedGoalId = goalId;
  document.getElementById("task-goal-id").value = goalId;
  document.getElementById("event-correlation-id").value = goalId;
  document.getElementById("trace-goal-id").value = goalId;
  document.getElementById("fault-goal-id").value = goalId;
  await refreshAll();
  await runPlannerPreview(goalId);
}

async function reviewSelectedPlannerSuggestions(decision) {
  if (!plannerPreview?.goal_id) {
    throw new Error("Run Plan Preview before reviewing suggested tasks.");
  }
  if (!["deferred", "rejected"].includes(decision)) {
    throw new Error(`Unsupported planner review decision: ${decision}`);
  }
  const suggestionIndexes = selectedPlannerSuggestionIndexes();
  if (!suggestionIndexes.length) {
    throw new Error("Select at least one planner suggestion before bulk review.");
  }
  suggestionIndexes.forEach((index) => ensurePlannerSuggestionReadyForReview(plannerPreview.suggestions[index]));
  const goalId = plannerPreview.goal_id;
  const comment = collectPlannerBulkReviewComment();
  ensureMutationAllowed(`Bulk planner review ${decision}`);
  const response = await api(`/goals/${encodeURIComponent(goalId)}/plan/reviews/bulk`, {
    method: "POST",
    body: JSON.stringify({
      suggestion_indexes: suggestionIndexes,
      decision,
      ...(comment ? { comment } : {}),
    }),
  });
  document.getElementById("system-feedback").textContent = (
    `Bulk planner review completed: ${response.reviews.length} suggestions marked ${response.decision}.`
  );
  selectedGoalId = goalId;
  document.getElementById("task-goal-id").value = goalId;
  document.getElementById("event-correlation-id").value = goalId;
  document.getElementById("trace-goal-id").value = goalId;
  document.getElementById("fault-goal-id").value = goalId;
  await refreshAll();
  await runPlannerPreview(goalId);
  setActiveJump("goals-section");
}

async function reopenPlannerSuggestionReview(indexValue) {
  if (!plannerPreview?.goal_id) {
    throw new Error("Run Plan Preview before reopening a suggested task review.");
  }
  const suggestionIndex = parsePlannerSuggestionIndex(indexValue);
  const goalId = plannerPreview.goal_id;
  ensureMutationAllowed("Planner review reopen");
  const response = await api(
    `/goals/${encodeURIComponent(goalId)}/plan/reviews/${encodeURIComponent(suggestionIndex)}`,
    { method: "DELETE" },
  );
  document.getElementById("system-feedback").textContent = (
    `Planner suggestion #${response.suggestion_index + 1} reopened from ${response.cleared_review.decision}.`
  );
  selectedGoalId = goalId;
  document.getElementById("task-goal-id").value = goalId;
  document.getElementById("event-correlation-id").value = goalId;
  document.getElementById("trace-goal-id").value = goalId;
  document.getElementById("fault-goal-id").value = goalId;
  await refreshAll();
  await runPlannerPreview(goalId);
}

async function reopenPlannerDeferredFollowup(goalId, indexValue) {
  if (!goalId) {
    throw new Error("Deferred follow-up is missing a goal id.");
  }
  const suggestionIndex = Number.parseInt(indexValue, 10);
  if (!Number.isInteger(suggestionIndex) || suggestionIndex < 0) {
    throw new Error("Deferred follow-up is missing a valid suggestion index.");
  }
  ensureMutationAllowed("Planner follow-up reopen");
  const response = await api(
    `/goals/${encodeURIComponent(goalId)}/plan/reviews/${encodeURIComponent(suggestionIndex)}`,
    { method: "DELETE" },
  );
  document.getElementById("system-feedback").textContent = (
    `Planner follow-up #${response.suggestion_index + 1} reopened from ${response.cleared_review.decision}.`
  );
  selectedGoalId = goalId;
  document.getElementById("task-goal-id").value = goalId;
  document.getElementById("event-correlation-id").value = goalId;
  document.getElementById("trace-goal-id").value = goalId;
  document.getElementById("fault-goal-id").value = goalId;
  await refreshAll();
  await runPlannerPreview(goalId);
  setActiveJump("goals-section");
}

async function createTaskFromPlannerSuggestion(indexValue) {
  if (!plannerPreview?.goal_id) {
    throw new Error("Run Plan Preview before creating a suggested task.");
  }
  const suggestionIndex = parsePlannerSuggestionIndex(indexValue);
  ensurePlannerSuggestionReadyForCreate(plannerPreview.suggestions[suggestionIndex]);
  const goalId = plannerPreview.goal_id;
  const override = collectPlannerSuggestionOverride(suggestionIndex);
  ensureMutationAllowed("Planner task creation");
  const response = await api(`/goals/${encodeURIComponent(goalId)}/plan/tasks`, {
    method: "POST",
    body: JSON.stringify({
      suggestion_index: suggestionIndex,
      ...(override ? { override } : {}),
    }),
  });
  document.getElementById("system-feedback").textContent = (
    `Created planner task ${response.task.task_id}`
    + `${override ? " with operator edits." : "."}`
  );
  selectedGoalId = goalId;
  document.getElementById("task-goal-id").value = goalId;
  document.getElementById("event-correlation-id").value = goalId;
  document.getElementById("trace-goal-id").value = goalId;
  document.getElementById("fault-goal-id").value = goalId;
  await refreshAll();
  await runPlannerPreview(goalId);
  setActiveJump("tasks-section");
}

async function createTasksFromSelectedPlannerSuggestions() {
  if (!plannerPreview?.goal_id) {
    throw new Error("Run Plan Preview before creating suggested tasks.");
  }
  const suggestionIndexes = selectedPlannerSuggestionIndexes();
  if (!suggestionIndexes.length) {
    throw new Error("Select at least one planner suggestion before bulk creation.");
  }
  suggestionIndexes.forEach((index) => ensurePlannerSuggestionReadyForCreate(plannerPreview.suggestions[index]));
  const goalId = plannerPreview.goal_id;
  const overrides = collectPlannerSuggestionOverrides(suggestionIndexes);
  ensureMutationAllowed("Bulk planner task creation");
  const response = await api(`/goals/${encodeURIComponent(goalId)}/plan/tasks/bulk`, {
    method: "POST",
    body: JSON.stringify({
      suggestion_indexes: suggestionIndexes,
      overrides,
    }),
  });
  document.getElementById("system-feedback").textContent = (
    `Bulk planner create completed: ${response.created.length} created, `
    + `${response.skipped_duplicates.length} duplicates skipped.`
  );
  selectedGoalId = goalId;
  document.getElementById("task-goal-id").value = goalId;
  document.getElementById("event-correlation-id").value = goalId;
  document.getElementById("trace-goal-id").value = goalId;
  document.getElementById("fault-goal-id").value = goalId;
  await refreshAll();
  await runPlannerPreview(goalId);
  setActiveJump("tasks-section");
}

function parseWorkflowPayload(text) {
  if (!text.trim()) {
    return {};
  }
  let payload;
  try {
    payload = JSON.parse(text);
  } catch {
    throw new Error("Workflow payload must be valid JSON.");
  }
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    throw new Error("Workflow payload must be a JSON object.");
  }
  return payload;
}

async function runWorkflowStart(workflowId) {
  if (!workflowId) {
    throw new Error("Select a workflow first.");
  }
  ensureMutationAllowed(`Workflow start ${workflowId}`);
  const requestedBy = document.getElementById("workflow-requested-by").value.trim() || "operator";
  const idempotencyKey = document.getElementById("workflow-idempotency-key").value.trim();
  const payloadText = document.getElementById("workflow-payload").value;
  const payload = parseWorkflowPayload(payloadText);
  const response = await api(`/workflows/${encodeURIComponent(workflowId)}/start`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
    },
    body: JSON.stringify({
      requested_by: requestedBy,
      payload,
    }),
  });
  const run = response.run;
  const replayNote = run.idempotency_replay ? " (idempotency replay)" : "";
  const reaperNote = run.stale_runs_reaped
    ? ` Reaper closed ${run.stale_runs_reaped} stale runs before execution.`
    : "";
  const queuedNote = run.status === "queued" ? " Run is queued and will execute asynchronously." : "";
  document.getElementById("system-feedback").textContent = (
    `Workflow ${run.workflow_name} accepted with status ${run.status}${replayNote}.${queuedNote}${reaperNote}`
  );
  await Promise.all([refreshWorkflows(), refreshHealth(), refreshEvents()]);
}

async function runWorkflowReaper() {
  ensureMutationAllowed("Workflow reaper");
  const response = await api("/workflows/runs/reap", { method: "POST" });
  document.getElementById("system-feedback").textContent = (
    `Workflow reaper marked ${response.reaped_count} stale runs as timed_out.`
  );
  await Promise.all([refreshWorkflows(), refreshHealth(), refreshEvents()]);
}

async function runWorkflowCancel(runId) {
  ensureMutationAllowed(`Workflow cancel ${runId}`, { allowWhenBlocked: true });
  const response = await api(`/workflows/runs/${encodeURIComponent(runId)}/cancel`, {
    method: "POST",
    body: JSON.stringify({
      requested_by: "operator",
      reason: "Cancelled from dashboard",
    }),
  });
  const run = response.run;
  document.getElementById("system-feedback").textContent = (
    `Workflow run ${run.run_id} is now ${run.status}.`
  );
  await Promise.all([refreshWorkflows(), refreshHealth(), refreshEvents()]);
}

async function runFaultAction(action, failureId) {
  ensureMutationAllowed(`Fault remediation ${action}`);
  const reasonInput = document.getElementById("fault-remediation-reason");
  const dryRunInput = document.getElementById("fault-remediation-dry-run");
  const reason = (reasonInput?.value || "").trim();
  const dryRun = Boolean(dryRunInput?.checked);
  if (!reason) {
    throw new Error("Remediation reason is required.");
  }

  const actionMap = {
    retry: "retry",
    requeue_goal: "requeue_goal",
    resolve: "resolve",
  };
  const actionPath = actionMap[action];
  if (!actionPath) {
    throw new Error(`Unsupported fault action: ${action}`);
  }
  const payload = await api(`/system/faults/${encodeURIComponent(failureId)}/${actionPath}`, {
    method: "POST",
    body: JSON.stringify({ reason, dry_run: dryRun }),
  });

  const feedback = document.getElementById("system-feedback");
  if (dryRun) {
    const blockers = payload.blockers?.length ? ` Blockers: ${payload.blockers.join(" | ")}` : "";
    feedback.textContent = payload.allowed
      ? `Dry run passed for ${actionPath} on failure ${failureId}.`
      : `Dry run blocked for ${actionPath} on failure ${failureId}.${blockers}`;
    await Promise.all([refreshFaults(), refreshHealth()]);
    return;
  }

  if (action === "retry") {
    feedback.textContent = (
      `Retry task ${payload.retry_task.task_id} queued for source task ${payload.source_task_id}.`
    );
  } else if (action === "resolve") {
    feedback.textContent = `Failure ${payload.failure_id} marked as resolved.`;
  } else {
    feedback.textContent = `Goal ${payload.goal.goal_id} requeued to state ${payload.goal.state}.`;
  }
  await refreshAll();
}

async function runFaultBulkResolve() {
  ensureMutationAllowed("Fault bulk resolve");
  const reasonInput = document.getElementById("fault-remediation-reason");
  const dryRunInput = document.getElementById("fault-remediation-dry-run");
  const limitInput = document.getElementById("fault-bulk-limit");
  const reason = (reasonInput?.value || "").trim();
  const dryRun = Boolean(dryRunInput?.checked);
  const parsedLimit = Number(limitInput?.value || 50);
  const limit = Number.isFinite(parsedLimit)
    ? Math.max(1, Math.min(500, Math.trunc(parsedLimit)))
    : 50;
  if (!reason) {
    throw new Error("Remediation reason is required.");
  }

  const payload = {
    reason,
    dry_run: dryRun,
    ...collectFaultFilters(),
    limit,
  };
  const result = await api("/system/faults/resolve_bulk", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  const feedback = document.getElementById("system-feedback");
  if (dryRun) {
    feedback.textContent = (
      `Dry run: ${result.will_resolve_count} filtered faults would be resolved `
      + `(${result.skipped_already_resolved_count} already resolved skipped).`
    );
    await Promise.all([refreshFaults(), refreshHealth()]);
    return;
  }
  feedback.textContent = (
    `Bulk resolve completed: ${result.resolved_count} failures resolved `
    + `(${result.skipped_already_resolved_count} already resolved skipped).`
  );
  await refreshAll();
}

document.getElementById("goal-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("goal-error", null);
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  try {
    ensureMutationAllowed("Goal creation");
    const goal = await api("/goals", {
      method: "POST",
      body: JSON.stringify({
        title: form.get("title"),
        description: form.get("description"),
        urgency: Number(form.get("urgency")),
        value: Number(form.get("value")),
        deadline_score: Number(form.get("deadline_score")),
      }),
    });
    selectedGoalId = goal.goal_id;
    document.getElementById("task-goal-id").value = goal.goal_id;
    document.getElementById("event-correlation-id").value = goal.goal_id;
    document.getElementById("trace-goal-id").value = goal.goal_id;
    document.getElementById("fault-goal-id").value = goal.goal_id;
    formElement.reset();
    await refreshAll();
  } catch (error) {
    showError("goal-error", error);
  }
});

document.getElementById("task-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("task-error", null);
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  const goalId = String(form.get("goal_id") || "").trim();
  try {
    ensureMutationAllowed("Task creation");
    await api("/tasks", {
      method: "POST",
      body: JSON.stringify({
        goal_id: goalId,
        title: form.get("title"),
      }),
    });
    formElement.reset();
    document.getElementById("task-goal-id").value = goalId;
    await refreshAll();
  } catch (error) {
    showError("task-error", error);
  }
});

document.getElementById("workflow-start-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("workflow-error", null);
  const workflowId = document.getElementById("workflow-select").value.trim();
  try {
    await runWorkflowStart(workflowId);
  } catch (error) {
    showError("workflow-error", error);
  }
});

document.getElementById("workflow-reap-runs").addEventListener("click", async () => {
  try {
    showError("workflow-error", null);
    await runWorkflowReaper();
  } catch (error) {
    showError("workflow-error", error);
  }
});

document.getElementById("event-filter-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await refreshEvents();
});

document.getElementById("audit-filter-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await refreshAudit();
});

document.getElementById("fault-filter-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await refreshFaults();
});

document.getElementById("bulk-resolve-faults").addEventListener("click", async () => {
  try {
    showError("system-feedback", null);
    await runFaultBulkResolve();
  } catch (error) {
    showError("system-feedback", error);
  }
});

document.getElementById("flow-trace-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await refreshFlowTrace();
});

document.getElementById("refresh-goals").addEventListener("click", async () => {
  await Promise.all([refreshGoals(), refreshPlannerReviewInbox(), refreshPlannerDeferredFollowups()]);
});
document.getElementById("refresh-tasks").addEventListener("click", refreshTasks);
document.getElementById("refresh-workflows").addEventListener("click", refreshWorkflows);
document.getElementById("refresh-events").addEventListener("click", refreshEvents);
document.getElementById("refresh-flow-trace").addEventListener("click", refreshFlowTrace);
document.getElementById("refresh-audit").addEventListener("click", refreshAudit);
document.getElementById("refresh-faults").addEventListener("click", refreshFaults);
document.getElementById("refresh-queue").addEventListener("click", refreshQueue);
document.getElementById("global-filter").addEventListener("input", (event) => {
  globalFilterTerm = String(event.target.value || "").trim().toLowerCase();
  rerenderFilteredViews();
  updateToolbarStatus();
});
document.getElementById("toggle-refresh").addEventListener("click", async () => {
  setAutoRefresh(!autoRefreshEnabled);
  if (autoRefreshEnabled) {
    await refreshAll();
  }
});
document.getElementById("toggle-density").addEventListener("click", () => {
  toggleDensityMode();
});
document.getElementById("toggle-visual-mode").addEventListener("click", () => {
  toggleVisualMode();
});

function isTextInputTarget(target) {
  return target instanceof HTMLInputElement
    || target instanceof HTMLTextAreaElement
    || target instanceof HTMLSelectElement
    || target?.isContentEditable;
}

function handleDesktopShortcut(event) {
  if (!IS_DESKTOP_MODE || !event.ctrlKey || event.altKey) {
    return false;
  }
  const key = String(event.key || "").toLowerCase();
  if (event.shiftKey && key === "f") {
    event.preventDefault();
    const filterInput = document.getElementById("global-filter");
    filterInput?.focus();
    filterInput?.select();
    return true;
  }
  if (event.shiftKey && key === "r") {
    event.preventDefault();
    refreshAll().catch((error) => {
      showError("system-feedback", error);
    });
    return true;
  }
  if (!event.shiftKey && ["1", "2", "3"].includes(key) && !isTextInputTarget(event.target)) {
    event.preventDefault();
    const presetByShortcut = { "1": "warm", "2": "graphite", "3": "signal" };
    setVisualMode(presetByShortcut[key]);
    return true;
  }
  return false;
}

window.addEventListener("scroll", () => {
  if (jumpScrollScheduled) {
    return;
  }
  jumpScrollScheduled = true;
  window.requestAnimationFrame(() => {
    updateActiveJumpByScroll();
    jumpScrollScheduled = false;
  });
}, { passive: true });

document.addEventListener("keydown", (event) => {
  if (handleDesktopShortcut(event)) {
    return;
  }
  if (event.key !== "/") {
    return;
  }
  if (isTextInputTarget(event.target)) {
    return;
  }
  event.preventDefault();
  const filterInput = document.getElementById("global-filter");
  filterInput?.focus();
  filterInput?.select();
});

document.addEventListener("click", async (event) => {
  const goalAction = event.target.dataset.goalAction;
  const goalId = event.target.dataset.goalId;
  const taskAction = event.target.dataset.taskAction;
  const taskId = event.target.dataset.taskId;
  const faultAction = event.target.dataset.faultAction;
  const failureId = event.target.dataset.failureId;
  const workflowStart = event.target.dataset.workflowStart;
  const workflowCancel = event.target.dataset.workflowCancel;
  const correlation = event.target.dataset.correlation;
  const selectGoal = event.target.dataset.selectGoal;
  const planGoal = event.target.dataset.planGoal;
  const planSuggestionIndex = event.target.dataset.planSuggestionIndex;
  const planReviewIndex = event.target.dataset.planReviewIndex;
  const planReviewDecision = event.target.dataset.planReviewDecision;
  const planReviewReopenIndex = event.target.dataset.planReviewReopenIndex;
  const planBulkCreate = event.target.dataset.planBulkCreate;
  const planBulkReviewDecision = event.target.dataset.planBulkReviewDecision;
  const planInboxStatus = event.target.dataset.planInboxStatus;
  const planFollowupsRefresh = event.target.dataset.planFollowupsRefresh;
  const planFollowupReopenGoal = event.target.dataset.planFollowupReopenGoal;
  const planFollowupReopenIndex = event.target.dataset.planFollowupReopenIndex;
  const planNextReview = event.target.dataset.planNextReview;
  const operatorAction = event.target.dataset.operatorAction;
  const jumpTarget = event.target.dataset.jumpTarget;

  try {
    if (jumpTarget) {
      jumpToSection(jumpTarget);
      return;
    }

    if (operatorAction) {
      await runOperatorAction(operatorAction);
    }

    if (faultAction && failureId) {
      await runFaultAction(faultAction, failureId);
    }

    if (workflowStart) {
      document.getElementById("workflow-select").value = workflowStart;
      await runWorkflowStart(workflowStart);
    }

    if (workflowCancel) {
      await runWorkflowCancel(workflowCancel);
    }

    if (goalAction && goalId) {
      ensureMutationAllowed(`Goal action ${goalAction}`);
      await api(`/goals/${goalId}/${goalAction}`, { method: "POST" });
      selectedGoalId = goalId;
      document.getElementById("task-goal-id").value = goalId;
      document.getElementById("event-correlation-id").value = goalId;
      document.getElementById("trace-goal-id").value = goalId;
      document.getElementById("fault-goal-id").value = goalId;
      await refreshAll();
    }

    if (planGoal) {
      await runPlannerPreview(planGoal);
      setActiveJump("goals-section");
    }

    if (planInboxStatus) {
      plannerReviewInboxStatus = planInboxStatus;
      await refreshPlannerReviewInbox();
    }

    if (planFollowupsRefresh) {
      await refreshPlannerDeferredFollowups();
    }

    if (planFollowupReopenGoal && planFollowupReopenIndex !== undefined) {
      showError("goal-error", null);
      await reopenPlannerDeferredFollowup(planFollowupReopenGoal, planFollowupReopenIndex);
    }

    if (planNextReview) {
      const nextGoalId = nextPlannerReviewGoalId();
      if (!nextGoalId) {
        throw new Error("No planner review item is available in the current inbox view.");
      }
      await runPlannerPreview(nextGoalId);
      setActiveJump("goals-section");
    }

    if (planSuggestionIndex !== undefined) {
      showError("task-error", null);
      await createTaskFromPlannerSuggestion(planSuggestionIndex);
    }

    if (planReviewIndex !== undefined && planReviewDecision) {
      showError("task-error", null);
      await reviewPlannerSuggestion(planReviewIndex, planReviewDecision);
    }

    if (planBulkReviewDecision) {
      showError("task-error", null);
      await reviewSelectedPlannerSuggestions(planBulkReviewDecision);
    }

    if (planReviewReopenIndex !== undefined) {
      showError("task-error", null);
      await reopenPlannerSuggestionReview(planReviewReopenIndex);
    }

    if (planBulkCreate) {
      showError("task-error", null);
      await createTasksFromSelectedPlannerSuggestions();
    }

    if (taskAction && taskId) {
      ensureMutationAllowed(`Task action ${taskAction}`);
      if (taskAction === "success") {
        await api(`/tasks/${taskId}/success`, { method: "POST" });
      } else {
        const failureMap = {
          skill: { failure_type: "SkillFailure", error_message: "Repeated skill failure" },
          execution: { failure_type: "ExecutionFailure", error_message: "Transient execution failure" },
          external: { failure_type: "ExternalFailure", error_message: "External dependency outage" },
        };
        await api(`/tasks/${taskId}/fail`, {
          method: "POST",
          body: JSON.stringify(failureMap[taskAction]),
        });
      }
      await refreshAll();
    }

    if (correlation) {
      document.getElementById("event-correlation-id").value = correlation;
      document.getElementById("trace-goal-id").value = correlation.split(":")[0];
      document.getElementById("fault-goal-id").value = correlation.split(":")[0];
      setActiveJump("events-section");
      await refreshEvents();
      await refreshFlowTrace();
      await refreshFaults();
    }

    if (selectGoal) {
      selectedGoalId = selectGoal;
      document.getElementById("task-goal-id").value = selectGoal;
      document.getElementById("event-correlation-id").value = selectGoal;
      document.getElementById("trace-goal-id").value = selectGoal;
      document.getElementById("fault-goal-id").value = selectGoal;
      setActiveJump("goals-section");
      await refreshTasks();
      await refreshEvents();
      await refreshFlowTrace();
      await refreshFaults();
      updateSelectedGoalLabel();
    }
  } catch (error) {
    const target = operatorAction || faultAction
      ? "system-feedback"
      : workflowStart || workflowCancel
        ? "workflow-error"
        : goalAction || planGoal || planInboxStatus || planFollowupsRefresh || planFollowupReopenGoal || planNextReview
          ? "goal-error"
          : "task-error";
    showError(target, error);
  }
});

document.addEventListener("change", async (event) => {
  if (!event.target.dataset.planInboxSort) {
    return;
  }
  plannerReviewInboxSort = event.target.value || "needs_review";
  try {
    await refreshPlannerReviewInbox();
  } catch (error) {
    showError("goal-error", error);
  }
});

try {
  const storedDensity = localStorage.getItem("goal_ops_density");
  setDensityMode(storedDensity === "compact" ? "compact" : "comfy");
} catch {
  setDensityMode("comfy");
}
try {
  const storedVisualMode = localStorage.getItem("goal_ops_visual_mode");
  setVisualMode(storedVisualMode);
} catch {
  setVisualMode("warm");
}
setAutoRefresh(true);
setActiveJump("goals-section");
updateActiveJumpByScroll();
refreshAll();
startAutoRefreshLoop();
