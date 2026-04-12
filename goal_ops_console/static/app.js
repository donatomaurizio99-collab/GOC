const stateClass = (value) => `state-${String(value || "").replaceAll(" ", "_")}`;

let selectedGoalId = "";
let defaultConsumerId = "goal_ops_console";

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
  const content = goals.length
    ? `<div class="stack-list">
        ${goals.map((goal) => `
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
    : `<div class="meta">No goals yet.</div>`;
  document.getElementById("goals-table").innerHTML = content;
}

function renderTasks(tasks) {
  const container = document.getElementById("tasks-table");
  if (!tasks.length) {
    container.innerHTML = `<div class="meta">No tasks for the current selection.</div>`;
    return;
  }
  container.innerHTML = `<div class="stack-list">
    ${tasks.map((task) => `
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
  if (!events.length) {
    container.innerHTML = `<div class="meta">No events match the current filter.</div>`;
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
      ${events.map((event) => `
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
  if (!entries.length) {
    container.innerHTML = `<div class="meta">No audit entries yet.</div>`;
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
      ${entries.map((entry) => `
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

  if (!entries.length) {
    tableTarget.innerHTML = `<div class="meta">No fault records for current filter.</div>`;
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
      ${entries.map((item) => `
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
  if (!goals.length) {
    container.innerHTML = `<div class="meta">No queue entries yet.</div>`;
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
      ${goals.map((goal) => `
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

async function refreshGoals() {
  const goals = await api("/goals");
  renderGoals(goals);
  const selectedLabel = selectedGoalId ? `Selected goal: ${selectedGoalId}` : "No goal selected.";
  document.getElementById("selected-goal-label").textContent = selectedLabel;
}

async function refreshQueue() {
  const queue = await api("/system/queue");
  renderQueue(queue);
}

async function refreshTasks() {
  const goalId = document.getElementById("task-goal-id").value.trim();
  const path = goalId ? `/tasks?goal_id=${encodeURIComponent(goalId)}` : "/tasks";
  const tasks = await api(path);
  renderTasks(tasks);
}

async function refreshEvents() {
  const correlationId = document.getElementById("event-correlation-id").value.trim();
  const entityId = document.getElementById("event-entity-id").value.trim();
  const params = new URLSearchParams();
  if (correlationId) params.set("correlation_id", correlationId);
  if (entityId) params.set("entity_id", entityId);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const events = await api(`/events${suffix}`);
  renderEvents(events);
}

async function refreshAudit() {
  const action = document.getElementById("audit-action").value.trim();
  const status = document.getElementById("audit-status").value.trim();
  const params = new URLSearchParams();
  if (action) params.set("action", action);
  if (status) params.set("status", status);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const payload = await api(`/system/audit${suffix}`);
  renderAudit(payload.entries || []);
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
  renderFaults(summaryPayload, entryPayload.entries || []);
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
  renderHealth(health);
}

async function refreshAll() {
  await Promise.all([
    refreshGoals(),
    refreshTasks(),
    refreshEvents(),
    refreshFlowTrace(),
    refreshAudit(),
    refreshFaults(),
    refreshHealth(),
    refreshQueue(),
  ]);
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
document.getElementById("refresh-events").addEventListener("click", refreshEvents);
document.getElementById("refresh-flow-trace").addEventListener("click", refreshFlowTrace);
document.getElementById("refresh-audit").addEventListener("click", refreshAudit);
document.getElementById("refresh-faults").addEventListener("click", refreshFaults);
document.getElementById("refresh-queue").addEventListener("click", refreshQueue);

document.addEventListener("click", async (event) => {
  const goalAction = event.target.dataset.goalAction;
  const goalId = event.target.dataset.goalId;
  const taskAction = event.target.dataset.taskAction;
  const taskId = event.target.dataset.taskId;
  const faultAction = event.target.dataset.faultAction;
  const failureId = event.target.dataset.failureId;
  const correlation = event.target.dataset.correlation;
  const selectGoal = event.target.dataset.selectGoal;
  const operatorAction = event.target.dataset.operatorAction;

  try {
    if (operatorAction) {
      await runOperatorAction(operatorAction);
    }

    if (faultAction && failureId) {
      await runFaultAction(faultAction, failureId);
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
      await refreshTasks();
      await refreshEvents();
      await refreshFlowTrace();
      await refreshFaults();
      document.getElementById("selected-goal-label").textContent = `Selected goal: ${selectGoal}`;
    }
  } catch (error) {
    const target = operatorAction || faultAction ? "system-feedback" : goalAction ? "goal-error" : "task-error";
    showError(target, error);
  }
});

refreshAll();
setInterval(() => {
  refreshGoals().catch(() => {});
  refreshTasks().catch(() => {});
  refreshEvents().catch(() => {});
  refreshFlowTrace().catch(() => {});
  refreshAudit().catch(() => {});
  refreshFaults().catch(() => {});
  refreshHealth().catch(() => {});
  refreshQueue().catch(() => {});
}, 5000);
