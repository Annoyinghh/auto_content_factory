const pages = new Set([
  "https://creator.douyin.com/creator-micro/content/upload",
  "https://member.bilibili.com/platform/upload/video/frame",
]);
const results = new Set(["ready", "partial", "no_form", "transfer_error", "needs_check"]);

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.frameId !== 0 || !sender.url || !message || !Number.isInteger(message.port) ||
      message.port < 1 || message.port > 65535 || !/^[0-9a-f]{64}$/.test(message.token)) return;
  const page = new URL(sender.url);
  if (!pages.has(page.origin + page.pathname.replace(/\/$/, ""))) return;
  const { action, status, port, token } = message;
  if (action !== "job" && action !== "video" && !(action === "result" && results.has(status))) return;
  const path = action === "result" ? `result/${status}` : action;
  (async () => {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/${path}`, {
        method: action === "result" ? "POST" : "GET",
        headers: { "X-ACF-Token": token },
        cache: "no-store",
        credentials: "omit",
        redirect: "error",
        signal: AbortSignal.timeout(25000),
      });
      if (!response.ok) throw new Error("handoff unavailable");
      if (action === "job") respond({ job: await response.json() });
      else if (action === "video") respond({ blob: await response.blob() });
      else respond({ ok: true });
    } catch {
      respond({ unavailable: true });
    }
  })();
  return true;
});
