(function () {
  const SAVED_KEYS_STORAGE_KEY = "comfyui-gateway.queryTokens.v1";
  const BUCKET_LABELS = { free: "免费额度", bonus: "奖励额度", paid: "兑换额度" };
  const ENTRY_TYPE_LABELS = {
    charge: "扣费",
    refund: "退款",
    reward: "奖励",
    recharge: "兑换",
    adjust: "调整",
  };
  const authBlock = document.querySelector("#auth-block");
  const accountBlock = document.querySelector("#account-block");
  const logoutButton = document.querySelector("#logout-button");
  const galleryDetail = document.querySelector("#gallery-detail");
  const galleryDetailTitle = document.querySelector("#gallery-detail-title");
  const galleryDetailMeta = document.querySelector("#gallery-detail-meta");
  const galleryDetailGrid = document.querySelector("#gallery-detail-grid");
  const galleryDetailEmpty = document.querySelector("#gallery-detail-empty");
  const galleryDetailMessage = document.querySelector("#gallery-detail-message");
  const galleryRenameInput = document.querySelector("#gallery-rename-input");
  const galleryShareState = document.querySelector("#gallery-share-state");
  const galleryShareCreate = document.querySelector("#gallery-share-create");
  const galleryShareCopy = document.querySelector("#gallery-share-copy");
  const galleryShareRevoke = document.querySelector("#gallery-share-revoke");

  let state = {
    balances: null,
    galleries: [],
    tokens: [],
    accountTokens: [],
    accountTokenPage: { limit: 10, offset: 0, total: 0, search: "" },
    ledgerPage: { limit: 10, offset: 0, total: 0 },
    activeGallery: null,
    galleryItems: [],
  };
  let galleryDetailRequestId = 0;
  let galleryDetailLastFocus = null;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function setMessage(element, text, isError = false) {
    element.textContent = text || "";
    element.classList.toggle("error", isError);
  }

  function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? value
      : date.toLocaleString("zh-CN", { hour12: false });
  }

  function formatBytes(bytes) {
    const value = Number(bytes || 0);
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / 1024 / 1024).toFixed(1)} MB`;
  }

  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: "same-origin", ...options });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = data.detail;
      const message =
        typeof detail === "string"
          ? detail
          : (detail && detail.message) || `请求失败（${response.status}）`;
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function showAuth() {
    closeGalleryDetail();
    authBlock.classList.remove("hidden");
    accountBlock.classList.add("hidden");
    logoutButton.classList.add("hidden");
  }

  function showAccount() {
    authBlock.classList.add("hidden");
    accountBlock.classList.remove("hidden");
    logoutButton.classList.remove("hidden");
  }

  function renderBalances(balances) {
    if (!balances) return;
    state.balances = balances;
    document.querySelector("#balance-free").textContent = balances.free;
    document.querySelector("#balance-free-note").textContent =
      `当日上限 ${balances.free_limit}，已用 ${balances.free_used}`;
    document.querySelector("#balance-bonus").textContent = balances.bonus;
    document.querySelector("#balance-paid").textContent = balances.paid;
  }

  function readLocalTokens() {
    try {
      const raw = localStorage.getItem(SAVED_KEYS_STORAGE_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];
      return parsed.filter(
        (item) => item && typeof item.token === "string" && item.token,
      );
    } catch (error) {
      return [];
    }
  }

  function buildTokenRow(entry, source, options = {}) {
    const row = el("div", "account-token");
    if (options.thumbnail) row.classList.add("account-token-with-thumb");
    const main = el("div", "account-token-main");
    const meta = el("div", "account-token-meta");
    meta.appendChild(el("span", "account-token-badge", source));
    meta.appendChild(
      el("span", "account-token-job", entry.jobId || entry.job_id || "任务未知"),
    );
    main.appendChild(meta);
    main.appendChild(el("div", "account-token-code", entry.token));

    const createdAt = entry.createdAt || entry.created_at;
    const expiresAt = entry.expiresAt || entry.expires_at;
    main.appendChild(
      el(
        "div",
        "account-note",
        `创建 ${formatDate(createdAt)} · 保留至 ${formatDate(expiresAt)}`,
      ),
    );
    row.appendChild(main);

    if (options.thumbnail) {
      const thumb = el("div", "account-token-thumb");
      const image = document.createElement("img");
      image.loading = "lazy";
      image.alt = "取图记录缩略图";
      image.src = `/api/account/tokens/${encodeURIComponent(entry.job_id)}/thumbnail`;
      image.addEventListener("error", () => {
        image.remove();
        thumb.textContent = "图片已不可用";
      });
      thumb.appendChild(image);
      row.appendChild(thumb);
    }

    const actions = el("div", "account-token-actions");
    const copy = el("button", "", "复制");
    copy.type = "button";
    copy.addEventListener("click", () => copyText(entry.token));
    actions.appendChild(copy);
    if (options.onDelete) {
      const remove = el("button", "account-token-delete", "删除记录");
      remove.type = "button";
      remove.addEventListener("click", options.onDelete);
      actions.appendChild(remove);
    }
    if (state.galleries.length) {
      const select = document.createElement("select");
      select.className = "account-select";
      for (const gallery of state.galleries) {
        const option = el("option", "", gallery.name);
        option.value = gallery.gallery_id;
        select.appendChild(option);
      }
      actions.appendChild(select);
      const join = el("button", "", "加入私库");
      join.type = "button";
      join.addEventListener("click", () =>
        addItemToGallery(
          select.value,
          entry.token,
          options.messageTarget || document.querySelector("#local-token-message"),
        ),
      );
      actions.appendChild(join);
    }
    row.appendChild(actions);
    return row;
  }

  function renderAccountTokens() {
    const list = document.querySelector("#account-token-list");
    list.replaceChildren();
    if (!state.accountTokens.length) {
      list.appendChild(
        el(
          "p",
          "account-empty",
          state.accountTokenPage.search
            ? "没有匹配的取图记录。"
            : "登录后生成的取图密钥会自动记录在这里。",
        ),
      );
    }
    for (const entry of state.accountTokens) {
      list.appendChild(
        buildTokenRow(entry, "账号", {
          thumbnail: Boolean(entry.has_thumbnail),
          onDelete: () => deleteAccountToken(entry),
          messageTarget: document.querySelector("#account-token-message"),
        }),
      );
    }
    renderAccountTokenPager();
  }

  function renderAccountTokenPager() {
    const page = state.accountTokenPage;
    const totalPages = Math.max(1, Math.ceil(page.total / page.limit));
    const currentPage = Math.floor(page.offset / page.limit) + 1;
    document.querySelector("#account-token-page").textContent =
      `第 ${currentPage} / ${totalPages} 页 · 共 ${page.total} 条`;
    document.querySelector("#account-token-prev").disabled = currentPage <= 1;
    document.querySelector("#account-token-next").disabled = currentPage >= totalPages;
    document
      .querySelector("#account-token-pager")
      .classList.toggle("hidden", page.total <= page.limit);
  }

  function renderLocalTokens() {
    const list = document.querySelector("#local-token-list");
    const message = document.querySelector("#local-token-message");
    list.replaceChildren();
    state.tokens = readLocalTokens();
    if (!state.tokens.length) {
      list.appendChild(el("p", "account-empty", "当前浏览器没有保存的取图密钥。"));
      setMessage(message, "");
      return;
    }
    if (!state.galleries.length) {
      setMessage(message, "请先创建一个私人图库，再加入图片。");
    } else {
      setMessage(message, "");
    }
    for (const entry of state.tokens) {
      list.appendChild(buildTokenRow(entry, "本机"));
    }
  }

  function galleryById(galleryId) {
    return state.galleries.find((item) => item.gallery_id === galleryId) || null;
  }

  function galleryItemUrl(galleryId, itemId, kind) {
    return (
      "/api/private/galleries/" +
      encodeURIComponent(galleryId) +
      "/items/" +
      encodeURIComponent(itemId) +
      "/" +
      kind
    );
  }

  function galleryShareLink(url) {
    return url && url.startsWith("http") ? url : `${window.location.origin}${url}`;
  }

  function renderGalleries() {
    const list = document.querySelector("#gallery-list");
    list.replaceChildren();
    if (!state.galleries.length) {
      list.appendChild(el("p", "account-empty", "还没有图库，先在上方新建一个。"));
      return;
    }
    for (const gallery of state.galleries) {
      const card = el("div", "account-gallery");
      const head = el("div", "account-gallery-head");
      head.appendChild(el("strong", "", gallery.name));
      head.appendChild(
        el(
          "span",
          "account-note",
          `${gallery.item_count} 张 · ${formatBytes(gallery.item_bytes)}`,
        ),
      );
      card.appendChild(head);

      card.appendChild(
        el(
          "div",
          "account-gallery-share",
          gallery.share_url && gallery.share_expires
            ? `分享中：${galleryShareLink(gallery.share_url)}（至 ${formatDate(gallery.share_expires)}）`
            : "未分享",
        ),
      );

      const actions = el("div", "account-gallery-actions");
      const open = el("button", "", "查看与管理");
      open.type = "button";
      open.addEventListener("click", () => openGalleryDetail(gallery.gallery_id));
      actions.appendChild(open);
      if (gallery.share_url) {
        const copy = el("button", "", "复制分享链接");
        copy.type = "button";
        copy.addEventListener("click", () =>
          copyText(galleryShareLink(gallery.share_url)),
        );
        actions.appendChild(copy);
      }
      card.appendChild(actions);
      list.appendChild(card);
    }
  }

  async function copyText(value) {
    try {
      await navigator.clipboard.writeText(value);
    } catch (error) {
      window.prompt("请手动复制以下链接", value);
    }
  }

  async function addItemToGallery(galleryId, token, message) {
    try {
      await api(`/api/private/galleries/${galleryId}/items`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });
      setMessage(message, "已加入私库。");
      await loadGalleries();
    } catch (error) {
      setMessage(message, error.message, true);
    }
  }

  function renderGalleryShare(gallery) {
    const active = Boolean(gallery.share_url && gallery.share_expires);
    galleryShareState.textContent = active
      ? `分享中：${galleryShareLink(gallery.share_url)}（至 ${formatDate(gallery.share_expires)}）`
      : "未分享";
    galleryShareCreate.textContent = active ? "重新创建分享" : "创建分享";
    galleryShareCopy.classList.toggle("hidden", !active);
    galleryShareRevoke.classList.toggle("hidden", !active);
  }

  function renderGalleryItems() {
    const gallery = state.activeGallery;
    if (!gallery) return;
    galleryDetailGrid.replaceChildren();
    const items = state.galleryItems;
    galleryDetailEmpty.classList.toggle("hidden", items.length > 0);
    if (items.length) {
      for (const item of items) {
        const tile = el("div", "account-gallery-tile");
        const card = el("button", "share-card");
        card.type = "button";
        card.setAttribute("aria-label", "查看私库大图");
        const image = document.createElement("img");
        image.loading = "lazy";
        image.alt = "私库图片";
        image.src = galleryItemUrl(gallery.gallery_id, item.item_id, "thumbnail");
        image.addEventListener("error", () => {
          if (image.dataset.fallback === "full") {
            image.remove();
            card.classList.add("share-card-missing");
            card.appendChild(
              el("span", "share-card-placeholder", "图片暂时无法显示"),
            );
            return;
          }
          image.dataset.fallback = "full";
          image.src = galleryItemUrl(gallery.gallery_id, item.item_id, "image");
        });
        card.appendChild(image);
        card.addEventListener("click", () => {
          if (card.classList.contains("share-card-missing")) return;
          openGalleryViewer(item);
        });
        tile.appendChild(card);
        tile.appendChild(
          el(
            "span",
            "account-gallery-tile-note",
            `加入 ${formatDate(item.added_at)} · 保留至 ${formatDate(item.expires_at)}`,
          ),
        );
        const actions = el("div", "account-gallery-actions");
        const remove = el("button", "", "删除这张");
        remove.type = "button";
        remove.addEventListener("click", () => deleteGalleryItem(item));
        actions.appendChild(remove);
        tile.appendChild(actions);
        galleryDetailGrid.appendChild(tile);
      }
    }

    const bytes = items.reduce(
      (total, item) => total + Number(item.size_bytes || 0),
      0,
    );
    gallery.item_count = items.length;
    gallery.item_bytes = bytes;
    const listed = galleryById(gallery.gallery_id);
    if (listed) {
      listed.item_count = items.length;
      listed.item_bytes = bytes;
      renderGalleries();
    }
    galleryDetailMeta.textContent =
      `${items.length} 张 · ${formatBytes(bytes)}`;
  }

  function openGalleryViewer(item) {
    const gallery = state.activeGallery;
    if (!gallery) return;
    const viewer = el("div", "share-viewer");
    viewer.setAttribute("role", "dialog");
    viewer.setAttribute("aria-modal", "true");
    viewer.setAttribute("aria-label", "查看私库大图");
    const backdrop = el("div", "share-viewer-backdrop");
    const card = el("div", "share-viewer-card");
    const close = el("button", "secondary-button share-viewer-close", "关闭");
    close.type = "button";
    const image = document.createElement("img");
    image.alt = "私库图片";
    image.src = galleryItemUrl(gallery.gallery_id, item.item_id, "image");
    image.addEventListener("error", () => {
      image.remove();
      card.appendChild(el("p", "share-message", "图片暂时无法显示。"));
    });
    card.appendChild(close);
    card.appendChild(image);
    viewer.appendChild(backdrop);
    viewer.appendChild(card);
    document.body.appendChild(viewer);
    document.body.classList.add("share-viewer-open");
    close.focus();

    function closeViewer() {
      viewer.remove();
      document.body.classList.remove("share-viewer-open");
      document.removeEventListener("keydown", onKeydown);
    }

    function onKeydown(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeViewer();
      }
    }

    backdrop.addEventListener("click", closeViewer);
    close.addEventListener("click", closeViewer);
    document.addEventListener("keydown", onKeydown);
  }

  function openGalleryDetail(galleryId) {
    const gallery = galleryById(galleryId);
    if (!gallery) return;
    state.activeGallery = { ...gallery };
    state.galleryItems = [];
    galleryDetailLastFocus = document.activeElement;
    galleryDetail.classList.remove("hidden");
    galleryDetail.setAttribute("aria-hidden", "false");
    document.body.classList.add("publish-modal-open");
    galleryDetailTitle.textContent = gallery.name;
    galleryRenameInput.value = gallery.name;
    galleryDetailMeta.textContent =
      `${gallery.item_count} 张 · ${formatBytes(gallery.item_bytes)}`;
    setMessage(galleryDetailMessage, "");
    galleryDetailGrid.replaceChildren();
    galleryDetailEmpty.classList.add("hidden");
    renderGalleryShare(state.activeGallery);
    galleryDetail.querySelector(".account-gallery-modal").scrollTop = 0;
    document.querySelector("#gallery-detail-close").focus();
    loadGalleryDetail();
  }

  function closeGalleryDetail() {
    if (galleryDetail.classList.contains("hidden")) return;
    galleryDetailRequestId += 1;
    if (galleryDetailLastFocus && galleryDetailLastFocus.focus) {
      galleryDetailLastFocus.focus();
    }
    galleryDetailLastFocus = null;
    galleryDetail.classList.add("hidden");
    galleryDetail.setAttribute("aria-hidden", "true");
    document.body.classList.remove("publish-modal-open");
    state.activeGallery = null;
    state.galleryItems = [];
  }

  async function loadGalleryDetail() {
    const gallery = state.activeGallery;
    if (!gallery) return;
    const requestId = ++galleryDetailRequestId;
    try {
      const [itemsData, shareData] = await Promise.all([
        api(
          `/api/private/galleries/${encodeURIComponent(gallery.gallery_id)}/items`,
        ),
        api(
          `/api/private/galleries/${encodeURIComponent(gallery.gallery_id)}/share`,
        ),
      ]);
      if (requestId !== galleryDetailRequestId || !state.activeGallery) return;
      state.galleryItems = Array.isArray(itemsData.items) ? itemsData.items : [];
      gallery.share_url = null;
      gallery.share_expires = null;
      if (shareData.share) {
        gallery.share_url = shareData.share.url;
        gallery.share_expires = shareData.share.expires_at;
      }
      renderGalleryItems();
      renderGalleryShare(gallery);
    } catch (error) {
      if (requestId !== galleryDetailRequestId) return;
      setMessage(galleryDetailMessage, error.message, true);
      galleryDetailGrid.replaceChildren();
    }
  }

  async function renameGallery() {
    const gallery = state.activeGallery;
    if (!gallery) return;
    const name = galleryRenameInput.value.trim();
    if (!name) {
      setMessage(galleryDetailMessage, "图库名称不能为空。", true);
      galleryRenameInput.focus();
      return;
    }
    try {
      const data = await api(
        `/api/private/galleries/${encodeURIComponent(gallery.gallery_id)}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name }),
        },
      );
      const updated = data.gallery || {};
      gallery.name = updated.name || name;
      galleryDetailTitle.textContent = gallery.name;
      const listed = galleryById(gallery.gallery_id);
      if (listed) {
        listed.name = gallery.name;
        renderGalleries();
      }
      setMessage(galleryDetailMessage, "图库名称已更新。");
    } catch (error) {
      setMessage(galleryDetailMessage, error.message, true);
    }
  }

  async function createGalleryShare() {
    const gallery = state.activeGallery;
    if (!gallery) return;
    const password = window.prompt("设置分享密码（8~32 位）", "");
    if (password === null) return;
    try {
      const data = await api(
        `/api/private/galleries/${encodeURIComponent(gallery.gallery_id)}/share`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password, valid_days: 7 }),
        },
      );
      gallery.share_url = data.share.url;
      gallery.share_expires = data.share.expires_at;
      const listed = galleryById(gallery.gallery_id);
      if (listed) {
        listed.share_url = gallery.share_url;
        listed.share_expires = gallery.share_expires;
        renderGalleries();
      }
      renderGalleryShare(gallery);
      setMessage(galleryDetailMessage, "分享已创建，有效期 7 天。");
    } catch (error) {
      setMessage(galleryDetailMessage, error.message, true);
    }
  }

  async function revokeGalleryShare() {
    const gallery = state.activeGallery;
    if (!gallery) return;
    if (!window.confirm("撤销后原分享链接立即失效，确认撤销？")) return;
    try {
      await api(
        `/api/private/galleries/${encodeURIComponent(gallery.gallery_id)}/share`,
        { method: "DELETE" },
      );
      gallery.share_url = null;
      gallery.share_expires = null;
      const listed = galleryById(gallery.gallery_id);
      if (listed) {
        listed.share_url = null;
        listed.share_expires = null;
        renderGalleries();
      }
      renderGalleryShare(gallery);
      setMessage(galleryDetailMessage, "分享已撤销。");
    } catch (error) {
      setMessage(galleryDetailMessage, error.message, true);
    }
  }

  async function deleteGalleryItem(item) {
    const gallery = state.activeGallery;
    if (!gallery) return;
    const confirmed = window.confirm(
      "删除这张私库副本？删除后无法恢复；只影响图库副本，不影响生成任务本身和原图。",
    );
    if (!confirmed) return;
    try {
      await api(
        `/api/private/galleries/${encodeURIComponent(gallery.gallery_id)}/items/${encodeURIComponent(item.item_id)}`,
        { method: "DELETE" },
      );
      setMessage(galleryDetailMessage, "已删除该图片。");
      await loadGalleryDetail();
    } catch (error) {
      setMessage(galleryDetailMessage, error.message, true);
    }
  }

  async function deleteActiveGallery() {
    const gallery = state.activeGallery;
    if (!gallery) return;
    const confirmed = window.confirm(
      `删除图库「${gallery.name}」？其中 ${gallery.item_count} 张私库副本会一并删除，无法恢复。只影响图库副本，不影响生成任务本身和原图。`,
    );
    if (!confirmed) return;
    try {
      await api(
        `/api/private/galleries/${encodeURIComponent(gallery.gallery_id)}`,
        { method: "DELETE" },
      );
      closeGalleryDetail();
      await loadGalleries();
      setMessage(document.querySelector("#gallery-message"), "图库已删除。");
    } catch (error) {
      setMessage(galleryDetailMessage, error.message, true);
    }
  }

  async function loadGalleries() {
    const message = document.querySelector("#gallery-message");
    try {
      const data = await api("/api/private/galleries");
      state.galleries = (data.items || []).map((item) => ({
        ...item,
        share_url: null,
        share_expires: null,
      }));
      await Promise.all(
        state.galleries.map(async (gallery) => {
          try {
            const share = await api(
              `/api/private/galleries/${gallery.gallery_id}/share`,
            );
            if (share.share) {
              gallery.share_url = share.share.url;
              gallery.share_expires = share.share.expires_at;
            }
          } catch (error) {
            gallery.share_url = null;
          }
        }),
      );
      if (state.activeGallery) {
        const active = galleryById(state.activeGallery.gallery_id);
        if (active) {
          state.activeGallery.name = active.name;
          state.activeGallery.item_count = active.item_count;
          state.activeGallery.item_bytes = active.item_bytes;
          state.activeGallery.share_url = active.share_url;
          state.activeGallery.share_expires = active.share_expires;
          galleryDetailTitle.textContent = active.name;
          galleryRenameInput.value = active.name;
          galleryDetailMeta.textContent =
            `${active.item_count} 张 · ${formatBytes(active.item_bytes)}`;
          renderGalleryShare(state.activeGallery);
        }
      }
      renderGalleries();
      renderAccountTokens();
      renderLocalTokens();
    } catch (error) {
      setMessage(message, error.message, true);
    }
  }

  async function loadAccountTokens(offset = state.accountTokenPage.offset) {
    const message = document.querySelector("#account-token-message");
    const page = state.accountTokenPage;
    const requested = Math.max(0, offset);
    const params = new URLSearchParams({
      limit: String(page.limit),
      offset: String(requested),
    });
    if (page.search) params.set("search", page.search);
    try {
      const data = await api(`/api/account/tokens?${params.toString()}`);
      const total = Number(data.total || 0);
      const maxOffset =
        total > 0 ? Math.floor((total - 1) / page.limit) * page.limit : 0;
      const clamped = Math.min(requested, maxOffset);
      if (clamped !== requested) return loadAccountTokens(clamped);
      page.offset = clamped;
      page.total = total;
      state.accountTokens = data.items || [];
      document.querySelector("#account-token-retention").textContent =
        `保留 ${data.retention_days || 7} 天`;
      renderAccountTokens();
    } catch (error) {
      if (error.status !== 401) {
        setMessage(message, error.message, true);
      }
    }
  }

  async function deleteAccountToken(entry) {
    const confirmed = window.confirm(
      "只删除这条取图密钥记录，不会删除生成任务、图片，也不影响已加入图库的副本。删除后无法在本账号记录中找回；若已自行保存密钥，仍可继续取图。确认删除？",
    );
    if (!confirmed) return;
    const message = document.querySelector("#account-token-message");
    try {
      await api(`/api/account/tokens/${encodeURIComponent(entry.job_id)}`, {
        method: "DELETE",
      });
      setMessage(message, "已删除该条取图记录。");
      await loadAccountTokens();
    } catch (error) {
      setMessage(message, error.message, true);
    }
  }

  function renderLedgerPager() {
    const page = state.ledgerPage;
    const totalPages = Math.max(1, Math.ceil(page.total / page.limit));
    const currentPage = Math.floor(page.offset / page.limit) + 1;
    document.querySelector("#ledger-page").textContent =
      `第 ${currentPage} / ${totalPages} 页 · 共 ${page.total} 条`;
    document.querySelector("#ledger-prev").disabled = currentPage <= 1;
    document.querySelector("#ledger-next").disabled = currentPage >= totalPages;
    document
      .querySelector("#ledger-pager")
      .classList.toggle("hidden", page.total <= page.limit);
  }

  async function loadLedger(offset = 0) {
    const message = document.querySelector("#ledger-message");
    const requested = Math.max(0, offset);
    try {
      const data = await api(
        `/api/account/ledger?limit=${state.ledgerPage.limit}&offset=${requested}`,
      );
      const clamped = Number(data.offset ?? requested);
      if (clamped !== requested) return loadLedger(clamped);
      state.ledgerPage.offset = clamped;
      state.ledgerPage.total = Number(data.total || 0);
      renderBalances(data.balances);
      const tbody = document.querySelector("#ledger-rows");
      tbody.replaceChildren();
      if (!data.items.length) {
        const row = el("tr");
        const cell = el("td", "account-note", "暂无流水。");
        cell.colSpan = 6;
        row.appendChild(cell);
        tbody.appendChild(row);
      }
      for (const entry of data.items) {
        const row = el("tr");
        row.appendChild(el("td", "", formatDate(entry.created_at)));
        row.appendChild(
          el("td", "", ENTRY_TYPE_LABELS[entry.entry_type] || entry.entry_type),
        );
        row.appendChild(el("td", "", BUCKET_LABELS[entry.bucket] || entry.bucket));
        row.appendChild(el("td", "", entry.delta > 0 ? `+${entry.delta}` : entry.delta));
        row.appendChild(el("td", "", entry.balance_after));
        row.appendChild(el("td", "", entry.note || "—"));
        tbody.appendChild(row);
      }
      renderLedgerPager();
      setMessage(message, "");
    } catch (error) {
      setMessage(message, error.message, true);
    }
  }

  async function loadAccount() {
    try {
      const data = await api("/api/account/me");
      document.querySelector("#account-name").textContent = data.account.username;
      document.querySelector("#account-meta").textContent =
        `状态 ${data.account.status} · 注册于 ${formatDate(data.account.created_at)}`;
      renderBalances(data.balances);
      showAccount();
      await Promise.all([loadLedger(), loadGalleries(), loadAccountTokens()]);
      return true;
    } catch (error) {
      if (error.status === 401) {
        showAuth();
        return false;
      }
      showAuth();
      setMessage(document.querySelector("#auth-message"), error.message, true);
      return false;
    }
  }

  document.querySelector("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = document.querySelector("#auth-message");
    setMessage(message, "正在登录…");
    try {
      await api("/api/account/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: document.querySelector("#login-username").value,
          password: document.querySelector("#login-password").value,
        }),
      });
      setMessage(message, "");
      await loadAccount();
    } catch (error) {
      setMessage(message, error.message, true);
    }
  });

  document
    .querySelector("#register-form")
    .addEventListener("submit", async (event) => {
      event.preventDefault();
      const message = document.querySelector("#auth-message");
      setMessage(message, "正在注册…");
      try {
        await api("/api/account/register", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            invite_code: document.querySelector("#register-invite").value,
            username: document.querySelector("#register-username").value,
            password: document.querySelector("#register-password").value,
          }),
        });
        setMessage(message, "");
        await loadAccount();
      } catch (error) {
        setMessage(message, error.message, true);
      }
    });

  logoutButton.addEventListener("click", async () => {
    try {
      await api("/api/account/logout", { method: "POST" });
    } catch (error) {
    }
    showAuth();
  });

  document
    .querySelector("#recharge-form")
    .addEventListener("submit", async (event) => {
      event.preventDefault();
      const message = document.querySelector("#recharge-message");
      const key = document.querySelector("#recharge-key").value.trim();
      setMessage(message, "正在兑换…");
      try {
        const data = await api("/api/account/recharge", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key }),
        });
        renderBalances(data.balances);
        document.querySelector("#recharge-key").value = "";
        setMessage(message, "兑换成功。");
        await loadLedger();
      } catch (error) {
        setMessage(message, error.message, true);
      }
    });

  document
    .querySelector("#gallery-form")
    .addEventListener("submit", async (event) => {
      event.preventDefault();
      const message = document.querySelector("#gallery-message");
      const name = document.querySelector("#gallery-name").value.trim();
      try {
        await api("/api/private/galleries", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name }),
        });
        document.querySelector("#gallery-name").value = "";
        setMessage(message, "图库已创建。");
        await loadGalleries();
      } catch (error) {
        setMessage(message, error.message, true);
      }
    });

  document.querySelector("#ledger-prev").addEventListener("click", () =>
    loadLedger(state.ledgerPage.offset - state.ledgerPage.limit),
  );
  document.querySelector("#ledger-next").addEventListener("click", () =>
    loadLedger(state.ledgerPage.offset + state.ledgerPage.limit),
  );

  document
    .querySelector("#account-token-search")
    .addEventListener("submit", (event) => {
      event.preventDefault();
      state.accountTokenPage.search = document
        .querySelector("#account-token-search-input")
        .value.trim();
      loadAccountTokens(0);
    });
  document
    .querySelector("#account-token-search-clear")
    .addEventListener("click", () => {
      document.querySelector("#account-token-search-input").value = "";
      state.accountTokenPage.search = "";
      loadAccountTokens(0);
    });
  document.querySelector("#account-token-prev").addEventListener("click", () =>
    loadAccountTokens(
      state.accountTokenPage.offset - state.accountTokenPage.limit,
    ),
  );
  document.querySelector("#account-token-next").addEventListener("click", () =>
    loadAccountTokens(
      state.accountTokenPage.offset + state.accountTokenPage.limit,
    ),
  );

  document
    .querySelector("#gallery-detail-close")
    .addEventListener("click", closeGalleryDetail);
  galleryDetail.addEventListener("click", (event) => {
    if (event.target.matches("[data-gallery-detail-close]")) closeGalleryDetail();
  });
  document.querySelector("#gallery-rename").addEventListener("click", renameGallery);
  galleryRenameInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      renameGallery();
    }
  });
  galleryShareCreate.addEventListener("click", createGalleryShare);
  galleryShareCopy.addEventListener("click", async () => {
    const gallery = state.activeGallery;
    if (!gallery || !gallery.share_url) return;
    await copyText(galleryShareLink(gallery.share_url));
    setMessage(galleryDetailMessage, "分享链接已复制。");
  });
  galleryShareRevoke.addEventListener("click", revokeGalleryShare);
  document.querySelector("#gallery-delete").addEventListener("click", deleteActiveGallery);

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (document.querySelector(".share-viewer")) return;
    closeGalleryDetail();
  });

  loadAccount();
})();
