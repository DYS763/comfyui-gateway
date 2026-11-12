const state = {
  tab: "tasks",
  tasks: {
    status: "",
    endpoint: "",
    search: "",
    limit: 20,
    offset: 0,
    total: 0,
    items: [],
    selectedJobId: null,
  },
  queue: { paused: false, queued: 0, waiting_node: 0, submitted: 0, running: 0 },
  works: { status: "", search: "", limit: 20, offset: 0, total: 0 },
  site: { days: 14 },
  visitors: { visitor_id: "", action: "", limit: 50, offset: 0, total: 0 },
  commentAdmin: { status: "", search: "", limit: 20, offset: 0, total: 0 },
  keys: {
    status: "",
    search: "",
    limit: 50,
    offset: 0,
    counts: { unused: 0, used: 0 },
  },
  reports: { status: "", limit: 24, offset: 0, total: 0, pending: 0 },
  superKeys: { usages: { id: null, key: "", limit: 20, offset: 0, total: 0 } },
  nodes: { items: [], editingId: null, loading: false },
  loras: { items: [], editingId: null, loading: false },
  logs: { kind: "audit", limit: 200 },
  nodeOwner: {
    endpoint: null,
    ownerAccountId: null,
    limit: 20,
    offset: 0,
    total: 0,
    search: "",
  },
};

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
};

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function qqContextLines(item) {
  const group = item.qq_group_name
    ? `${item.qq_group_name}${item.qq_group ? `（${item.qq_group}）` : ""}`
    : item.qq_group || "";
  const member = item.qq_name
    ? `${item.qq_name}${item.qq_number ? `（${item.qq_number}）` : ""}`
    : item.qq_number || "";
  const lines = [];
  if (group) lines.push(`群：${group}`);
  if (member) lines.push(`成员：${member}`);
  return lines;
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function statusLabel(status) {
  return {
    queued: "等待提交",
    waiting_node: "等待节点上线",
    submitted: "排队中",
    running: "生成中",
    completed: "已完成",
    failed: "生成失败",
    expired: "已过期",
    deleted: "任务记录已删除",
  }[status] || status;
}

function setMessage(element, text, isError = false) {
  element.textContent = text || "";
  element.classList.toggle("error", isError);
}

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", ...options });
  if (response.status === 401) {
    window.location.replace("/adminlogin");
    throw new Error("登录状态已失效，请重新登录。");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const raw = data.detail;
    const detail =
      Array.isArray(raw)
        ? "请求参数不合法"
        : raw && typeof raw === "object"
          ? raw.message || `请求失败（${response.status}）`
          : raw;
    const error = new Error(detail || `请求失败（${response.status}）`);
    error.status = response.status;
    throw error;
  }
  return data;
}

async function copyText(value) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(value);
      return;
    } catch (error) {
      console.warn("Clipboard API unavailable, using compatibility copy", error);
    }
  }
  const helper = document.createElement("textarea");
  helper.value = value;
  helper.setAttribute("readonly", "");
  helper.className = "admin-copy-helper";
  document.body.appendChild(helper);
  helper.select();
  helper.setSelectionRange(0, helper.value.length);
  const copied = document.execCommand("copy");
  helper.remove();
  if (!copied) throw new Error("浏览器拒绝了复制操作，请手动选择复制。");
}

function fieldGrid(fields) {
  const grid = el("dl", "admin-kv");
  for (const [name, value] of fields) {
    const row = el("div");
    row.appendChild(el("dt", "", name));
    row.appendChild(el("dd", "", value ?? "—"));
    grid.appendChild(row);
  }
  return grid;
}

function textBlock(title, text) {
  const block = el("div", "admin-text-block");
  block.appendChild(el("h3", "", title));
  block.appendChild(el("p", "admin-long-text", text || "（空）"));
  return block;
}

function copyButton(label, value) {
  const button = el("button", "secondary-button", label);
  button.type = "button";
  button.addEventListener("click", async () => {
    try {
      await copyText(value);
      button.textContent = "已复制";
      setTimeout(() => {
        button.textContent = label;
      }, 1500);
    } catch (error) {
      button.textContent = error.message;
    }
  });
  return button;
}

async function loadStats() {
  try {
    const [system, keys] = await Promise.all([api("/api/admin/system"), api("/api/admin/keys")]);
    const counts = system.tasks.by_status || {};
    const active = (counts.queued || 0) + (counts.submitted || 0) + (counts.running || 0);
    document.querySelector("#stat-total").textContent = system.tasks.total;
    document.querySelector("#stat-active").textContent = active;
    document.querySelector("#stat-completed").textContent = counts.completed || 0;
    document.querySelector("#stat-bad").textContent = (counts.failed || 0) + (counts.expired || 0);
    document.querySelector("#stat-keys").textContent = keys.counts.unused;
    document.querySelector("#stat-disk").textContent = formatBytes(system.jobs_dir.bytes);
    updateReportBadge(Number(system.pending_reports || 0));
  } catch (error) {
    console.warn("could not load admin stats", error);
  }
}

function applyQueueStatus(data) {
  state.queue = data;
  const button = document.querySelector("#queue-toggle");
  const active = Number(data.submitted || 0) + Number(data.running || 0);
  const waiting = Number(data.waiting_node || 0);
  button.textContent = data.paused ? "恢复队列" : "暂停队列";
  button.setAttribute("aria-pressed", data.paused ? "true" : "false");
  button.title = data.paused
    ? "恢复后会按照排队顺序继续提交任务"
    : "暂停后不再提交新任务，当前任务继续生成";
  document.querySelector("#queue-state").textContent = data.paused
    ? `队列已暂停 · 排队 ${data.queued} 个${waiting ? ` · 等待节点 ${waiting} 个` : ""}`
    : `队列运行中 · 排队 ${data.queued} 个${active ? ` · 生成 ${active} 个` : ""}${waiting ? ` · 等待节点 ${waiting} 个` : ""}`;
}

async function loadQueueStatus() {
  try {
    applyQueueStatus(await api("/api/admin/queue"));
  } catch (error) {
    console.warn("could not load queue status", error);
  }
}

