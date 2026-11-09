(() => {
  const ticket = /^#acf-post=(\d{1,5})\.([a-f0-9]{64})$/.exec(location.hash);
  const bili = location.origin === "https://member.bilibili.com" && /^\/platform\/upload\/video\/frame\/?$/.test(location.pathname);
  const douyin = location.origin === "https://creator.douyin.com" && /^\/creator-micro\/content\/upload\/?$/.test(location.pathname);
  if (!ticket || !(bili || douyin)) return;
  history.replaceState(history.state, "", location.pathname + location.search);

  const send = (action, status) => chrome.runtime.sendMessage({ action, status, port: Number(ticket[1]), token: ticket[2] });
  const notify = (message) => {
    const note = document.createElement("div");
    note.setAttribute("role", "status");
    note.textContent = `内容工厂：${message}`;
    note.style.cssText = "position:fixed;bottom:18px;left:18px;z-index:2147483647;padding:12px 16px;background:#24463a;color:white;border-radius:6px;font:14px system-ui;pointer-events:none;max-width:420px";
    document.body.append(note);
    setTimeout(() => note.remove(), 16000);
  };
  const finish = async (status, message) => {
    notify(message);
    try { await send("result", status); } catch { /* Desktop times out with a check prompt. */ }
  };

  const visible = (node) => node && node.getClientRects().length > 0 && getComputedStyle(node).visibility === "visible";
  const editors = 'input:not([type]),input[type="text"],textarea,[contenteditable="true"],[contenteditable="plaintext-only"]';
  const titleName = /^(?:(?:请输入|添加|填写)\s*)?(?:(?:作品|视频|稿件|短)\s*)?标题(?:[\s（(].*|[：:]|\.{3}|…)?$/;
  const bodyName = /^(?:(?:请输入|请填写|填写|添加)\s*)?(?:(?:作品|视频|稿件)\s*)?(?:简介|描述|文案)(?:[\s，,（(].*|[：:]|\.{3}|…)?$/;
  const names = (field) => [
    field.getAttribute("placeholder"), field.getAttribute("aria-label"), field.getAttribute("data-placeholder"),
    ...Array.from(field.labels || [], (label) => label.textContent),
    ...(field.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean).map(id => document.getElementById(id)?.textContent),
    ...Array.from(field.querySelectorAll("[data-placeholder]"), child => child.getAttribute("data-placeholder")),
  ];

  function findField(pattern, description = false) {
    const match = (text) => pattern.test((text || "").trim().replace(/^[*＊]\s*/, ""));
    const fields = [...document.querySelectorAll(editors)].filter(node => visible(node) && !node.disabled && !node.readOnly &&
      (!description || node.tagName !== "INPUT"));
    const direct = fields.filter(node => names(node).some(match));
    if (direct.length) return direct.length === 1 ? direct[0] : null;
    const nearby = fields.filter(node => {
      for (let wrap = node.parentElement, i = 0; wrap && i < 4; wrap = wrap.parentElement, i++) {
        if (wrap.querySelectorAll(editors).length !== 1) break;
        if ([...wrap.querySelectorAll("label,span,div")].some(label => !label.contains(node) &&
            !node.contains(label) && visible(label) && match(label.textContent))) return true;
      }
      return false;
    });
    return nearby.length === 1 ? nearby[0] : null;
  }

  function uploadField() {
    if (findField(titleName) || findField(bodyName, true)) return null;
    const selector = bili ? '.upload-wrp .bcc-upload-wrapper input[type="file"]' : 'input[type="file"]';
    const candidates = [...document.querySelectorAll(selector)].filter(input => {
      if (input.disabled || input.files.length) return false;
      if (/(?:video\/|\.mp4\b|\.mov\b)/i.test(input.accept)) return true;
      return !bili && !input.accept && /上传视频/.test(input.parentElement?.innerText || "");
    });
    return candidates.length === 1 ? candidates[0] : null;
  }

  const waitFor = async (read, timeout) => {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const value = read();
      if (value) return value;
      await new Promise(resolve => setTimeout(resolve, 200));
    }
    return null;
  };

  function fill(node, value, replace = []) {
    if (!node || !value) return false;
    const current = () => (node.isContentEditable ? node.innerText : node.value).trim();
    if (current() === value) return true;
    if (current() && !replace.includes(current())) return false;
    if (node.isContentEditable) {
      node.focus();
      const range = document.createRange();
      range.selectNodeContents(node);
      const selection = getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      if (node.contentEditable === "plaintext-only") {
        document.execCommand("insertText", false, value);
      } else {
        const markup = document.createElement("div");
        value.split("\n").forEach((line, index) => {
          if (index) markup.append(document.createElement("br"));
          markup.append(document.createTextNode(line));
        });
        document.execCommand("insertHTML", false, markup.innerHTML);
      }
      selection.removeAllRanges();
    } else {
      if (node.maxLength >= 0 && value.length > node.maxLength) return false;
      const proto = node.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, "value").set.call(node, value);
      node.dispatchEvent(new Event("input", { bubbles: true }));
      node.dispatchEvent(new Event("change", { bubbles: true }));
    }
    return current() === value;
  }

  let dispatched = false;
  (async () => {
    const { job } = await send("job") || {};
    if (!job) return notify("无法连接桌面程序，请从 EXE 重新发起。");
    const input = await waitFor(uploadField, 14000);
    if (!input) return finish("no_form", "没找到空白的视频上传区。请先登录，或在当前页面手动处理。");
    const { blob } = await send("video") || {};
    if (!(blob instanceof Blob) || !blob.size) return finish("transfer_error", "未能接收视频。请更新浏览器或改用手动投稿。");
    if (uploadField() !== input) return finish("no_form", "上传区已改变，请检查当前页面。");
    const files = new DataTransfer();
    files.items.add(new File([blob], job.filename, { type: "video/mp4" }));
    input.files = files.files;
    dispatched = true;
    input.dispatchEvent(new Event("change", { bubbles: true }));

    let title, body;
    await waitFor(() => {
      title = findField(titleName);
      body = findField(bodyName, true);
      return title && body;
    }, 45000);
    const titleFilled = fill(title, job.title, [job.filename, job.filename.replace(/\.mp4$/i, "")]);
    const bodyFilled = fill(body, job.body);
    if (titleFilled && bodyFilled) {
      return finish("ready", "标题和文案已填写。请检查视频上传进度，审稿后自行发布。");
    }
    return finish("partial", "视频已交给投稿页。请补填未完成的标题或文案，并检查上传进度。");
  })().catch(() => finish(dispatched ? "needs_check" : "transfer_error", dispatched
    ? "请检查当前投稿页，确认上传和填写情况。" : "无法准备投稿，请从 EXE 重试或手动处理。"));
})();
