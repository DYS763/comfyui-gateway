const state = {
  limit: 24,
  offset: 0,
  total: 0,
  search: "",
  sort: "latest",
  likedOnly: false,
  likes: new Map(),
  likeButtons: new Map(),
  likePending: new Set(),
  viewValues: new Map(),
  viewCountEls: new Map(),
  currentWorkId: null,
  commentValues: new Map(),
  commentCountEls: new Map(),
  comments: {
    workId: null,
    items: [],
    total: 0,
    limit: 10,
    offset: 0,
    loading: false,
    requestId: 0,
  },
  commentSubmitting: false,
  report: { workId: null, submitting: false },
};

const SVG_NS = "http://www.w3.org/2000/svg";

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
};

function galleryIcon(kind) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.classList.add("gallery-icon");
  const shapes = {
    heart: [
      "M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78L12 21.23l8.84-8.84a5.5 5.5 0 0 0 0-7.78z",
    ],
    comment: [
      "M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z",
    ],
    view: [
      "M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z",
      "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
    ],
    flag: [
      "M4 21V3",
      "M4 4h14l-2 4 2 4H4",
    ],
    download: [
      "M12 3v12",
      "M6 11l6 6 6-6",
      "M4 21h16",
    ],
  }[kind] || [];
  for (const d of shapes) {
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", d);
    svg.appendChild(path);
  }
  return svg;
}

function hydrateGalleryIcons() {
  for (const host of document.querySelectorAll("[data-gallery-icon]")) {
    host.replaceChildren(galleryIcon(host.dataset.galleryIcon));
  }
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function setMessage(element, text, isError = false) {
  element.textContent = text || "";
  element.classList.toggle("error", isError);
}

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? "请求参数不合法" : data.detail;
    throw new Error(detail || `请求失败（${response.status}）`);
  }
  return data;
}

async function loadGallery() {
  const message = document.querySelector("#gallery-message");
  setMessage(message, "正在加载作品…");
  try {
    const params = new URLSearchParams({
      limit: String(state.limit),
      offset: String(state.offset),
      sort: state.sort,
    });
    if (state.search) params.set("search", state.search);
    if (state.likedOnly) params.set("liked", "true");
    const data = await api(`/api/gallery?${params.toString()}`);
    const maxOffset = Math.max(
      0,
      Math.floor(Math.max(0, data.total - 1) / state.limit) * state.limit,
    );
    if (data.total > 0 && state.offset > maxOffset) {
      state.offset = maxOffset;
      return loadGallery();
    }
    if (data.total === 0) state.offset = 0;
    state.total = data.total;
    state.likes.clear();
    state.likeButtons.clear();
    state.commentValues.clear();
    state.commentCountEls.clear();
    state.viewValues.clear();
    state.viewCountEls.clear();
    for (const item of data.items) {
      state.likes.set(item.work_id, {
        liked: Boolean(item.liked),
        count: Number(item.like_count) || 0,
      });
      state.commentValues.set(item.work_id, Number(item.comment_count) || 0);
      state.viewValues.set(item.work_id, Number(item.view_count) || 0);
    }
    const filtered = Boolean(state.search || state.likedOnly);
    const emptyText = filtered
      ? "没有符合条件的作品。"
      : "画廊里还没有作品。";
    renderCards(data.items, emptyText);
    const pages = Math.max(1, Math.ceil(data.total / state.limit));
    const page = Math.floor(state.offset / state.limit) + 1;
    document.querySelector("#gallery-summary").textContent =
      `共 ${data.total} 件作品${filtered ? "（筛选结果）" : ""}`;
    document.querySelector("#gallery-page").textContent = `第 ${page} / ${pages} 页`;
    document.querySelector("#gallery-prev").disabled = state.offset <= 0;
    document.querySelector("#gallery-next").disabled = state.offset + state.limit >= data.total;
    setMessage(message, "");
  } catch (error) {
    setMessage(message, error.message, true);
  }
}

