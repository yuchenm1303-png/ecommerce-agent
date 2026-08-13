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

  if (config.wallpaperUrl) {
    document.documentElement.style.setProperty(
      "--wallpaper-image",
      `url(${JSON.stringify(config.wallpaperUrl).slice(1, -1)})`
    );
  }
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 2600);
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
    loginMessage.textContent = "视觉预览已就绪；登录服务等待 Supabase 配置。";
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
  submitButton.textContent = "登录中…";
  loginMessage.textContent = "正在验证账号…";

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
    submitButton.textContent = "登录";
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
  downloadButtonHint.textContent = "正在生成链接";

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

function installPetals() {
  const host = $("petals");
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduced) return;

  const fragment = document.createDocumentFragment();
  const count = Math.min(18, Math.max(10, Math.floor(window.innerWidth / 95)));

  for (let index = 0; index < count; index += 1) {
    const petal = document.createElement("i");
    petal.className = "petal";
    petal.style.setProperty("--size", `${8 + Math.random() * 8}px`);
    petal.style.setProperty("--x", `${Math.random() * 100}vw`);
    petal.style.setProperty("--drift", `${-14 + Math.random() * 28}vw`);
    petal.style.setProperty("--duration", `${10 + Math.random() * 10}s`);
    petal.style.setProperty("--delay", `${-Math.random() * 16}s`);
    petal.style.setProperty("--opacity", `${0.42 + Math.random() * 0.45}`);
    fragment.appendChild(petal);
  }

  host.replaceChildren(fragment);
}

function installCursor() {
  if (!window.matchMedia("(pointer: fine)").matches) return;

  const cursor = $("cursor");
  let targetX = -40;
  let targetY = -40;
  let currentX = -40;
  let currentY = -40;
  let frame = 0;

  const tick = () => {
    currentX += (targetX - currentX) * 0.34;
    currentY += (targetY - currentY) * 0.34;
    cursor.style.transform = `translate3d(${currentX - 9}px, ${currentY - 9}px, 0)`;
    if (Math.abs(targetX - currentX) > 0.1 || Math.abs(targetY - currentY) > 0.1) {
      frame = requestAnimationFrame(tick);
    } else {
      frame = 0;
    }
  };

  window.addEventListener("pointermove", (event) => {
    targetX = event.clientX;
    targetY = event.clientY;
    if (!frame) frame = requestAnimationFrame(tick);
  }, { passive: true });

  document.addEventListener("pointerdown", () => cursor.classList.add("active"), { passive: true });
  document.addEventListener("pointerup", () => cursor.classList.remove("active"), { passive: true });
}

applyConfig();
installPetals();
installCursor();
await initAuth();
