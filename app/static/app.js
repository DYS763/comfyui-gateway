const DEFAULT_PROMPT = "a cinematic illustration of a quiet mountain lake at sunrise, soft mist over the water, warm golden light, detailed textures, no text, no watermark.";
const submitForm = document.querySelector("#submit-form");
const queryForm = document.querySelector("#query-form");
const promptInput = document.querySelector("#prompt");
const promptCount = document.querySelector("#prompt-count");
const promptBlock = document.querySelector("#prompt-block");
const advancedToggle = document.querySelector("#advanced-toggle");
const advancedFields = document.querySelector("#advanced-fields");
const expertToggle = document.querySelector("#expert-toggle");
const layoutFields = document.querySelector("#layout-fields");
const layoutInput = document.querySelector("#layout-json");
const layoutCount = document.querySelector("#layout-count");
const layoutExampleButton = document.querySelector("#layout-example");
const layoutClearButton = document.querySelector("#layout-clear");
const negativePromptInput = document.querySelector("#negative-prompt");
const negativeCount = document.querySelector("#negative-count");
const stepsInput = document.querySelector("#steps");
const seedInput = document.querySelector("#seed");
const widthInput = document.querySelector("#width");
const heightInput = document.querySelector("#height");
const endpointSelect = document.querySelector("#endpoint-select");
const loraSelect = document.querySelector("#lora-select");
const loraStrength = document.querySelector("#lora-strength");
const submitMessage = document.querySelector("#submit-message");
const queryMessage = document.querySelector("#query-message");
const tokenReveal = document.querySelector("#token-reveal");
const queryTokenOutput = document.querySelector("#query-token");
const queryTokenInput = document.querySelector("#query-token-input");
const savedKeysToggle = document.querySelector("#saved-keys-toggle");
const savedKeysPanel = document.querySelector("#saved-keys-panel");
const savedKeysCount = document.querySelector("#saved-keys-count");
const savedKeysList = document.querySelector("#saved-keys-list");
const savedKeysMessage = document.querySelector("#saved-keys-message");
const savedKeysClear = document.querySelector("#saved-keys-clear");
const savedKeysClose = document.querySelector("#saved-keys-close");
const taskCard = document.querySelector("#task-card");
const taskStatus = document.querySelector("#task-status");
const taskId = document.querySelector("#task-id");
const queueLabel = document.querySelector("#queue-label");
const queuePosition = document.querySelector("#queue-position");
const queueSuffix = document.querySelector("#queue-suffix");
const createdAt = document.querySelector("#created-at");
const expiresAt = document.querySelector("#expires-at");
const taskNode = document.querySelector("#task-node");
const taskLora = document.querySelector("#task-lora");
const resultArea = document.querySelector("#result-area");
const resultImage = document.querySelector("#result-image");
const downloadResult = document.querySelector("#download-result");
const publishButton = document.querySelector("#publish-result");
const publishModal = document.querySelector("#publish-modal");
const publishWorkName = document.querySelector("#publish-work-name");
const publishAuthor = document.querySelector("#publish-author");
const publishMessage = document.querySelector("#publish-message");
const galleryAddButton = document.querySelector("#gallery-add");
const galleryModal = document.querySelector("#gallery-modal");
const galleryOptions = document.querySelector("#gallery-options");
const galleryEmpty = document.querySelector("#gallery-empty");
const galleryUsage = document.querySelector("#gallery-usage");
const galleryNewName = document.querySelector("#gallery-new-name");
const galleryCreateButton = document.querySelector("#gallery-create");
const galleryConfirmButton = document.querySelector("#gallery-confirm");
const galleryCancelButton = document.querySelector("#gallery-cancel");
const galleryMessage = document.querySelector("#gallery-message");
const serviceStatus = document.querySelector("#service-status");
const serviceStatusText = document.querySelector("#service-status-text");
const serviceStatusHint = document.querySelector("#service-status-hint");
const servicePopover = document.querySelector("#service-popover");
const serviceSummary = document.querySelector("#service-summary");
const serviceNodeList = document.querySelector("#service-node-list");
const servicePopoverClose = document.querySelector("#service-popover-close");

const SERVICE_STATUS_LABELS = {
  online: "在线",
  offline: "离线",
  probing: "探测中",
  unknown: "未知",
};
let servicePopoverTimer = null;
let publicNodes = [];

const SAVED_KEYS_STORAGE_KEY = "comfyui-gateway.queryTokens.v1";
const SAVED_KEYS_VERSION = 1;
const SAVED_KEYS_LIMIT = 20;
const SAVED_KEYS_EXPIRED_RETENTION_MS = 7 * 24 * 60 * 60 * 1000;
const SAVED_KEYS_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;
let savedKeysWriteFailed = false;

const FREE_NOTICE_VERSION = 1;
const FREE_NOTICE_ACK_KEY = `comfyui-gateway.freeNoticeAck.v${FREE_NOTICE_VERSION}`;

const accessKeyLabel = document.querySelector("#access-key-label");
const accessKeyInput = document.querySelector("#access-key");
const accountQuotaRow = document.querySelector("#account-quota-row");
const accountQuotaHint = document.querySelector("#account-quota-hint");
const useAccountQuota = document.querySelector("#use-account-quota");
const accountEntry = document.querySelector("#account-entry");
const accountEntryText = document.querySelector("#account-entry-text");
const freeNotice = document.querySelector("#free-notice");
const freeNoticeAck = document.querySelector("#free-notice-ack");

function isFreeNoticeAcknowledged() {
  try {
    return localStorage.getItem(FREE_NOTICE_ACK_KEY) === "1";
  } catch (error) {
    return false;
  }
}

function dismissFreeNotice() {
  freeNotice.classList.add("hidden");
  try {
    localStorage.setItem(FREE_NOTICE_ACK_KEY, "1");
  } catch (error) {
  }
}

function initFreeNotice() {
  if (!freeNotice || !freeNoticeAck) return;
  if (isFreeNoticeAcknowledged()) {
    freeNotice.classList.add("hidden");
  }
  freeNoticeAck.addEventListener("click", dismissFreeNotice);
}