function renderCards(items, emptyText = "画廊里还没有作品。") {
  const grid = document.querySelector("#gallery-grid");
  grid.replaceChildren();
  if (!items.length) {
    grid.appendChild(el("p", "gallery-empty", emptyText));
    return;
  }
  for (const item of items) {
    const card = el("article", "gallery-card");
    const opener = el("button", "gallery-card-open");
    opener.type = "button";
    opener.title = item.work_name || "未命名作品";

    const image = el("img");
    image.loading = "lazy";
    image.decoding = "async";
    image.alt = item.work_name || "未命名作品";
    image.src = `/api/gallery/${encodeURIComponent(item.work_id)}/thumbnail`;

    const body = el("div", "gallery-card-body");
    body.appendChild(el("strong", "gallery-card-name", item.work_name || "未命名作品"));
    body.appendChild(el("span", "gallery-card-author", item.author || "匿名"));

    image.addEventListener("error", () => {
      image.classList.add("hidden");
      if (!opener.querySelector(".gallery-card-missing")) {
        opener.insertBefore(el("div", "gallery-card-missing", "图片不可用"), body);
      }
    });

    opener.appendChild(image);
    opener.appendChild(body);
    opener.addEventListener("click", () => openWork(item.work_id));
    card.appendChild(opener);

    const actions = el("div", "gallery-card-actions");
    actions.appendChild(createViewButton(item.work_id));
    actions.appendChild(createCommentButton(item.work_id));
    actions.appendChild(createLikeButton(item.work_id));
    actions.appendChild(createReportButton(item.work_id));
    card.appendChild(actions);

    grid.appendChild(card);
    applyLikeState(item.work_id);
    applyViewState(item.work_id);
  }
}

function createViewButton(workId) {
  const button = el("button", "gallery-like gallery-view-stat");
  button.type = "button";
  button.setAttribute("aria-label", "浏览量");
  button.appendChild(galleryIcon("view"));
  const count = el("span", "gallery-view-count", "0");
  button.appendChild(count);
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    openWork(workId);
  });
  state.viewCountEls.set(workId, count);
  return button;
}

function applyViewState(workId) {
  const count = state.viewValues.get(workId) || 0;
  const cardCount = state.viewCountEls.get(workId);
  if (cardCount) cardCount.textContent = String(count);
  if (state.currentWorkId === workId) {
    document.querySelector("#viewer-view-count").textContent = String(count);
  }
}

function updateViewCount(workId, count) {
  const value = Math.max(0, Number(count) || 0);
  if (value <= (state.viewValues.get(workId) || 0)) return;
  state.viewValues.set(workId, value);
  applyViewState(workId);
}

async function recordView(workId) {
  try {
    const data = await api(`/api/gallery/${encodeURIComponent(workId)}/view`, {
      method: "POST",
    });
    updateViewCount(workId, data.view_count);
  } catch (error) {
    console.warn("could not record gallery view", error);
  }
}

function createLikeButton(workId) {
  const button = el("button", "gallery-like");
  button.type = "button";
  button.appendChild(galleryIcon("heart"));
  button.appendChild(el("span", "gallery-like-count", "0"));
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleLike(workId);
  });
  state.likeButtons.set(workId, button);
  return button;
}

function createReportButton(workId) {
  const button = el("button", "gallery-like gallery-report-button");
  button.type = "button";
  button.setAttribute("aria-label", "举报作品");
  button.title = "举报作品";
  button.appendChild(galleryIcon("flag"));
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    openReportModal(workId);
  });
  return button;
}

let reportLastFocus = null;

function openReportModal(workId) {
  if (!workId) return;
  state.report.workId = workId;
  reportLastFocus = document.activeElement;
  document.querySelector("#report-content").value = "";
  setMessage(document.querySelector("#report-message"), "");
  document.querySelector("#report-submit").disabled = false;
  const modal = document.querySelector("#gallery-report-modal");
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
  document.body.classList.add("report-open");
  document.querySelector("#report-content").focus();
}

function closeReportModal() {
  const modal = document.querySelector("#gallery-report-modal");
  if (modal.classList.contains("hidden")) return;
  state.report.workId = null;
  state.report.submitting = false;
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
  document.body.classList.remove("report-open");
  if (reportLastFocus && reportLastFocus.focus) reportLastFocus.focus();
  reportLastFocus = null;
}

async function submitReport() {
  if (state.report.submitting || !state.report.workId) return;
  const content = document.querySelector("#report-content").value.trim();
  const message = document.querySelector("#report-message");
  const button = document.querySelector("#report-submit");
  state.report.submitting = true;
  button.disabled = true;
  setMessage(message, "正在提交…");
  try {
    await api(`/api/gallery/${encodeURIComponent(state.report.workId)}/report`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: content || null }),
    });
    document.querySelector("#report-content").value = "";
    setMessage(message, "已提交，管理员会尽快处理");
  } catch (error) {
    setMessage(message, error.message, true);
  } finally {
    state.report.submitting = false;
    button.disabled = false;
  }
}

