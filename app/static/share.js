(function () {
  const root = document.querySelector("#share-app");
  if (!root) return;
  const token = root.dataset.token || "";

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? value
      : date.toLocaleString("zh-CN", { hour12: false });
  }

  function shareFileUrl(kind, itemId) {
    return (
      "/share/" +
      encodeURIComponent(token) +
      "/" +
      kind +
      "/" +
      encodeURIComponent(itemId)
    );
  }

  function showMessage(message) {
    root.innerHTML = "";
    root.appendChild(el("p", "share-message", message));
  }

  function appendInfo(container, meta) {
    container.appendChild(
      el(
        "p",
        "share-owner",
        meta.owner_username
          ? `由 ${meta.owner_username} 分享`
          : "由图库主人分享",
      ),
    );
    const expiry = meta.expires_at
      ? `分享有效期至 ${formatDate(meta.expires_at)}`
      : "分享有效期未设置";
    container.appendChild(
      el(
        "p",
        "share-note",
        `只读展示，不提供原图，也不能二次分享；${expiry}。`,
      ),
    );
  }

  async function loadItems(meta) {
    const response = await fetch(
      "/api/share/" + encodeURIComponent(token) + "/items"
    );
    if (response.status === 403) {
      renderPasswordForm(meta);
      return;
    }
    if (!response.ok) {
      showMessage("分享不存在或已失效");
      return;
    }
    renderGallery(await response.json());
  }

  function renderGallery(data) {
    root.innerHTML = "";
    root.appendChild(el("h2", "share-title", data.gallery_name || "私人图库"));
    appendInfo(root, data);
    if (!data.items.length) {
      root.appendChild(
        el("p", "share-message", "图库中还没有可查看的图片。"),
      );
      return;
    }
    const grid = el("div", "share-grid");
    for (const [index, item] of data.items.entries()) {
      const button = el("button", "share-card");
      button.type = "button";
      button.setAttribute("aria-label", `查看大图：第 ${index + 1} 张`);
      const image = document.createElement("img");
      image.loading = "lazy";
      image.alt = "私库图片";
      image.src = shareFileUrl("thumbnail", item.item_id);
      image.addEventListener("error", () => {
        if (image.dataset.fallback === "full") {
          image.remove();
          button.classList.add("share-card-missing");
          button.appendChild(
            el("span", "share-card-placeholder", "图片暂时无法显示"),
          );
          return;
        }
        image.dataset.fallback = "full";
        image.src = shareFileUrl("image", item.item_id);
      });
      button.appendChild(image);
      button.addEventListener("click", () => {
        if (button.classList.contains("share-card-missing")) return;
        openViewer(data, index, button);
      });
      grid.appendChild(button);
    }
    root.appendChild(grid);
  }

  function openViewer(data, index, trigger) {
    const item = data.items[index];
    const viewer = el("div", "share-viewer");
    viewer.setAttribute("role", "dialog");
    viewer.setAttribute("aria-modal", "true");
    viewer.setAttribute("aria-label", "查看大图");
    const backdrop = el("div", "share-viewer-backdrop");
    const card = el("div", "share-viewer-card");
    const close = el("button", "secondary-button share-viewer-close", "关闭");
    close.type = "button";
    const image = document.createElement("img");
    image.alt = "私库图片";
    image.src = shareFileUrl("image", item.item_id);
    card.appendChild(close);
    card.appendChild(image);
    viewer.appendChild(backdrop);
    viewer.appendChild(card);
    document.body.appendChild(viewer);
    document.body.classList.add("share-viewer-open");
    close.focus();

    function onKeydown(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeViewer();
      }
    }

    function closeViewer() {
      viewer.remove();
      document.body.classList.remove("share-viewer-open");
      document.removeEventListener("keydown", onKeydown);
      trigger.focus();
    }

    backdrop.addEventListener("click", closeViewer);
    close.addEventListener("click", closeViewer);
    document.addEventListener("keydown", onKeydown);
  }

  function renderPasswordForm(meta) {
    root.innerHTML = "";
    root.appendChild(
      el("h2", "share-title", meta.gallery_name || "私人图库")
    );
    appendInfo(root, meta);
    root.appendChild(el("h3", "share-lock-title", "需要访问密码"));
    const form = el("form", "share-form");
    const input = document.createElement("input");
    input.type = "password";
    input.name = "password";
    input.id = "share-password";
    input.required = true;
    input.minLength = 8;
    input.maxLength = 32;
    input.autocomplete = "new-password";
    input.placeholder = "请输入分享密码";
    input.setAttribute("aria-label", "分享密码");
    const button = document.createElement("button");
    button.type = "submit";
    button.className = "primary-button share-submit";
    button.textContent = "查看";
    const error = el("p", "message error hidden");
    form.appendChild(input);
    form.appendChild(button);
    root.appendChild(form);
    root.appendChild(error);

    function setError(message) {
      error.textContent = message;
      error.classList.toggle("hidden", !message);
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      setError("");
      button.disabled = true;
      input.disabled = true;
      const response = await fetch(
        "/api/share/" + encodeURIComponent(token) + "/unlock",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password: input.value }),
        }
      );
      button.disabled = false;
      input.disabled = false;
      if (response.status === 403) {
        setError("密码错误，请重试。");
        input.value = "";
        input.focus();
        return;
      }
      if (response.status === 429) {
        setError("尝试次数过多，请等待约 15 分钟后再试。");
        input.value = "";
        input.focus();
        return;
      }
      if (!response.ok) {
        setError("分享不存在或已失效。");
        return;
      }
      await loadItems(meta);
    });
  }

  async function loadShare() {
    const response = await fetch(
      "/api/share/" + encodeURIComponent(token)
    );
    if (!response.ok) {
      showMessage("分享不存在或已失效");
      return;
    }
    const meta = await response.json();
    if (meta.unlocked) {
      await loadItems(meta);
    } else {
      renderPasswordForm(meta);
    }
  }

  loadShare();
})();
