const form = document.querySelector("#admin-login-form");
const message = document.querySelector("#admin-login-message");
const usernameInput = document.querySelector("#admin-username");
const passwordInput = document.querySelector("#admin-password");

function setMessage(text, isError = false) {
  message.textContent = text || "";
  message.classList.toggle("error", isError);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = form.querySelector("button[type=submit]");
  button.disabled = true;
  setMessage("正在验证身份…");
  try {
    const response = await fetch("/api/admin/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: usernameInput.value.trim(),
        password: passwordInput.value,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "登录失败");
    setMessage("登录成功，正在进入管理台…");
    window.location.replace("/admin");
  } catch (error) {
    setMessage(error.message, true);
    button.disabled = false;
    passwordInput.value = "";
    passwordInput.focus();
  }
});

usernameInput.focus();