function createCommentButton(workId) {
  const button = el("button", "gallery-like gallery-comment-stat");
  button.type = "button";
  button.setAttribute("aria-label", "查看评论");
  button.appendChild(galleryIcon("comment"));
  const count = el(
    "span",
    "gallery-comment-count",
    `(${state.commentValues.get(workId) || 0})`,
  );
  button.appendChild(count);
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    openWork(workId);
  });
  state.commentCountEls.set(workId, count);
  return button;
}

function applyLikeState(workId) {
  const like = state.likes.get(workId) || { liked: false, count: 0 };
  const pending = state.likePending.has(workId);
  const button = state.likeButtons.get(workId);
  if (button) {
    button.classList.toggle("active", like.liked);
    button.setAttribute("aria-pressed", like.liked ? "true" : "false");
    button.setAttribute("aria-label", like.liked ? "取消点赞" : "点赞");
    button.disabled = pending;
    button.querySelector(".gallery-like-count").textContent = String(like.count);
  }
  if (state.currentWorkId === workId) {
    const viewer = document.querySelector("#viewer-like");
    viewer.classList.toggle("active", like.liked);
    viewer.setAttribute("aria-pressed", like.liked ? "true" : "false");
    viewer.setAttribute("aria-label", like.liked ? "取消点赞" : "点赞");
    viewer.disabled = pending;
    document.querySelector("#viewer-like-count").textContent = String(like.count);
  }
}

function setLikeState(workId, next) {
  state.likes.set(workId, {
    liked: Boolean(next.liked),
    count: Math.max(0, Number(next.count) || 0),
  });
  applyLikeState(workId);
}

async function toggleLike(workId) {
  const current = state.likes.get(workId);
  if (!current || state.likePending.has(workId)) return;
  const message = document.querySelector("#gallery-message");
  const desired = !current.liked;
  const previous = { ...current };
  state.likePending.add(workId);
  setLikeState(workId, {
    liked: desired,
    count: Math.max(0, current.count + (desired ? 1 : -1)),
  });
  try {
    const data = await api(`/api/gallery/${encodeURIComponent(workId)}/like`, {
      method: desired ? "PUT" : "DELETE",
    });
    setLikeState(workId, { liked: data.liked, count: data.like_count });
    setMessage(message, "");
  } catch (error) {
    setLikeState(workId, previous);
    setMessage(message, error.message, true);
  } finally {
    state.likePending.delete(workId);
    applyLikeState(workId);
  }
}

function updateCommentCount(workId, count) {
  const value = Math.max(0, Number(count) || 0);
  state.commentValues.set(workId, value);
  const cardCount = state.commentCountEls.get(workId);
  if (cardCount) cardCount.textContent = `(${value})`;
  if (state.comments.workId === workId) {
    document.querySelector("#comments-total").textContent = `(${value})`;
    document.querySelector("#viewer-comment-count").textContent = `(${value})`;
  }
}

function resetCommentForm() {
  document.querySelector("#comment-content").value = "";
  document.querySelector("#comment-nickname").value = "";
  document.querySelector("#comment-length").textContent = "0 / 500";
  document.querySelector("#comment-length").classList.remove("over");
  document.querySelector('input[name="comment-mode"][value="anonymous"]').checked = true;
  document.querySelector("#comment-nickname-wrap").classList.add("hidden");
  document.querySelector("#comment-form-message").textContent = "";
  document.querySelector("#comment-form-message").classList.remove("error");
}

function prepareComments(workId) {
  const comments = state.comments;
  comments.requestId += 1;
  comments.workId = workId;
  comments.items = [];
  comments.total = state.commentValues.get(workId) || 0;
  comments.offset = 0;
  comments.loading = false;
  document.querySelector("#comments-list").replaceChildren();
  document.querySelector("#comments-more").classList.add("hidden");
  document.querySelector("#comments-total").textContent = `(${comments.total})`;
  document.querySelector("#viewer-comment-count").textContent = `(${comments.total})`;
  setMessage(document.querySelector("#comments-status"), "");
  resetCommentForm();
}

