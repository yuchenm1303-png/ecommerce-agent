const config = window.DOWNLOAD_PORTAL_CONFIG ?? {};
const release = config.release ?? {};
const authConfig = config.auth ?? {};

const $ = (id) => document.getElementById(id);
const versionNumber = $("versionNumber");
const publishedAt = $("publishedAt");
const platformText = $("platformText");
const fileSizeText = $("fileSizeText");
const releaseNotes = $("releaseNotes");
const loginForm = $("loginForm");
const loginMessage = $("loginMessage");
const emailInput = $("emailInput");
const passwordInput = $("passwordInput");
const loggedOutState = $("loggedOutState");
const loggedInState = $("loggedInState");
const accountEmail = $("accountEmail");
const logoutButton = $("logoutButton");
const downloadButton = $("downloadButton");
const downloadButtonHint = $("downloadButtonHint");
const toast = $("toast");

let supabase = null;
let session = null;
let toastTimer = null;

function applyConfig() {
  versionNumber.textContent = release.version || "—";
  publishedAt.textContent = `${release.publishedAt || "待发布"} 发布`;
  platformText.textContent = release.platform || "Windows x64";
  fileSizeText.textContent = release.fileSize ? `安装包 ${release.fileSize}` : "安装包大小待发布";

  releaseNotes.replaceChildren();
  for (const note of release.notes ?? []) {
    const li = document.createElement("li");
    li.textContent = note;
    releaseNotes.appendChild(li);
  }
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 2400);
}

function setSession(nextSession) {
  session = nextSession ?? null;
  const user = session?.user;
  const signedIn = Boolean(user);

  loggedOutState.hidden = signedIn;
  loggedInState.hidden = !signedIn;
  accountEmail.textContent = user?.email || "已授权用户";

  downloadButton.disabled = !signedIn;
  downloadButtonHint.textContent = signedIn ? (release.version || "最新版") : "请先登录";

  if (!signedIn && supabase) {
    loginMessage.textContent = "登录后即可获取最新版安装包。";
  }
}

async function initAuth() {
  if (!authConfig.supabaseUrl || !authConfig.supabaseAnonKey) {
    loginMessage.textContent = "页面预览已就绪；登录服务等待 Supabase 配置。";
    setSession(null);
    return;
  }

  try {
    const { createClient } = await import("https://esm.sh/@supabase/supabase-js@2");
    supabase = createClient(authConfig.supabaseUrl, authConfig.supabaseAnonKey, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true
      }
    });

    const { data, error } = await supabase.auth.getSession();
    if (error) throw error;
    setSession(data.session);

    supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession);
    });
  } catch (error) {
    console.error(error);
    loginMessage.textContent = "登录服务初始化失败，请检查站点配置。";
    showToast("登录服务初始化失败");
  }
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  if (!supabase) {
    showToast("登录服务尚未配置");
    return;
  }

  const submitButton = loginForm.querySelector("button[type='submit']");
  submitButton.disabled = true;
  submitButton.textContent = "验证中…";
  loginMessage.textContent = "正在验证账户…";

  try {
    const { data, error } = await supabase.auth.signInWithPassword({
      email: emailInput.value.trim(),
      password: passwordInput.value
    });
    if (error) throw error;

    setSession(data.session);
    passwordInput.value = "";
    showToast("登录成功");
  } catch (error) {
    console.error(error);
    loginMessage.textContent = "邮箱或密码错误，请重试。";
    showToast("登录失败");
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "登录账户";
  }
});

logoutButton.addEventListener("click", async () => {
  if (supabase) await supabase.auth.signOut();
  setSession(null);
  showToast("已退出登录");
});

downloadButton.addEventListener("click", async () => {
  if (!session) {
    showToast("请先登录");
    return;
  }

  downloadButton.disabled = true;
  const originalHint = downloadButtonHint.textContent;
  downloadButtonHint.textContent = "正在生成安全链接";

  try {
    let url = release.downloadUrl || "";

    if (authConfig.downloadFunctionUrl) {
      const response = await fetch(authConfig.downloadFunctionUrl, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${session.access_token}`,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ platform: "windows-x64", version: release.version })
      });

      if (!response.ok) throw new Error(`download function returned ${response.status}`);
      const payload = await response.json();
      url = payload.url || "";
    }

    if (!url) {
      showToast("最新版安装包尚未发布");
      return;
    }

    window.location.assign(url);
  } catch (error) {
    console.error(error);
    showToast("暂时无法生成下载链接");
  } finally {
    downloadButton.disabled = !session;
    downloadButtonHint.textContent = originalHint;
  }
});

applyConfig();
await initAuth();
