window.DOWNLOAD_PORTAL_CONFIG = {
  brand: {
    name: "Listing Studio",
    domain: "download portal"
  },

  // Put a licensed wallpaper under download-site/assets/ and set it here, e.g.
  // "./assets/wallpaper.jpg". Empty uses the built-in gradient preview.
  wallpaperUrl: "",

  release: {
    version: "v0.1.0",
    publishedAt: "2026.08.13",
    platform: "Windows 10 / 11 · x64",
    fileSize: "待发布",

    // Development fallback only. For production prefer downloadFunctionUrl below
    // so the real storage URL is signed only after authentication.
    downloadUrl: "",

    notes: [
      "Single 固定工作区与实时任务日志",
      "Batch 多商品任务队列",
      "独立 Makro Browser 会话管理",
      "Windows 安装包与便携版构建"
    ]
  },

  auth: {
    // Production recommendation: Supabase email/password auth + Edge Function
    // returning a short-lived signed installer URL.
    supabaseUrl: "",
    supabaseAnonKey: "",
    downloadFunctionUrl: ""
  }
};