async function loadComments(reset = false) {
  const comments = state.comments;
  if (!comments.workId) return;
  if (reset) {
    comments.items = [];
    comments.offset = 0;
  }
  const requestId = ++comments.requestId;
  const list = document.querySelector("#comments-list");
  const more = document.querySelector("#comments-more");
  const status = document.querySelector("#comments-status");
  comments.loading = true;
  more.disabled = true;
  if (reset) {
    list.replaceChildren(el("p", "gallery-comments-empty", "正在加载评论…"));
    setMessage(status, "正在加载评论…");
  }
  try {
    const data = await api(
      `/api/gallery/${encodeURIComponent(comments.workId)}/comments?limit=${comments.limit}&offset=${comments.offset}`,
    );
    if (requestId !== comments.requestId) return;
    comments.items.push(...data.items);
    comments.offset = comments.items.length;
    comments.total = data.total;
    renderComments();
    updateCommentCount(comments.workId, data.total);
    setMessage(status, "");
  } catch (error) {
    if (requestId !== comments.requestId) return;
    setMessage(status, error.message, true);
  } finally {
    if (requestId === comments.requestId) {
      comments.loading = false;
      more.disabled = false;
    }
  }
}

function renderComments() {
  const comments = state.comments;
  const list = document.querySelector("#comments-list");
  list.replaceChildren();
  if (!comments.items.length) {
    list.appendChild(el("p", "gallery-comments-empty", "还没有评论，来抢沙发。"));
  } else {
    for (const comment of comments.items) {
      const item = el("article", "gallery-comment");
      const head = el("div", "gallery-comment-head");
      if (comment.display_name) {
        head.appendChild(el("strong", "gallery-comment-name", comment.display_name));
      }
      head.appendChild(el("span", "gallery-comment-time", formatDate(comment.created_at)));
      item.appendChild(head);
      item.appendChild(el("p", "gallery-comment-content", comment.content));
      list.appendChild(item);
    }
  }
  const more = document.querySelector("#comments-more");
  more.classList.toggle("hidden", comments.items.length >= comments.total);
  more.textContent = "加载更多评论";
  document.querySelector("#comments-total").textContent = `(${comments.total})`;
}

function updateCommentLength() {
  const value = document.querySelector("#comment-content").value;
  const count = Array.from(value).length;
  const label = document.querySelector("#comment-length");
  label.textContent = `${count} / 500`;
  label.classList.toggle("over", count > 500);
}

async function submitComment(event) {
  event.preventDefault();
  if (state.commentSubmitting) return;
  const workId = state.comments.workId;
  if (!workId) return;
  const contentInput = document.querySelector("#comment-content");
  const nicknameInput = document.querySelector("#comment-nickname");
  const formMessage = document.querySelector("#comment-form-message");
  const submitButton = document.querySelector("#comment-submit");
  const content = contentInput.value.trim();
  const contentLength = Array.from(content).length;
  if (!content) {
    setMessage(formMessage, "评论内容不能为空", true);
    contentInput.focus();
    return;
  }
  if (contentLength > 500) {
    setMessage(formMessage, "评论内容不能超过 500 字", true);
    contentInput.focus();
    return;
  }
  const mode = document.querySelector('input[name="comment-mode"]:checked').value;
  let displayName = null;
  if (mode === "named") {
    displayName = nicknameInput.value.trim();
    if (!displayName) {
      setMessage(formMessage, "请填写昵称，或选择匿名发布", true);
      nicknameInput.focus();
      return;
    }
    if (Array.from(displayName).length > 40) {
      setMessage(formMessage, "昵称不能超过 40 个字符", true);
      nicknameInput.focus();
      return;
    }
  }

  state.commentSubmitting = true;
  submitButton.disabled = true;
  document.querySelector("#comment-submit-label").textContent = "发布中…";
  setMessage(formMessage, "");
  try {
    const data = await api(
      `/api/gallery/${encodeURIComponent(workId)}/comments`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, display_name: displayName }),
      },
    );
    resetCommentForm();
    updateCommentCount(workId, data.total);
    setMessage(
      formMessage,
      data.forced_anonymous
        ? "未识别到访客身份，已按匿名发布。"
        : "评论已发布。",
    );
    await loadComments(true);
  } catch (error) {
    setMessage(formMessage, error.message, true);
  } finally {
    state.commentSubmitting = false;
    submitButton.disabled = false;
    document.querySelector("#comment-submit-label").textContent = "发布评论";
  }
}

let viewerRequestId = 0;
let viewerLastFocus = null;

