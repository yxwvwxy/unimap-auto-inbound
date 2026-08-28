// ==UserScript==
// @name         UniMap 一键入库（SI）
// @namespace    unimap-auto-inbound
// @version      1.0.0
// @description  在 us.si.uniuni.com 选单号，发给本机 Playwright 入库。UniMap 点击仍在本机。
// @match        https://us.si.uniuni.com/*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// @connect      localhost
// ==/UserScript==

(function () {
  "use strict";

  const API = "http://127.0.0.1:18787";
  const ORDER_RE = /\bUUS[A-Z0-9]{8,}\b/gi;

  function uniq(list) {
    const seen = new Set();
    const out = [];
    for (const item of list) {
      const value = String(item || "").trim().toUpperCase();
      if (!value || seen.has(value)) continue;
      seen.add(value);
      out.push(value);
    }
    return out;
  }

  function extractOrders(text) {
    const matches = String(text || "").match(ORDER_RE) || [];
    return uniq(matches);
  }

  function collectFromPage() {
    const fromSelection = extractOrders(String(window.getSelection() || ""));
    if (fromSelection.length) return fromSelection;

    const checkedRows = [];
    document.querySelectorAll("tr").forEach((tr) => {
      const checkbox = tr.querySelector("input[type=checkbox], input[type=radio]");
      if (checkbox && checkbox.checked) {
        checkedRows.push(...extractOrders(tr.innerText || ""));
      }
    });
    if (checkedRows.length) return uniq(checkedRows);

    return extractOrders(document.body ? document.body.innerText : "");
  }

  function gmRequest(method, path, body) {
    return new Promise((resolve, reject) => {
      const send = typeof GM_xmlhttpRequest === "function" ? GM_xmlhttpRequest : null;
      if (!send) {
        reject(new Error("请用 Tampermonkey / Violentmonkey 安装此脚本"));
        return;
      }
      send({
        method,
        url: API + path,
        headers: { "Content-Type": "application/json" },
        data: body ? JSON.stringify(body) : undefined,
        onload: (res) => {
          try {
            resolve(JSON.parse(res.responseText || "{}"));
          } catch (err) {
            reject(err);
          }
        },
        onerror: () => reject(new Error("连不上本机。请先双击「一键入库-启动」并保持窗口开着。")),
      });
    });
  }

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    Object.assign(node, attrs || {});
    for (const child of children || []) node.appendChild(child);
    return node;
  }

  const panel = el("div", {
    id: "unimap-inbound-panel",
  });
  Object.assign(panel.style, {
    position: "fixed",
    right: "16px",
    bottom: "16px",
    zIndex: "2147483647",
    width: "320px",
    background: "#111827",
    color: "#f9fafb",
    border: "1px solid #374151",
    borderRadius: "12px",
    boxShadow: "0 12px 40px rgba(0,0,0,.35)",
    fontFamily: "ui-sans-serif, system-ui, sans-serif",
    fontSize: "13px",
    padding: "12px",
  });

  const title = el("div", { textContent: "一键入库（本机）" });
  title.style.fontWeight = "700";
  title.style.marginBottom = "8px";

  const status = el("div", { textContent: "正在检测本机监听…" });
  status.style.color = "#9ca3af";
  status.style.marginBottom = "8px";

  const area = el("textarea");
  Object.assign(area, {
    rows: 6,
    placeholder: "单号，每行一个。可先在表格里选中，再点「抓取页面单号」。",
  });
  Object.assign(area.style, {
    width: "100%",
    boxSizing: "border-box",
    borderRadius: "8px",
    border: "1px solid #4b5563",
    background: "#030712",
    color: "#f9fafb",
    padding: "8px",
    marginBottom: "8px",
    resize: "vertical",
  });

  function btn(label) {
    const node = el("button", { type: "button", textContent: label });
    Object.assign(node.style, {
      border: 0,
      borderRadius: "8px",
      padding: "7px 10px",
      cursor: "pointer",
      background: "#f97316",
      color: "#111827",
      fontWeight: "700",
      marginRight: "6px",
      marginBottom: "6px",
    });
    return node;
  }

  const grabBtn = btn("抓取页面单号");
  const startBtn = btn("开始入库");
  const stopBtn = btn("停止");
  stopBtn.style.background = "#6b7280";
  stopBtn.style.color = "#fff";

  panel.append(title, status, area, grabBtn, startBtn, stopBtn);
  document.documentElement.appendChild(panel);

  function setStatus(text, ok) {
    status.textContent = text;
    status.style.color = ok === false ? "#fca5a5" : ok ? "#86efac" : "#9ca3af";
  }

  grabBtn.addEventListener("click", () => {
    const orders = collectFromPage();
    area.value = orders.join("\n");
    setStatus(orders.length ? `已抓取 ${orders.length} 单` : "没找到 UUS 单号。请先选中表格行，或手动粘贴。", orders.length > 0);
  });

  startBtn.addEventListener("click", async () => {
    const orders = area.value
      .split(/[\s,;]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (!orders.length) {
      setStatus("请先抓取或粘贴单号", false);
      return;
    }
    try {
      const res = await gmRequest("POST", "/queue", { orders, force: true });
      if (!res.ok) {
        setStatus(res.error || "排队失败", false);
        return;
      }
      setStatus(`已发给本机 ${res.count} 单，UniMap 开始点页面`, true);
    } catch (err) {
      setStatus(err.message || String(err), false);
    }
  });

  stopBtn.addEventListener("click", async () => {
    try {
      const res = await gmRequest("POST", "/stop", {});
      setStatus(res.ok ? `已请求停止（取消 ${res.cancelled || 0} 单）` : res.error || "停止失败", !!res.ok);
    } catch (err) {
      setStatus(err.message || String(err), false);
    }
  });

  async function ping() {
    try {
      const res = await gmRequest("GET", "/status");
      const pending = (res.counts && res.counts.pending) || 0;
      const running = (res.counts && res.counts.running) || 0;
      setStatus(`本机已连接 · ${res.flag || "idle"} · pending ${pending} / running ${running}`, true);
    } catch (err) {
      setStatus("本机未开监听。请双击「一键入库-启动」。", false);
    }
  }

  ping();
  setInterval(ping, 4000);
})();