function applyAccountQuotaUI() {
  const usingAccount = useAccountQuota.checked;
  accessKeyLabel.style.display = usingAccount ? "none" : "";
  accessKeyInput.style.display = usingAccount ? "none" : "";
  accessKeyInput.required = !usingAccount;
}

async function loadAccountQuota() {
  try {
    const response = await fetch("/api/account/me", { credentials: "same-origin" });
    if (!response.ok) return;
    const data = await response.json();
    const balances = data.balances || {};
    const username = data.account && data.account.username;
    if (username) {
      accountLoggedIn = true;
      galleryAddButton.classList.remove("hidden");
      accountEntryText.textContent = username;
      accountEntry.classList.add("logged-in");
      accountEntry.setAttribute("aria-label", `账号中心：${username}`);
    }
    document.querySelector("#quota-free").textContent = balances.free ?? "—";
    document.querySelector("#quota-paid").textContent =
      Number(balances.bonus || 0) + Number(balances.paid || 0);
    accountQuotaRow.classList.remove("hidden");
    accountQuotaHint.classList.remove("hidden");
  } catch (error) {
  }
}

let accountLoggedIn = false;
let activeToken = "";
let pollTimer = null;
let resultUrl = null;

function element(tagName, className = "", text = "") {
  const node = document.createElement(tagName);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

const LAYOUT_MAX_CHARS = 32768;
const LAYOUT_EXAMPLE = JSON.stringify(
  {
    summary: {
      meta: ["poster"],
      canvas: "single",
      caption: "A minimalist character poster on a warm cream background. On the left, a full-body illustration of a traveler. On the right, a very large dark-blue uppercase title IMAGE.",
    },
    items: [
      {
        label: "character",
        caption: "a traveler in a long coat, full body, standing, clean character illustration.",
        bbox: [-384, -512, 384, -64],
      },
      {
        label: "text",
        caption: "Very large dark-blue uppercase title on the right side.",
        text: "IMAGE",
        bbox: [-160, 64, 224, 448],
      },
    ],
    bbox_coord_space: "abs_centralized",
    bbox_format: "yxyx",
  },
  null,
  2,
);

promptInput.value = DEFAULT_PROMPT;

async function loadServiceNodes() {
  try {
    const response = await fetch("/api/comfyui/nodes", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    publicNodes = Array.isArray(data.items) ? data.items : [];
    updateEndpointOptions(publicNodes);
    renderServicePopover(data);
    return data;
  } catch (error) {
    serviceSummary.textContent = "无法读取节点状态。";
    serviceNodeList.replaceChildren();
    return null;
  }
}

function updateEndpointOptions(items) {
  const current = endpointSelect.value;
  endpointSelect.replaceChildren();
  const automatic = element("option", "", "自动分配");
  automatic.value = "";
  endpointSelect.appendChild(automatic);
  for (const item of items) {
    const option = element("option");
    option.value = item.endpoint_id;
    let label = item.public_name || "生成节点";
    if (item.status === "offline") {
      label += "（离线，将等待上线）";
    } else if (item.status === "probing") {
      label += "（探测中，将等待上线）";
    } else if (item.status === "unknown") {
      label += "（状态未知，将等待上线）";
    }
    option.textContent = label;
    endpointSelect.appendChild(option);
  }
  const stillExists = Array.from(endpointSelect.options).some(
    (option) => option.value === current,
  );
  endpointSelect.value = stillExists ? current : "";
}

async function loadLoras() {
  try {
    const response = await fetch("/api/loras", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const current = loraSelect.value;
    loraSelect.replaceChildren();
    const defaultOption = element(
      "option",
      "",
      `默认（${data.default_name || "默认"}）`,
    );
    defaultOption.value = "";
    loraSelect.appendChild(defaultOption);
    const noneOption = element("option", "", "不使用 LoRA");
    noneOption.value = "none";
    loraSelect.appendChild(noneOption);
    const defaultId = data.default_id ?? null;
    for (const item of Array.isArray(data.items) ? data.items : []) {
      if (defaultId !== null && Number(item.id) === Number(defaultId)) continue;
      const option = element("option");
      option.value = String(item.id);
      option.textContent = item.display_name;
      loraSelect.appendChild(option);
    }
    const stillExists = Array.from(loraSelect.options).some(
      (option) => option.value === current,
    );
    loraSelect.value = stillExists ? current : "";
    syncLoraStrength();
  } catch (error) {
    loraSelect.replaceChildren();
    const defaultOption = element("option", "", "默认（默认）");
    defaultOption.value = "";
    loraSelect.appendChild(defaultOption);
    const noneOption = element("option", "", "不使用 LoRA");
    noneOption.value = "none";
    loraSelect.appendChild(noneOption);
    syncLoraStrength();
  }
}

function renderServicePopover(data) {
  const total = Number(data.nodes_total || 0);
  const online = Number(data.nodes_online || 0);
  serviceSummary.textContent = online
    ? `${online} 个节点在线 / 共 ${total} 个`
    : "暂无可用节点";
  serviceNodeList.replaceChildren();
  const items = Array.isArray(data.items) ? data.items : [];
  if (!items.length) {
    serviceNodeList.appendChild(
      element("div", "service-node-empty", "暂无可用节点"),
    );
    return;
  }
  for (const item of items) {
    const row = element("div", "service-node-row");
    const name = element("div", "service-node-name");
    const dot = element("span", `service-node-dot ${item.status || "unknown"}`);
    dot.setAttribute("aria-hidden", "true");
    name.appendChild(dot);
    name.appendChild(element("span", "", item.public_name || "生成节点"));
    row.appendChild(name);
    const statusLabel = SERVICE_STATUS_LABELS[item.status] || "未知";
    const busy =
      item.status === "online" &&
      Number(item.in_flight || 0) >= Number(item.max_concurrency || 1);
    row.appendChild(
      element(
        "span",
        "service-node-meta",
        `${statusLabel} · 在途 ${item.in_flight || 0}/${item.max_concurrency || 1}${busy ? " · 繁忙" : ""}`,
      ),
    );
    serviceNodeList.appendChild(row);
  }
}

function openServicePopover() {
  servicePopover.classList.remove("hidden");
  serviceStatus.setAttribute("aria-expanded", "true");
  loadServiceNodes();
  if (!servicePopoverTimer) {
    servicePopoverTimer = window.setInterval(loadServiceNodes, 15000);
  }
}

function closeServicePopover() {
  servicePopover.classList.add("hidden");
  serviceStatus.setAttribute("aria-expanded", "false");
  if (servicePopoverTimer) {
    window.clearInterval(servicePopoverTimer);
    servicePopoverTimer = null;
  }
}

serviceStatus.addEventListener("click", (event) => {
  event.stopPropagation();
  if (servicePopover.classList.contains("hidden")) {
    openServicePopover();
  } else {
    closeServicePopover();
  }
});
servicePopoverClose.addEventListener("click", (event) => {
  event.stopPropagation();
  closeServicePopover();
});
servicePopover.addEventListener("click", (event) => event.stopPropagation());
document.addEventListener("click", () => {
  if (!servicePopover.classList.contains("hidden")) closeServicePopover();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeServicePopover();
});

async function refreshServiceStatus() {
  try {
    const response = await fetch("/api/comfyui/status", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const paused = Boolean(data.paused);
    const online = Boolean(data.reachable);
    serviceStatus.classList.remove("checking", "online", "offline", "paused");
    if (paused) {
      serviceStatus.classList.add("paused");
      if (data.pause_reason === "no_nodes") {
        serviceStatusText.textContent = "暂无可用节点";
        serviceStatus.title = "所有生成节点当前不可用，恢复后会按照排队顺序继续生图";
      } else {
        serviceStatusText.textContent = "暂停中";
        serviceStatus.title = "队列已暂停，当恢复时会按照排队顺序继续生图";
      }
      serviceStatusHint.classList.remove("hidden");
    } else {
      serviceStatus.classList.add(online ? "online" : "offline");
      serviceStatusText.textContent = online ? "服务在线" : "ComfyUI 未连接";
      serviceStatus.title = online
        ? "服务器已连接到 ComfyUI"
        : "服务器暂时无法连接到 ComfyUI";
      serviceStatusHint.classList.add("hidden");
    }
  } catch (error) {
    serviceStatus.classList.remove("checking", "online", "paused");
    serviceStatus.classList.add("offline");
    serviceStatusText.textContent = "服务状态未知";
    serviceStatus.title = "无法读取服务状态";
    serviceStatusHint.classList.add("hidden");
  }
}

function setMessage(element, message, isError = false) {
  element.textContent = message || "";
  element.classList.toggle("error", isError);
}

function updatePromptCount() {
  promptCount.textContent = `${promptInput.value.length} / 8000`;
}

function updateNegativeCount() {
  negativeCount.textContent = `${negativePromptInput.value.length} / 8000`;
}

function syncLoraStrength() {
  const enabled =
    advancedToggle.checked && loraSelect.value !== "none";
  loraStrength.disabled = !enabled;
}

function syncAdvancedFields() {
  const enabled = advancedToggle.checked;
  advancedFields.classList.toggle("hidden", !enabled);
  advancedFields.setAttribute("aria-hidden", String(!enabled));
  advancedToggle.setAttribute("aria-expanded", String(enabled));
  negativePromptInput.disabled = !enabled;
  stepsInput.disabled = !enabled;
  seedInput.disabled = !enabled;
  widthInput.disabled = !enabled;
  heightInput.disabled = !enabled;
  loraSelect.disabled = !enabled;
  syncLoraStrength();
}

function syncExpertFields() {
  const enabled = expertToggle.checked;
  layoutFields.classList.toggle("hidden", !enabled);
  layoutFields.setAttribute("aria-hidden", String(!enabled));
  expertToggle.setAttribute("aria-expanded", String(enabled));
  promptBlock.classList.toggle("hidden", enabled);
  promptInput.disabled = enabled;
  layoutInput.disabled = !enabled;
  layoutExampleButton.disabled = !enabled;
  layoutClearButton.disabled = !enabled;
}

function updateLayoutCount() {
  layoutCount.textContent = `${layoutInput.value.length} / ${LAYOUT_MAX_CHARS}`;
}

const SIZE_MIN = 16;
const SIZE_MAX = 1920;
const SIZE_DEFAULT = 1024;
const SIZE_STEP = 16;

function normalizeDimension(raw) {
  if (String(raw).trim() === "") return SIZE_DEFAULT;
  const parsed = Number(raw);
  const value = Number.isFinite(parsed) ? parsed : SIZE_DEFAULT;
  const clamped = Math.max(SIZE_MIN, Math.min(SIZE_MAX, value));
  const snapped = Math.round(clamped / SIZE_STEP) * SIZE_STEP;
  return Math.max(SIZE_MIN, Math.min(SIZE_MAX, snapped));
}

function snapDimensionInput(input) {
  const value = normalizeDimension(input.value);
  input.value = String(value);
  return value;
}

function formatDate(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function parseSavedDate(value) {
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : null;
}

function normalizeSavedKeyEntry(raw) {
  if (!raw || typeof raw !== "object") return null;
  const token = typeof raw.token === "string" ? raw.token.trim() : "";
  const jobId = typeof raw.job_id === "string" ? raw.job_id.trim() : "";
  const createdAt = typeof raw.created_at === "string" ? raw.created_at : "";
  const expiresAt = typeof raw.expires_at === "string" ? raw.expires_at : "";
  if (!token || token.length > 512) return null;
  if (!jobId || jobId.length > 128) return null;
  if (parseSavedDate(createdAt) === null || parseSavedDate(expiresAt) === null) {
    return null;
  }
  return {
    token,
    job_id: jobId,
    created_at: createdAt,
    expires_at: expiresAt,
  };
}

function writeSavedKeys(entries) {
  try {
    window.localStorage.setItem(
      SAVED_KEYS_STORAGE_KEY,
      JSON.stringify({ version: SAVED_KEYS_VERSION, entries }),
    );
    savedKeysWriteFailed = false;
    return true;
  } catch (error) {
    savedKeysWriteFailed = true;
    return false;
  }
}

function readSavedKeys() {
  let raw = null;
  try {
    raw = window.localStorage.getItem(SAVED_KEYS_STORAGE_KEY);
  } catch (error) {
    return { entries: [], error: "unavailable" };
  }
  if (!raw) return { entries: [], error: null };
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (error) {
    return { entries: [], error: "corrupt" };
  }
  if (
    !parsed ||
    typeof parsed !== "object" ||
    !Array.isArray(parsed.entries)
  ) {
    return { entries: [], error: "corrupt" };
  }
  if (parsed.version !== SAVED_KEYS_VERSION) {
    return { entries: [], error: "version" };
  }
  const now = Date.now();
  const entries = [];
  let changed = false;
  for (const rawEntry of parsed.entries) {
    const entry = normalizeSavedKeyEntry(rawEntry);
    if (!entry) {
      changed = true;
      continue;
    }
    const createdMs = parseSavedDate(entry.created_at);
    const expiresMs = parseSavedDate(entry.expires_at);
    if (
      now - expiresMs > SAVED_KEYS_EXPIRED_RETENTION_MS ||
      now - createdMs > SAVED_KEYS_MAX_AGE_MS
    ) {
      changed = true;
      continue;
    }
    if (entries.some((item) => item.token === entry.token)) {
      changed = true;
      continue;
    }
    entries.push(entry);
  }
  entries.sort(
    (left, right) =>
      parseSavedDate(right.created_at) - parseSavedDate(left.created_at),
  );
  if (entries.length > SAVED_KEYS_LIMIT) {
    entries.length = SAVED_KEYS_LIMIT;
    changed = true;
  }
  if (changed) writeSavedKeys(entries);
  return { entries, error: null };
}

function updateSavedKeysCount(count) {
  savedKeysCount.textContent = String(count);
}

function rememberSavedKey(task, token) {
  const entry = normalizeSavedKeyEntry({
    token,
    job_id: task ? task.job_id : "",
    created_at: task ? task.created_at : "",
    expires_at: task ? task.expires_at : "",
  });
  if (!entry) return;
  const { entries } = readSavedKeys();
  const nextEntries = [
    entry,
    ...entries.filter((item) => item.token !== entry.token),
  ]
    .sort(
      (left, right) =>
        parseSavedDate(right.created_at) - parseSavedDate(left.created_at),
    )
    .slice(0, SAVED_KEYS_LIMIT);
  if (!writeSavedKeys(nextEntries)) {
    if (!savedKeysPanel.classList.contains("hidden")) renderSavedKeys();
    return;
  }
  updateSavedKeysCount(nextEntries.length);
  if (!savedKeysPanel.classList.contains("hidden")) renderSavedKeys();
}

function maskSavedToken(token) {
  if (token.length <= 12) return token;
  return `${token.slice(0, 6)}…${token.slice(-6)}`;
}

function renderSavedKeys() {
  const { entries, error } = readSavedKeys();
  updateSavedKeysCount(entries.length);
  savedKeysList.replaceChildren();
  if (error === "unavailable") {
    setMessage(
      savedKeysMessage,
      "本机存储不可用，无法读取或保存取图密钥。",
      true,
    );
  } else if (error === "version") {
    setMessage(
      savedKeysMessage,
      "本机记录格式版本不匹配，已忽略旧记录；新提交会重新保存。",
      true,
    );
  } else if (error === "corrupt") {
    setMessage(
      savedKeysMessage,
      "本机记录已损坏，已忽略；新提交会重新保存。",
      true,
    );
  } else if (savedKeysWriteFailed) {
    setMessage(
      savedKeysMessage,
      "本次提交未能写入本机存储，请手动保存取图密钥。",
      true,
    );
  } else {
    setMessage(savedKeysMessage, "");
  }
  if (!entries.length) {
    savedKeysList.appendChild(
      element(
        "div",
        "saved-key-empty",
        "暂无本机保存的取图密钥。提交成功后会自动保存。",
      ),
    );
    return;
  }
  for (const entry of entries) {
    const row = element("div", "saved-key-row");
    const main = element("div", "saved-key-main");
    const line = element("div", "saved-key-line");
    const job = element(
      "span",
      "saved-key-job",
      `任务 ${entry.job_id.slice(0, 12)}`,
    );
    job.title = entry.job_id;
    line.appendChild(job);
    const expired = parseSavedDate(entry.expires_at) <= Date.now();
    line.appendChild(
      element(
        "span",
        `saved-key-state ${expired ? "expired" : "active"}`,
        expired ? "已过期" : `有效至 ${formatDate(entry.expires_at)}`,
      ),
    );
    main.appendChild(line);
    main.appendChild(
      element(
        "div",
        "saved-key-meta",
        `创建 ${formatDate(entry.created_at)} · 截止 ${formatDate(entry.expires_at)}`,
      ),
    );
    main.appendChild(
      element("span", "saved-key-token", maskSavedToken(entry.token)),
    );
    row.appendChild(main);
    const copy = element("button", "secondary-button", "复制密钥");
    copy.type = "button";
    copy.addEventListener("click", async () => {
      try {
        await copyText(entry.token);
        setMessage(savedKeysMessage, "取图密钥已复制。");
      } catch (error) {
        setMessage(savedKeysMessage, error.message, true);
      }
    });
    row.appendChild(copy);
    savedKeysList.appendChild(row);
  }
}

function setSavedKeysPanel(open) {
  savedKeysPanel.classList.toggle("hidden", !open);
  savedKeysPanel.setAttribute("aria-hidden", String(!open));
  savedKeysToggle.setAttribute("aria-expanded", String(open));
  if (open) renderSavedKeys();
}

function clearSavedKeys() {
  if (
    !window.confirm(
      "确定清空本机保存的全部取图密钥吗？清空后无法恢复。",
    )
  ) {
    return;
  }
  try {
    window.localStorage.removeItem(SAVED_KEYS_STORAGE_KEY);
  } catch (error) {
    setMessage(savedKeysMessage, "本机存储不可用，无法清空。", true);
    return;
  }
  savedKeysWriteFailed = false;
  renderSavedKeys();
  setMessage(savedKeysMessage, "已清空本机保存的取图密钥。");
}

function setBusy(form, busy) {
  form.querySelector("button[type=submit]").disabled = busy;
}

function statusLabel(status) {
  return { queued: "等待提交", waiting_node: "等待节点上线", submitted: "排队中", running: "生成中", completed: "已完成", failed: "生成失败", expired: "已过期" }[status] || status;
}

function renderTask(task) {
  taskCard.classList.remove("hidden");
  taskStatus.textContent = statusLabel(task.status);
  taskStatus.className = `state-pill ${task.status === "failed" || task.status === "expired" ? task.status : ""}`;
  taskId.textContent = task.job_id ? `任务 ${task.job_id.slice(0, 12)}` : "";
  createdAt.textContent = formatDate(task.created_at);
  expiresAt.textContent = formatDate(task.expires_at);
  const nodeLabel = task.endpoint_public_name || "生成节点";
  if (task.endpoint_public_name || task.status === "waiting_node") {
    taskNode.textContent = nodeLabel;
  } else {
    taskNode.textContent = task.status === "queued" ? "待分配" : "—";
  }
  taskLora.textContent = task.lora_disabled
    ? "不使用"
    : task.lora_name || "默认";
  if (task.status === "queued") {
    queueLabel.textContent = "当前排在第";
    queuePosition.textContent = task.queue_position ?? "?";
    queueSuffix.textContent = "位";
  } else if (task.status === "waiting_node") {
    queueLabel.textContent = `等待 ${nodeLabel} 上线后继续生图`;
    queuePosition.textContent = "·";
    queueSuffix.textContent = "";
  } else if (task.status === "submitted" || task.status === "running") {
    if (task.status === "running") {
      queueLabel.textContent = `正在由 ${nodeLabel} 生成，请稍等片刻`;
      queuePosition.textContent = "·";
      queueSuffix.textContent = "";
    } else {
      const ahead = Math.max(0, Number(task.queue_position ?? 1) - 1);
      if (ahead === 0) {
        queueLabel.textContent = `已交给 ${nodeLabel}，即将开始`;
        queuePosition.textContent = "·";
        queueSuffix.textContent = "";
      } else {
        queueLabel.textContent = "前面还有";
        queuePosition.textContent = ahead;
        queueSuffix.textContent = `个任务 · ${nodeLabel}`;
      }
    }
  } else {
    queueLabel.textContent = task.status === "completed" ? "结果已准备" : (task.status === "failed" ? "无法生成" : "状态");
    queuePosition.textContent = task.status === "completed" ? "✓" : "—";
    queueSuffix.textContent = "";
  }
  if (task.status === "failed") setMessage(queryMessage, task.error || "生成失败", true);
  if (task.status === "expired") setMessage(queryMessage, "取图时间已过期，图片不再提供下载。", true);
  if (task.status === "completed" && task.has_result) fetchResult();
}

async function readJson(response) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail && typeof detail.message === "string"
          ? detail.message
          : "请求失败";
    throw new Error(message);
  }
  return data;
}

async function queryStatus(token, quiet = false) {
  if (!token) return;
  if (!quiet) setMessage(queryMessage, "正在查询…");
  try {
    const response = await fetch("/api/query", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }) });
    const data = await readJson(response);
    activeToken = token;
    renderTask(data.task);
    if (["queued", "waiting_node", "submitted", "running"].includes(data.task.status)) {
      clearTimeout(pollTimer);
      pollTimer = setTimeout(() => queryStatus(activeToken, true), 2000);
      if (!quiet) setMessage(queryMessage, "状态会自动更新。");
    } else if (data.task.status === "completed") {
      setMessage(queryMessage, "生成完成，可以预览或下载图片。（请稍等一会，图片需要加载）");
    }
  } catch (error) {
    clearTimeout(pollTimer);
    setMessage(queryMessage, error.message, true);
  }
}