async function openWork(workId) {
  const viewer = document.querySelector("#gallery-viewer");
  const image = document.querySelector("#viewer-image");
  const meta = document.querySelector("#viewer-meta");
  const requestId = ++viewerRequestId;
  viewerLastFocus = document.activeElement;
  state.currentWorkId = workId;
  if (!state.likes.has(workId)) {
    state.likes.set(workId, { liked: false, count: 0 });
  }
  if (!state.viewValues.has(workId)) {
    state.viewValues.set(workId, 0);
  }
  applyLikeState(workId);
  applyViewState(workId);
  recordView(workId);
  prepareComments(workId);
  loadComments(true);

  document.querySelector("#viewer-title").textContent = "作品";
  document.querySelector("#viewer-download").href =
    `/api/gallery/${encodeURIComponent(workId)}/download`;
  image.src = `/api/gallery/${encodeURIComponent(workId)}/image`;
  image.alt = "作品大图";
  meta.replaceChildren(el("p", "message", "正在加载作品信息…"));
  viewer.classList.remove("hidden");
  viewer.setAttribute("aria-hidden", "false");
  document.body.classList.add("viewer-open");
  document.querySelector("#viewer-close").focus({ preventScroll: true });

  try {
    const data = await api(`/api/gallery/${encodeURIComponent(workId)}`);
    if (requestId !== viewerRequestId || viewer.classList.contains("hidden")) return;
    document.querySelector("#viewer-title").textContent = data.work_name || "未命名作品";
    image.alt = data.work_name || "作品大图";
    state.likes.set(data.work_id, {
      liked: Boolean(data.liked),
      count: Number(data.like_count) || 0,
    });
    applyLikeState(data.work_id);
    updateViewCount(data.work_id, data.view_count);
    updateCommentCount(data.work_id, data.comment_count);
    renderMeta(data);
  } catch (error) {
    if (requestId !== viewerRequestId || viewer.classList.contains("hidden")) return;
    meta.replaceChildren(el("p", "message error", error.message));
  }
}

function renderMeta(data) {
  const meta = document.querySelector("#viewer-meta");
  meta.replaceChildren();
  const fields = [
    ["作者", data.author || "匿名"],
    ["完成时间", formatDate(data.completed_at)],
    ["分辨率", data.width && data.height ? `${data.width}×${data.height}` : "—"],
    ["步数", data.steps ?? "默认"],
    ["种子", data.seed ?? "—"],
    ["LoRA", data.lora_name || "默认"],
    ["发布时间", formatDate(data.published_at)],
  ];
  const grid = el("dl", "gallery-kv");
  for (const [name, value] of fields) {
    const row = el("div");
    row.appendChild(el("dt", "", name));
    row.appendChild(el("dd", "", value ?? "—"));
    grid.appendChild(row);
  }
  meta.appendChild(grid);
  meta.appendChild(textBlock("正向提示词", data.prompt));
  meta.appendChild(textBlock("负面提示词", data.negative_prompt));
}

function textBlock(title, text) {
  const block = el("div", "gallery-text-block");
  block.appendChild(el("h3", "", title));
  block.appendChild(el("p", "gallery-long-text", text || "（未填写）"));
  return block;
}

function openFullscreen(src, alt) {
  if (!src) return;
  const layer = document.querySelector("#gallery-fullscreen");
  const image = document.querySelector("#gallery-fullscreen-image");
  image.src = src;
  image.alt = alt || "全屏作品";
  layer.classList.remove("hidden");
  layer.setAttribute("aria-hidden", "false");
  document.body.classList.add("fullscreen-open");
  document.querySelector("#gallery-fullscreen-close").focus();
}

function closeFullscreen() {
  const layer = document.querySelector("#gallery-fullscreen");
  if (layer.classList.contains("hidden")) return;
  layer.classList.add("hidden");
  layer.setAttribute("aria-hidden", "true");
  document.querySelector("#gallery-fullscreen-image").removeAttribute("src");
  document.body.classList.remove("fullscreen-open");
}

function closeViewer() {
  const viewer = document.querySelector("#gallery-viewer");
  if (viewer.classList.contains("hidden")) return;
  closeFullscreen();
  viewerRequestId += 1;
  viewer.classList.add("hidden");
  viewer.setAttribute("aria-hidden", "true");
  document.body.classList.remove("viewer-open");
  state.currentWorkId = null;
  state.comments.requestId += 1;
  state.comments.workId = null;
  document.querySelector("#viewer-image").removeAttribute("src");
  document.querySelector("#viewer-download").setAttribute("href", "#");
  document.querySelector("#viewer-meta").replaceChildren();
  if (viewerLastFocus && viewerLastFocus.focus) viewerLastFocus.focus();
  viewerLastFocus = null;
}

