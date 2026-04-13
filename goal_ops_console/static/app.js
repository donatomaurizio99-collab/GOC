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
let defaultConsumerId = "goal_ops_console";
let autoRefreshEnabled = true;
let autoRefreshHandle = null;
let densityMode = "comfy";
let visualMode = "warm";
let globalFilterTerm = "";
let jumpScrollScheduled = false;

const viewCache = {
  goals: [],
  tasks: [],
  workflows: [],
  workflowRuns: [],
  events: [],
  auditEntries: [],
  faultSummary: {},
  faultEntries: [],
  queue: [],
  health: null,
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
  const desktopPart = IS_DESKTOP_MODE
    ? "Desktop shortcuts: Ctrl+1/2/3 visual, Ctrl+Shift+F filter, Ctrl+Shift+R refresh."
    : "";
  status.textContent = `${filterPart} ${refreshPart} ${densityPart} ${visualPart}${desktopPart ? ` ${desktopPart}` : ""}`;
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
  return actions.map(([action, label]) => {
    if (action === "trace") {
      return `<button class="secondary" data-correlation="${goal.goal_id}">${label}</button>`;
    }
    return `<button class="secondary" data-goal-action="${action}" data-goal-id="${goal.goal_id}">${label}</button>`;
  }).join("");
}

function renderTaskButtons(task) {
  const terminalStates = new Set(["poison", "exhausted", "succeeded"]);
  const buttons = [];
  if (!terminalStates.has(task.status)) {
    buttons.push(`<button class="secondary" data-task-action="success" data-task-id="${task.task_id}">Success</button>`);
    buttons.push(`<button class="secondary" data-task-action="skill" data-task-id="${task.task_id}">Skill Fail</button>`);
    buttons.push(`<button class="secondary" data-task-action="execution" data-task-id="${task.task_id}">Exec Fail</button>`);
    buttons.push(`<button class="secondary" data-task-action="external" data-task-id="${task.task_id}">External Fail</button>`);
  }
  buttons.push(`<button class="secondary" data-correlation="${task.goal_id}">Goal Trace</button>`);
  return buttons.join("");
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
  ]);
  if (!filteredTasks.length) {
    container.innerHTML = `<div class="meta">${filteredEmptyMessage("No tasks for the current selection.")}</div>`;
    return;
  }
  container.innerHTML = `<div class="stack-list">
    ${filteredTasks.map((task) => `
      <article class="entity-card">
        <div class="entity-header">
          <div>
            <div class="entity-title">${task.title}</div>
            <div class="meta">${task.task_id}</div>
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
}