async function fetchResult() {
  if (!activeToken) return;
  try {
    const response = await fetch("/api/result", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: activeToken }) });
    if (!response.ok) throw new Error("图片暂时无法提取");
    const blob = await response.blob();
    if (resultUrl) URL.revokeObjectURL(resultUrl);
    resultUrl = URL.createObjectURL(blob);
    resultImage.src = resultUrl;
    downloadResult.href = resultUrl;
    document.querySelector("#destroy-result").disabled = false;
    publishButton.disabled = false;
    publishButton.textContent = "发布至画廊";
    resultArea.classList.remove("hidden");
  } catch (error) {
    setMessage(queryMessage, error.message, true);
  }
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
  helper.style.position = "fixed";
  helper.style.opacity = "0";
  document.body.appendChild(helper);
  helper.select();
  helper.setSelectionRange(0, helper.value.length);
  const copied = document.execCommand("copy");
  helper.remove();
  if (!copied) throw new Error("浏览器拒绝了复制操作，请手动选择并复制令牌。");
}

promptInput.addEventListener("input", updatePromptCount);
updatePromptCount();
advancedToggle.addEventListener("change", () => {
  if (!advancedToggle.checked && expertToggle.checked) {
    expertToggle.checked = false;
  }
  syncAdvancedFields();
  syncExpertFields();
});
expertToggle.addEventListener("change", () => {
  if (expertToggle.checked && !advancedToggle.checked) {
    advancedToggle.checked = true;
  }
  syncAdvancedFields();
  syncExpertFields();
});
layoutInput.addEventListener("input", updateLayoutCount);
layoutExampleButton.addEventListener("click", () => {
  layoutInput.value = LAYOUT_EXAMPLE;
  updateLayoutCount();
});
layoutClearButton.addEventListener("click", () => {
  layoutInput.value = "";
  updateLayoutCount();
});
negativePromptInput.addEventListener("input", updateNegativeCount);
loraSelect.addEventListener("change", syncLoraStrength);
syncAdvancedFields();
syncExpertFields();
updateNegativeCount();
updateLayoutCount();
widthInput.addEventListener("change", () => snapDimensionInput(widthInput));
widthInput.addEventListener("blur", () => snapDimensionInput(widthInput));
widthInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") snapDimensionInput(widthInput);
});
heightInput.addEventListener("change", () => snapDimensionInput(heightInput));
heightInput.addEventListener("blur", () => snapDimensionInput(heightInput));
heightInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") snapDimensionInput(heightInput);
});

submitForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setMessage(submitMessage, "正在提交…");
  setBusy(submitForm, true);
  tokenReveal.classList.add("hidden");
  try {
    const payload = {};
    const keyValue = document.querySelector("#access-key").value.trim();
    if (useAccountQuota.checked) {
      payload.use_account_quota = true;
    } else {
      if (!keyValue) {
        throw new Error("请输入密钥，或勾选「使用账号额度」。");
      }
      payload.key = keyValue;
    }
    if (expertToggle.checked) {
      const rawLayout = layoutInput.value.trim();
      if (!rawLayout) {
        throw new Error("开启专家模式后请填写 MOSS Layout JSON。");
      }
      let layout;
      try {
        layout = JSON.parse(rawLayout);
      } catch (error) {
        throw new Error(`Layout JSON 解析失败：${error.message}`);
      }
      if (!layout || typeof layout !== "object" || Array.isArray(layout)) {
        throw new Error("Layout JSON 必须是一个对象。");
      }
      payload.layout = layout;
    } else {
      payload.prompt = promptInput.value;
    }
    if (advancedToggle.checked) {
      const negativePrompt = negativePromptInput.value.trim();
      if (negativePrompt) payload.negative_prompt = negativePrompt;
      const steps = Number.parseInt(stepsInput.value, 10);
      if (!Number.isInteger(steps) || steps < 3 || steps > 70) {
        throw new Error("生成步数必须是 3 到 70 之间的整数。");
      }
      payload.steps = steps;
      const width = snapDimensionInput(widthInput);
      const height = snapDimensionInput(heightInput);
      payload.width = width;
      payload.height = height;
      const seedValue = seedInput.value.trim();
      if (seedValue) {
        const seed = Number(seedValue);
        if (!Number.isSafeInteger(seed) || seed < 0) {
          throw new Error("生图种子必须是 0 到 9007199254740991 之间的整数。");
        }
        payload.seed = seed;
      }
      if (loraSelect.value === "none") {
        payload.disable_lora = true;
      } else {
        if (loraSelect.value) payload.lora_id = Number(loraSelect.value);
        const strength = Number(loraStrength.value);
        if (!Number.isFinite(strength) || strength < 0 || strength > 2) {
          throw new Error("LoRA 强度必须在 0 到 2 之间。");
        }
        if (Math.abs(strength - Math.round(strength * 100) / 100) > 1e-9) {
          throw new Error("LoRA 强度最多保留两位小数。");
        }
        payload.lora_strength = strength;
      }
    }
    if (endpointSelect.value) payload.endpoint_id = endpointSelect.value;
    const response = await fetch("/api/submit", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const data = await readJson(response);
    rememberSavedKey(data.task, data.query_token);
    if (payload.use_account_quota) loadAccountQuota();
    queryTokenOutput.textContent = data.query_token;
    queryTokenInput.value = data.query_token;
    tokenReveal.classList.remove("hidden");
    setMessage(submitMessage, "已提交。请立即复制或下载查询令牌。");
    await queryStatus(data.query_token);
    document.querySelector("#access-key").value = "";
  } catch (error) {
    setMessage(submitMessage, error.message, true);
  } finally {
    setBusy(submitForm, false);
  }
});

queryForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearTimeout(pollTimer);
  resultArea.classList.add("hidden");
  await queryStatus(queryTokenInput.value.trim());
});

savedKeysToggle.addEventListener("click", () => {
  setSavedKeysPanel(savedKeysPanel.classList.contains("hidden"));
});
savedKeysClose.addEventListener("click", () => setSavedKeysPanel(false));
savedKeysClear.addEventListener("click", clearSavedKeys);

document.querySelector("#copy-token").addEventListener("click", async () => {
  try {
    await copyText(queryTokenOutput.textContent);
    setMessage(submitMessage, "查询令牌已复制。");
  } catch (error) {
    setMessage(submitMessage, error.message, true);
  }
});

document.querySelector("#copy-query-link").addEventListener("click", async () => {
  const link = `${window.location.origin}${window.location.pathname}#token=${encodeURIComponent(queryTokenOutput.textContent)}`;
  try {
    await copyText(link);
    setMessage(submitMessage, "查询链接已复制。令牌只放在链接片段中，不会提交到服务器日志。");
  } catch (error) {
    setMessage(submitMessage, error.message, true);
  }
});

document.querySelector("#download-token").addEventListener("click", () => {
  const blob = new Blob([`ComfyUI Prompt Gateway 查询令牌\n${queryTokenOutput.textContent}\n`], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "comfyui-query-token.txt";
  anchor.click();
  URL.revokeObjectURL(url);
});

let publishLastFocus = null;
let publishRequestId = 0;

function openPublishModal() {
  publishLastFocus = document.activeElement;
  publishRequestId += 1;
  publishWorkName.value = "";
  publishAuthor.value = "";
  setMessage(publishMessage, "");
  const confirmButton = document.querySelector("#publish-confirm");
  confirmButton.disabled = false;
  publishModal.classList.remove("hidden");
  publishModal.setAttribute("aria-hidden", "false");
  document.body.classList.add("publish-modal-open");
  publishWorkName.focus();
}

function closePublishModal() {
  if (publishModal.classList.contains("hidden")) return;
  publishRequestId += 1;
  publishModal.classList.add("hidden");
  publishModal.setAttribute("aria-hidden", "true");
  document.body.classList.remove("publish-modal-open");
  if (publishLastFocus && publishLastFocus.focus) publishLastFocus.focus();
  publishLastFocus = null;
}

async function confirmPublish() {
  if (!activeToken) return;
  const confirmButton = document.querySelector("#publish-confirm");
  const requestId = ++publishRequestId;
  confirmButton.disabled = true;
  setMessage(publishMessage, "正在发布…");
  try {
    const response = await fetch("/api/publish", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token: activeToken,
        work_name: publishWorkName.value.trim() || null,
        author: publishAuthor.value.trim() || null,
      }),
    });
    if (response.status === 409) {
      publishButton.disabled = true;
      publishButton.textContent = "已发布";
      closePublishModal();
      setMessage(queryMessage, "该图片已经发布过。");
      return;
    }
    const data = await readJson(response);
    if (requestId !== publishRequestId || publishModal.classList.contains("hidden")) return;
    if (!data.work_id) throw new Error("发布失败");
    closePublishModal();
    publishButton.disabled = true;
    publishButton.textContent = "已发布";
    setMessage(queryMessage, "已发布至画廊。发布不可撤销。");
  } catch (error) {
    if (requestId !== publishRequestId) return;
    setMessage(publishMessage, error.message, true);
    confirmButton.disabled = false;
  }
}