document.querySelector("#gallery-refresh").addEventListener("click", loadGallery);

const gallerySearchInput = document.querySelector("#gallery-search");
const gallerySort = document.querySelector("#gallery-sort");
const galleryLikedFilter = document.querySelector("#gallery-liked-filter");
let gallerySearchTimer = null;

function applyGallerySearch(immediate = false) {
  const value = gallerySearchInput.value.trim();
  clearTimeout(gallerySearchTimer);
  if (immediate || !value) {
    if (state.search === value) return;
    state.search = value;
    state.offset = 0;
    loadGallery();
    return;
  }
  gallerySearchTimer = setTimeout(() => {
    state.search = value;
    state.offset = 0;
    loadGallery();
  }, 350);
}

gallerySearchInput.addEventListener("input", () => applyGallerySearch(false));
gallerySearchInput.addEventListener("search", () => applyGallerySearch(true));

gallerySort.addEventListener("change", () => {
  state.sort = gallerySort.value;
  state.offset = 0;
  loadGallery();
});

galleryLikedFilter.addEventListener("click", () => {
  state.likedOnly = !state.likedOnly;
  galleryLikedFilter.setAttribute(
    "aria-pressed",
    String(state.likedOnly),
  );
  state.offset = 0;
  loadGallery();
});

document.querySelector("#viewer-like").addEventListener("click", () => {
  if (state.currentWorkId) toggleLike(state.currentWorkId);
});

document.querySelector("#viewer-comment-jump").addEventListener("click", () => {
  document.querySelector("#gallery-comments").scrollIntoView({
    behavior: "smooth",
    block: "start",
  });
});

document.querySelector("#comment-form").addEventListener("submit", submitComment);

document.querySelector("#comment-content").addEventListener("input", updateCommentLength);

for (const radio of document.querySelectorAll('input[name="comment-mode"]')) {
  radio.addEventListener("change", () => {
    const named = document.querySelector('input[name="comment-mode"]:checked').value === "named";
    const wrap = document.querySelector("#comment-nickname-wrap");
    wrap.classList.toggle("hidden", !named);
    if (named) {
      document.querySelector("#comment-nickname").focus();
    } else {
      document.querySelector("#comment-nickname").value = "";
    }
  });
}

document.querySelector("#comments-more").addEventListener("click", () => {
  loadComments(false);
});

document.querySelector("#gallery-prev").addEventListener("click", () => {
  state.offset = Math.max(0, state.offset - state.limit);
  loadGallery();
});

document.querySelector("#gallery-next").addEventListener("click", () => {
  state.offset += state.limit;
  loadGallery();
});

document.querySelector("#viewer-close").addEventListener("click", closeViewer);

document.querySelector("#viewer-image").addEventListener("click", () => {
  const image = document.querySelector("#viewer-image");
  if (image.getAttribute("src")) {
    openFullscreen(image.src, image.alt);
  }
});

document.querySelector("#gallery-fullscreen-close").addEventListener(
  "click",
  closeFullscreen,
);

document.querySelector("#gallery-fullscreen").addEventListener("click", (event) => {
  if (event.target.matches("[data-fullscreen-close]")) closeFullscreen();
});

document.querySelector("#viewer-report").addEventListener("click", () => {
  if (state.currentWorkId) openReportModal(state.currentWorkId);
});

document.querySelector("#report-close").addEventListener("click", closeReportModal);

document.querySelector("#report-cancel").addEventListener("click", closeReportModal);

document.querySelector("#report-submit").addEventListener("click", submitReport);

document.querySelector("#gallery-report-modal").addEventListener("click", (event) => {
  if (event.target.matches("[data-report-close]")) closeReportModal();
});

document.querySelector("#gallery-viewer").addEventListener("click", (event) => {
  if (event.target.matches("[data-viewer-close]")) closeViewer();
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  const fullscreen = document.querySelector("#gallery-fullscreen");
  if (!fullscreen.classList.contains("hidden")) {
    closeFullscreen();
    return;
  }
  const reportModal = document.querySelector("#gallery-report-modal");
  if (!reportModal.classList.contains("hidden")) {
    closeReportModal();
    return;
  }
  closeViewer();
});

async function initGallery() {
  hydrateGalleryIcons();
  await loadGallery();
  const hash = window.location.hash;
  if (hash.startsWith("#work=")) {
    const workId = decodeURIComponent(hash.slice("#work=".length));
    if (workId) openWork(workId);
  }
}

initGallery();