async function toggleQueue() {
  const message = document.querySelector("#task-message");
  const button = document.querySelector("#queue-toggle");
  button.disabled = true;
  try {
    const data = await api("/api/admin/queue", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paused: !state.queue.paused }),
    });
    applyQueueStatus(data);
    setMessage(
      message,
      data.paused
        ? "已暂停队列：当前任务继续生成，恢复后按排队顺序继续提交。"
        : "已恢复队列：将按排队顺序继续提交任务。",
    );
  } catch (error) {
    setMessage(message, error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function loadRateLimitExemptions() {
  const message = document.querySelector("#ratelimit-exemption-message");
  setMessage(message, "正在加载限流豁免…");
  try {
    const data = await api("/api/admin/rate-limit-exemptions");
    renderRateLimitExemptions(data.items);
    document.querySelector("#ratelimit-exemption-summary").textContent =
      `共 ${data.items.length} 项豁免`;
    setMessage(message, data.items.length ? "" : "当前没有豁免项。");
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

function renderRateLimitExemptions(items) {
  const tbody = document.querySelector("#ratelimit-exemption-rows");
  tbody.replaceChildren();
  if (!items.length) {
    const row = el("tr");
    const cell = el("td", "admin-empty", "暂无豁免项。");
    cell.colSpan = 5;
    row.appendChild(cell);
    tbody.appendChild(row);
    return;
  }
  for (const item of items) {
    const row = el("tr");
    row.appendChild(
      el("td", "", item.value.includes("/") ? "CIDR 网段" : "单个 IP"),
    );
    row.appendChild(el("td", "admin-mono", item.value));
    row.appendChild(el("td", "admin-nowrap", formatDate(item.created_at)));
    row.appendChild(el("td", "", item.created_by || "—"));
    const action = el("td");
    const button = el("button", "secondary-button admin-compact", "删除");
    button.type = "button";
    button.addEventListener("click", () => deleteRateLimitExemption(item));
    action.appendChild(button);
    row.appendChild(action);
    tbody.appendChild(row);
  }
}

async function addRateLimitExemption() {
  const input = document.querySelector("#ratelimit-exemption-input");
  const button = document.querySelector("#ratelimit-exemption-add");
  const message = document.querySelector("#ratelimit-exemption-message");
  const value = input.value.trim();
  if (!value) {
    setMessage(message, "请输入 IP 或 CIDR。", true);
    return;
  }
  button.disabled = true;
  try {
    const data = await api("/api/admin/rate-limit-exemptions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value }),
    });
    input.value = "";
    await loadRateLimitExemptions();
    setMessage(message, data.created ? "已添加豁免项。" : "该豁免项已存在。");
  } catch (error) {
    setMessage(message, error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function deleteRateLimitExemption(item) {
  if (!window.confirm(`确定删除豁免项 ${item.value} 吗？`)) return;
  const message = document.querySelector("#ratelimit-exemption-message");
  try {
    await api(`/api/admin/rate-limit-exemptions/${item.id}`, {
      method: "DELETE",
    });
    await loadRateLimitExemptions();
    setMessage(message, "已删除豁免项。");
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

const NODE_STATUS_LABELS = {
  online: "在线",
  busy: "繁忙",
  offline: "离线",
  disabled: "禁用",
  unknown: "未知",
};

function nodeSvg(paths) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.8");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  for (const pathData of paths) {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", pathData);
    svg.appendChild(path);
  }
  return svg;
}

function nodeActionButton(label, paths, handler, disabled = false) {
  const button = el("button", "secondary-button node-icon-button");
  button.type = "button";
  button.title = label;
  button.setAttribute("aria-label", label);
  button.disabled = disabled;
  button.appendChild(nodeSvg(paths));
  button.addEventListener("click", handler);
  return button;
}

function nodeKindLabel(kind) {
  return kind === "gateway" ? "远程网关" : "ComfyUI";
}

function renderNodeRows(items) {
  const tbody = document.querySelector("#node-rows");
  tbody.replaceChildren();
  if (!items.length) {
    const row = el("tr");
    const cell = el("td", "admin-empty", "暂无节点。");
    cell.colSpan = 11;
    row.appendChild(cell);
    tbody.appendChild(row);
    return;
  }
  for (const item of items) {
    const row = el("tr");
    const statusCell = el("td");
    const status = el(
      "span",
      `node-status ${item.status}`,
      NODE_STATUS_LABELS[item.status] || item.status,
    );
    status.title = [item.last_error, formatDate(item.last_probe_at)]
      .filter(Boolean)
      .join(" · ");
    status.prepend(el("span", "node-status-dot"));
    statusCell.appendChild(status);
    row.appendChild(statusCell);

    const kindCell = el("td");
    kindCell.appendChild(
      el(
        "span",
        item.kind === "gateway" ? "state-pill gateway" : "state-pill",
        nodeKindLabel(item.kind),
      ),
    );
    row.appendChild(kindCell);
    row.appendChild(el("td", "", item.name || "—"));
    row.appendChild(el("td", "", item.public_name || "生成节点"));
    const ownerCell = el(
      "td",
      "admin-nowrap",
      item.owner_username || (item.owner_account_id ? item.owner_account_id : "未绑定"),
    );
    ownerCell.title = item.owner_account_id || "";
    row.appendChild(ownerCell);
    const noteCell = el("td", "", item.note || "—");
    noteCell.title = item.note || "";
    row.appendChild(noteCell);
    const addressCell = el("td", "admin-mono");
    addressCell.appendChild(el("div", "", item.base_url));
    if (item.kind === "gateway") {
      addressCell.appendChild(
        el("div", "admin-page-info", `账号：${item.remote_username || "—"}`),
      );
    }
    addressCell.title = [
      item.base_url,
      item.remote_username,
      item.remote_gateway_id,
    ]
      .filter(Boolean)
      .join(" · ");
    row.appendChild(addressCell);
    row.appendChild(
      el("td", "admin-mono", `${item.in_flight ?? 0} / ${item.max_concurrency}`),
    );
    row.appendChild(
      el(
        "td",
        "admin-nowrap",
        item.kind === "gateway" ? (item.remote_balance ?? "—") : "—",
      ),
    );
    const probeCell = el("td", "admin-nowrap", formatDate(item.last_probe_at));
    probeCell.title = item.remote_last_error || item.last_error || "";
    row.appendChild(probeCell);

    const actions = el("td");
    const actionWrap = el("div", "node-actions");
    actionWrap.appendChild(
      nodeActionButton("编辑节点", [
        "M12 20h9",
        "M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z",
      ], () => beginNodeEdit(item)),
    );
    if (item.kind === "gateway") {
      actionWrap.appendChild(
        nodeActionButton(
          "测试连接",
          ["M5 3l14 9-14 9Z"],
          () => testNode(item),
        ),
      );
      actionWrap.appendChild(
        nodeActionButton(
          "刷新能力与余额",
          ["M21 12a9 9 0 1 1-3-6.7", "M21 3v6h-6"],
          () => refreshNode(item),
        ),
      );
    }
    actionWrap.appendChild(
      nodeActionButton(
        item.enabled ? "禁用节点（不中断在途任务）" : "启用节点",
        ["M12 3v9", "M18.36 6.64a9 9 0 1 1-12.73 0"],
        () => toggleNode(item),
      ),
    );
    actionWrap.appendChild(
      nodeActionButton(
        "删除节点",
        ["M3 6h18", "M8 6V4h8v2", "M19 6l-1 14H6L5 6", "M10 11v5", "M14 11v5"],
        () => deleteNode(item),
        (item.in_flight ?? 0) > 0,
      ),
    );
    if (item.kind !== "gateway") {
      actionWrap.appendChild(
        nodeActionButton(
          item.owner_account_id ? "更换归属账号" : "绑定归属账号",
          ["M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2", "M9 7a4 4 0 1 0 0-8 4 4 0 0 0 0 8"],
          () => openNodeOwnerPicker(item),
        ),
      );
      if (item.owner_account_id) {
        actionWrap.appendChild(
          nodeActionButton(
            "解绑归属账号",
            ["M18 6 6 18", "M6 6l12 12"],
            () => unbindNodeOwner(item),
          ),
        );
      }
    }
    actions.appendChild(actionWrap);
    row.appendChild(actions);
    tbody.appendChild(row);
  }
}

async function loadNodes({ silent = false } = {}) {
  if (state.nodes.loading) return false;
  state.nodes.loading = true;
  const message = document.querySelector("#node-message");
  if (!silent) setMessage(message, "正在加载节点…");
  try {
    const data = await api("/api/admin/nodes");
    state.nodes.items = data.items;
    renderNodeRows(data.items);
    const online = data.items.filter((item) => item.status === "online").length;
    const busy = data.items.filter((item) => item.status === "busy").length;
    document.querySelector("#node-summary").textContent =
      `共 ${data.items.length} 个节点 · 在线 ${online} · 繁忙 ${busy}`;
    if (!silent) {
      setMessage(message, data.items.length ? "" : "当前没有节点。");
    }
    return true;
  } catch (error) {
    setMessage(message, error.message, true);
    return false;
  } finally {
    state.nodes.loading = false;
  }
}

function syncNodeKindFields() {
  const kind = document.querySelector("#node-kind").value;
  document
    .querySelector("#node-username-field")
    .classList.toggle("hidden", kind !== "gateway");
  document
    .querySelector("#node-password-field")
    .classList.toggle("hidden", kind !== "gateway");
  document.querySelector("#node-kind").disabled = Boolean(state.nodes.editingId);
}

function resetNodeForm() {
  state.nodes.editingId = null;
  document.querySelector("#node-kind").value = "comfyui";
  document.querySelector("#node-name").value = "";
  document.querySelector("#node-public-name").value = "";
  document.querySelector("#node-note").value = "";
  document.querySelector("#node-base-url").value = "";
  document.querySelector("#node-base-url").disabled = false;
  document.querySelector("#node-remote-username").value = "";
  document.querySelector("#node-remote-password").value = "";
  document.querySelector("#node-concurrency").value = "1";
  document.querySelector("#node-enabled").checked = true;
  document.querySelector("#node-save").textContent = "添加节点";
  document.querySelector("#node-cancel-edit").classList.add("hidden");
  document.querySelector("#node-edit-hint").textContent = "";
  syncNodeKindFields();
}

function beginNodeEdit(item) {
  state.nodes.editingId = item.id;
  document.querySelector("#node-kind").value =
    item.kind === "gateway" ? "gateway" : "comfyui";
  document.querySelector("#node-name").value = item.name || "";
  document.querySelector("#node-public-name").value = item.public_name || "";
  document.querySelector("#node-note").value = item.note || "";
  document.querySelector("#node-base-url").value = item.base_url || "";
  document.querySelector("#node-remote-username").value =
    item.remote_username || "";
  document.querySelector("#node-remote-password").value = "";
  document.querySelector("#node-concurrency").value = String(item.max_concurrency);
  document.querySelector("#node-enabled").checked = Boolean(item.enabled);
  document.querySelector("#node-save").textContent = "保存修改";
  document.querySelector("#node-cancel-edit").classList.remove("hidden");
  const baseInput = document.querySelector("#node-base-url");
  const hint = document.querySelector("#node-edit-hint");
  baseInput.disabled = (item.in_flight ?? 0) > 0;
  hint.textContent = baseInput.disabled
    ? `该节点有 ${item.in_flight} 个在途任务，完成后才能修改地址。`
    : "正在编辑节点；地址可修改。";
  syncNodeKindFields();
  document.querySelector(".admin-node-form").scrollIntoView({
    behavior: "smooth",
    block: "start",
  });
}

async function saveNode() {
  const message = document.querySelector("#node-message");
  const button = document.querySelector("#node-save");
  const kind = document.querySelector("#node-kind").value;
  const name = document.querySelector("#node-name").value.trim();
  const publicName = document.querySelector("#node-public-name").value.trim();
  const note = document.querySelector("#node-note").value.trim();
  const baseUrl = document.querySelector("#node-base-url").value.trim();
  const remoteUsername = document
    .querySelector("#node-remote-username")
    .value.trim();
  const remotePassword = document.querySelector("#node-remote-password").value;
  if (!name) {
    setMessage(message, "请填写内部名称。", true);
    return;
  }
  if (name.length > 64 || note.length > 200 || publicName.length > 80) {
    setMessage(message, "名称、备注或公开名称超出长度限制。", true);
    return;
  }
  if (!baseUrl) {
    setMessage(message, "请填写节点地址。", true);
    return;
  }
  if (kind === "gateway") {
    if (!remoteUsername) {
      setMessage(message, "请填写网关账号。", true);
      return;
    }
    if (!state.nodes.editingId && !remotePassword) {
      setMessage(message, "请填写网关密码。", true);
      return;
    }
  }
  const payload = {
    name,
    note,
    public_name: publicName,
    base_url: baseUrl,
    max_concurrency: Number.parseInt(
      document.querySelector("#node-concurrency").value,
      10,
    ),
    enabled: document.querySelector("#node-enabled").checked,
  };
  const editingId = state.nodes.editingId;
  if (!editingId) payload.kind = kind;
  if (kind === "gateway") {
    payload.remote_username = remoteUsername;
    if (remotePassword) payload.remote_password = remotePassword;
  }
  button.disabled = true;
  setMessage(message, editingId ? "正在保存…" : "正在连接远程网关…");
  try {
    await api(
      editingId ? `/api/admin/nodes/${editingId}` : "/api/admin/nodes",
      {
        method: editingId ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
    resetNodeForm();
    await loadNodes();
    setMessage(message, editingId ? "节点已保存。" : "节点已添加。");
  } catch (error) {
    setMessage(message, error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function testNode(item) {
  const message = document.querySelector("#node-message");
  setMessage(message, `正在测试 ${item.name}…`);
  try {
    const data = await api(`/api/admin/nodes/${item.id}/test`, {
      method: "POST",
    });
    if (!data.ok) {
      setMessage(message, `连接失败：${data.error || "未知错误"}`, true);
      return;
    }
    setMessage(
      message,
      `连接成功：网关 ${data.gateway_id} · 余额 ${data.balance ?? "—"}`,
    );
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

async function refreshNode(item) {
  const message = document.querySelector("#node-message");
  setMessage(message, `正在刷新 ${item.name} 的能力与余额…`);
  try {
    const data = await api(`/api/admin/nodes/${item.id}/refresh`, {
      method: "POST",
    });
    await loadNodes({ silent: true });
    setMessage(
      message,
      `刷新成功：余额 ${data.result?.balance ?? "—"}`,
    );
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

async function toggleNode(item) {
  const message = document.querySelector("#node-message");
  if (
    item.enabled &&
    (item.in_flight ?? 0) > 0 &&
    !window.confirm(
      `该节点有 ${item.in_flight} 个在途任务。禁用后不再派发新任务，但在途任务会继续执行。确定禁用吗？`,
    )
  ) {
    return;
  }
  try {
    await api(`/api/admin/nodes/${item.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !item.enabled }),
    });
    if (state.nodes.editingId === item.id) resetNodeForm();
    await loadNodes();
    setMessage(message, item.enabled ? "节点已禁用。" : "节点已启用。");
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

async function deleteNode(item) {
  const message = document.querySelector("#node-message");
  if (!window.confirm(`确定删除节点 ${item.name} 吗？`)) return;
  try {
    await api(`/api/admin/nodes/${item.id}`, { method: "DELETE" });
    if (state.nodes.editingId === item.id) resetNodeForm();
    await loadNodes();
    setMessage(message, "节点已删除。");
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

function renderLoraRows(items) {
  const tbody = document.querySelector("#lora-rows");
  tbody.replaceChildren();
  if (!items.length) {
    renderEmptyRow(tbody, 7, "暂无 LoRA 条目。");
    return;
  }
  for (const item of items) {
    const row = el("tr");

    const statusCell = el("td");
    statusCell.appendChild(
      el(
        "span",
        item.enabled ? "state-pill" : "state-pill expired",
        item.enabled ? "启用" : "禁用",
      ),
    );
    row.appendChild(statusCell);

    const filenameCell = el("td", "admin-mono", item.filename);
    filenameCell.title = item.filename;
    row.appendChild(filenameCell);
    row.appendChild(el("td", "", item.display_name));
    row.appendChild(el("td", "admin-mono", item.sort_order));
    row.appendChild(el("td", "", item.is_default ? "默认" : "—"));
    row.appendChild(el("td", "admin-nowrap", formatDate(item.created_at)));

    const actions = el("td");
    const actionWrap = el("div", "admin-report-actions");
    const edit = el("button", "secondary-button", "编辑");
    edit.type = "button";
    edit.addEventListener("click", () => beginLoraEdit(item));
    actionWrap.appendChild(edit);

    const toggle = el(
      "button",
      "secondary-button",
      item.enabled ? "禁用" : "启用",
    );
    toggle.type = "button";
    toggle.addEventListener("click", () => toggleLora(item));
    actionWrap.appendChild(toggle);

    const remove = el("button", "secondary-button", "删除");
    remove.type = "button";
    remove.addEventListener("click", () => deleteLora(item));
    actionWrap.appendChild(remove);

    actions.appendChild(actionWrap);
    row.appendChild(actions);
    tbody.appendChild(row);
  }
}

async function loadLoras() {
  if (state.loras.loading) return;
  state.loras.loading = true;
  const message = document.querySelector("#lora-message");
  setMessage(message, "正在加载 LoRA…");
  try {
    const data = await api("/api/admin/loras");
    state.loras.items = data.items;
    renderLoraRows(data.items);
    document.querySelector("#lora-summary").textContent =
      `共 ${data.total} 条 · 启用 ${data.counts.enabled} · 禁用 ${data.counts.disabled}`;
    setMessage(message, data.items.length ? "" : "当前没有 LoRA 条目。");
  } catch (error) {
    setMessage(message, error.message, true);
  } finally {
    state.loras.loading = false;
  }
}

function resetLoraForm() {
  state.loras.editingId = null;
  document.querySelector("#lora-filename").value = "";
  document.querySelector("#lora-display-name").value = "";
  document.querySelector("#lora-sort-order").value = "0";
  document.querySelector("#lora-enabled").checked = true;
  document.querySelector("#lora-save").textContent = "添加 LoRA";
  document.querySelector("#lora-cancel-edit").classList.add("hidden");
  document.querySelector("#lora-edit-hint").textContent = "";
}

function beginLoraEdit(item) {
  state.loras.editingId = item.id;
  document.querySelector("#lora-filename").value = item.filename || "";
  document.querySelector("#lora-display-name").value = item.display_name || "";
  document.querySelector("#lora-sort-order").value = String(item.sort_order ?? 0);
  document.querySelector("#lora-enabled").checked = Boolean(item.enabled);
  document.querySelector("#lora-save").textContent = "保存修改";
  document.querySelector("#lora-cancel-edit").classList.remove("hidden");
  document.querySelector("#lora-edit-hint").textContent =
    item.is_default
      ? "正在编辑默认条目；默认生成仍使用模板内置 LoRA。"
      : "正在编辑 LoRA 条目。";
  document.querySelector("#panel-loras .admin-node-form").scrollIntoView({
    behavior: "smooth",
    block: "start",
  });
}

async function saveLora() {
  const message = document.querySelector("#lora-message");
  const button = document.querySelector("#lora-save");
  const filename = document.querySelector("#lora-filename").value.trim();
  const displayName = document.querySelector("#lora-display-name").value.trim();
  const sortOrder = Number.parseInt(
    document.querySelector("#lora-sort-order").value,
    10,
  );
  if (!filename) {
    setMessage(message, "请填写文件名字。", true);
    return;
  }
  if (
    filename.length > 128 ||
    filename.includes("/") ||
    filename.includes("\\") ||
    filename.includes("..")
  ) {
    setMessage(message, "文件名字最多 128 个字符，不能包含路径分隔符或 ..。", true);
    return;
  }
  if (!displayName || displayName.length > 64) {
    setMessage(message, "显示名称必须为 1 到 64 个字符。", true);
    return;
  }
  if (!Number.isInteger(sortOrder) || sortOrder < 0 || sortOrder > 10000) {
    setMessage(message, "排序值必须在 0 到 10000 之间。", true);
    return;
  }
  const payload = {
    filename,
    display_name: displayName,
    enabled: document.querySelector("#lora-enabled").checked,
    sort_order: sortOrder,
  };
  const editingId = state.loras.editingId;
  button.disabled = true;
  try {
    await api(
      editingId ? `/api/admin/loras/${editingId}` : "/api/admin/loras",
      {
        method: editingId ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
    resetLoraForm();
    await loadLoras();
    setMessage(message, editingId ? "LoRA 条目已保存。" : "LoRA 条目已添加。");
  } catch (error) {
    setMessage(message, error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function toggleLora(item) {
  const message = document.querySelector("#lora-message");
  try {
    await api(`/api/admin/loras/${item.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        filename: item.filename,
        display_name: item.display_name,
        enabled: !item.enabled,
        sort_order: item.sort_order,
      }),
    });
    if (state.loras.editingId === item.id) resetLoraForm();
    await loadLoras();
    setMessage(message, item.enabled ? "LoRA 条目已禁用。" : "LoRA 条目已启用。");
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

async function deleteLora(item) {
  const message = document.querySelector("#lora-message");
  if (
    !window.confirm(
      `确定删除 LoRA 条目「${item.display_name}」吗？只删除条目，不会删除节点上的模型文件。`,
    )
  ) {
    return;
  }
  try {
    await api(`/api/admin/loras/${item.id}`, { method: "DELETE" });
    if (state.loras.editingId === item.id) resetLoraForm();
    await loadLoras();
    setMessage(message, "LoRA 条目已删除。");
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

async function loadTaskEndpointOptions() {
  const select = document.querySelector("#task-endpoint-filter");
  const current = state.tasks.endpoint;
  select.replaceChildren();
  const all = document.createElement("option");
  all.value = "";
  all.textContent = "全部节点";
  select.appendChild(all);
  try {
    const data = await api("/api/admin/nodes");
    for (const item of data.items) {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.public_name
        ? `${item.name} / ${item.public_name}`
        : item.name;
      select.appendChild(option);
    }
  } catch (error) {
    console.warn("could not load node options", error);
  }
  select.value = current || "";
  if (select.value !== (current || "")) {
    state.tasks.endpoint = "";
    select.value = "";
  }
}

async function loadTasks() {
  const message = document.querySelector("#task-message");
  const params = new URLSearchParams({
    limit: String(state.tasks.limit),
    offset: String(state.tasks.offset),
  });
  if (state.tasks.status) params.set("status", state.tasks.status);
  if (state.tasks.endpoint) params.set("endpoint_id", state.tasks.endpoint);
  if (state.tasks.search) params.set("search", state.tasks.search);
  setMessage(message, "正在加载任务…");
  try {
    const data = await api(`/api/admin/tasks?${params.toString()}`);
    state.tasks.total = data.total;
    renderTaskRows(data.items);
    const pages = Math.max(1, Math.ceil(data.total / state.tasks.limit));
    const page = Math.floor(state.tasks.offset / state.tasks.limit) + 1;
    document.querySelector("#task-page").textContent = `第 ${page} / ${pages} 页 · 共 ${data.total} 个任务`;
    document.querySelector("#task-prev").disabled = state.tasks.offset <= 0;
    document.querySelector("#task-next").disabled = state.tasks.offset + state.tasks.limit >= data.total;
    setMessage(message, data.items.length ? "" : "没有匹配的任务。");
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

function renderTaskRows(tasks) {
  state.tasks.items = tasks;
  const tbody = document.querySelector("#task-rows");
  tbody.replaceChildren();
  if (!tasks.length) {
    const row = el("tr");
    const cell = el("td", "admin-empty", "暂无任务。");
    cell.colSpan = 8;
    row.appendChild(cell);
    tbody.appendChild(row);
    updateTaskRetryUi();
    return;
  }
  for (const task of tasks) {
    const row = el("tr", "admin-task-row");
    row.dataset.jobId = task.job_id;
    if (state.tasks.selectedJobId === task.job_id) row.classList.add("selected");
    row.addEventListener("click", () => selectTask(task.job_id));
    row.appendChild(el("td", "admin-mono", task.job_id.slice(0, 12)));
    const statusCell = el("td");
    const statusClass = task.status === "failed" || task.status === "expired" ? `state-pill ${task.status}` : "state-pill";
    statusCell.appendChild(el("span", statusClass, statusLabel(task.status)));
    row.appendChild(statusCell);
    const promptCell = el("td", "admin-prompt-cell", task.prompt_preview || "—");
    promptCell.title = task.prompt_preview || "";
    row.appendChild(promptCell);
    const nodeCell = el("td", "", task.endpoint_name || "—");
    nodeCell.title = task.endpoint_public_name || "";
    row.appendChild(nodeCell);
    row.appendChild(el("td", "admin-mono", task.steps ?? "默认"));
    row.appendChild(el("td", "admin-mono", task.retrieval_count ?? 0));
    row.appendChild(el("td", "admin-nowrap", formatDate(task.created_at)));
    const actionCell = el("td");
    const button = el("button", "secondary-button", "详情");
    button.type = "button";
    button.addEventListener("click", () => openTask(task.job_id));
    actionCell.appendChild(button);
    row.appendChild(actionCell);
    tbody.appendChild(row);
  }
  updateTaskRetryUi();
}

function selectTask(jobId) {
  state.tasks.selectedJobId = jobId;
  for (const row of document.querySelectorAll("#task-rows .admin-task-row")) {
    row.classList.toggle("selected", row.dataset.jobId === jobId);
  }
  updateTaskRetryUi();
}

function selectedTask() {
  if (!state.tasks.selectedJobId) return null;
  return (
    state.tasks.items.find(
      (item) => item.job_id === state.tasks.selectedJobId,
    ) || null
  );
}

function canRetryTask(task) {
  return Boolean(
    task &&
      ["failed", "expired"].includes(task.status) &&
      !task.destroyed_at &&
      !task.published_at,
  );
}

function updateTaskRetryUi() {
  const button = document.querySelector("#task-retry");
  if (!button) return;
  const task = selectedTask();
  button.disabled = !canRetryTask(task);
  if (!task) {
    button.title = "请先在任务列表中选择一条失败或已过期的任务";
  } else if (!canRetryTask(task)) {
    button.title = "只有失败或已过期、且未销毁/发布的任务可以重试";
  } else {
    button.title = "使用原提示词和参数重新加入队列";
  }
}

async function openTask(jobId) {
  const container = document.querySelector("#task-detail");
  container.dataset.jobId = jobId;
  container.classList.remove("hidden");
  container.replaceChildren(el("p", "message", "正在加载任务详情…"));
  try {
    const data = await api(`/api/admin/tasks/${encodeURIComponent(jobId)}`);
    renderTaskDetail(data);
    container.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    container.replaceChildren(el("p", "message error", error.message));
  }
}

async function retryTask(jobId, button) {
  const message = document.querySelector("#task-message");
  button.disabled = true;
  try {
    await api(`/api/admin/tasks/${encodeURIComponent(jobId)}/retry`, {
      method: "POST",
    });
    await Promise.all([loadTasks(), loadQueueStatus(), loadStats()]);
    setMessage(message, "已重新加入队列，将按排队顺序重新提交。");
    const detail = document.querySelector("#task-detail");
    if (!detail.classList.contains("hidden") && detail.dataset.jobId === jobId) {
      await openTask(jobId);
    }
  } catch (error) {
    setMessage(message, error.message, true);
    button.disabled = false;
  }
}

function renderTaskDetail(data) {
  const container = document.querySelector("#task-detail");
  container.replaceChildren();

  const head = el("div", "admin-detail-head");
  const heading = el("div");
  heading.appendChild(el("p", "section-kicker", "TASK DETAIL"));
  heading.appendChild(el("h2", "", `任务 ${data.job_id}`));
  head.appendChild(heading);
  const close = el("button", "secondary-button", "收起");
  close.type = "button";
  close.addEventListener("click", () => container.classList.add("hidden"));
  const actions = el("div", "admin-detail-actions");
  actions.appendChild(close);
  head.appendChild(actions);
  container.appendChild(head);

  const statusClass = data.status === "failed" || data.status === "expired" ? `state-pill ${data.status}` : "state-pill";
  container.appendChild(el("span", statusClass, statusLabel(data.status)));

  const metadata = data.metadata;
  const fields = [
    ["生图账号", taskAccountLabel(data.account)],
    ["创建时间", formatDate(data.created_at)],
    ["提交时间", formatDate(data.submitted_at)],
    ["完成时间", formatDate(data.completed_at)],
    ["取图截止", formatDate(data.expires_at)],
    ["ComfyUI prompt ID", data.comfy_prompt_id || "—"],
    ["节点", data.endpoint_name || "—"],
    [
      "节点类型",
      data.endpoint_kind === "gateway"
        ? "远程网关"
        : data.endpoint_kind === "comfyui"
          ? "ComfyUI"
          : "—",
    ],
    ["公开节点名称", data.endpoint_public_name || "生成节点"],
    ["指定节点", data.requested_endpoint_id || "自动分配"],
    ["来源网关", data.origin_gateway_id || "本机"],
    ["链路跳数", data.hop ?? 0],
    ["远程状态", data.remote?.remote_status || "—"],
    ["远程任务 ID", data.remote?.remote_job_id || "—"],
    ["远程错误", data.remote?.last_error || "—"],
    [
      "LoRA",
      data.lora_disabled
        ? "不使用 LoRA"
        : data.lora_name || "默认（模板内置）",
    ],
    ["LoRA 文件名字", data.lora_filename || "—"],
    [
      "LoRA 强度",
      data.lora_disabled
        ? "不适用"
        : data.lora_strength ?? "—",
    ],
    ["失败原因", data.failure_reason || "—"],
    ["换节点次数", data.dispatch_switch_count ?? 0],
    ["最近进展", formatDate(data.last_progress_at)],
    ["队列位置", data.queue_position ?? "—"],
    ["步数", data.steps ?? "默认"],
    ["生图 seed", data.seed ?? metadata?.seed ?? "—"],
    ["自动重新排队", data.auto_requeue_count ?? 0],
    ["分辨率", data.width && data.height ? `${data.width}×${data.height}` : "—"],
    ["取图次数", data.retrieval_count ?? 0],
    ["首次取图", formatDate(data.first_retrieved_at)],
    ["最近取图", formatDate(data.last_retrieved_at)],
    ["销毁时间", formatDate(data.destroyed_at)],
    ["发布至画廊", formatDate(data.published_at)],
    ["作品名", data.work_name || "—"],
    ["作者", data.author || "—"],
    ["发布 IP", data.published_ip || "—"],
    ["提交访客", data.visitor_id || "—"],
    ["最后取图访客", data.last_retrieval_visitor_id || "—"],
    ["QQ 群号", data.qq_group || "—"],
    ["QQ 群名称", data.qq_group_name || "—"],
    ["QQ 成员号", data.qq_number || "—"],
    ["QQ 成员昵称", data.qq_name || "—"],
    ["生成模式", data.layout_json ? "MOSS Layout" : "普通文本"],
    ["公开画廊状态", data.published_at ? (data.public_removed_at ? "已下架" : "展示中") : "未发布"],
    ["下架时间", formatDate(data.public_removed_at)],
    ["下架操作人", data.public_removed_by || "—"],
    ["错误信息", data.error || "—"],
  ];
  if (metadata) {
    fields.push(
      ["客户端 IP", metadata.client_ip || "—"],
      ["X-Forwarded-For", metadata.x_forwarded_for || "—"],
      ["User-Agent", metadata.user_agent || "—"],
      ["工作流文件", metadata.workflow_file || "—"],
      ["密钥类型", metadata.key_type === "super" ? "超级密钥" : "一次性密钥"],
      ["超级密钥 ID", metadata.super_key_id ?? "—"],
      ["高级模式", metadata.advanced ? "是" : "否"],
      ["元数据更新时间", formatDate(metadata.updated_at)],
    );
  }
  container.appendChild(fieldGrid(fields));

  if (data.metadata_missing) {
    container.appendChild(
      el("p", "message error", "该任务缺少 metadata.json（可能由旧版本产生），部分审计字段无法显示。"),
    );
  }

  container.appendChild(el("div", "admin-hashes", `密钥哈希：${data.key_hash || "—"}　查询令牌哈希：${data.query_token_hash || "—"}`));
  container.appendChild(textBlock("正向提示词", data.prompt));
  container.appendChild(textBlock("负面提示词", data.negative_prompt));
  if (Array.isArray(data.bindings) && data.bindings.length) {
    const lines = data.bindings.map((binding) => {
      const stateLabel = {
        active: "生效中",
        abandoned: "已放弃",
        completed: "已完成",
        discarded: "已丢弃",
      }[binding.state] || binding.state;
      return [
        binding.endpoint_name || binding.endpoint_id,
        binding.comfy_prompt_id || "无 prompt_id",
        stateLabel,
        formatDate(binding.created_at),
      ].join(" · ");
    });
    container.appendChild(textBlock("节点绑定历史", lines.join("\n")));
  }
  if (data.layout_json) {
    const layoutBlock = el("div", "admin-layout-block");
    layoutBlock.appendChild(el("h3", "", "MOSS Layout JSON"));
    layoutBlock.appendChild(el("pre", "admin-layout-json", data.layout_json));
    container.appendChild(layoutBlock);
  }

}

let galleryRequestId = 0;
let galleryLastFocus = null;

function taskAccountLabel(account) {
  if (!account) return "未记录";
  const shortId =
    account.account_id && account.account_id.length > 8
      ? `${account.account_id.slice(0, 8)}…`
      : account.account_id || "—";
  if (account.status === "missing" || !account.username) {
    return `账号记录缺失（${shortId}）`;
  }
  if (account.status !== "active") {
    return `${account.username}（${accountStatusLabel(account.status)}）`;
  }
  return `${account.username}（${shortId}）`;
}

function renderGalleryMeta(data) {
  const meta = document.querySelector("#gallery-modal-meta");
  meta.replaceChildren();
  const status = data.status === "failed" || data.status === "expired" || data.status === "deleted"
    ? `state-pill ${data.status === "deleted" ? "failed" : data.status}`
    : "state-pill";
  meta.appendChild(el("span", status, statusLabel(data.status)));

  const metadata = data.metadata;
  const fields = [
    ["任务 ID", data.job_id],
    ["生图账号", taskAccountLabel(data.account)],
    ["创建时间", formatDate(data.created_at)],
    ["完成时间", formatDate(data.completed_at)],
    ["取图截止", formatDate(data.expires_at)],
    ["步数", data.steps ?? "默认"],
    ["生图 seed", data.seed ?? data.metadata?.seed ?? "—"],
    ["分辨率", data.width && data.height ? `${data.width}×${data.height}` : "—"],
    ["取图次数", data.retrieval_count ?? 0],
    ["ComfyUI prompt ID", data.comfy_prompt_id || "—"],
    ["执行节点", data.endpoint_name || data.endpoint_public_name || "—"],
    [
      "LoRA",
      data.lora_disabled
        ? "不使用 LoRA"
        : data.lora_name || "默认（模板内置）",
    ],
    ["客户端 IP", metadata ? (metadata.client_ip || "—") : "—"],
    ["销毁时间", formatDate(data.destroyed_at)],
    ["发布至画廊", formatDate(data.published_at)],
    ["作品名", data.work_name || "—"],
    ["作者", data.author || "—"],
    ["提交访客", data.visitor_id || "—"],
    ["QQ 群号", data.qq_group || "—"],
    ["QQ 群名称", data.qq_group_name || "—"],
    ["QQ 成员号", data.qq_number || "—"],
    ["QQ 成员昵称", data.qq_name || "—"],
    ["公开画廊状态", data.published_at ? (data.public_removed_at ? "已下架" : "展示中") : "未发布"],
    ["下架时间", formatDate(data.public_removed_at)],
  ];
  if (data.public_id) fields.push(["作品 ID", data.public_id]);
  if (data.like_count !== undefined) fields.push(["点赞数", data.like_count]);
  if (data.comment_count !== undefined) fields.push(["评论数", data.comment_count]);
  if (data.view_count !== undefined) fields.push(["浏览量", data.view_count]);
  meta.appendChild(fieldGrid(fields));
  if (data.metadata_missing) {
    meta.appendChild(el("p", "message", "该任务缺少 metadata.json，部分元信息不可用。"));
  }
  meta.appendChild(textBlock("正向提示词", data.prompt));
  meta.appendChild(textBlock("负面提示词", data.negative_prompt));
}

async function openWorkViewer(publicId, options = {}) {
  const modal = document.querySelector("#gallery-modal");
  const image = document.querySelector("#gallery-modal-image");
  const meta = document.querySelector("#gallery-modal-meta");
  const requestId = ++galleryRequestId;
  galleryLastFocus = document.activeElement;

  document.querySelector("#gallery-modal-title").textContent = "公开作品";
  image.onerror = () => {
    image.alt = "作品图片不可用";
    image.removeAttribute("src");
  };
  image.alt = "公开作品大图";
  image.src = `/api/admin/works/${encodeURIComponent(publicId)}/image`;
  meta.replaceChildren(el("p", "message", "正在加载作品详情…"));
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
  document.body.classList.add("admin-modal-open");
  document.querySelector("#gallery-modal-close").focus();

  try {
    const data = await api(`/api/admin/works/${encodeURIComponent(publicId)}`);
    if (requestId !== galleryRequestId || modal.classList.contains("hidden")) return;
    const work = data.work;
    document.querySelector("#gallery-modal-title").textContent =
      `作品：${work.work_name || publicId}`;
    let detail;
    if (data.task) {
      detail = {
        ...data.task,
        ...data.stats,
        public_id: work.public_id,
        work_name: work.work_name,
        author: work.author,
        published_at: work.published_at,
        published_ip: work.published_ip,
        public_removed_at: work.removed_at,
      };
    } else {
      detail = {
        job_id: work.job_id,
        status: "deleted",
        public_id: work.public_id,
        work_name: work.work_name,
        author: work.author,
        published_at: work.published_at,
        published_ip: work.published_ip,
        public_removed_at: work.removed_at,
        completed_at: work.completed_at,
        created_at: null,
        expires_at: null,
        prompt: work.prompt,
        negative_prompt: work.negative_prompt,
        steps: work.steps,
        seed: work.seed,
        width: work.width,
        height: work.height,
        lora_name: work.lora_name || "未知",
        metadata: data.metadata,
        metadata_missing: data.metadata === null,
        account: null,
        retrieval_count: data.stats.retrieval_count,
        ...data.stats,
      };
    }
    renderGalleryMeta(detail);
    const metaBox = document.querySelector("#gallery-modal-meta");
    if (!data.task) {
      metaBox.appendChild(
        el(
          "p",
          "message",
          "任务记录已删除；以下为公开作品快照。",
        ),
      );
    }
    if (options.comment) {
      metaBox.insertBefore(buildWorkCommentBlock(options.comment), metaBox.firstChild);
    }
  } catch (error) {
    if (requestId !== galleryRequestId || modal.classList.contains("hidden")) return;
    if (error.status === 404) {
      image.removeAttribute("src");
      meta.replaceChildren(
        el("span", "state-pill failed", "作品记录已删除"),
        el("p", "message", "该作品记录已不可查询，无法显示大图与详情。"),
      );
      return;
    }
    meta.replaceChildren(
      el("p", "message error", `作品详情加载失败：${error.message}`),
    );
  }
}

function buildWorkCommentBlock(comment) {
  const block = el("div", "admin-work-comment-block");
  block.appendChild(el("h3", "", "评论"));
  block.appendChild(
    fieldGrid([
      ["评论者", comment.display_name || "匿名"],
      ["评论时间", formatDate(comment.created_at)],
      ["状态", comment.hidden_at ? "已隐藏" : "展示中"],
      ["访客 ID", comment.visitor_id || "—"],
      ["IP", comment.client_ip || "—"],
    ]),
  );
  block.appendChild(textBlock("评论内容", comment.content));
  return block;
}

async function loadCleanupSettings() {
  const message = document.querySelector("#cleanup-message");
  try {
    const data = await api("/api/admin/cleanup-settings");
    document.querySelector("#auto-delete-expired").checked =
      Boolean(data.auto_delete_expired);
    document.querySelector("#auto-delete-destroyed").checked =
      Boolean(data.auto_delete_destroyed);
    setMessage(message, "");
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

async function saveCleanupSettings() {
  const message = document.querySelector("#cleanup-message");
  const button = document.querySelector("#cleanup-save");
  button.disabled = true;
  try {
    const data = await api("/api/admin/cleanup-settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        auto_delete_expired: document.querySelector("#auto-delete-expired").checked,
        auto_delete_destroyed: document.querySelector("#auto-delete-destroyed").checked,
      }),
    });
    document.querySelector("#auto-delete-expired").checked =
      Boolean(data.auto_delete_expired);
    document.querySelector("#auto-delete-destroyed").checked =
      Boolean(data.auto_delete_destroyed);
    setMessage(message, "自动清理设置已保存，后台每分钟直接删除符合条件的图片和任务记录。");
    loadStats();
  } catch (error) {
    setMessage(message, error.message, true);
  } finally {
    button.disabled = false;
  }
}

function openAdminFullscreen(src, alt) {
  if (!src) return;
  const layer = document.querySelector("#admin-fullscreen");
  const image = document.querySelector("#admin-fullscreen-image");
  image.src = src;
  image.alt = alt || "全屏图像";
  layer.classList.remove("hidden");
  layer.setAttribute("aria-hidden", "false");
  document.body.classList.add("admin-fullscreen-open");
  document.querySelector("#admin-fullscreen-close").focus();
}

function closeAdminFullscreen() {
  const layer = document.querySelector("#admin-fullscreen");
  if (layer.classList.contains("hidden")) return;
  layer.classList.add("hidden");
  layer.setAttribute("aria-hidden", "true");
  document.querySelector("#admin-fullscreen-image").removeAttribute("src");
  document.body.classList.remove("admin-fullscreen-open");
}

function closeGalleryModal() {
  const modal = document.querySelector("#gallery-modal");
  if (modal.classList.contains("hidden")) return;
  closeAdminFullscreen();
  galleryRequestId += 1;
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
  document.body.classList.remove("admin-modal-open");
  document.querySelector("#gallery-modal-image").removeAttribute("src");
  document.querySelector("#gallery-modal-meta").replaceChildren();
  if (galleryLastFocus && galleryLastFocus.focus) galleryLastFocus.focus();
  galleryLastFocus = null;
}

async function loadWorks() {
  const message = document.querySelector("#work-message");
  const params = new URLSearchParams({
    limit: String(state.works.limit),
    offset: String(state.works.offset),
  });
  if (state.works.status) params.set("status", state.works.status);
  if (state.works.search) params.set("search", state.works.search);
  setMessage(message, "正在加载公开画廊…");
  try {
    const data = await api(`/api/admin/works?${params.toString()}`);
    state.works.total = data.total;
    renderWorkRows(data.items);
    const pages = Math.max(1, Math.ceil(data.total / state.works.limit));
    const page = Math.floor(state.works.offset / state.works.limit) + 1;
    document.querySelector("#work-page").textContent = `第 ${page} / ${pages} 页`;
    document.querySelector("#work-summary").textContent = `共 ${data.total} 件公开作品`;
    document.querySelector("#work-prev").disabled = state.works.offset <= 0;
    document.querySelector("#work-next").disabled =
      state.works.offset + state.works.limit >= data.total;
    setMessage(message, data.items.length ? "" : "还没有公开作品。");
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

function renderWorkRows(items) {
  const tbody = document.querySelector("#work-rows");
  tbody.replaceChildren();
  if (!items.length) {
    const row = el("tr");
    const cell = el("td", "admin-empty", "暂无公开作品。");
    cell.colSpan = 7;
    row.appendChild(cell);
    tbody.appendChild(row);
    return;
  }
  for (const work of items) {
    const row = el("tr");

    const thumbCell = el("td");
    const thumbButton = el("button", "admin-work-thumb-button");
    thumbButton.type = "button";
    thumbButton.setAttribute(
      "aria-label",
      `查看作品大图：${work.work_name || work.public_id}`,
    );
    const image = el("img", "admin-work-thumb");
    image.loading = "lazy";
    image.alt = work.work_name || "作品缩略图";
    image.src = `/api/admin/works/${encodeURIComponent(work.public_id)}/thumbnail`;
    image.addEventListener("error", () => image.classList.add("hidden"));
    thumbButton.appendChild(image);
    thumbButton.addEventListener("click", () =>
      openWorkViewer(work.public_id),
    );
    thumbCell.appendChild(thumbButton);
    row.appendChild(thumbCell);

    const workCell = el("td");
    const workButton = el("button", "admin-work-open");
    workButton.type = "button";
    workButton.setAttribute(
      "aria-label",
      `查看作品大图与详情：${work.work_name || work.public_id}`,
    );
    workButton.appendChild(el("div", "admin-work-name", work.work_name || "未命名作品"));
    workButton.appendChild(el("div", "admin-work-id", work.public_id));
    workButton.addEventListener("click", () => openWorkViewer(work.public_id));
    workCell.appendChild(workButton);
    row.appendChild(workCell);

    row.appendChild(el("td", "", work.author || "匿名"));
    row.appendChild(el("td", "admin-nowrap", formatDate(work.published_at)));
    row.appendChild(el("td", "admin-mono", work.published_ip || "—"));

    const statusCell = el("td");
    statusCell.appendChild(
      work.public_removed_at
        ? el("span", "state-pill failed", "已下架")
        : el("span", "state-pill", "展示中"),
    );
    row.appendChild(statusCell);

    const actionCell = el("td", "admin-work-actions");
    const taskButton = el("button", "secondary-button", "任务");
    taskButton.type = "button";
    taskButton.addEventListener("click", () => {
      activateTab("tasks");
      openTask(work.job_id);
    });
    actionCell.appendChild(taskButton);

    const toggleButton = el(
      "button",
      "secondary-button",
      work.public_removed_at ? "恢复" : "下架",
    );
    toggleButton.type = "button";
    toggleButton.addEventListener("click", () => {
      if (work.public_removed_at) restoreWork(work);
      else removeWork(work);
    });
    actionCell.appendChild(toggleButton);
    row.appendChild(actionCell);

    tbody.appendChild(row);
  }
}

async function removeWork(work) {
  const message = document.querySelector("#work-message");
  const name = work.work_name || "未命名作品";
  if (!window.confirm(`确认从公开画廊下架「${name}」？图片和任务不会被删除。`)) return;
  try {
    await api(`/api/admin/works/${encodeURIComponent(work.public_id)}/remove`, {
      method: "POST",
    });
    setMessage(message, "已下架该作品。");
    await loadWorks();
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

async function restoreWork(work) {
  const message = document.querySelector("#work-message");
  const name = work.work_name || "未命名作品";
  if (!window.confirm(`确认恢复「${name}」到公开画廊？`)) return;
  try {
    await api(`/api/admin/works/${encodeURIComponent(work.public_id)}/restore`, {
      method: "POST",
    });
    setMessage(message, "已恢复展示。");
    await loadWorks();
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

const SITE_PAGE_LABELS = {
  "/": "首页",
  "/gallery": "作品画廊",
};

function sitePageLabel(path) {
  return SITE_PAGE_LABELS[path] || path;
}

function renderEmptyRow(tbody, colspan, text) {
  const row = el("tr");
  const cell = el("td", "admin-empty", text);
  cell.colSpan = colspan;
  row.appendChild(cell);
  tbody.appendChild(row);
}

function renderSiteDaily(rows) {
  const tbody = document.querySelector("#site-daily-rows");
  tbody.replaceChildren();
  if (!rows.length) {
    renderEmptyRow(tbody, 4, "暂无访问记录。");
    return;
  }
  const max = Math.max(1, ...rows.map((row) => Number(row.pv) || 0));
  for (const row of rows) {
    const line = el("tr");
    line.appendChild(el("td", "admin-nowrap", row.date));
    line.appendChild(el("td", "admin-mono", row.pv));
    line.appendChild(el("td", "admin-mono", row.uv));
    const barCell = el("td");
    const track = el("div", "admin-bar-track");
    const fill = el("div", "admin-bar-fill");
    fill.style.width = row.pv
      ? `${Math.max(4, Math.round((row.pv / max) * 100))}%`
      : "0";
    track.appendChild(fill);
    barCell.appendChild(track);
    line.appendChild(barCell);
    tbody.appendChild(line);
  }
}

function renderSitePages(rows) {
  const tbody = document.querySelector("#site-page-rows");
  tbody.replaceChildren();
  if (!rows.length) {
    renderEmptyRow(tbody, 3, "暂无访问记录。");
    return;
  }
  for (const row of rows) {
    const line = el("tr");
    const pathCell = el("td");
    pathCell.appendChild(el("div", "admin-work-name", sitePageLabel(row.path)));
    pathCell.appendChild(el("div", "admin-work-id", row.path));
    line.appendChild(pathCell);
    line.appendChild(el("td", "admin-mono", row.pv));
    line.appendChild(el("td", "admin-mono", row.uv));
    tbody.appendChild(line);
  }
}

function renderSiteRecent(rows) {
  const tbody = document.querySelector("#site-recent-rows");
  tbody.replaceChildren();
  if (!rows.length) {
    renderEmptyRow(tbody, 4, "暂无访问记录。");
    return;
  }
  for (const row of rows) {
    const line = el("tr");
    line.appendChild(el("td", "admin-nowrap", formatDate(row.created_at)));
    line.appendChild(el("td", "", sitePageLabel(row.path)));
    line.appendChild(el("td", "admin-visitor-id", row.visitor_id || "—"));
    line.appendChild(el("td", "admin-mono", row.client_ip || "—"));
    tbody.appendChild(line);
  }
}

async function loadSiteStats() {
  const message = document.querySelector("#stats-message");
  setMessage(message, "正在加载访问统计…");
  try {
    const data = await api(`/api/admin/site-stats?days=${state.site.days}`);
    document.querySelector("#site-stat-pv").textContent = data.totals.pv;
    document.querySelector("#site-stat-uv").textContent = data.totals.uv;
    document.querySelector("#site-stat-today-pv").textContent = data.today.pv;
    document.querySelector("#site-stat-today-uv").textContent = data.today.uv;
    document.querySelector("#site-stat-gallery").textContent = data.gallery_views.total;
    document.querySelector("#site-stat-gallery-works").textContent = data.gallery_views.works;
    renderSiteDaily(data.daily);
    renderSitePages(data.top_pages);
    renderSiteRecent(data.recent);
    setMessage(message, `统计范围：${data.range.start} ~ ${data.range.end}（服务器本地日期）`);
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

const VISITOR_ACTION_LABELS = {
  "task.submitted": "生成提交",
  "image.retrieved": "取图",
  "image.published": "发布至画廊",
  "image.destroyed": "销毁图片",
  "gallery.liked": "点赞",
  "gallery.unliked": "取消点赞",
  "gallery.commented": "评论",
};

async function loadVisitorEvents() {
  const message = document.querySelector("#visitor-message");
  const params = new URLSearchParams({
    limit: String(state.visitors.limit),
    offset: String(state.visitors.offset),
  });
  if (state.visitors.visitor_id) params.set("visitor_id", state.visitors.visitor_id);
  if (state.visitors.action) params.set("action", state.visitors.action);
  setMessage(message, "正在加载访客行为…");
  try {
    const data = await api(`/api/admin/visitor-events?${params.toString()}`);
    state.visitors.total = data.total;
    renderVisitorRows(data.items);
    const pages = Math.max(1, Math.ceil(data.total / state.visitors.limit));
    const page = Math.floor(state.visitors.offset / state.visitors.limit) + 1;
    document.querySelector("#visitor-page").textContent = `第 ${page} / ${pages} 页`;
    document.querySelector("#visitor-summary").textContent = `共 ${data.total} 条行为记录`;
    document.querySelector("#visitor-prev").disabled = state.visitors.offset <= 0;
    document.querySelector("#visitor-next").disabled =
      state.visitors.offset + state.visitors.limit >= data.total;
    setMessage(message, data.items.length ? "" : "没有匹配的访客行为。");
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

function renderVisitorRows(items) {
  const tbody = document.querySelector("#visitor-rows");
  tbody.replaceChildren();
  if (!items.length) {
    const row = el("tr");
    const cell = el("td", "admin-empty", "暂无访客行为记录。");
    cell.colSpan = 5;
    row.appendChild(cell);
    tbody.appendChild(row);
    return;
  }
  for (const event of items) {
    const row = el("tr");
    row.appendChild(el("td", "admin-nowrap", formatDate(event.created_at)));
    row.appendChild(el("td", "admin-visitor-id", event.visitor_id));
    row.appendChild(
      el("td", "admin-visitor-action", VISITOR_ACTION_LABELS[event.action] || event.action),
    );

    const relatedCell = el("td");
    const related = el("div", "admin-visitor-related");
    if (event.job_id) related.appendChild(el("span", "", `任务 ${event.job_id}`));
    if (event.public_id) related.appendChild(el("span", "", `作品 ${event.public_id}`));
    if (!event.job_id && !event.public_id) related.appendChild(el("span", "", "—"));
    relatedCell.appendChild(related);
    row.appendChild(relatedCell);

    row.appendChild(el("td", "admin-mono", event.client_ip || "—"));
    tbody.appendChild(row);
  }
}

async function loadAdminComments() {
  const message = document.querySelector("#admin-comment-message");
  const params = new URLSearchParams({
    limit: String(state.commentAdmin.limit),
    offset: String(state.commentAdmin.offset),
  });
  if (state.commentAdmin.status) params.set("status", state.commentAdmin.status);
  if (state.commentAdmin.search) params.set("search", state.commentAdmin.search);
  setMessage(message, "正在加载评论…");
  try {
    const data = await api(`/api/admin/comments?${params.toString()}`);
    state.commentAdmin.total = data.total;
    renderAdminCommentRows(data.items);
    const pages = Math.max(1, Math.ceil(data.total / state.commentAdmin.limit));
    const page = Math.floor(state.commentAdmin.offset / state.commentAdmin.limit) + 1;
    document.querySelector("#admin-comment-page").textContent = `第 ${page} / ${pages} 页`;
    document.querySelector("#admin-comment-summary").textContent = `共 ${data.total} 条评论`;
    document.querySelector("#admin-comment-prev").disabled = state.commentAdmin.offset <= 0;
    document.querySelector("#admin-comment-next").disabled =
      state.commentAdmin.offset + state.commentAdmin.limit >= data.total;
    setMessage(message, data.items.length ? "" : "没有匹配的评论。");
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

function renderAdminCommentRows(items) {
  const tbody = document.querySelector("#admin-comment-rows");
  tbody.replaceChildren();
  if (!items.length) {
    const row = el("tr");
    const cell = el("td", "admin-empty", "暂无评论。");
    cell.colSpan = 8;
    row.appendChild(cell);
    tbody.appendChild(row);
    return;
  }
  for (const comment of items) {
    const row = el("tr");
    row.appendChild(el("td", "admin-nowrap", formatDate(comment.created_at)));

    const workCell = el("td");
    const work = el("div", "admin-comment-work");
    work.appendChild(
      el("span", "admin-comment-work-name", comment.work_name || "未命名作品"),
    );
    work.appendChild(el("span", "admin-work-id", comment.public_id || "—"));
    workCell.appendChild(work);
    row.appendChild(workCell);

    row.appendChild(
      el(
        "td",
        comment.display_name ? "admin-comment-name" : "",
        comment.display_name || "匿名",
      ),
    );
    const contentCell = el("td");
    const contentButton = el("button", "admin-comment-open");
    contentButton.type = "button";
    contentButton.appendChild(
      el("div", "admin-comment-content", comment.content),
    );
    if (comment.public_id) {
      contentButton.title = "查看所属作品大图与详情";
      contentButton.setAttribute(
        "aria-label",
        `查看评论所属作品：${comment.work_name || comment.public_id}`,
      );
      contentButton.addEventListener("click", () =>
        openWorkViewer(comment.public_id, { comment }),
      );
    } else {
      contentButton.disabled = true;
      contentButton.title = "所属作品记录已删除";
    }
    contentCell.appendChild(contentButton);
    row.appendChild(contentCell);
    row.appendChild(el("td", "admin-visitor-id", comment.visitor_id || "—"));
    row.appendChild(el("td", "admin-mono", comment.client_ip || "—"));

    const statusCell = el("td");
    statusCell.appendChild(
      comment.hidden_at
        ? el("span", "state-pill failed", "已隐藏")
        : el("span", "state-pill", "展示中"),
    );
    if (comment.hidden_by) {
      statusCell.appendChild(el("div", "admin-work-id", `by ${comment.hidden_by}`));
    }
    row.appendChild(statusCell);

    const actionCell = el("td", "admin-comment-actions");
    const button = el(
      "button",
      "secondary-button",
      comment.hidden_at ? "恢复" : "隐藏",
    );
    button.type = "button";
    button.addEventListener("click", () => {
      if (comment.hidden_at) restoreAdminComment(comment);
      else hideAdminComment(comment);
    });
    actionCell.appendChild(button);
    row.appendChild(actionCell);
    tbody.appendChild(row);
  }
}

async function hideAdminComment(comment) {
  const message = document.querySelector("#admin-comment-message");
  const name = comment.display_name || "匿名";
  if (!window.confirm(`确认隐藏 ${name} 的评论？公开画廊将立即不再显示，可随时恢复。`)) {
    return;
  }
  try {
    await api(`/api/admin/comments/${comment.id}/hide`, { method: "POST" });
    setMessage(message, "已隐藏该评论。");
    await loadAdminComments();
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

async function restoreAdminComment(comment) {
  const message = document.querySelector("#admin-comment-message");
  const name = comment.display_name || "匿名";
  if (!window.confirm(`确认恢复 ${name} 的评论到公开画廊？`)) return;
  try {
    await api(`/api/admin/comments/${comment.id}/restore`, { method: "POST" });
    setMessage(message, "已恢复该评论。");
    await loadAdminComments();
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

const REPORT_STATUS_LABELS = {
  pending: "待处理",
  handled: "已处理",
  ignored: "已忽略",
};

function updateReportBadge(count) {
  const badge = document.querySelector("#reports-badge");
  if (!badge) return;
  badge.textContent = count > 0 ? String(count) : "";
  badge.classList.toggle("hidden", count <= 0);
}

async function loadReports() {
  const message = document.querySelector("#report-message");
  const params = new URLSearchParams({
    limit: String(state.reports.limit),
    offset: String(state.reports.offset),
  });
  if (state.reports.status) params.set("status", state.reports.status);
  setMessage(message, "正在加载举报…");
  try {
    const data = await api(`/api/admin/reports?${params.toString()}`);
    const maxOffset = Math.max(
      0,
      Math.floor(Math.max(0, data.total - 1) / data.limit) * data.limit,
    );
    if (data.total > 0 && data.offset > maxOffset) {
      state.reports.offset = maxOffset;
      return loadReports();
    }
    if (data.total === 0) state.reports.offset = 0;
    state.reports.total = data.total;
    state.reports.pending = data.pending;
    renderReportRows(data.items);
    const pages = Math.max(1, Math.ceil(data.total / data.limit));
    const page = data.total ? Math.floor(data.offset / data.limit) + 1 : 1;
    document.querySelector("#report-page").textContent =
      `第 ${page} / ${pages} 页 · 共 ${data.total} 件`;
    document.querySelector("#report-pending-info").textContent =
      `待处理 ${data.pending} 件`;
    document.querySelector("#report-prev").disabled = data.offset <= 0;
    document.querySelector("#report-next").disabled =
      data.offset + data.limit >= data.total;
    updateReportBadge(data.pending);
    setMessage(message, data.items.length ? "" : "没有举报记录。");
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

function renderReportRows(items) {
  const tbody = document.querySelector("#report-rows");
  tbody.replaceChildren();
  if (!items.length) {
    renderEmptyRow(tbody, 6, "没有举报记录。");
    return;
  }
  for (const item of items) {
    const row = el("tr");

    const workCell = el("td");
    const work = el("div", "admin-report-work");
    if (item.thumbnail_url) {
      const image = el("img", "admin-report-thumb");
      image.loading = "lazy";
      image.decoding = "async";
      image.alt = item.work_name || "举报作品缩略图";
      image.src = item.thumbnail_url;
      image.addEventListener("error", () => {
        image.replaceWith(
          el("div", "admin-report-thumb-missing", "图片不可用"),
        );
      });
      work.appendChild(image);
    } else {
      work.appendChild(el("div", "admin-report-thumb-missing", "作品已删除"));
    }
    const workText = el("div");
    workText.appendChild(el("div", "admin-report-work-id", item.public_id));
    work.appendChild(workText);
    workCell.appendChild(work);
    row.appendChild(workCell);

    const titleCell = el("td");
    titleCell.appendChild(el("div", "admin-work-name", item.work_name || "未命名作品"));
    if (!item.work_exists) {
      titleCell.appendChild(el("div", "admin-work-id", "作品已删除"));
    } else if (item.work_removed) {
      titleCell.appendChild(el("div", "admin-work-id", "作品已下架"));
    }
    row.appendChild(titleCell);
    const reportCell = el("td");
    reportCell.appendChild(el("div", "", `${item.report_count} 次`));
    reportCell.appendChild(
      el("div", "admin-work-id", formatDate(item.last_reported_at)),
    );
    row.appendChild(reportCell);

    const statusCell = el("td");
    statusCell.appendChild(
      el(
        "span",
        item.status === "pending" ? "state-pill failed" : "state-pill",
        REPORT_STATUS_LABELS[item.status] || item.status,
      ),
    );
    row.appendChild(statusCell);

    const handlerCell = el("td");
    if (item.handled_at || item.handled_by || item.handler_note) {
      handlerCell.appendChild(
        el("div", "admin-work-id", `${item.handled_by || "—"} · ${formatDate(item.handled_at)}`),
      );
      if (item.handler_note) {
        handlerCell.appendChild(el("div", "admin-long-text", item.handler_note));
      }
    } else {
      handlerCell.textContent = "—";
    }
    row.appendChild(handlerCell);

    const actionCell = el("td");
    const actions = el("div", "admin-report-actions");
    const view = el("button", "secondary-button", "查看作品");
    view.type = "button";
    const canView = item.work_exists && !item.work_removed && item.job_id;
    view.disabled = !canView;
    if (!item.work_exists) view.title = "作品已删除";
    else if (item.work_removed) view.title = "作品已下架";
    view.addEventListener("click", () => {
      if (!canView) return;
      window.open(
        `/gallery#work=${encodeURIComponent(item.public_id)}`,
        "_blank",
        "noopener,noreferrer",
      );
    });
    actions.appendChild(view);

    for (const status of ["handled", "ignored"]) {
      const button = el(
        "button",
        "secondary-button",
        status === "handled" ? "标记已处理" : "忽略",
      );
      button.type = "button";
      button.addEventListener("click", () => updateReportStatus(item, status));
      actions.appendChild(button);
    }

    const remove = el("button", "secondary-button", "删除");
    remove.type = "button";
    remove.addEventListener("click", () => deleteReport(item));
    actions.appendChild(remove);

    actionCell.appendChild(actions);
    row.appendChild(actionCell);
    tbody.appendChild(row);
  }
}

async function updateReportStatus(item, status) {
  const message = document.querySelector("#report-message");
  const note = window.prompt(
    status === "handled" ? "处理备注（可选，最多 200 字）" : "忽略原因（可选，最多 200 字）",
    item.handler_note || "",
  );
  if (note === null) return;
  try {
    await api(`/api/admin/reports/${encodeURIComponent(item.public_id)}/status`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status, note: note.trim() || null }),
    });
    setMessage(
      message,
      status === "handled" ? "已标记为已处理。" : "已标记为已忽略。",
    );
    await Promise.all([loadReports(), loadStats()]);
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

async function deleteReport(item) {
  const message = document.querySelector("#report-message");
  const label = item.work_name || item.public_id;
  if (
    !window.confirm(`确定删除「${label}」的全部举报记录吗？此操作不可恢复。`)
  ) {
    return;
  }
  try {
    await api(`/api/admin/reports/${encodeURIComponent(item.public_id)}`, {
      method: "DELETE",
    });
    setMessage(message, "已删除举报记录。");
    await Promise.all([loadReports(), loadStats()]);
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

function updateKeyActions(unusedCount, usedCount) {
  const noUnused = unusedCount <= 0;
  for (const id of ["copy-unused-keys", "export-unused-txt", "export-unused-csv"]) {
    document.querySelector(`#${id}`).disabled = noUnused;
  }
  const purge = document.querySelector("#key-purge-used");
  if (purge) purge.disabled = usedCount <= 0;
}

function renderKeyData(data) {
  renderKeyRows(data.items);
  const counts = data.counts || { unused: 0, used: 0 };
  state.keys.counts = counts;
  let summary = `未使用 ${counts.unused} · 已使用 ${counts.used} · 共 ${data.total}`;
  if (state.keys.status || state.keys.search) {
    summary += ` · 筛选结果 ${data.filtered_total} 条`;
  }
  document.querySelector("#key-summary").textContent = summary;
  const pages = Math.max(1, Math.ceil(data.filtered_total / data.limit));
  const page = data.filtered_total
    ? Math.floor(data.offset / data.limit) + 1
    : 1;
  document.querySelector("#key-page").textContent =
    `第 ${page} / ${pages} 页 · 共 ${data.filtered_total} 条`;
  document.querySelector("#key-prev").disabled = data.offset <= 0;
  document.querySelector("#key-next").disabled =
    data.offset + data.limit >= data.filtered_total;
  updateKeyActions(counts.unused, counts.used);
}

async function loadKeys() {
  const message = document.querySelector("#key-message");
  setMessage(message, "正在加载密钥…");
  const params = new URLSearchParams({
    limit: String(state.keys.limit),
    offset: String(state.keys.offset),
  });
  if (state.keys.status) params.set("status", state.keys.status);
  if (state.keys.search) params.set("search", state.keys.search);
  try {
    const data = await api(`/api/admin/keys?${params.toString()}`);
    const maxOffset = Math.max(
      0,
      Math.floor(Math.max(0, data.filtered_total - 1) / data.limit) * data.limit,
    );
    if (data.filtered_total > 0 && data.offset > maxOffset) {
      state.keys.offset = maxOffset;
      return loadKeys();
    }
    if (data.filtered_total === 0) state.keys.offset = 0;
    renderKeyData(data);
    setMessage(message, "");
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

async function purgeUsedKeys() {
  const message = document.querySelector("#key-message");
  const button = document.querySelector("#key-purge-used");
  const used = state.keys.counts.used || 0;
  if (used <= 0) {
    setMessage(message, "没有已使用的密钥。");
    return;
  }
  if (
    !window.confirm(`将永久删除 ${used} 条已使用密钥，无法恢复。`)
  ) {
    return;
  }
  button.disabled = true;
  setMessage(message, "正在清理已使用密钥…");
  try {
    const data = await api("/api/admin/keys/purge-used", { method: "POST" });
    state.keys.offset = 0;
    await Promise.all([loadKeys(), loadStats()]);
    setMessage(message, `已永久删除 ${data.deleted} 条已使用密钥。`);
  } catch (error) {
    setMessage(message, error.message, true);
    updateKeyActions(state.keys.counts.unused, state.keys.counts.used);
  }
}

function fileStamp() {
  const pad = (value) => String(value).padStart(2, "0");
  const now = new Date();
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`;
}

function csvCell(value) {
  let text = String(value);
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return `"${text.replace(/"/g, '""')}"`;
}

function downloadTextFile(filename, content, mimeType) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function fetchUnusedKeys(message) {
  const data = await api("/api/admin/keys/unused");
  const keys = data.items.map((entry) => entry.key);
  if (!keys.length) {
    setMessage(message, "没有未使用的密钥。");
    return null;
  }
  return keys;
}

async function copyUnusedKeys() {
  const message = document.querySelector("#key-message");
  try {
    const keys = await fetchUnusedKeys(message);
    if (!keys) return;
    await copyText(keys.join("\n"));
    setMessage(message, `已复制 ${keys.length} 个未使用密钥。`);
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

async function exportUnusedKeys(format) {
  const message = document.querySelector("#key-message");
  try {
    const keys = await fetchUnusedKeys(message);
    if (!keys) return;
    const stamp = fileStamp();
    if (format === "csv") {
      const rows = [["key", "status"], ...keys.map((key) => [key, "unused"])];
      const content = rows.map((row) => row.map(csvCell).join(",")).join("\r\n") + "\r\n";
      downloadTextFile(`unused-keys-${stamp}.csv`, content, "text/csv;charset=utf-8");
    } else {
      downloadTextFile(
        `unused-keys-${stamp}.txt`,
        keys.join("\n") + "\n",
        "text/plain;charset=utf-8",
      );
    }
    setMessage(message, `已导出 ${keys.length} 个未使用密钥（${format.toUpperCase()}）。`);
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

function renderKeyRows(items) {
  const tbody = document.querySelector("#key-rows");
  tbody.replaceChildren();
  if (!items.length) {
    const row = el("tr");
    const cell = el("td", "admin-empty", "暂无密钥。");
    cell.colSpan = 5;
    row.appendChild(cell);
    tbody.appendChild(row);
    return;
  }
  for (const entry of items) {
    const row = el("tr");
    row.appendChild(el("td", "admin-mono admin-key-cell", entry.key));
    row.appendChild(el("td", "", entry.status === "used" ? "已使用" : "未使用"));
    row.appendChild(el("td", "admin-mono", entry.job_id || "—"));
    row.appendChild(el("td", "admin-nowrap", formatDate(entry.used_at)));
    const actionCell = el("td");
    actionCell.appendChild(copyButton("复制", entry.key));
    row.appendChild(actionCell);
    tbody.appendChild(row);
  }
}

async function generateKeys() {
  const message = document.querySelector("#key-message");
  const count = Number.parseInt(document.querySelector("#key-count").value, 10);
  if (!Number.isInteger(count) || count < 1 || count > 200) {
    setMessage(message, "生成数量必须是 1 到 200 之间的整数。", true);
    return;
  }
  if (!window.confirm(`确定生成 ${count} 个新密钥吗？`)) return;
  const button = document.querySelector("#key-generate");
  button.disabled = true;
  setMessage(message, "正在生成…");
  try {
    const data = await api("/api/admin/keys/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ count }),
    });
    const box = document.querySelector("#generated-keys");
    box.classList.remove("hidden");
    document.querySelector("#generated-keys-text").textContent = data.keys.join("\n");
    setMessage(message, `已生成 ${data.keys.length} 个密钥，请立即复制。`);
    await Promise.all([loadKeys(), loadStats()]);
  } catch (error) {
    setMessage(message, error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function loadSuperKeys() {
  const message = document.querySelector("#superkey-message");
  setMessage(message, "正在加载超级密钥…");
  try {
    const data = await api("/api/admin/super-keys");
    renderSuperKeyRows(data.items);
    document.querySelector("#superkey-summary").textContent =
      `共 ${data.total} 个超级密钥 · 可用 ${data.counts.active} · 已耗尽 ${data.counts.exhausted} · 已冻结 ${data.counts.frozen}`;
    setMessage(message, data.items.length ? "" : "还没有超级密钥。");
    if (!data.items.length) closeSuperKeyUsages();
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

function renderSuperKeyRows(items) {
  const tbody = document.querySelector("#superkey-rows");
  tbody.replaceChildren();
  if (!items.length) {
    const row = el("tr");
    const cell = el("td", "admin-empty", "还没有超级密钥。");
    cell.colSpan = 9;
    row.appendChild(cell);
    tbody.appendChild(row);
    return;
  }
  for (const item of items) {
    const row = el("tr");
    row.appendChild(el("td", "admin-mono admin-superkey-value", item.key));
    row.appendChild(
      el("td", "admin-mono", item.quota === -1 ? "无限" : item.quota),
    );
    row.appendChild(el("td", "admin-mono", item.used_count));
    row.appendChild(
      el("td", "admin-mono", item.remaining === null ? "无限" : item.remaining),
    );

    const statusCell = el("td");
    statusCell.appendChild(
      item.status === "frozen"
        ? el("span", "state-pill failed", "已冻结")
        : item.status === "exhausted"
          ? el("span", "state-pill failed", "已耗尽")
          : el("span", "state-pill", "可用"),
    );
    if (item.frozen_at) {
      statusCell.appendChild(
        el(
          "div",
          "admin-work-id",
          `${formatDate(item.frozen_at)} · ${item.frozen_by || "—"}`,
        ),
      );
    }
    row.appendChild(statusCell);
    const noteCell = el("td", "admin-superkey-note", item.note || "—");
    noteCell.title = item.note || "";
    row.appendChild(noteCell);
    row.appendChild(el("td", "admin-nowrap", formatDate(item.created_at)));
    row.appendChild(el("td", "admin-nowrap", formatDate(item.last_used_at)));

    const actionCell = el("td", "admin-work-actions");
    actionCell.appendChild(copyButton("复制", item.key));
    const usageButton = el("button", "secondary-button", "使用记录");
    usageButton.type = "button";
    usageButton.addEventListener("click", () => openSuperKeyUsages(item));
    actionCell.appendChild(usageButton);

    const quotaButton = el("button", "secondary-button", "补充额度");
    quotaButton.type = "button";
    quotaButton.disabled = item.quota === -1;
    quotaButton.title = item.quota === -1 ? "无限额度无需补充" : "";
    quotaButton.addEventListener("click", () => addSuperKeyQuota(item));
    actionCell.appendChild(quotaButton);

    const deductButton = el("button", "secondary-button", "扣减额度");
    deductButton.type = "button";
    deductButton.addEventListener("click", () => deductSuperKeyQuota(item));
    actionCell.appendChild(deductButton);

    const setQuotaButton = el("button", "secondary-button", "设定额度");
    setQuotaButton.type = "button";
    setQuotaButton.addEventListener("click", () => setSuperKeyQuota(item));
    actionCell.appendChild(setQuotaButton);

    const noteButton = el("button", "secondary-button", "编辑备注");
    noteButton.type = "button";
    noteButton.addEventListener("click", () => editSuperKeyNote(item));
    actionCell.appendChild(noteButton);

    const deleteButton = el("button", "secondary-button", "删除");
    deleteButton.type = "button";
    deleteButton.addEventListener("click", () => deleteSuperKey(item));
    actionCell.appendChild(deleteButton);

    const freezeButton = el(
      "button",
      "secondary-button",
      item.frozen_at ? "解冻" : "冻结",
    );
    freezeButton.type = "button";
    freezeButton.addEventListener("click", () => toggleSuperKeyFreeze(item));
    actionCell.appendChild(freezeButton);
    row.appendChild(actionCell);
    tbody.appendChild(row);
  }
}

async function createSuperKey() {
  const message = document.querySelector("#superkey-message");
  const keyInput = document.querySelector("#superkey-value");
  const quotaInput = document.querySelector("#superkey-quota");
  const button = document.querySelector("#superkey-create");
  const key = keyInput.value.trim();
  const quota = Number.parseInt(quotaInput.value, 10);
  const keyLength = Array.from(key).length;
  if (!key) {
    setMessage(message, "请填写超级密钥。", true);
    keyInput.focus();
    return;
  }
  if (keyLength < 8 || keyLength > 128) {
    setMessage(message, "超级密钥长度必须是 8~128 个字符。", true);
    keyInput.focus();
    return;
  }
  if (/\s/.test(key)) {
    setMessage(message, "超级密钥不能包含空白字符。", true);
    keyInput.focus();
    return;
  }
  if (!Number.isInteger(quota) || quota === 0 || quota < -1 || quota > 1000000) {
    setMessage(message, "额度必须是 -1（无限）或 1~1000000 之间的整数。", true);
    quotaInput.focus();
    return;
  }
  const quotaText = quota === -1 ? "无限" : `${quota} 次`;
  if (!window.confirm(`确认创建超级密钥「${key}」，额度 ${quotaText}？`)) return;

  button.disabled = true;
  setMessage(message, "正在创建…");
  try {
    await api("/api/admin/super-keys", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key, quota }),
    });
    keyInput.value = "";
    await loadSuperKeys();
    setMessage(message, "超级密钥已创建。");
  } catch (error) {
    setMessage(message, error.message, true);
  } finally {
    button.disabled = false;
  }
}

function randomSuperKeyValue() {
  const bytes = new Uint8Array(24);
  window.crypto.getRandomValues(bytes);
  return `sk_${Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("")}`;
}

async function addSuperKeyQuota(item) {
  const message = document.querySelector("#superkey-message");
  const input = window.prompt(
    `为超级密钥「${item.key}」补充额度，请输入增加次数：`,
    "100",
  );
  if (input === null) return;
  const amount = Number.parseInt(input, 10);
  if (!Number.isInteger(amount) || amount < 1 || amount > 1000000) {
    setMessage(message, "补充次数必须是 1~1000000 之间的整数。", true);
    return;
  }
  try {
    const data = await api(`/api/admin/super-keys/${item.id}/quota`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ amount }),
    });
    await loadSuperKeys();
    setMessage(
      message,
      `已补充 ${amount} 次，当前额度 ${data.quota === -1 ? "无限" : data.quota}。`,
    );
    if (state.superKeys.usages.id === item.id) loadSuperKeyUsages();
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

async function deductSuperKeyQuota(item) {
  const message = document.querySelector("#superkey-message");
  const input = window.prompt(
    `为超级密钥「${item.key}」扣减额度，请输入扣减次数：`,
    "1",
  );
  if (input === null) return;
  const amount = Number.parseInt(input, 10);
  if (!Number.isInteger(amount) || amount < 1) {
    setMessage(message, "扣减数量必须是正整数。", true);
    return;
  }
  try {
    const data = await api(`/api/admin/super-keys/${item.id}/quota/deduct`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ amount }),
    });
    await loadSuperKeys();
    setMessage(
      message,
      `已扣减 ${amount} 次，当前额度 ${data.quota === -1 ? "无限" : data.quota}。`,
    );
    if (state.superKeys.usages.id === item.id) loadSuperKeyUsages();
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

async function setSuperKeyQuota(item) {
  const message = document.querySelector("#superkey-message");
  const input = window.prompt(
    `设置超级密钥「${item.key}」的额度（-1 为无限，0 或正整数）：`,
    String(item.quota),
  );
  if (input === null) return;
  const quota = Number.parseInt(input, 10);
  if (!Number.isSafeInteger(quota) || (quota !== -1 && quota < 0)) {
    setMessage(message, "额度必须是 -1（无限）或 0 及以上的整数。", true);
    return;
  }
  try {
    const data = await api(`/api/admin/super-keys/${item.id}/quota`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ quota }),
    });
    await loadSuperKeys();
    setMessage(
      message,
      `额度已设为 ${data.quota === -1 ? "无限" : data.quota}。`,
    );
    if (state.superKeys.usages.id === item.id) loadSuperKeyUsages();
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

async function editSuperKeyNote(item) {
  const message = document.querySelector("#superkey-message");
  const input = window.prompt(
    `编辑超级密钥「${item.key}」的备注（最多 200 字，留空清除）：`,
    item.note || "",
  );
  if (input === null) return;
  try {
    const data = await api(`/api/admin/super-keys/${item.id}/note`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note: input }),
    });
    await loadSuperKeys();
    if (state.superKeys.usages.id === item.id) {
      document.querySelector("#superkey-usages-note").textContent =
        data.note || "";
    }
    setMessage(message, data.note ? "备注已保存。" : "备注已清除。");
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

async function deleteSuperKey(item) {
  const message = document.querySelector("#superkey-message");
  if (
    !window.confirm(
      `确定永久删除超级密钥「${item.key}」吗？删除后无法恢复；使用日志、已生成任务、图片和审计历史会保留。`,
    )
  ) {
    return;
  }
  try {
    await api(`/api/admin/super-keys/${item.id}`, { method: "DELETE" });
    if (state.superKeys.usages.id === item.id) closeSuperKeyUsages();
    await loadSuperKeys();
    setMessage(message, "已永久删除该超级密钥。");
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

async function toggleSuperKeyFreeze(item) {
  const message = document.querySelector("#superkey-message");
  const freezing = !item.frozen_at;
  if (
    !window.confirm(
      freezing
        ? `确认冻结超级密钥「${item.key}」？冻结后不能继续提交，已有任务不受影响。`
        : `确认解冻超级密钥「${item.key}」？`,
    )
  ) {
    return;
  }
  try {
    await api(
      `/api/admin/super-keys/${item.id}/${freezing ? "freeze" : "unfreeze"}`,
      { method: "POST" },
    );
    await loadSuperKeys();
    setMessage(message, `已${freezing ? "冻结" : "解冻"}该超级密钥。`);
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

function openSuperKeyUsages(item) {
  const container = document.querySelector("#superkey-usages");
  state.superKeys.usages = {
    id: item.id,
    key: item.key,
    limit: 20,
    offset: 0,
    total: 0,
  };
  document.querySelector("#superkey-usages-title").textContent =
    `使用记录 · ${item.key}`;
  document.querySelector("#superkey-usages-note").textContent = item.note || "";
  document.querySelector("#superkey-usage-rows").replaceChildren();
  container.classList.remove("hidden");
  loadSuperKeyUsages();
  container.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function loadSuperKeyUsages() {
  const usages = state.superKeys.usages;
  if (!usages.id) return;
  const message = document.querySelector("#superkey-usage-message");
  const params = new URLSearchParams({
    limit: String(usages.limit),
    offset: String(usages.offset),
  });
  setMessage(message, "正在加载使用记录…");
  try {
    const data = await api(
      `/api/admin/super-keys/${usages.id}/usages?${params.toString()}`,
    );
    usages.total = data.total;
    renderSuperKeyUsageRows(data.items);
    const pages = Math.max(1, Math.ceil(data.total / usages.limit));
    const page = Math.floor(usages.offset / usages.limit) + 1;
    document.querySelector("#superkey-usage-page").textContent =
      `第 ${page} / ${pages} 页`;
    document.querySelector("#superkey-usages-note").textContent =
      data.key.note || "";
    document.querySelector("#superkey-usage-summary").textContent =
      `共 ${data.total} 次使用 · 剩余额度 ${data.key.remaining}`;
    document.querySelector("#superkey-usage-prev").disabled = usages.offset <= 0;
    document.querySelector("#superkey-usage-next").disabled =
      usages.offset + usages.limit >= data.total;
    setMessage(message, data.items.length ? "" : "还没有使用记录。");
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

function renderSuperKeyUsageRows(items) {
  const tbody = document.querySelector("#superkey-usage-rows");
  tbody.replaceChildren();
  if (!items.length) {
    const row = el("tr");
    const cell = el("td", "admin-empty", "还没有使用记录。");
    cell.colSpan = 8;
    row.appendChild(cell);
    tbody.appendChild(row);
    return;
  }
  for (const item of items) {
    const row = el("tr");
    row.appendChild(el("td", "admin-nowrap", formatDate(item.used_at)));

    const taskCell = el("td");
    taskCell.appendChild(el("div", "admin-work-id", item.job_id));
    if (item.work_name) {
      taskCell.appendChild(el("div", "admin-work-name", item.work_name));
    }
    row.appendChild(taskCell);

    const statusCell = el("td");
    const statusClass =
      item.status === "failed" || item.status === "expired"
        ? `state-pill ${item.status}`
        : "state-pill";
    statusCell.appendChild(el("span", statusClass, statusLabel(item.status)));
    row.appendChild(statusCell);

    const promptCell = el("td", "admin-prompt-cell", item.prompt_preview || "—");
    promptCell.title = item.prompt_preview || "";
    row.appendChild(promptCell);

    const qqCell = el("td");
    const qqBox = el("div", "admin-visitor-related");
    const qqLines = qqContextLines(item);
    if (qqLines.length) {
      for (const line of qqLines) qqBox.appendChild(el("span", "", line));
    } else {
      qqBox.appendChild(el("span", "", "—"));
    }
    qqCell.appendChild(qqBox);
    row.appendChild(qqCell);

    row.appendChild(el("td", "admin-mono", item.retrieval_count));
    row.appendChild(el("td", "admin-mono", item.client_ip || "—"));
    row.appendChild(el("td", "admin-visitor-id", item.visitor_id || "—"));
    tbody.appendChild(row);
  }
}

function closeSuperKeyUsages() {
  state.superKeys.usages.id = null;
  document.querySelector("#superkey-usages").classList.add("hidden");
  document.querySelector("#superkey-usage-rows").replaceChildren();
  document.querySelector("#superkey-usage-message").textContent = "";
}

async function loadLogs() {
  const message = document.querySelector("#log-message");
  const list = document.querySelector("#audit-list");
  const logText = document.querySelector("#gateway-log");
  setMessage(message, "正在加载日志…");
  try {
    if (state.logs.kind === "gateway") {
      const data = await api(`/api/admin/logs/gateway?limit=${state.logs.limit}`);
      list.classList.add("hidden");
      logText.classList.remove("hidden");
      logText.textContent = data.lines.join("\n") || "（暂无日志）";
      setMessage(message, data.path);
    } else {
      const data = await api(`/api/admin/audit?limit=${state.logs.limit}`);
      logText.classList.add("hidden");
      list.classList.remove("hidden");
      renderAudit(data.items);
      setMessage(message, `共加载 ${data.items.length} 条审计记录。`);
    }
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

function renderAudit(items) {
  const list = document.querySelector("#audit-list");
  list.replaceChildren();
  if (!items.length) {
    list.appendChild(el("p", "admin-empty", "暂无审计记录。"));
    return;
  }
  for (const record of [...items].reverse()) {
    const card = el("article", "admin-audit-item");
    const head = el("div", "admin-audit-head");
    head.appendChild(el("strong", "", record.event || "unknown"));
    head.appendChild(el("span", "admin-audit-time", formatDate(record.recorded_at)));
    card.appendChild(head);
    const fields = Object.entries(record)
      .filter(([name]) => name !== "event" && name !== "recorded_at")
      .map(([name, value]) => [name, typeof value === "string" ? value : JSON.stringify(value)]);
    card.appendChild(fieldGrid(fields));
    list.appendChild(card);
  }
}

const SYSTEM_HEALTH_LABELS = {
  online: "在线",
  offline: "离线",
  unknown: "探测中",
};

function formatSecondsReadable(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return "已关闭";
  if (seconds >= 60) {
    const minutes = seconds / 60;
    const text = Number.isInteger(minutes)
      ? `${minutes} 分钟`
      : `${minutes.toFixed(1)} 分钟`;
    return `${seconds} 秒（${text}）`;
  }
  return `${seconds} 秒`;
}

function systemNodeSection(data) {
  const summary = data.nodes_summary || {
    total: 0,
    enabled: 0,
    online: 0,
    all_unavailable: true,
  };
  const section = el("section", "admin-system-section");
  section.appendChild(el("h2", "", "节点实况"));
  section.appendChild(
    el(
      "p",
      "admin-system-summary",
      `${summary.online} 在线 / ${summary.enabled} 启用 / 共 ${summary.total} 个节点`,
    ),
  );
  if (summary.all_unavailable) {
    section.appendChild(
      el(
        "p",
        "admin-system-warning",
        "暂无可用节点：队列可能已自动暂停，节点恢复后会自动继续（手动暂停除外）。",
      ),
    );
  }
  const grid = el("div", "admin-system-nodes");
  const nodes = data.nodes || [];
  if (!nodes.length) {
    grid.appendChild(el("p", "admin-empty", "还没有节点。"));
  }
  for (const item of nodes) {
    const card = el(
      "article",
      item.enabled ? "admin-system-node" : "admin-system-node disabled",
    );
    const head = el("div", "admin-system-node-head");
    head.appendChild(el("strong", "admin-system-node-name", item.name || "未命名节点"));
    head.appendChild(
      el(
        "span",
        item.health_state === "offline" ? "state-pill failed" : "state-pill",
        SYSTEM_HEALTH_LABELS[item.health_state] || "未知",
      ),
    );
    card.appendChild(head);
    const fields = [
      ["公开名称", item.public_name || "生成节点"],
      ["地址", item.base_url || "—"],
      ["启用状态", item.enabled ? "启用" : "禁用"],
      [
        "探测方式",
        item.probe_method === "queue"
          ? "queue（退化）"
          : item.probe_method || "—",
      ],
      ["在途 / 并发", `${item.in_flight ?? 0} / ${item.max_concurrency ?? 1}`],
      ["最近成功完成", formatDate(item.last_task_completed_at)],
      ["最近探测", formatDate(item.last_probe_at)],
    ];
    if (item.last_error) fields.push(["最近错误", item.last_error]);
    card.appendChild(fieldGrid(fields));
    grid.appendChild(card);
  }
  section.appendChild(grid);
  return section;
}

async function loadSystem() {
  const message = document.querySelector("#system-message");
  setMessage(message, "正在加载系统信息…");
  try {
    const data = await api("/api/admin/system");
    const content = document.querySelector("#system-content");
    const config = data.config || {};
    const queue = data.queue || {};
    content.replaceChildren();

    content.appendChild(systemNodeSection(data));

    const queueReason =
      queue.pause_reason === "manual"
        ? "手动暂停"
        : queue.pause_reason === "no_nodes"
          ? "暂无可用节点，自动暂停"
          : queue.pause_reason || "未知原因";
    content.appendChild(
      systemSection("队列与调度", [
        ["队列状态", queue.paused ? `已暂停（${queueReason}）` : "运行中"],
        [
          "当前任务",
          `排队 ${queue.queued || 0} 个 · 等待节点 ${queue.waiting_node || 0} 个 · 已提交 ${queue.submitted || 0} 个 · 生成中 ${queue.running || 0} 个`,
        ],
        ["调度模式", "单 worker 串行调度；各节点按自身并发上限独立派发"],
        [
          "节点并发默认值",
          `${config.node_default_concurrency ?? 1}（单节点可在节点页设为 2）`,
        ],
        ["换节点上限", `${config.dispatch_switch_max ?? 3} 次`],
        [
          "任务丢失宽限",
          formatSecondsReadable(config.missing_prompt_grace_seconds),
        ],
        [
          "无进展超时",
          formatSecondsReadable(config.node_no_progress_timeout_seconds),
        ],
        ["下载重试上限", `${config.comfy_download_retry_max ?? 3} 次`],
        [
          "健康探测间隔",
          `在线 ${formatSecondsReadable(config.probe_interval_seconds)} / 离线 ${formatSecondsReadable(config.offline_probe_interval_seconds)}`,
        ],
      ]),
    );

    content.appendChild(
      systemSection("生图参数", [
        [
          "默认步数 / 上限",
          `${config.steps_default ?? 50} 步 / ${config.steps_max ?? 70} 步`,
        ],
        [
          "默认分辨率 / 上限",
          `${config.size_default ?? 1024}×${config.size_default ?? 1024} / ${config.size_max ?? 1920}×${config.size_max ?? 1920}`,
        ],
        ["分辨率步进", `${config.size_step ?? 16} 像素`],
        ["工作流文件", config.workflow_file || "—"],
        [
          "提示词节点 / 负面节点",
          `${config.prompt_node_id ?? "—"} / ${config.negative_prompt_node_id ?? "—"}`,
        ],
        ["提示词上限", `${config.prompt_max_length ?? "—"} 字符`],
      ]),
    );

    const rate = config.rate_limit || {};
    content.appendChild(
      systemSection("提交限流", [
        [
          "状态",
          rate.enabled ? "开启" : "已关闭（窗口或次数配置为 0/负数）",
        ],
        [
          "窗口",
          rate.enabled ? formatSecondsReadable(rate.window_seconds) : `${rate.window_seconds ?? 0} 秒`,
        ],
        ["最大次数", `${rate.max_requests ?? 0} 次`],
        ["豁免条数", `${rate.exemptions ?? 0} 条`],
      ]),
    );

    content.appendChild(
      systemSection("存储与保留", [
        [
          "任务图片目录",
          `${config.jobs_dir || "—"}（${data.jobs_dir?.files ?? 0} 个文件，${formatBytes(data.jobs_dir?.bytes ?? 0)}）`,
        ],
        [
          "公开作品目录",
          `${config.public_dir || "—"}（${data.public_dir?.files ?? 0} 个文件，${formatBytes(data.public_dir?.bytes ?? 0)}）`,
        ],
        [
          "数据库文件",
          `${data.database?.path || "—"}（${formatBytes(data.database?.bytes ?? 0)}）`,
        ],
        ["图片保留时长", `${config.result_ttl_hours ?? "—"} 小时`],
        ["过期处理", "超过保留时长后任务变为 expired，图片保留但不可再取"],
      ]),
    );

    const taskRows = Object.entries(data.tasks.by_status).map(([name, count]) => [
      statusLabel(name),
      `${count} 个`,
    ]);
    content.appendChild(systemSection(`任务（共 ${data.tasks.total} 个）`, taskRows));

    content.appendChild(
      systemSection("其他运行参数", [
        ["监听地址", `${config.host || "—"}:${config.port || "—"}`],
        ["ComfyUI 轮询间隔", `${config.comfy_poll_seconds ?? "—"} 秒`],
        ["ComfyUI 请求超时", `${config.comfy_timeout_seconds ?? "—"} 秒`],
        ["状态探测超时", `${config.comfy_status_timeout_seconds ?? "—"} 秒`],
        ["管理员会话时长", `${config.admin_session_hours ?? "—"} 小时`],
        ["密钥文件", config.key_file || "—"],
        ["日志目录", config.log_dir || "—"],
        ["技能文件", config.skill_file || "—"],
        ["已消费密钥", `${data.consumed_keys ?? 0} 个`],
        ["待处理举报", `${data.pending_reports ?? 0} 件`],
      ]),
    );
    setMessage(message, "");
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

function systemSection(title, rows) {
  const section = el("section", "admin-system-section");
  section.appendChild(el("h2", "", title));
  section.appendChild(fieldGrid(rows));
  return section;
}

let nodeRefreshTimer = null;

function activateTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".admin-tab").forEach((button) => {
    const active = button.dataset.tab === tab;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll(".admin-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `panel-${tab}`);
  });
  if (tab === "tasks") {
    loadTaskEndpointOptions();
    loadTasks();
    loadQueueStatus();
  }
  if (tab === "works") loadWorks();
  if (tab === "nodes") {
    loadNodes();
    if (!nodeRefreshTimer) {
      nodeRefreshTimer = window.setInterval(() => {
        if (state.tab === "nodes") loadNodes({ silent: true });
      }, 10000);
    }
  } else if (nodeRefreshTimer) {
    window.clearInterval(nodeRefreshTimer);
    nodeRefreshTimer = null;
  }
  if (tab === "loras") loadLoras();
  if (tab === "accounts") loadAccounts();
  if (tab === "invites") loadInvites();
  if (tab === "recharge") loadRechargeKeys();
  if (tab === "ratelimit") loadRateLimitExemptions();
  if (tab === "stats") loadSiteStats();
  if (tab === "visitors") loadVisitorEvents();
  if (tab === "comments") loadAdminComments();
  if (tab === "reports") loadReports();
  if (tab === "keys") loadKeys();
  if (tab === "superkeys") loadSuperKeys();
  if (tab === "audit") loadLogs();
  if (tab === "system") {
    loadCleanupSettings();
    loadSystem();
  }
}

let accountOffset = 0;
const ACCOUNT_PAGE_SIZE = 50;
const ACCOUNT_STATUS_LABELS = { active: "正常", frozen: "冻结", disabled: "停用" };
const ACCOUNT_BUCKET_LABELS = { free: "免费额度", bonus: "奖励额度", paid: "兑换额度" };
const ACCOUNT_ENTRY_TYPE_LABELS = {
  charge: "扣费",
  refund: "退款",
  reward: "奖励",
  recharge: "兑换",
  adjust: "调整",
};

function accountStatusLabel(value) {
  return ACCOUNT_STATUS_LABELS[value] || value || "—";
}

async function loadAccounts() {
  const message = document.querySelector("#account-message");
  setMessage(message, "正在加载账号…");
  try {
    const data = await api(
      `/api/admin/accounts?limit=${ACCOUNT_PAGE_SIZE}&offset=${accountOffset}`,
    );
    renderAccountRows(data.items);
    const page = Math.floor(accountOffset / ACCOUNT_PAGE_SIZE) + 1;
    const totalPages = Math.max(1, Math.ceil(data.total / ACCOUNT_PAGE_SIZE));
    document.querySelector("#account-page").textContent = `${page} / ${totalPages}`;
    document.querySelector("#account-prev").disabled = accountOffset <= 0;
    document.querySelector("#account-next").disabled = page >= totalPages;
    document.querySelector("#account-summary").textContent = `共 ${data.total} 个账号`;
    setMessage(message, "");
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

function renderAccountRows(items) {
  const tbody = document.querySelector("#account-rows");
  tbody.replaceChildren();
  if (!items.length) {
    const row = el("tr");
    const cell = el("td", "admin-empty", "暂无账号。");
    cell.colSpan = 9;
    row.appendChild(cell);
    tbody.appendChild(row);
    return;
  }
  for (const item of items) {
    const row = el("tr");
    const nameCell = el("td", "admin-nowrap");
    nameCell.appendChild(el("span", "", item.username));
    if (item.is_gateway) {
      const badge = el("span", "state-pill gateway", "网关");
      badge.style.marginLeft = "6px";
      badge.title = item.bound_gateway_id
        ? `已绑定上游 ${item.bound_gateway_id}`
        : "已标记为网关账号";
      nameCell.appendChild(badge);
    }
    nameCell.title = item.account_id;
    row.appendChild(nameCell);
    const idCell = el("td", "admin-account-id");
    idCell.appendChild(el("code", "admin-mono", item.account_id));
    idCell.appendChild(copyButton("复制", item.account_id));
    row.appendChild(idCell);
    const status = el(
      "span",
      item.status === "active" ? "node-status online" : "node-status offline",
      accountStatusLabel(item.status),
    );
    const statusCell = el("td");
    statusCell.appendChild(status);
    row.appendChild(statusCell);
    const remainingCell = el(
      "td",
      "admin-nowrap admin-num",
      item.remaining_quota,
    );
    remainingCell.title =
      `奖励额度 ${item.bonus_balance} + 兑换额度 ${item.paid_balance}`;
    row.appendChild(remainingCell);
    row.appendChild(el("td", "admin-mono", item.register_ip || "—"));
    row.appendChild(el("td", "admin-nowrap", item.task_count));
    row.appendChild(el("td", "admin-nowrap", item.endpoint_count));
    row.appendChild(el("td", "admin-nowrap", formatDate(item.last_login_at)));

    const actions = el("td");
    const wrap = el("div", "node-actions");
    wrap.appendChild(
      nodeActionButton("查看详情", ["M4 7h16", "M4 12h16", "M4 17h10"], () =>
        loadAccountDetail(item),
      ),
    );
    wrap.appendChild(
      nodeActionButton(
        item.status === "active" ? "冻结账号" : "恢复账号",
        ["M12 3v9", "M18.36 6.64a9 9 0 1 1-12.73 0"],
        () =>
          changeAccountStatus(item, item.status === "active" ? "frozen" : "active"),
      ),
    );
    wrap.appendChild(
      nodeActionButton(
        item.status === "disabled" ? "启用账号" : "停用账号",
        ["M18 6 6 18", "M6 6l12 12"],
        () =>
          changeAccountStatus(item, item.status === "disabled" ? "active" : "disabled"),
      ),
    );
    wrap.appendChild(
      nodeActionButton("解绑 IP", ["M3 6h18", "M19 6l-1 14H6L5 6"], () =>
        releaseAccountIp(item),
      ),
    );
    wrap.appendChild(
      nodeActionButton(
        "重置密码",
        ["M12 20h9", "M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"],
        () => resetAccountPassword(item),
      ),
    );
    wrap.appendChild(
      nodeActionButton(
        item.is_gateway ? "取消网关账号" : "设为网关账号",
        ["M12 3v18", "M3 12h18"],
        () => configureAccountGateway(item),
      ),
    );
    actions.appendChild(wrap);
    row.appendChild(actions);
    tbody.appendChild(row);
  }
}

async function changeAccountStatus(item, status) {
  const message = document.querySelector("#account-message");
  let reason = null;
  if (status !== "active") {
    reason = window.prompt("请填写操作原因（会写入审计）", "");
    if (reason === null) return;
    if (!reason.trim()) {
      setMessage(message, "原因不能为空", true);
      return;
    }
  }
  try {
    await api(`/api/admin/accounts/${item.account_id}/status`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(reason ? { status, reason: reason.trim() } : { status }),
    });
    setMessage(message, "状态已更新");
    loadAccounts();
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

async function releaseAccountIp(item) {
  const message = document.querySelector("#account-message");
  const reason = window.prompt("解绑注册 IP 的原因（会写入审计）", "误伤/NAT");
  if (reason === null) return;
  if (!reason.trim()) {
    setMessage(message, "原因不能为空", true);
    return;
  }
  try {
    await api(`/api/admin/accounts/${item.account_id}/release-ip`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: reason.trim() }),
    });
    setMessage(message, "IP 已解绑");
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

async function resetAccountPassword(item) {
  const message = document.querySelector("#account-message");
  const password = window.prompt(
    "输入新密码（8~128 位，提交后该账号所有会话会被撤销）",
    "",
  );
  if (password === null) return;
  try {
    const data = await api(`/api/admin/accounts/${item.account_id}/password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    setMessage(message, `密码已重置，撤销会话 ${data.sessions_revoked} 个`);
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

async function configureAccountGateway(item) {
  const message = document.querySelector("#account-message");
  if (item.is_gateway) {
    if (!window.confirm(`取消「${item.username}」的网关账号标记？`)) return;
    try {
      await api(`/api/admin/accounts/${item.account_id}/gateway`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_gateway: false }),
      });
      setMessage(message, "已取消网关账号标记");
      loadAccounts();
    } catch (error) {
      setMessage(message, error.message, true);
    }
    return;
  }
  const gatewayId = window.prompt(
    "输入上游网关 ID（对方网关 /api/gateway/info 返回的 gateway_id）",
    "",
  );
  if (gatewayId === null) return;
  if (!gatewayId.trim()) {
    setMessage(message, "网关 ID 不能为空", true);
    return;
  }
  try {
    await api(`/api/admin/accounts/${item.account_id}/gateway`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        is_gateway: true,
        gateway_id: gatewayId.trim(),
      }),
    });
    setMessage(message, "已设为网关账号");
    loadAccounts();
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

let activeAccountId = null;
let activeAccountName = "";

async function loadAccountDetail(item) {
  activeAccountId = item.account_id;
  activeAccountName = item.username;
  const panel = document.querySelector("#account-detail");
  panel.classList.remove("hidden");
  document.querySelector("#account-detail-title").textContent =
    `账号详情：${item.username}`;
  const detailNote = document.querySelector("#account-detail-note");
  detailNote.replaceChildren();
  const remainingQuota =
    item.remaining_quota ??
    (Number(item.bonus_balance || 0) + Number(item.paid_balance || 0));
  detailNote.appendChild(
    el(
      "span",
      "",
      `ID ${item.account_id} · 注册 IP ${item.register_ip || "—"} · 状态 ${accountStatusLabel(item.status)} · 剩余额度（不含免费） ${remainingQuota} · 网关账号 ${item.is_gateway ? `（上游 ${item.bound_gateway_id || "未绑定"}）` : "否"}`,
    ),
  );
  detailNote.appendChild(copyButton("复制 ID", item.account_id));
  const ledgerBody = document.querySelector("#account-ledger-rows");
  ledgerBody.replaceChildren();
  try {
    const ledger = await api(`/api/admin/accounts/${item.account_id}/ledger?limit=50`);
    for (const entry of ledger.items) {
      const row = el("tr");
      row.appendChild(el("td", "admin-nowrap", formatDate(entry.created_at)));
      row.appendChild(el("td", "admin-nowrap", ACCOUNT_ENTRY_TYPE_LABELS[entry.entry_type] || entry.entry_type));
      row.appendChild(el("td", "admin-nowrap", ACCOUNT_BUCKET_LABELS[entry.bucket] || entry.bucket));
      row.appendChild(el("td", "admin-nowrap", entry.delta));
      row.appendChild(el("td", "admin-nowrap", entry.balance_after));
      row.appendChild(el("td", "admin-mono", entry.job_id || "—"));
      row.appendChild(el("td", "", entry.note || "—"));
      row.appendChild(el("td", "admin-nowrap", entry.operator || "—"));
      ledgerBody.appendChild(row);
    }
  } catch (error) {
    document.querySelector("#account-detail-note").textContent = error.message;
  }
}

async function submitAccountBalance() {
  if (!activeAccountId) return;
  const message = document.querySelector("#account-balance-message");
  const bucket = document.querySelector("#account-balance-bucket").value;
  const delta = Number(document.querySelector("#account-balance-delta").value || 0);
  const reason = document.querySelector("#account-balance-reason").value.trim();
  if (!reason) {
    setMessage(message, "原因不能为空", true);
    return;
  }
  try {
    const data = await api(`/api/admin/accounts/${activeAccountId}/balance`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bucket, delta, reason }),
    });
    setMessage(message, `调整成功，当前余额 ${data.balance.balance_after}`);
    loadAccountDetail({ account_id: activeAccountId, username: activeAccountName });
    loadAccounts();
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

async function loadInvites() {
  const message = document.querySelector("#invite-message");
  setMessage(message, "正在加载邀请码…");
  try {
    const data = await api("/api/admin/invites?limit=100");
    const tbody = document.querySelector("#invite-rows");
    tbody.replaceChildren();
    if (!data.items.length) {
      const row = el("tr");
      const cell = el("td", "admin-empty", "暂无邀请码。");
      cell.colSpan = 7;
      row.appendChild(cell);
      tbody.appendChild(row);
    }
    for (const item of data.items) {
      const row = el("tr");
      const codeCell = el("td", "admin-mono", item.code);
      codeCell.title = "点击复制";
      codeCell.style.cursor = "pointer";
      codeCell.addEventListener("click", () => copyText(item.code));
      row.appendChild(codeCell);
      row.appendChild(el("td", "admin-nowrap", item.status));
      row.appendChild(el("td", "admin-nowrap", `${item.used_count} / ${item.max_uses}`));
      row.appendChild(el("td", "admin-nowrap", formatDate(item.expires_at)));
      row.appendChild(el("td", "", item.note || "—"));
      row.appendChild(el("td", "admin-nowrap", formatDate(item.created_at)));
      const actions = el("td");
      const wrap = el("div", "node-actions");
      wrap.appendChild(
        nodeActionButton("兑换记录", ["M4 7h16", "M4 12h16", "M4 17h10"], () =>
          showInviteRedemptions(item),
        ),
      );
      if (item.status === "active") {
        wrap.appendChild(
          nodeActionButton("撤销", ["M18 6 6 18", "M6 6l12 12"], () =>
            revokeInvite(item),
          ),
        );
      }
      actions.appendChild(wrap);
      row.appendChild(actions);
      tbody.appendChild(row);
    }
    document.querySelector("#invite-summary").textContent = `共 ${data.total} 个邀请码`;
    setMessage(message, "");
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

async function createInvites() {
  const message = document.querySelector("#invite-message");
  const validDays = Number(document.querySelector("#invite-valid-days").value || 0);
  const maxUses = Number(document.querySelector("#invite-max-uses").value || 0);
  const count = Number(document.querySelector("#invite-count").value || 1);
  const note = document.querySelector("#invite-note").value.trim();
  try {
    const data = await api("/api/admin/invites", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ valid_days: validDays, max_uses: maxUses, count, note }),
    });
    setMessage(
      message,
      `已生成 ${data.items.length} 个邀请码：${data.items.map((item) => item.code).join("、")}`,
    );
    loadInvites();
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

async function revokeInvite(item) {
  const message = document.querySelector("#invite-message");
  const reason = window.prompt("撤销原因（会写入审计）", "");
  if (reason === null) return;
  if (!reason.trim()) {
    setMessage(message, "原因不能为空", true);
    return;
  }
  try {
    await api(`/api/admin/invites/${item.id}/revoke`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: reason.trim() }),
    });
    setMessage(message, "邀请码已撤销");
    loadInvites();
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

async function showInviteRedemptions(item) {
  const block = document.querySelector("#invite-redemptions");
  block.classList.remove("hidden");
  document.querySelector("#invite-redemptions-title").textContent =
    `兑换记录：${item.code}`;
  const tbody = document.querySelector("#invite-redemption-rows");
  tbody.replaceChildren();
  try {
    const data = await api(`/api/admin/invites/${item.id}/redemptions`);
    if (!data.items.length) {
      const row = el("tr");
      const cell = el("td", "admin-empty", "暂无兑换记录。");
      cell.colSpan = 4;
      row.appendChild(cell);
      tbody.appendChild(row);
      return;
    }
    for (const record of data.items) {
      const row = el("tr");
      row.appendChild(el("td", "admin-nowrap", formatDate(record.redeemed_at)));
      row.appendChild(el("td", "", record.username));
      row.appendChild(el("td", "admin-nowrap", record.status));
      row.appendChild(el("td", "admin-mono", record.client_ip || "—"));
      tbody.appendChild(row);
    }
  } catch (error) {
    const row = el("tr");
    const cell = el("td", "admin-empty", error.message);
    cell.colSpan = 4;
    row.appendChild(cell);
    tbody.appendChild(row);
  }
}

async function loadRechargeKeys() {
  const message = document.querySelector("#recharge-message");
  setMessage(message, "正在加载兑换密钥…");
  try {
    const data = await api("/api/admin/recharge-keys?limit=100");
    const tbody = document.querySelector("#recharge-rows");
    tbody.replaceChildren();
    if (!data.items.length) {
      const row = el("tr");
      const cell = el("td", "admin-empty", "暂无兑换密钥。");
      cell.colSpan = 8;
      row.appendChild(cell);
      tbody.appendChild(row);
    }
    for (const item of data.items) {
      const row = el("tr");
      const keyCell = el("td", "admin-mono", item.key);
      keyCell.title = "点击复制";
      keyCell.style.cursor = "pointer";
      keyCell.addEventListener("click", () => copyText(item.key));
      row.appendChild(keyCell);
      row.appendChild(
        el("td", "admin-nowrap", item.kind === "single" ? "一次性" : "每账号一次"),
      );
      row.appendChild(el("td", "admin-nowrap", item.amount));
      row.appendChild(el("td", "admin-nowrap", item.status));
      row.appendChild(el("td", "admin-nowrap", item.redeemed_count));
      row.appendChild(el("td", "admin-nowrap", formatDate(item.expires_at)));
      row.appendChild(el("td", "admin-nowrap", formatDate(item.created_at)));
      const actions = el("td");
      const wrap = el("div", "node-actions");
      wrap.appendChild(
        nodeActionButton("兑换记录", ["M4 7h16", "M4 12h16", "M4 17h10"], () =>
          showRechargeRedemptions(item),
        ),
      );
      if (item.status === "active") {
        wrap.appendChild(
          nodeActionButton("撤销", ["M18 6 6 18", "M6 6l12 12"], () =>
            revokeRechargeKey(item),
          ),
        );
      }
      actions.appendChild(wrap);
      row.appendChild(actions);
      tbody.appendChild(row);
    }
    document.querySelector("#recharge-summary").textContent =
      `共 ${data.total} 个兑换密钥`;
    setMessage(message, "");
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

async function createRechargeKey() {
  const message = document.querySelector("#recharge-message");
  const kind = document.querySelector("#recharge-kind").value;
  const amount = Number(document.querySelector("#recharge-amount").value || 0);
  const validDays = Number(document.querySelector("#recharge-valid-days").value || 0);
  const note = document.querySelector("#recharge-note").value.trim();
  try {
    const data = await api("/api/admin/recharge-keys", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, amount, valid_days: validDays, note }),
    });
    setMessage(message, `已生成：${data.key}（点击列表可复制）`);
    loadRechargeKeys();
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

async function revokeRechargeKey(item) {
  const message = document.querySelector("#recharge-message");
  const reason = window.prompt("撤销原因（会写入审计）", "");
  if (reason === null) return;
  if (!reason.trim()) {
    setMessage(message, "原因不能为空", true);
    return;
  }
  try {
    await api(`/api/admin/recharge-keys/${item.id}/revoke`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: reason.trim() }),
    });
    setMessage(message, "兑换密钥已撤销");
    loadRechargeKeys();
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

async function showRechargeRedemptions(item) {
  const block = document.querySelector("#recharge-redemptions");
  block.classList.remove("hidden");
  document.querySelector("#recharge-redemptions-title").textContent =
    `兑换记录：#${item.id}`;
  const tbody = document.querySelector("#recharge-redemption-rows");
  tbody.replaceChildren();
  try {
    const data = await api(`/api/admin/recharge-keys/${item.id}/redemptions`);
    if (!data.items.length) {
      const row = el("tr");
      const cell = el("td", "admin-empty", "暂无兑换记录。");
      cell.colSpan = 5;
      row.appendChild(cell);
      tbody.appendChild(row);
      return;
    }
    for (const record of data.items) {
      const row = el("tr");
      row.appendChild(el("td", "admin-nowrap", formatDate(record.redeemed_at)));
      row.appendChild(el("td", "", record.username));
      row.appendChild(el("td", "admin-nowrap", record.account_status));
      row.appendChild(el("td", "admin-nowrap", record.amount));
      row.appendChild(el("td", "admin-mono", record.client_ip || "—"));
      tbody.appendChild(row);
    }
  } catch (error) {
    const row = el("tr");
    const cell = el("td", "admin-empty", error.message);
    cell.colSpan = 5;
    row.appendChild(cell);
    tbody.appendChild(row);
  }
}

let nodeOwnerLastFocus = null;

function openNodeOwnerPicker(item) {
  const modal = document.querySelector("#node-owner-modal");
  state.nodeOwner.endpoint = item;
  state.nodeOwner.ownerAccountId = item.owner_account_id || null;
  state.nodeOwner.offset = 0;
  state.nodeOwner.search = "";
  nodeOwnerLastFocus = document.activeElement;
  document.querySelector("#node-owner-search").value = "";
  document.querySelector("#node-owner-reason").value = "手动绑定";
  document.querySelector("#node-owner-node").textContent =
    `${item.name} · 当前归属 ${item.owner_username || "未绑定"}`;
  document.querySelector("#node-owner-rows").replaceChildren();
  setMessage(document.querySelector("#node-owner-message"), "");
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
  document.body.classList.add("admin-modal-open");
  document.querySelector("#node-owner-search").focus();
  loadNodeOwnerAccounts();
}

function closeNodeOwnerPicker() {
  const modal = document.querySelector("#node-owner-modal");
  if (modal.classList.contains("hidden")) return;
  const lastFocus = nodeOwnerLastFocus;
  nodeOwnerLastFocus = null;
  if (lastFocus && lastFocus.focus) lastFocus.focus();
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
  document.body.classList.remove("admin-modal-open");
  state.nodeOwner.endpoint = null;
}

async function loadNodeOwnerAccounts() {
  const message = document.querySelector("#node-owner-message");
  setMessage(message, "正在加载账号…");
  try {
    const params = new URLSearchParams({
      limit: String(state.nodeOwner.limit),
      offset: String(state.nodeOwner.offset),
    });
    if (state.nodeOwner.search) params.set("search", state.nodeOwner.search);
    const data = await api(`/api/admin/accounts?${params.toString()}`);
    state.nodeOwner.total = data.total;
    renderNodeOwnerRows(data.items);
    const page =
      Math.floor(state.nodeOwner.offset / state.nodeOwner.limit) + 1;
    const totalPages = Math.max(
      1,
      Math.ceil(data.total / state.nodeOwner.limit),
    );
    document.querySelector("#node-owner-page").textContent =
      `${page} / ${totalPages}`;
    document.querySelector("#node-owner-prev").disabled =
      state.nodeOwner.offset <= 0;
    document.querySelector("#node-owner-next").disabled = page >= totalPages;
    document.querySelector("#node-owner-summary").textContent =
      `共 ${data.total} 个账号`;
    setMessage(message, data.items.length ? "" : "没有匹配的账号。");
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

function renderNodeOwnerRows(items) {
  const tbody = document.querySelector("#node-owner-rows");
  tbody.replaceChildren();
  if (!items.length) {
    const row = el("tr");
    const cell = el("td", "admin-empty", "没有匹配的账号。");
    cell.colSpan = 4;
    row.appendChild(cell);
    tbody.appendChild(row);
    return;
  }
  for (const account of items) {
    const row = el("tr");
    row.appendChild(el("td", "admin-nowrap", account.username));
    const idCell = el("td", "admin-owner-account-id");
    const shortId = el("code", "admin-mono", `${account.account_id.slice(0, 8)}…`);
    shortId.title = account.account_id;
    idCell.appendChild(shortId);
    row.appendChild(idCell);
    const statusCell = el("td");
    statusCell.appendChild(
      el(
        "span",
        account.status === "active"
          ? "node-status online"
          : "node-status offline",
        accountStatusLabel(account.status),
      ),
    );
    row.appendChild(statusCell);
    const actionCell = el("td");
    const button = el(
      "button",
      account.status === "active" ? "primary-button" : "secondary-button",
      account.account_id === state.nodeOwner.ownerAccountId
        ? "重新绑定"
        : "绑定",
    );
    button.type = "button";
    button.disabled = account.status !== "active";
    if (button.disabled) {
      button.title = `账号${accountStatusLabel(account.status)}，不能绑定`;
    }
    button.addEventListener("click", () => bindNodeOwnerAccount(account));
    actionCell.appendChild(button);
    if (account.account_id === state.nodeOwner.ownerAccountId) {
      actionCell.appendChild(el("span", "admin-owner-current", "当前归属"));
    }
    row.appendChild(actionCell);
    tbody.appendChild(row);
  }
}

async function bindNodeOwnerAccount(account) {
  const endpoint = state.nodeOwner.endpoint;
  if (!endpoint) return;
  if (account.status !== "active") {
    setMessage(
      document.querySelector("#node-owner-message"),
      `账号「${account.username}」${accountStatusLabel(account.status)}，不能绑定`,
      true,
    );
    return;
  }
  const reason =
    document.querySelector("#node-owner-reason").value.trim() || "手动绑定";
  const message = document.querySelector("#node-owner-message");
  setMessage(message, "正在绑定…");
  try {
    await api(`/api/admin/endpoints/${endpoint.id}/owner`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        account_id: account.account_id,
        reason,
      }),
    });
    setMessage(
      document.querySelector("#node-message"),
      `节点「${endpoint.name}」已绑定到 ${account.username}`,
    );
    closeNodeOwnerPicker();
    const loaded = await loadNodes();
    if (loaded) {
      setMessage(
        document.querySelector("#node-message"),
        `节点「${endpoint.name}」已绑定到 ${account.username}`,
      );
    }
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

async function unbindNodeOwner(item) {
  const message = document.querySelector("#node-message");
  try {
    await api(`/api/admin/endpoints/${item.id}/owner`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: "手动解绑" }),
    });
    const loaded = await loadNodes();
    if (loaded) setMessage(message, "归属账号已解绑");
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

document.querySelectorAll(".admin-tab").forEach((button) => {
  button.addEventListener("click", () => activateTab(button.dataset.tab));
});

document.querySelector("#task-status-filter").addEventListener("change", (event) => {
  state.tasks.status = event.target.value;
  state.tasks.offset = 0;
  loadTasks();
});

document.querySelector("#task-endpoint-filter").addEventListener("change", (event) => {
  state.tasks.endpoint = event.target.value;
  state.tasks.offset = 0;
  loadTasks();
});

let searchTimer = null;
document.querySelector("#task-search").addEventListener("input", (event) => {
  const value = event.target.value;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.tasks.search = value.trim();
    state.tasks.offset = 0;
    loadTasks();
  }, 400);
});

document.querySelector("#ratelimit-exemption-add").addEventListener("click", addRateLimitExemption);
document.querySelector("#ratelimit-exemption-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    addRateLimitExemption();
  }
});
document.querySelector("#ratelimit-exemption-refresh").addEventListener("click", loadRateLimitExemptions);

document.querySelector("#node-save").addEventListener("click", saveNode);
document.querySelector("#node-cancel-edit").addEventListener("click", resetNodeForm);
document.querySelector("#node-refresh").addEventListener("click", loadNodes);
document.querySelector("#node-kind").addEventListener("change", syncNodeKindFields);

document.querySelector("#lora-save").addEventListener("click", saveLora);
document.querySelector("#lora-cancel-edit").addEventListener("click", resetLoraForm);
document.querySelector("#lora-refresh").addEventListener("click", loadLoras);

document.querySelector("#queue-toggle").addEventListener("click", toggleQueue);

document.querySelector("#task-refresh").addEventListener("click", () => {
  loadQueueStatus();
  loadTasks();
  loadStats();
});

document.querySelector("#task-retry").addEventListener("click", async (event) => {
  const task = selectedTask();
  if (!canRetryTask(task)) return;
  await retryTask(task.job_id, event.currentTarget);
});

document.querySelector("#task-prev").addEventListener("click", () => {
  state.tasks.offset = Math.max(0, state.tasks.offset - state.tasks.limit);
  loadTasks();
});

document.querySelector("#task-next").addEventListener("click", () => {
  state.tasks.offset += state.tasks.limit;
  loadTasks();
});

document.querySelector("#cleanup-save").addEventListener("click", saveCleanupSettings);

document.querySelector("#work-status-filter").addEventListener("change", (event) => {
  state.works.status = event.target.value;
  state.works.offset = 0;
  loadWorks();
});

let workSearchTimer = null;
document.querySelector("#work-search").addEventListener("input", (event) => {
  const value = event.target.value;
  clearTimeout(workSearchTimer);
  workSearchTimer = setTimeout(() => {
    state.works.search = value.trim();
    state.works.offset = 0;
    loadWorks();
  }, 400);
});

document.querySelector("#work-refresh").addEventListener("click", loadWorks);

document.querySelector("#work-prev").addEventListener("click", () => {
  state.works.offset = Math.max(0, state.works.offset - state.works.limit);
  loadWorks();
});

document.querySelector("#work-next").addEventListener("click", () => {
  state.works.offset += state.works.limit;
  loadWorks();
});

document.querySelector("#stats-days").addEventListener("change", (event) => {
  state.site.days = Number.parseInt(event.target.value, 10) || 14;
  loadSiteStats();
});

document.querySelector("#stats-refresh").addEventListener("click", loadSiteStats);

document.querySelector("#visitor-action-filter").addEventListener("change", (event) => {
  state.visitors.action = event.target.value;
  state.visitors.offset = 0;
  loadVisitorEvents();
});

let visitorSearchTimer = null;
document.querySelector("#visitor-filter").addEventListener("input", (event) => {
  const value = event.target.value;
  clearTimeout(visitorSearchTimer);
  visitorSearchTimer = setTimeout(() => {
    state.visitors.visitor_id = value.trim();
    state.visitors.offset = 0;
    loadVisitorEvents();
  }, 400);
});

document.querySelector("#visitor-refresh").addEventListener("click", loadVisitorEvents);

document.querySelector("#visitor-prev").addEventListener("click", () => {
  state.visitors.offset = Math.max(0, state.visitors.offset - state.visitors.limit);
  loadVisitorEvents();
});

document.querySelector("#visitor-next").addEventListener("click", () => {
  state.visitors.offset += state.visitors.limit;
  loadVisitorEvents();
});

document.querySelector("#admin-comment-status").addEventListener("change", (event) => {
  state.commentAdmin.status = event.target.value;
  state.commentAdmin.offset = 0;
  loadAdminComments();
});

let adminCommentSearchTimer = null;
document.querySelector("#admin-comment-search").addEventListener("input", (event) => {
  const value = event.target.value;
  clearTimeout(adminCommentSearchTimer);
  adminCommentSearchTimer = setTimeout(() => {
    state.commentAdmin.search = value.trim();
    state.commentAdmin.offset = 0;
    loadAdminComments();
  }, 400);
});

document.querySelector("#admin-comment-refresh").addEventListener("click", loadAdminComments);

document.querySelector("#admin-comment-prev").addEventListener("click", () => {
  state.commentAdmin.offset = Math.max(0, state.commentAdmin.offset - state.commentAdmin.limit);
  loadAdminComments();
});

document.querySelector("#admin-comment-next").addEventListener("click", () => {
  state.commentAdmin.offset += state.commentAdmin.limit;
  loadAdminComments();
});

document.querySelector("#report-status-filter").addEventListener("change", (event) => {
  state.reports.status = event.target.value;
  state.reports.offset = 0;
  loadReports();
});

document.querySelector("#report-refresh").addEventListener("click", () => {
  loadReports();
  loadStats();
});

document.querySelector("#report-prev").addEventListener("click", () => {
  state.reports.offset = Math.max(0, state.reports.offset - state.reports.limit);
  loadReports();
});

document.querySelector("#report-next").addEventListener("click", () => {
  state.reports.offset += state.reports.limit;
  loadReports();
});

document.querySelector("#gallery-modal-close").addEventListener("click", closeGalleryModal);

document.querySelector("#gallery-modal-image").addEventListener("click", () => {
  const image = document.querySelector("#gallery-modal-image");
  if (image.getAttribute("src")) {
    openAdminFullscreen(image.src, image.alt);
  }
});

document.querySelector("#admin-fullscreen-close").addEventListener(
  "click",
  closeAdminFullscreen,
);

document.querySelector("#admin-fullscreen").addEventListener("click", (event) => {
  if (event.target.matches("[data-admin-fullscreen-close]")) {
    closeAdminFullscreen();
  }
});

document.querySelector("#gallery-modal").addEventListener("click", (event) => {
  if (event.target.matches("[data-close-modal]")) closeGalleryModal();
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  const ownerModal = document.querySelector("#node-owner-modal");
  if (!ownerModal.classList.contains("hidden")) {
    closeNodeOwnerPicker();
    return;
  }
  const fullscreen = document.querySelector("#admin-fullscreen");
  if (!fullscreen.classList.contains("hidden")) {
    closeAdminFullscreen();
    return;
  }
  closeGalleryModal();
});

document.querySelector("#node-owner-close").addEventListener("click", closeNodeOwnerPicker);
document.querySelector("#node-owner-modal").addEventListener("click", (event) => {
  if (event.target.matches("[data-close-owner-modal]")) {
    closeNodeOwnerPicker();
  }
});
document.querySelector("#node-owner-search-button").addEventListener("click", () => {
  state.nodeOwner.search = document.querySelector("#node-owner-search").value.trim();
  state.nodeOwner.offset = 0;
  loadNodeOwnerAccounts();
});
document.querySelector("#node-owner-search").addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return;
  event.preventDefault();
  state.nodeOwner.search = document.querySelector("#node-owner-search").value.trim();
  state.nodeOwner.offset = 0;
  loadNodeOwnerAccounts();
});
document.querySelector("#node-owner-prev").addEventListener("click", () => {
  state.nodeOwner.offset = Math.max(0, state.nodeOwner.offset - state.nodeOwner.limit);
  loadNodeOwnerAccounts();
});
document.querySelector("#node-owner-next").addEventListener("click", () => {
  state.nodeOwner.offset += state.nodeOwner.limit;
  loadNodeOwnerAccounts();
});

document.querySelector("#key-status-filter").addEventListener("change", (event) => {
  state.keys.status = event.target.value;
  state.keys.offset = 0;
  loadKeys();
});

let keySearchTimer = null;
document.querySelector("#key-search").addEventListener("input", (event) => {
  const value = event.target.value;
  clearTimeout(keySearchTimer);
  keySearchTimer = setTimeout(() => {
    state.keys.search = value.trim();
    state.keys.offset = 0;
    loadKeys();
  }, 400);
});

document.querySelector("#key-prev").addEventListener("click", () => {
  state.keys.offset = Math.max(0, state.keys.offset - state.keys.limit);
  loadKeys();
});

document.querySelector("#key-next").addEventListener("click", () => {
  state.keys.offset += state.keys.limit;
  loadKeys();
});

document.querySelector("#key-refresh").addEventListener("click", () => {
  loadKeys();
  loadStats();
});

document.querySelector("#key-purge-used").addEventListener("click", purgeUsedKeys);

document.querySelector("#key-generate").addEventListener("click", generateKeys);

document.querySelector("#superkey-create").addEventListener("click", createSuperKey);

document.querySelector("#superkey-generate").addEventListener("click", () => {
  const input = document.querySelector("#superkey-value");
  input.value = randomSuperKeyValue();
  input.focus();
  input.select();
});

document.querySelector("#superkey-value").addEventListener("keydown", (event) => {
  if (event.key === "Enter") createSuperKey();
});

document.querySelector("#superkey-usages-close").addEventListener("click", closeSuperKeyUsages);

document.querySelector("#superkey-usage-prev").addEventListener("click", () => {
  state.superKeys.usages.offset = Math.max(
    0,
    state.superKeys.usages.offset - state.superKeys.usages.limit,
  );
  loadSuperKeyUsages();
});

document.querySelector("#superkey-usage-next").addEventListener("click", () => {
  state.superKeys.usages.offset += state.superKeys.usages.limit;
  loadSuperKeyUsages();
});

document.querySelector("#copy-unused-keys").addEventListener("click", copyUnusedKeys);

document.querySelector("#export-unused-txt").addEventListener("click", () => {
  exportUnusedKeys("txt");
});

document.querySelector("#export-unused-csv").addEventListener("click", () => {
  exportUnusedKeys("csv");
});

document.querySelector("#copy-generated").addEventListener("click", async () => {
  const message = document.querySelector("#key-message");
  try {
    await copyText(document.querySelector("#generated-keys-text").textContent);
    setMessage(message, "已复制全部新密钥。");
  } catch (error) {
    setMessage(message, error.message, true);
  }
});

document.querySelector("#log-kind").addEventListener("change", (event) => {
  state.logs.kind = event.target.value;
  loadLogs();
});

document.querySelector("#log-limit").addEventListener("change", (event) => {
  state.logs.limit = Number.parseInt(event.target.value, 10) || 200;
  loadLogs();
});

document.querySelector("#log-refresh").addEventListener("click", loadLogs);

document.querySelector("#system-refresh").addEventListener("click", () => {
  loadSystem();
  loadStats();
});

document.querySelectorAll("[data-system-tab]").forEach((button) => {
  button.addEventListener("click", () => activateTab(button.dataset.systemTab));
});

document.querySelector("#admin-logout").addEventListener("click", async () => {
  try {
    await api("/api/admin/logout", { method: "POST" });
  } catch (error) {
    console.warn("logout request failed", error);
  }
  window.location.replace("/adminlogin");
});

async function init() {
  try {
    const session = await api("/api/admin/session");
    document.querySelector("#admin-user").textContent = session.username;
  } catch (error) {
    return;
  }
  await Promise.all([loadStats(), loadTasks()]);
}

document.querySelector("#account-prev").addEventListener("click", () => {
  accountOffset = Math.max(0, accountOffset - ACCOUNT_PAGE_SIZE);
  loadAccounts();
});
document.querySelector("#account-next").addEventListener("click", () => {
  accountOffset += ACCOUNT_PAGE_SIZE;
  loadAccounts();
});
document.querySelector("#account-detail-close").addEventListener("click", () => {
  document.querySelector("#account-detail").classList.add("hidden");
});
document
  .querySelector("#account-balance-submit")
  .addEventListener("click", submitAccountBalance);
document.querySelector("#invite-create").addEventListener("click", createInvites);
document.querySelector("#invite-redemptions-close").addEventListener("click", () => {
  document.querySelector("#invite-redemptions").classList.add("hidden");
});
document.querySelector("#recharge-create").addEventListener("click", createRechargeKey);
document
  .querySelector("#recharge-redemptions-close")
  .addEventListener("click", () => {
    document.querySelector("#recharge-redemptions").classList.add("hidden");
  });

init();