publishButton.addEventListener("click", () => {
  if (!activeToken) return;
  const confirmed = window.confirm(
    "是否确认图片完全合规？发布功能不可撤销。发布违规图片会导致IP被ban。",
  );
  if (confirmed) openPublishModal();
});

document.querySelector("#publish-cancel").addEventListener("click", closePublishModal);
document.querySelector("#publish-confirm").addEventListener("click", confirmPublish);

publishModal.addEventListener("click", (event) => {
  if (event.target.matches("[data-publish-close]")) closePublishModal();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closePublishModal();
});

let galleryLastFocus = null;
let galleryRequestId = 0;
let galleryLoadId = 0;
let galleryItems = [];

const GALLERY_ADD_MESSAGES = {
  "该图片已在此图库中": "这张图片已经在这个图库里了，无需重复加入。",
  "该图库已达张数上限": "这个图库的图片张数已满，请换一个图库或新建。",
  "账号私库总张数已达上限": "账号私库总张数已满，请先删除一些图片。",
  "账号私库容量已达上限": "账号私库容量已满，请先删除一些图片。",
  "图片体积超过单张上限": "这张图片体积超过单张上限，无法加入私库。",
  "图片已过期": "图片已过期，无法再加入图库。",
  "图库不存在": "图库不存在，可能已被删除，请刷新后重试。",
  "图库已过期": "图库已过期或被删除，请刷新后重试。",
  "取图令牌无效": "取图令牌无效，无法加入图库。",
};