function renderFaultRemediationButtons(item) {
  const buttons = [];
  if (["failed", "exhausted", "poison"].includes(item.task_status)) {
    buttons.push(
      `<button class="secondary" data-fault-action="retry" data-failure-id="${item.failure_id}">Retry Task</button>`,
    );
  }
  if (["blocked", "escalation_pending"].includes(item.goal_state)) {
    buttons.push(
      `<button class="secondary" data-fault-action="requeue_goal" data-failure-id="${item.failure_id}">Requeue Goal</button>`,
    );
  }
  if (item.failure_status !== "resolved") {
    buttons.push(
      `<button class="secondary" data-fault-action="resolve" data-failure-id="${item.failure_id}">Resolve</button>`,
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
  const throttled = Boolean(backpressure.is_throttled);
  const deadLetterTasks = faults.dead_letter_tasks || 0;

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
  `;
}

function renderHealth(health) {
  renderTopKpis(health);
  defaultConsumerId = health.default_consumer_id || defaultConsumerId;
  const consumerInput = document.getElementById("consumer-id");
  if (consumerInput && !consumerInput.value.trim()) {
    consumerInput.value = defaultConsumerId;
  }
  const backpressure = health.backpressure || {};
  const retention = health.retention || {};
  const metrics = health.metrics || {};
  const audit = health.audit || {};
  const faults = health.faults || {};
  document.getElementById("health-cards").innerHTML = `
    <div class="card"><span class="meta">Events</span><strong>${health.totals.events}</strong></div>
    <div class="card"><span class="meta">Goals</span><strong>${health.totals.goals}</strong></div>
    <div class="card"><span class="meta">Tasks</span><strong>${health.totals.tasks}</strong></div>
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

  document.getElementById("consumer-stats").innerHTML = health.consumer_stats.length
    ? `<table><thead><tr><th>Consumer</th><th>Status</th><th>Count</th></tr></thead><tbody>${
        health.consumer_stats.map((item) => `<tr><td>${item.consumer_id}</td><td>${item.status}</td><td>${item.count}</td></tr>`).join("")
      }</tbody></table>`
    : `<div class="meta">No consumer activity yet.</div>`;

  document.getElementById("stuck-events").innerHTML = health.stuck_events.length
    ? `<table><thead><tr><th>Consumer</th><th>Event</th><th>Started</th></tr></thead><tbody>${
        health.stuck_events.map((item) => `<tr><td>${item.consumer_id}</td><td>${item.event_type} (${item.event_id})</td><td>${item.processing_started_at}</td></tr>`).join("")
      }</tbody></table>`
    : `<div class="meta">No stuck events.</div>`;

  document.getElementById("invariant-violations").innerHTML = health.invariant_violations.length
    ? `<ul>${health.invariant_violations.map((item) => `<li>${item}</li>`).join("")}</ul>`
    : `<div class="meta">No invariant violations detected.</div>`;

  const topFaults = faults.top_error_hashes || [];
  document.getElementById("fault-snapshot").innerHTML = topFaults.length
    ? `<table><thead><tr><th>Type</th><th>Hash</th><th>Count</th></tr></thead><tbody>${
        topFaults.map((item) => `<tr><td>${item.failure_type}</td><td>${item.error_hash || "-"}</td><td>${item.count}</td></tr>`).join("")
      }</tbody></table>`
    : `<div class="meta">No dead-letter faults in snapshot.</div>`;
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
                  <button type="button" class="secondary" data-workflow-start="${item.workflow_id}" ${item.is_enabled ? "" : "disabled"}>Start</button>
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
              </td>
              <td><pre>${JSON.stringify(run.result_payload || {}, null, 2)}</pre></td>
            </tr>`).join("")}
        </tbody>
      </table></div>`
    : `<div class="meta">${filteredEmptyMessage("No workflow runs yet.")}</div>`;
}

async function refreshGoals() {
  const goals = await api("/goals");
  viewCache.goals = goals;
  renderGoals(viewCache.goals);
  updateSelectedGoalLabel();
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
  const health = await api("/system/health");
  viewCache.health = health;
  renderHealth(viewCache.health);
}

async function refreshAll() {
  await Promise.all([
    refreshGoals(),
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
  }
  await refreshAll();
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
  const requestedBy = document.getElementById("workflow-requested-by").value.trim() || "operator";
  const payloadText = document.getElementById("workflow-payload").value;
  const payload = parseWorkflowPayload(payloadText);
  const response = await api(`/workflows/${encodeURIComponent(workflowId)}/start`, {
    method: "POST",
    body: JSON.stringify({
      requested_by: requestedBy,
      payload,
    }),
  });
  const run = response.run;
  document.getElementById("system-feedback").textContent = (
    `Workflow ${run.workflow_name} finished with status ${run.status}.`
  );
  await Promise.all([refreshWorkflows(), refreshHealth(), refreshEvents()]);
}

async function runFaultAction(action, failureId) {
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

document.getElementById("refresh-goals").addEventListener("click", refreshGoals);
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
  const correlation = event.target.dataset.correlation;
  const selectGoal = event.target.dataset.selectGoal;
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

    if (goalAction && goalId) {
      await api(`/goals/${goalId}/${goalAction}`, { method: "POST" });
      selectedGoalId = goalId;
      document.getElementById("task-goal-id").value = goalId;
      document.getElementById("event-correlation-id").value = goalId;
      document.getElementById("trace-goal-id").value = goalId;
      document.getElementById("fault-goal-id").value = goalId;
      await refreshAll();
    }

    if (taskAction && taskId) {
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
      : workflowStart
        ? "workflow-error"
        : goalAction
          ? "goal-error"
          : "task-error";
    showError(target, error);
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