function galleryError(status, detail) {
  const text =
    typeof detail === "string"
      ? detail
      : detail && typeof detail.message === "string"
        ? detail.message
        : "";
  if (status === 401) return "登录状态已失效，请重新登录后再加入图库。";
  if (status === 403) return text || "账号已被冻结或停用，无法加入图库。";
  return GALLERY_ADD_MESSAGES[text] || text || `操作失败（${status}）`;
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function selectedGalleryId() {
  const checked = galleryOptions.querySelector(
    'input[name="gallery-choice"]:checked',
  );
  return checked ? checked.value : "";
}

function renderGalleryOptions(items, usage, limits, preselectId = "") {
  galleryItems = items;
  galleryOptions.replaceChildren();
  const maxPerGallery = Number(limits && limits.max_items_per_gallery) || 0;
  const maxTotalItems = Number(limits && limits.max_items_per_account) || 0;
  const quotaBytes = Number(limits && limits.quota_bytes) || 0;
  galleryUsage.textContent = limits
    ? `账号私库已用 ${Number(usage.items || 0)} / ${maxTotalItems} 张 · ${formatBytes(usage.bytes)} / ${formatBytes(quotaBytes)}`
    : "";
  if (!items.length) {
    galleryEmpty.classList.remove("hidden");
    galleryConfirmButton.disabled = true;
    return;
  }
  galleryEmpty.classList.add("hidden");
  for (const gallery of items) {
    const option = element("label", "gallery-option");
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "gallery-choice";
    radio.value = gallery.gallery_id;
    if (gallery.gallery_id === preselectId) radio.checked = true;
    radio.addEventListener("change", () => {
      galleryConfirmButton.disabled = false;
      setMessage(galleryMessage, "");
    });
    option.appendChild(radio);
    const main = element("span", "gallery-option-main");
    main.appendChild(element("span", "gallery-option-name", gallery.name));
    const full = maxPerGallery > 0 && gallery.item_count >= maxPerGallery;
    main.appendChild(
      element(
        "span",
        "gallery-option-meta",
        `${gallery.item_count} 张 · ${formatBytes(gallery.item_bytes)}${full ? " · 已满" : ""}`,
      ),
    );
    option.appendChild(main);
    galleryOptions.appendChild(option);
  }
  galleryConfirmButton.disabled = !selectedGalleryId();
}

async function loadGalleryOptions(preselectId = "") {
  const loadId = ++galleryLoadId;
  galleryOptions.replaceChildren(
    element("p", "gallery-note", "正在读取图库…"),
  );
  galleryEmpty.classList.add("hidden");
  galleryUsage.textContent = "";
  galleryConfirmButton.disabled = true;
  try {
    const response = await fetch("/api/private/galleries", {
      credentials: "same-origin",
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(galleryError(response.status, data.detail));
    }
    if (loadId !== galleryLoadId) return;
    renderGalleryOptions(
      Array.isArray(data.items) ? data.items : [],
      data.usage || {},
      data.limits || null,
      preselectId,
    );
  } catch (error) {
    if (loadId !== galleryLoadId) return;
    galleryOptions.replaceChildren();
    galleryEmpty.classList.add("hidden");
    setMessage(galleryMessage, error.message, true);
  }
}

function openGalleryModal() {
  if (!activeToken || !accountLoggedIn) return;
  galleryLastFocus = document.activeElement;
  galleryRequestId += 1;
  galleryModal.classList.remove("hidden");
  galleryModal.setAttribute("aria-hidden", "false");
  document.body.classList.add("publish-modal-open");
  setMessage(galleryMessage, "");
  galleryNewName.value = "";
  galleryNewName.focus();
  loadGalleryOptions();
}

function closeGalleryModal() {
  if (galleryModal.classList.contains("hidden")) return;
  galleryRequestId += 1;
  galleryLoadId += 1;
  galleryModal.classList.add("hidden");
  galleryModal.setAttribute("aria-hidden", "true");
  document.body.classList.remove("publish-modal-open");
  if (galleryLastFocus && galleryLastFocus.focus) galleryLastFocus.focus();
  galleryLastFocus = null;
}

async function submitGalleryAdd({ galleryId = "", galleryName = "", createName = "" }) {
  if (!activeToken) return;
  const requestId = ++galleryRequestId;
  const creating = Boolean(createName);
  let targetId = galleryId;
  let targetName = galleryName || "所选图库";
  galleryConfirmButton.disabled = true;
  galleryCreateButton.disabled = true;
  setMessage(galleryMessage, creating ? "正在新建图库…" : "正在加入图库…");
  try {
    if (creating) {
      const createResponse = await fetch("/api/private/galleries", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: createName }),
      });
      const created = await createResponse.json().catch(() => ({}));
      if (!createResponse.ok) {
        throw new Error(galleryError(createResponse.status, created.detail));
      }
      targetId = created.gallery ? created.gallery.gallery_id : "";
      targetName = (created.gallery && created.gallery.name) || createName;
      if (!targetId) throw new Error("新建图库失败，请重试。");
      setMessage(galleryMessage, "图库已创建，正在加入图片…");
    }
    const response = await fetch(
      `/api/private/galleries/${encodeURIComponent(targetId)}/items`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: activeToken }),
      },
    );
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = galleryError(response.status, data.detail);
      throw new Error(
        creating ? `图库「${targetName}」已创建，但${message}` : message,
      );
    }
    if (
      requestId !== galleryRequestId ||
      galleryModal.classList.contains("hidden")
    ) {
      return;
    }
    galleryNewName.value = "";
    setMessage(galleryMessage, `已加入图库「${targetName}」。`);
    await loadGalleryOptions(targetId);
  } catch (error) {
    if (requestId !== galleryRequestId) return;
    setMessage(galleryMessage, error.message, true);
  } finally {
    if (requestId === galleryRequestId) {
      galleryCreateButton.disabled = false;
      galleryConfirmButton.disabled = !selectedGalleryId();
    }
  }
}

galleryAddButton.addEventListener("click", openGalleryModal);
galleryCancelButton.addEventListener("click", closeGalleryModal);
galleryModal.addEventListener("click", (event) => {
  if (event.target.matches("[data-gallery-close]")) closeGalleryModal();
});
galleryConfirmButton.addEventListener("click", () => {
  const galleryId = selectedGalleryId();
  if (!galleryId) {
    setMessage(galleryMessage, "请先选择一个图库。", true);
    return;
  }
  const gallery = galleryItems.find((item) => item.gallery_id === galleryId);
  submitGalleryAdd({
    galleryId,
    galleryName: gallery ? gallery.name : "",
  });
});
galleryCreateButton.addEventListener("click", () => {
  const name = galleryNewName.value.trim();
  if (!name) {
    setMessage(galleryMessage, "请先填写图库名称。", true);
    galleryNewName.focus();
    return;
  }
  submitGalleryAdd({ createName: name });
});
galleryNewName.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    galleryCreateButton.click();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeGalleryModal();
});

document.querySelector("#destroy-result").addEventListener("click", async () => {
  if (!activeToken) return;
  if (!window.confirm("该图片将被彻底销毁，无法再取图，确认么？")) return;
  const button = document.querySelector("#destroy-result");
  button.disabled = true;
  try {
    const response = await fetch("/api/destroy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: activeToken }),
    });
    const data = await readJson(response);
    if (!data.destroyed) throw new Error("销毁失败");
    clearTimeout(pollTimer);
    if (resultUrl) {
      URL.revokeObjectURL(resultUrl);
      resultUrl = null;
    }
    resultArea.classList.add("hidden");
    taskCard.classList.add("hidden");
    tokenReveal.classList.add("hidden");
    resultImage.removeAttribute("src");
    downloadResult.removeAttribute("href");
    queryTokenOutput.textContent = "";
    queryTokenInput.value = "";
    activeToken = "";
    if (window.location.hash.startsWith("#token=")) {
      history.replaceState(null, "", window.location.pathname + window.location.search);
    }
    setMessage(queryMessage, "图片已销毁，该查询令牌已失效。");
  } catch (error) {
    button.disabled = false;
    setMessage(queryMessage, error.message, true);
  }
});

refreshServiceStatus();
setInterval(refreshServiceStatus, 10000);
loadServiceNodes();
loadLoras();
updateSavedKeysCount(readSavedKeys().entries.length);
applyAccountQuotaUI();
initFreeNotice();
useAccountQuota.addEventListener("change", applyAccountQuotaUI);
accessKeyInput.addEventListener("invalid", (event) => {
  if (useAccountQuota.checked) return;
  event.preventDefault();
  setMessage(submitMessage, "请输入密钥，或勾选「使用账号额度」。", true);
});
loadAccountQuota();

const fragment = window.location.hash;
if (fragment.startsWith("#token=")) {
  const token = decodeURIComponent(fragment.slice("#token=".length));
  queryTokenInput.value = token;
  queryStatus(token);
}

