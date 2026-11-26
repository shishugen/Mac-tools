const TAURI = window.__TAURI__;
const invoke = TAURI && TAURI.core ? TAURI.core.invoke : null;
const LIVE = !!invoke;
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const LABEL = { S: "可重建", A: "含数据", B: "需管理员", C: "禁止" };
const LEVEL_TEXT = { S: "可重建", A: "含数据", B: "需管理员", C: "禁止" };
const LEVELS = ["S", "A", "B"];
const DEMO = true;

document.body.classList.toggle("tauri", LIVE);

const state = {
  items: [],
  summary: {},
  apps: [],
  appSort: { key: "name", dir: 1 },
  usageCovered: 0,
  plan: null,
  selected: new Set(),
  scannedAt: null,
  cancelling: false,
  busy: false,
};

const DEMO_ITEMS = [
  ["S", "pip 下载缓存", "~/Library/Caches/pip", 2684354560, "2026-11-25 18:40", "pip 下载的安装包缓存，重建时重新下载。"],
  ["S", "uv 缓存", "~/.cache/uv", 2469606195, "2026-11-25 19:02", "Python 包管理器缓存，删后下次安装重新下载。"],
  ["S", "WorkBuddy 缓存", "~/Library/Caches/com.workbuddy.workbuddy.Builder", 2362232013, "2026-11-25 20:18", "应用缓存。若正在使用该应用，建议先退出。"],
  ["S", "Google 浏览器缓存", "~/Library/Caches/Google", 2147483648, "2026-11-24 14:11", "网页资源缓存，不含 Cookie、历史与密码。"],
  ["S", "紫鸟浏览器缓存", "~/Library/Caches/ziniaobrowser", 1503238554, "2026-11-23 09:30", "网页资源缓存，不影响登录状态。"],
  ["S", "Trae CN 缓存", "~/Library/Caches/Trae CN", 1052770304, "2026-11-25 17:05", "编辑器缓存，下次打开自动重建。"],
  ["S", "puppeteer 浏览器缓存", "~/.cache/puppeteer", 1015021568, "2026-11-19 11:22", "自动化测试用的浏览器二进制。"],
  ["S", "Homebrew 缓存", "~/Library/Caches/Homebrew", 174063616, "2026-11-21 10:04", "安装包下载缓存。"],
  ["A", "Xcode 归档包", "~/Library/Developer/Xcode/Archives", 901775360, "2026-10-30 16:20", "用于上架与崩溃符号还原，删除后无法重新生成。"],
  ["A", "iPhone 本地备份", "~/Library/Application Support/MobileSync/Backup", 650117120, "2026-09-12 20:15", "设备备份，删除后无法从本机恢复。"],
  ["B", "系统级缓存", "/Library/Caches/Desktop Pictures", 10800332, "2026-11-20 08:00", "所有用户共享的缓存，需要管理员权限。"],
];

function bytes(n) {
  if (!n && n !== 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return (i === 0 ? Math.round(v) : v.toFixed(1)) + " " + units[i];
}

function shortTime(iso) {
  if (!iso) return "—";
  return iso.replace("T", " ").slice(0, 16);
}

function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("on");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove("on"), 3000);
}

// Tauri 的 WKWebView 首次点击可能只发 click（用于激活窗口），
// 所以 click 必须无条件响应；pointerdown/mousedown 让后续点击更快。
// 三种事件都触发 run，靠 350ms 去重保证一次点击只执行一次。
function onTap(el, fn) {
  if (!el) return;
  let last = 0;
  const run = (e) => {
    const now = Date.now();
    if (now - last < 350) return;
    last = now;
    fn(e);
  };
  el.addEventListener("pointerdown", run);
  el.addEventListener("mousedown", run);
  el.addEventListener("click", run);
}

function go(name) {
  $$(".nav").forEach((n) => n.classList.toggle("on", n.dataset.go === name));
  $$(".view").forEach((v) => v.classList.toggle("on", v.id === "v-" + name));
  try {
    if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
  } catch (_) {
    // Tauri 里 hash 可能被 WebView 锁住，切页不依赖它。
  }
}

$$("[data-go]").forEach((el) =>
  onTap(el, (e) => { e.stopPropagation(); go(el.dataset.go); })
);

function levelTotals() {
  const out = {};
  state.items.forEach((i) => {
    const row = out[i.level] || (out[i.level] = { count: 0, bytes: 0 });
    row.count++; row.bytes += i.size;
  });
  return out;
}

function renderSummary() {
  const totals = levelTotals();
  const safeBytes = (totals.S || {}).bytes || 0;
  const allBytes = Object.values(totals).reduce((a, b) => a + b.bytes, 0);
  $("#mTotal").textContent = bytes(allBytes);
  $("#mSafe").textContent = bytes(safeBytes);
  const badge = $("#badgeClean");
  if (badge) badge.textContent = state.items.length;
  const sub = $("#scanHint");
  if (sub) sub.textContent = state.scannedAt
    ? `上次扫描 ${shortTime(state.scannedAt)} · 只读，未改动任何文件`
    : "还没有扫描结果";
  const setSeg = (lv, label, count) => {
    const el = $(`#seg [data-lv='${lv}']`);
    if (el) el.textContent = `${label} ${count}`;
  };
  setSeg("all", "全部", state.items.length);
  setSeg("S", LABEL.S, (totals.S || {}).count || 0);
  setSeg("A", LABEL.A, (totals.A || {}).count || 0);
  setSeg("B", LABEL.B, (totals.B || {}).count || 0);

  const top = $("#topList");
  if (top) {
    top.innerHTML = state.items.slice(0, 6).map((i) =>
      `<li><span class="chip ${i.level}">${LEVEL_TEXT[i.level] || i.level}</span><span class="nm">${i.name}</span><span class="sz">${bytes(i.size)}</span></li>`
    ).join("") || '<li class="empty">暂无数据</li>';
  }
}

function renderDisk(disk) {
  if (!disk) return;
  const total = disk.total || 1;
  const pct = (n) => Math.max(0, Math.min(100, (n / total) * 100)).toFixed(2) + "%";
  const bar = $("#diskBar");
  if (!bar) return;
  bar.innerHTML =
    `<i style="width:${pct(disk.used - disk.reclaimable)};background:#8e8e93"></i>` +
    `<i style="width:${pct(disk.reclaimable)};background:#e8a33d"></i>` +
    `<i style="width:${pct(disk.free)};background:#ededf0"></i>`;
  $("#diskLegend").innerHTML =
    `<span><b style="background:#8e8e93"></b>已用 ${bytes(disk.used - disk.reclaimable)}</span>` +
    `<span><b style="background:#e8a33d"></b>可清理 ${bytes(disk.reclaimable)}</span>` +
    `<span><b style="background:#ededf0"></b>可用 ${bytes(disk.free)}</span>`;
  $("#diskTotal").textContent = bytes(disk.total) + " 总容量";
  if ($("#sideDiskTitle")) $("#sideDiskTitle").textContent = "Macintosh HD · " + bytes(disk.total);
  if ($("#sideDiskBar")) $("#sideDiskBar").style.width = Math.round((disk.used / disk.total) * 100) + "%";
  if ($("#sideDiskText")) $("#sideDiskText").textContent =
    `可用 ${bytes(disk.free)} · 可清理 ${bytes(disk.reclaimable)}`;
}

function rowEl(item) {
  const row = document.createElement("div");
  row.className = "row";
  row.dataset.level = item.level;
  row.dataset.path = item.path;
  row.dataset.name = item.name;
  row.dataset.size = item.size;
  row.dataset.time = item.mtime_iso || "";
  row.dataset.reason = item.reason || "";
  row.dataset.needsAdmin = item.needs_admin ? "1" : "0";
  const disabled = item.needs_admin;
  const checked = item.level === "S" && !disabled;
  row.innerHTML = `
    <span class="cb ${checked ? "on" : ""} ${disabled ? "dis" : ""}"></span>
    <div><div class="nm">${item.name}</div><div class="pt">${item.path}</div></div>
    <span class="chip ${item.level}">${LEVEL_TEXT[item.level] || item.level}</span>
    <span class="sz">${bytes(item.size)}</span>`;
  if (checked) state.selected.add(item.path);
  return row;
}

function renderRows(filter = "all", query = "") {
  const box = $("#rows");
  state.selected.clear();
  box.innerHTML = "";
  const q = query.trim().toLowerCase();
  LEVELS.forEach((lv) => {
    if (filter !== "all" && filter !== lv) return;
    const list = state.items.filter((i) =>
      i.level === lv && (!q || (i.name + i.path).toLowerCase().includes(q))
    );
    if (!list.length) return;
    const total = list.reduce((a, b) => a + b.size, 0);
    box.insertAdjacentHTML("beforeend",
      `<div class="grp"><b>${LABEL[lv]}</b><span>${list.length} 项</span><span class="sz">${bytes(total)}</span></div>`);
    list.forEach((item) => box.appendChild(rowEl(item)));
  });
  if (!box.children.length) {
    box.innerHTML = '<div class="empty" style="padding:40px 0">没有匹配的项目</div>';
  }
  syncSelectAll();
  refreshFooter();
}

function selectableRows() {
  return $$("#rows .row").filter((r) => !r.querySelector(".cb").classList.contains("dis"));
}

function syncSelectAll() {
  const cb = $("#selectAllCb");
  const label = $("#selectAllText");
  if (!cb) return;
  const rows = selectableRows();
  const on = rows.length > 0 && rows.every((r) => state.selected.has(r.dataset.path));
  cb.classList.toggle("on", on);
  if (label) label.textContent = on ? "取消全选" : "全选";
}

function refreshFooter() {
  let total = 0;
  state.selected.forEach((path) => {
    const item = state.items.find((i) => i.path === path);
    if (item) total += item.size;
  });
  $("#cnt").textContent = state.selected.size;
  $("#tot").textContent = bytes(total);
  $("#cleanBtn").disabled = state.selected.size === 0;
}

$("#rows").addEventListener("click", (e) => {
  const row = e.target.closest(".row");
  if (!row) return;
  const cb = e.target.closest(".cb");
  if (cb && !cb.classList.contains("dis")) {
    const path = row.dataset.path;
    if (state.selected.has(path)) state.selected.delete(path); else state.selected.add(path);
    cb.classList.toggle("on");
    syncSelectAll();
    refreshFooter();
  }
  $$("#rows .row").forEach((r) => r.classList.remove("on"));
  row.classList.add("on");
  showDetail(row.dataset);
});

function showDetail(d) {
  state.detailPath = d.path;
  $("#detail").classList.add("on");
  $("#dName").textContent = d.name;
  if ($("#dPath")) $("#dPath").textContent = d.path;
  if ($("#dLv")) $("#dLv").innerHTML = `<span class="chip ${d.level}">${LEVEL_TEXT[d.level] || d.level}</span>`;
  if ($("#dSize")) $("#dSize").textContent = bytes(Number(d.size));
  if ($("#dTime")) $("#dTime").textContent = d.time || "—";
  if ($("#dReason")) $("#dReason").textContent = d.reason || "—";
  const warn = $("#dWarn");
  if (!warn) return;
  if (d.level === "A") {
    warn.style.display = "block";
    $("#dImpact").textContent = "含用户数据，删除后无法重新生成。请先确认已有其他备份。";
  } else if (d.level === "B") {
    warn.style.display = "block";
    $("#dImpact").textContent = "需要管理员权限。工具只生成脚本，不会自动执行。";
  } else {
    warn.style.display = "none";
  }
}

$("#dClose").addEventListener("click", () => {
  $("#detail").classList.remove("on");
  $$("#rows .row").forEach((r) => r.classList.remove("on"));
});

$("#selectAllWrap") && $("#selectAllWrap").addEventListener("click", (e) => {
  e.preventDefault();
  const rows = selectableRows();
  if (!rows.length) return;
  const allOn = rows.every((r) => state.selected.has(r.dataset.path));
  rows.forEach((r) => {
    const path = r.dataset.path;
    if (allOn) state.selected.delete(path); else state.selected.add(path);
    r.querySelector(".cb").classList.toggle("on", !allOn);
  });
  syncSelectAll();
  refreshFooter();
});

$$("#seg button").forEach((b) => onTap(b, () => {
  $$("#seg button").forEach((x) => x.classList.remove("on"));
  b.classList.add("on");
  renderRows(b.dataset.lv, $("#q").value);
}));
$("#q").addEventListener("input", (e) => {
  const active = $("#seg button.on");
  renderRows(active ? active.dataset.lv : "all", e.target.value);
});

const modal = $("#modal");
let modalMode = "clean";

function openModal(mode, title, desc, rows, confirmText) {
  modalMode = mode;
  $("#mTitle").textContent = title;
  $("#mDesc").innerHTML = desc;
  $("#mList").innerHTML = rows.join("");
  $("#mOk").textContent = confirmText;
  modal.classList.add("on");
}
$("#mCancel").addEventListener("click", () => modal.classList.remove("on"));
modal.addEventListener("click", (e) => { if (e.target === modal) modal.classList.remove("on"); });

$("#planBtn").addEventListener("click", () => {
  const rows = [...state.selected].map((p) => {
    const i = state.items.find((x) => x.path === p);
    return `<div class="srow"><span class="chip ${i.level}">${LEVEL_TEXT[i.level] || i.level}</span><span class="p">${i.path}</span><span class="s">${bytes(i.size)}</span></div>`;
  });
  openModal("plan", "清理计划", `将处理 <b>${state.selected.size}</b> 项，不会直接删除。`, rows, "知道了");
});

$("#cleanBtn").addEventListener("click", () => {
  if (!state.selected.size) return;
  const rows = [...state.selected].map((p) => {
    const i = state.items.find((x) => x.path === p);
    return `<div class="srow"><span class="chip ${i.level}">${LEVEL_TEXT[i.level] || i.level}</span><span class="p">${i.path}</span><span class="s">${bytes(i.size)}</span></div>`;
  });
  const total = [...state.selected].reduce((a, p) => a + (state.items.find((x) => x.path === p) || {}).size, 0);
  openModal("clean", "确认移入废纸篓",
    `共 <b>${state.selected.size}</b> 项，预计释放 <b>${bytes(total)}</b>。不会直接删除，之后可从废纸篓放回。`,
    rows, "移入废纸篓");
});

$("#mOk").addEventListener("click", async () => {
  modal.classList.remove("on");
  if (modal._run) {
    const run = modal._run;
    modal._run = null;
    await run();
    return;
  }
  if (modalMode === "plan") return;
  const paths = [...state.selected];
  if (!paths.length) return;
  if (!LIVE) {
    state.items = state.items.filter((i) => !paths.includes(i.path));
    renderRows("all", ""); renderSummary(); toast("演示模式：已从列表移除");
    return;
  }
  try {
    const res = await invoke("clean_run", { paths });
    const okCount = (res.results || []).filter((r) => r.ok).length;
    const done = new Set((res.results || []).filter((r) => r.ok).map((r) => r.path));
    state.items = state.items.filter((i) => !done.has(i.path));
    renderRows("all", ""); renderSummary();
    toast(`已移入废纸篓 ${okCount} 项 · 释放 ${bytes(res.freed || 0)}`);
    if ((res.rejected || []).length) toast(`有 ${res.rejected.length} 项被安全闸拒绝`);
  } catch (err) {
    toast("清理失败：" + err);
  }
});

async function doScan() {
  if (state.busy) return;
  state.busy = true;
  state.cancelling = false;
  const view = $("#v-clean");
  view.classList.remove("noresult");
  view.classList.add("scanning");
  const started = performance.now();
  $("#scanCur").textContent = "正在读取缓存、日志与应用目录…";
  $("#scanCancel").textContent = LIVE ? "停止等待结果" : "取消扫描";
  if (LIVE) $("#scanBar").parentElement.classList.add("wait");
  const timer = setInterval(() => {
    const seconds = (performance.now() - started) / 1000;
    $("#scanTime").textContent = seconds.toFixed(1) + " s";
    $("#scanCount").textContent = LIVE ? `已扫描 ${Math.floor(seconds)} 秒` : "正在整理结果…";
  }, 120);

  try {
    let data;
    if (LIVE) {
      data = await invoke("scan", { budget: 90 });
    } else {
      await new Promise((r) => setTimeout(r, 1400));
      const items = DEMO_ITEMS.map(([level, name, path, size, time, reason], idx) => ({
        level, name, path, size, mtime_iso: time, reason,
        needs_admin: level === "B", rule_id: "demo-" + idx,
      }));
      data = { items, elapsed: 1.4 };
    }
    if (state.cancelling) {
      $("#scanNotice").classList.add("on");
      toast("已取消，保留上一次的结果");
      return;
    }
    state.items = data.items || [];
    state.scannedAt = new Date().toISOString();
    renderRows("all", "");
    renderSummary();
    if (data.items && data.items.length) {
      $("#v-clean").classList.remove("noresult");
    } else {
      $("#v-clean").classList.add("noresult");
    }
    if (data.redlines) renderRedlines(data.redlines);
    if (!LIVE) {
      const reclaim = state.items.filter((i) => i.level !== "B").reduce((a, b) => a + b.size, 0);
      renderDisk({ total: 530 * 1e9, used: 460 * 1e9, free: 70 * 1e9, reclaimable: reclaim });
    }
    toast(`扫描完成 · ${state.items.length} 项`);
    if (LIVE && data.elapsed) $("#scanHint").textContent =
      `上次扫描 ${shortTime(state.scannedAt)} · 用时 ${data.elapsed}s · 只读`;
    if (LIVE) {
      try {
        const disk = await invoke("disk_info");
        const reclaim = state.items.filter((i) => i.level !== "B").reduce((a, b) => a + b.size, 0);
        renderDisk({ ...disk, reclaimable: reclaim });
      } catch (_) { /* 磁盘信息可选 */ }
    }
  } catch (err) {
    toast("扫描失败：" + err);
    if (!state.items.length) $("#v-clean").classList.add("noresult");
  } finally {
    clearInterval(timer);
    $("#scanCancel").textContent = "取消扫描";
    $("#scanBar").parentElement.classList.remove("wait");
    view.classList.remove("scanning");
    state.busy = false;
  }
}

onTap($("#scanStart"), doScan);
onTap($("#rescan2"), doScan);
onTap($("#rescan"), () => { go("clean"); doScan(); });
$("#scanCancel").addEventListener("click", () => { state.cancelling = true; });

function renderRedlines(redlines) {
  const box = $("#redlines");
  if (!box) return;
  box.innerHTML = redlines.map((r) =>
    `<tr><td>${r.label}</td><td><code>${r.path.replace(/^\/Users\/[^/]+/, "~")}</code></td><td>只展示，禁止清理</td></tr>`
  ).join("");
}

function tileColor(name) {
  const palette = ["#2aae67", "#2f2f2f", "#5b3fa8", "#b25000", "#0f6e56", "#993c1d", "#3b6d11", "#185fa5"];
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.codePointAt(0)) % 9973;
  return palette[h % palette.length];
}

function relTime(ts) {
  if (!ts) return "";
  const diff = Date.now() / 1000 - ts;
  if (diff < 86400) return "今天用过";
  if (diff < 172800) return "昨天用过";
  if (diff < 2592000) return Math.floor(diff / 86400) + " 天前用过";
  if (diff < 31536000) return Math.floor(diff / 2592000) + " 个月前用过";
  return Math.floor(diff / 31536000) + " 年前用过";
}

function sortApps(list) {
  const { key, dir } = state.appSort;
  const out = [...list];
  out.sort((a, b) => {
    if (key === "name") return a.name.localeCompare(b.name, "zh-Hans-CN") * dir;
    if (key === "size") return ((a.total_size || a.size || 0) - (b.total_size || b.size || 0)) * dir;
    const pick = (x) => (key === "used" ? x.last_used : key === "accessed" ? x.accessed : x.added) || 0;
    const av = pick(a), bv = pick(b);
    if (!av && !bv) return a.name.localeCompare(b.name, "zh-Hans-CN");
    if (!av) return 1;
    if (!bv) return -1;
    return (av - bv) * dir;
  });
  return out;
}

function renderSortBar() {
  const sort = $("#appSort");
  if (!sort) return;
  sort.querySelectorAll("button").forEach((btn) => {
    const active = btn.dataset.sort === state.appSort.key;
    btn.classList.toggle("on", active);
    const label = btn.dataset.label || (btn.dataset.label = btn.textContent);
    btn.textContent = active ? label + (state.appSort.dir > 0 ? " ↑" : " ↓") : label;
  });
}

function renderApps(apps) {
  const box = $("#applist");
  if (!box) return;
  renderSortBar();
  if (!apps.length) { box.innerHTML = '<div class="empty">没有找到应用</div>'; return; }
  box.innerHTML = sortApps(apps).map((a) => {
    const acc = a.accessed
      ? "访问 " + new Date(a.accessed * 1000).toISOString().slice(0, 10) : "";
    const live = a.usage_source === "tracker" && a.last_used ? relTime(a.last_used) : "";
    const added = a.added ? new Date(a.added * 1000).toISOString().slice(0, 7) + " 安装" : "";
    const dataPart = a.data_size ? bytes(a.data_size) : "";
    return `
    <div class="approw" data-app="${encodeURIComponent(a.bundle_id || a.name)}">
      ${a.icon ? `<img class="ico" src="${a.icon}" alt="">` : `<div class="tile" style="background:${tileColor(a.name)}">${a.name.slice(0, 1)}</div>`}
      <div class="info"><b>${a.name}</b><span>${a.bundle_id || "无 Bundle ID"}${a.version ? " · " + a.version : ""}</span></div>
      <div class="meta"><b>${a.total_size_human || (a.size ? bytes(a.size) : "—")}</b><span>${dataPart ? "数据 " + dataPart + " · " : ""}${acc || live || added || "无记录"}</span></div>
      <span class="chev">›</span>
    </div>`;
  }).join("");
  $$("#applist .approw").forEach((row) =>
    onTap(row, () => openUninstall(decodeURIComponent(row.dataset.app)))
  );
}

async function loadApps() {
  if (!LIVE) {
    const now = Date.now() / 1000;
    state.apps = [
      { name: "微信", bundle_id: "com.tencent.xinWeChat", version: "4.1.2", size: 355000000, total_size: 14200000000, total_size_human: "14.2 GB", data_size: 13845000000,
        parts: [], last_used: now - 3 * 3600, accessed: now - 90 * 86400, added: now - 90 * 86400 },
      { name: "Notion", bundle_id: "notion.id", version: "3.13.0", size: 412000000, total_size: 520000000, total_size_human: "496 MB", data_size: 108000000,
        parts: [], last_used: now - 40 * 86400, accessed: now - 200 * 86400, added: now - 200 * 86400 },
      { name: "Adobe Photoshop 2026", bundle_id: "com.adobe.Photoshop", version: "27.0", size: 6520000000, total_size: 6900000000, total_size_human: "6.4 GB", data_size: 380000000,
        parts: [], last_used: null, accessed: now - 160 * 86400, added: now - 400 * 86400 },
      { name: "Termius", bundle_id: "com.termius.mac", version: "9.8.1", size: 268000000, total_size: 350000000, total_size_human: "334 MB", data_size: 82000000,
        parts: [], last_used: now - 86400, accessed: now - 30 * 86400, added: now - 30 * 86400 },
    ];
    renderApps(state.apps);
    $("#mApps").textContent = "4";
    $("#badgeApps").textContent = "4";
    return;
  }
  try {
    $("#applist").innerHTML = '<div class="empty">正在读取应用列表…</div>';
    const res = await invoke("apps", { enrich: true });
    state.apps = res.apps || [];
    state.usageCovered = res.usage_covered || 0;
    renderApps(state.apps);
    $("#mApps").textContent = res.total;
    $("#badgeApps").textContent = res.total;
    if ($("#metaHint")) {
      const local = res.tracker_covered || 0;
      $("#metaHint").textContent = local
        ? `使用时间：Spotlight + 本地记录，${res.total} 个应用中 ${state.usageCovered} 个有记录`
        : `使用时间来自 Spotlight，${res.total} 个应用中 ${state.usageCovered} 个有记录`;
    }
  } catch (err) {
    toast("读取应用列表失败：" + err);
  }
}

async function openUninstall(name) {
  // 列表已经算好体积和关联文件，先用它渲染，点进去不再等一遍遍历。
  const cached = state.apps.find((x) => x.bundle_id === name || x.name === name);
  if (LIVE && cached && (cached.parts || cached.total_size != null)) {
    const groups = { cache: [], data: [], probable: [], admin: [] };
    cached.parts.forEach((part) => {
      const row = { ...part };
      groups[row.group || "data"].push(row);
    });
    const plan = {
      app: { name: cached.name, bundle_id: cached.bundle_id, version: cached.version,
             size: cached.size, path: cached.path },
      groups, running: false, receipts: [], protected: (cached.bundle_id || "").startsWith("com.apple."),
      _fromCache: true,
    };
    go("uninstall");
    renderUninstallPlan(plan);
    // 运行状态等次要信息后台补，不挡着用户看内容。
    invoke("uninstall_plan", { app: name }).then((res) => {
      if (res && res.ok !== false && state.plan && state.plan._fromCache) {
        state.plan.running = !!res.running;
        state.plan.receipts = res.receipts || [];
        const tag = $("#unbody .apphero p");
        if (tag && res.running && !tag.textContent.includes("正在运行")) {
          tag.textContent += " · 正在运行";
        }
      }
    }).catch(() => {});
    return;
  }
  go("uninstall");
  const body = $("#unbody");
  body.innerHTML = '<div class="empty" style="padding:40px 0">正在读取关联文件…</div>';
  let plan;
  if (LIVE) {
    try { plan = await invoke("uninstall_plan", { app: name }); }
    catch (err) { body.innerHTML = `<div class="empty">读取失败：${err}</div>`; return; }
  } else {
    plan = {
      app: { name: (state.apps.find((x) => x.bundle_id === name) || {}).name || name,
             bundle_id: name, version: "1.0", size: 1288490189, path: "/Applications/" + name + ".app" },
      running: false, receipts: [], protected: false,
      groups: {
        cache: [{ path: "~/Library/Caches/" + name, size: 132120576, note: "Bundle ID 精确命中" }],
        data: [
          { path: "~/Library/Containers/" + name, size: 197132288, note: "沙盒容器" },
          { path: "~/Library/Application Support/" + name, size: 75497472, note: "按应用名精确匹配" },
        ],
        probable: [], admin: [],
      },
    };
  }
  renderUninstallPlan(plan);
}

function renderUninstallPlan(plan) {
  state.plan = plan;
  const body = $("#unbody");
  const appIcon = (state.apps.find((x) => x.bundle_id === plan.app.bundle_id) || {}).icon || "";

  const g = plan.groups;
  const sum = (list) => list.reduce((a, b) => a + (b.size || 0), 0);
  const mainSize = plan.app.size || 0;
  const cacheSize = sum(g.cache);
  const dataSize = sum(g.data) + sum(g.probable);
  const admSize = sum(g.admin);
  const fullTotal = mainSize + cacheSize + dataSize;
  const pct = (v) => (fullTotal ? (v / fullTotal * 100).toFixed(2) : 0) + "%";
  const POL = {
    safe: { label: "可删", why: "应用会自动重建，不含账号和文档" },
    careful: { label: "谨慎", why: "含登录态、本地文档与设置，删除后无法恢复" },
    manual: { label: "需判断", why: "证据不足，只作展示，默认不勾" },
    admin: { label: "禁自动删", why: "系统级组件，只生成脚本，需管理员确认" },
  };

  const rowsOf = (list, disabled) => list.map((c) => `
    <div class="irow" data-group="${c.group || ""}" data-path="${c.path}" data-size="${c.size}">
      <span class="cb ${!c.policy || c.policy === "safe" ? "on" : ""} ${disabled ? "dis" : ""}"></span>
      <span class="p" title="${c.note || ""}">${c.path}<em>${c.purpose || c.note || ""}</em></span>
      <span class="s">${c.size_human || bytes(c.size)}</span>
      <button class="btn sm exp" data-path="${c.path}" title="展开看里面有什么">展开</button>
    </div>`).join("");

  const card = (title, badge, why, list, disabled) => `
    <div class="card2">
      <div class="ch">${title}<span class="pol ${badge}">${POL[badge].label}</span>
        <span class="n">${list.length} 项</span><span class="sz">${bytes(sum(list))}</span></div>
      <div class="why">${why}</div>
      ${rowsOf(list, disabled) || '<div class="irow"><span class="p">无</span></div>'}
    </div>`;

  body.innerHTML = `
    <div class="apphero">
      ${appIcon ? `<img class="ico" style="width:52px;height:52px;flex:0 0 52px;border-radius:12px" src="${appIcon}" alt="">` : `<div class="tile" style="width:52px;height:52px;flex:0 0 52px;border-radius:12px;font-size:20px;background:${tileColor(plan.app.name)}">${plan.app.name.slice(0, 1)}</div>`}
      <div><h2>${plan.app.name}</h2><p>${plan.app.bundle_id || "无 Bundle ID"}${plan.app.version ? " · " + plan.app.version : ""}${plan.running ? " · 正在运行" : ""}</p></div>
      <div class="sz"><b>${bytes(fullTotal)}</b><span>含数据合计</span></div>
    </div>
    <div class="comp">
      <i style="width:${pct(mainSize)};background:#64a0d8"></i>
      <i style="width:${pct(cacheSize)};background:#97c459"></i>
      <i style="width:${pct(dataSize)};background:#e8a33d"></i>
    </div>
    <div class="leg">
      <span><b style="background:#64a0d8"></b>主程序 ${bytes(mainSize)}</span>
      <span><b style="background:#97c459"></b>缓存 ${bytes(cacheSize)}</span>
      <span><b style="background:#e8a33d"></b>应用数据 ${bytes(dataSize)}</span>
      <span><b style="background:#a3a3a8"></b>系统级 ${admSize ? bytes(admSize) : "—"}</span>
    </div>
    <div class="advice">
      <div class="box ok">
        <b>可以删除</b>
        <div class="li"><div>主程序<span>卸载时删，可从 App Store 或官网重装</span></div><em>${bytes(mainSize)}</em></div>
        <div class="li"><div>缓存 · ${g.cache.length} 项<span>自动重建，不含账号和文档</span></div><em>${bytes(cacheSize)}</em></div>
      </div>
      <div class="box no">
        <b>不要随便删</b>
        <div class="li"><div>应用数据 · ${g.data.length} 项<span>含登录态、本地文档与设置，删了不可恢复</span></div><em>${bytes(dataSize)}</em></div>
        <div class="li"><div>系统级 · ${g.admin.length} 项<span>只生成脚本，需管理员确认</span></div><em>${admSize ? bytes(admSize) : "—"}</em></div>
      </div>
    </div>
    ${plan.protected ? '<div class="note warn"><b>系统自带应用</b>不在卸载范围内。</div>' : ""}
    <div class="card2">
      <div class="ch">主程序 <span class="pol delete">卸载时删</span><span class="n">1 项</span><span class="sz">${bytes(mainSize)}</span></div>
      <div class="why">需要时可从 App Store 或官网重装</div>
      <div class="irow"><span class="cb on dis"></span><span class="p">${plan.app.path}<em>必选</em></span><span class="s">${bytes(mainSize)}</span></div>
    </div>
    ${card("缓存", "safe", POL.safe.why, g.cache, false)}
    ${card("应用数据", "careful", POL.careful.why, g.data, false)}
    ${card("疑似共享目录", "manual", POL.manual.why, g.probable, true)}
    ${card("系统级组件", "admin", POL.admin.why, g.admin, true)}
    <div class="note warn"><b>勾选应用数据后不可撤销</b>容器里通常存着聊天记录、登录态与本地文件。如果只是重装，保留数据即可。</div>
    <div class="uact">
      <span class="sum" style="font-size:12px;color:var(--muted)">已勾选 <b id="uCnt" style="color:var(--text)">0</b> 项关联文件 · <b id="uTot" style="color:var(--text)">0 MB</b><em>卸载时会额外删除主程序，删除所选会保留主程序</em></span>
      <div style="margin-left:auto;display:flex;gap:8px">
        <button class="btn" id="delBtn" disabled>删除所选…</button>
        <button class="btn primary" id="unBtn" ${plan.protected ? "disabled" : ""}>卸载…</button>
      </div>
    </div>`;

  $$("#unbody .irow .exp").forEach((b) => b.addEventListener("click", (e) => {
    e.stopPropagation();
    openContainer(b.dataset.path);
  }));

  const chosen = new Set(g.cache.filter((c) => !c.policy || c.policy === "safe").map((c) => c.path));
  const selectedBytes = () => {
    const all = [...g.cache, ...g.data, ...g.probable];
    return [...chosen].reduce((sum, p) => {
      const hit = all.find((c) => c.path === p);
      return sum + (hit ? hit.size : 0);
    }, 0);
  };
  const update = () => {
    let total = 0;
    chosen.forEach((p) => {
      const hit = [...g.cache, ...g.data, ...g.probable].find((c) => c.path === p);
      if (hit) total += hit.size;
    });
    $("#uCnt").textContent = chosen.size;
    $("#uTot").textContent = bytes(total);
    $("#delBtn").disabled = chosen.size === 0;
  };
  update();

  $$("#unbody .irow .cb").forEach((cb) => {
    if (cb.classList.contains("dis")) return;
    cb.addEventListener("click", () => {
      const row = cb.closest(".irow");
      const p = row.dataset.path;
      if (chosen.has(p)) chosen.delete(p); else chosen.add(p);
      cb.classList.toggle("on");
      update();
    });
  });

  $("#delBtn").addEventListener("click", () => {
    const paths = [...chosen];
    if (!paths.length) return;
    const rows = paths.map((p) => `<div class="srow"><span class="chip S">·</span><span class="p">${p}</span></div>`);
    openModal("delete-selected", `删除 ${plan.app.name} 的所选文件`,
      `只删除勾选的 <b>${paths.length}</b> 项，约 <b>${$("#uTot").textContent}</b>。<b>主程序会保留。</b>`,
      rows, "删除所选");
    modal._run = async () => {
      if (!LIVE) { toast("演示模式：未执行"); return; }
      try {
        const res = await invoke("delete_paths", { app: plan.app.bundle_id, paths });
        const ok = (res.results || []).filter((r) => r.ok).length;
        toast(`已删除 ${ok} 项，主程序保留 · 释放 ${bytes(res.freed || 0)}`);
        openUninstall(plan.app.bundle_id);
      } catch (err) {
        toast("删除失败：" + err);
      }
    };
  });

  $("#unBtn").addEventListener("click", () => {
    const withData = [...chosen].some((p) => g.data.some((c) => c.path === p));
    const paths = [...chosen];
    openModal("uninstall", `卸载 ${plan.app.name}`,
      `将移入废纸篓 <b>${paths.length + 1}</b> 项（含主程序 ${bytes(plan.app.size)}），约 <b>${bytes(selectedBytes() + plan.app.size)}</b>。${withData ? "包含应用数据，不可重新生成。" : "仅主程序与缓存。"}`,
      paths.map((p) => `<div class="srow"><span class="chip S">·</span><span class="p">${p}</span></div>`),
      "移入废纸篓");
    modal._run = async () => {
      if (!LIVE) { toast("演示模式：未执行"); return; }
      try {
        const res = await invoke("uninstall_run", { app: plan.app.bundle_id || plan.app.name, withData, includeProbable: false });
        const ok = (res.results || []).filter((r) => r.ok).length;
        toast(`已卸载并清理 ${ok} 项`);
        go("apps"); loadApps();
      } catch (err) { toast("卸载失败：" + err); }
    };
  });
}

const DEMO_CONTAINER = {
  actual: "/Users/demo/Library/Containers/com.demo.app/Data",
  total_human: "14.2 GB",
  hint: "这些只是提示，是否删除由你决定；删除会进废纸篓，可以放回。",
  children: [
    { name: "Library", path: "/demo/Library", size: 12900000000, size_human: "12.9 GB", count: 54211,
      kind: "dir", purpose: "应用数据主目录：配置、缓存和数据", policy: "manual", policy_label: "需判断", why: "点进去看具体子目录再决定" },
    { name: "tmp", path: "/demo/tmp", size: 635000000, size_human: "635 MB", count: 142,
      kind: "dir", purpose: "临时目录，可能含回收站和缓存", policy: "safe", policy_label: "可清", why: "清掉后微信里也恢复不了" },
    { name: ".wxapplet", path: "/demo/.wxapplet", size: 418000000, size_human: "418 MB", count: 948,
      kind: "dir", purpose: "小程序缓存", policy: "safe", policy_label: "可清", why: "小程序会重新下载" },
    { name: "Documents", path: "/demo/Documents", size: 68000, size_human: "68 KB", count: 12,
      kind: "dir", purpose: "应用自己管理的小配置文件", policy: "no", policy_label: "重要数据", why: "很小，别动" },
  ],
  exts: [],
};

async function openContainer(path, from) {
  state.containerFrom = from || state.containerFrom || "uninstall";
  const back = $("#ctBack");
  if (back) {
    back.dataset.go = state.containerFrom;
    back.textContent = state.containerFrom === "clean" ? "‹ 返回清理列表" : "‹ 返回应用详情";
  }
  go("container");
  const table = $("#ctTable");
  const loading = $("#ctLoading");
  $("#ctLoadPath").textContent = path;
  $("#ctPath").textContent = path + " · 正在读取…";
  $("#ctTotal").textContent = "—";
  $("#ctHint").textContent = "";
  table.innerHTML = "";
  table.style.display = "none";
  loading.classList.add("on");
  let data;
  try {
    if (LIVE) {
      data = await invoke("inspect_container", { path });
    } else {
      await new Promise((r) => setTimeout(r, 700));
      data = { ...DEMO_CONTAINER, requested: path };
    }
    if (!data || data.ok === false) throw new Error(data?.error || "读取失败");
  } catch (err) {
    table.style.display = "";
    loading.classList.remove("on");
    $("#ctPath").textContent = path;
    const denied = /Operation not permitted|Permission denied|denied/i.test(String(err));
    $("#ctHint").textContent = denied
      ? "这个目录被 macOS 隐私保护挡住了，需要授予「完全磁盘访问」才能读取。见下方说明。"
      : "";
    table.innerHTML = `<tr><td colspan="7" style="padding:30px">
      <div style="font-size:13px;margin-bottom:8px">读取失败：${err}</div>
      ${denied ? `<div style="font-size:12px;color:var(--muted);line-height:1.9">
        授权步骤：<br>
        1. 打开「系统设置 → 隐私与安全性 → 完全磁盘访问」<br>
        2. 点 <b>+</b>，选中终端（Terminal）或运行本程序的 App，打开开关<br>
        3. <b>完全退出终端/本程序后重新打开</b>，再点展开<br>
        提示：用 <code>cargo run</code> 启动时，要授权的是终端；直接双击 App 启动时，授权 App 本身。
      </div>` : ""}
    </td></tr>`;
    return;
  }
  table.style.display = "";
  loading.classList.remove("on");
  state.container = data;
  renderContainer(data);
}

function renderContainer(data) {
  $("#ctPath").textContent = data.actual || data.requested || "";
  $("#ctTotal").textContent = data.total_human || "—";
  $("#ctHint").textContent = data.hint || "";
  const table = $("#ctTable");
  table.innerHTML =
    '<thead><tr><th style="width:34px"></th><th style="width:26%">名称</th><th style="width:90px">大小</th>' +
    '<th style="width:70px">文件数</th><th style="width:96px">提示</th><th>是什么 / 删了会怎样</th><th style="width:60px"></th></tr></thead><tbody>' +
    (data.children || []).map((c, idx) => `
      <tr data-path="${c.path}" data-policy="${c.policy}" data-size="${c.size}" data-idx="${idx}">
        <td><span class="cb"></span></td>
        <td><b>${c.name}</b><span class="sub">${c.path}</span></td>
        <td class="n">${c.size_human}</td>
        <td class="n">${c.count || 0}</td>
        <td><span class="pol ${c.policy === "safe" ? "safe" : c.policy === "no" ? "no" : c.policy === "warn" ? "warnp" : "manual"}">${c.policy_label}</span></td>
        <td>${c.purpose}<span class="sub">${c.why}</span></td>
        <td>${c.kind === "dir" && c.policy !== "no" ? `<button class="btn sm exp" data-path="${c.path}">展开</button>` : ""}</td>
      </tr>`).join("") || '<tr><td colspan="7" style="padding:30px;text-align:center;color:var(--hint)">这个目录是空的，或者里面只有隐藏文件</td></tr>' + "</tbody>";

  const exts = $("#ctExts");
  exts.innerHTML = (data.exts || []).length
    ? '<h2 style="font-size:14px;font-weight:500;margin:0 0 10px">文件类型分布</h2>' +
      '<table class="rules"><thead><tr><th>类型</th><th>数量</th><th>体积</th><th>是什么</th></tr></thead><tbody>' +
      data.exts.map((e) => `<tr><td><code>${e.ext}</code></td><td class="n">${e.count.toLocaleString()}</td>` +
        `<td class="n">${e.size_human}</td><td>${e.purpose}</td></tr>`).join("") + "</tbody></table>"
    : "";

  state.ctSelected = new Set();
  updateCtFooter();
}

function updateCtFooter() {
  let total = 0;
  (state.container?.children || []).forEach((c) => {
    if (state.ctSelected?.has(c.path)) total += c.size;
  });
  $("#ctCnt").textContent = state.ctSelected ? state.ctSelected.size : 0;
  $("#ctTot").textContent = bytes(total);
  $("#ctDel").disabled = !state.ctSelected || state.ctSelected.size === 0;
}

$("#ctTable").addEventListener("click", (e) => {
  const expand = e.target.closest("button.exp");
  if (expand) { openContainer(expand.dataset.path); return; }
  const row = e.target.closest("tr[data-path]");
  if (!row) return;
  const cb = row.querySelector(".cb");
  const path = row.dataset.path;
  if (state.ctSelected.has(path)) state.ctSelected.delete(path); else state.ctSelected.add(path);
  cb.classList.toggle("on");
  updateCtFooter();
});

$("#ctDel").addEventListener("click", () => {
  const paths = [...(state.ctSelected || [])];
  if (!paths.length || !state.container) return;
  const rows = paths.map((p) => {
    const c = (state.container.children || []).find((x) => x.path === p);
    return `<div class="srow"><span class="pol ${c?.policy === "safe" ? "safe" : c?.policy === "no" ? "no" : "warnp"}">${c?.policy_label || "需判断"}</span>` +
      `<span class="p">${p}<em style="display:block;font-style:normal;font-size:10.5px;color:var(--hint)">${c?.why || ""}</em></span></div>`;
  });
  const risky = paths.filter((p) => (state.container.children || []).find((x) => x.path === p)?.policy !== "safe").length;
  openModal("container", "删除所选子项",
    `将移入废纸篓 <b>${paths.length}</b> 项，约 <b>${$("#ctTot").textContent}</b>。` +
    (risky ? `<b>${risky} 项带有提示，请确认你已看过上面的说明。</b>` : "这些是缓存类文件。"),
    rows, "删除所选子项");
  modal._run = async () => {
    if (!LIVE) { toast("演示模式：未执行"); return; }
    try {
      const res = await invoke("delete_subpaths", { root: state.container.actual, paths });
      const ok = (res.results || []).filter((r) => r.ok).length;
      toast(`已移入废纸篓 ${ok} 项${(res.rejected || []).length ? `，拒绝 ${res.rejected.length} 项` : ""}`);
      openContainer(state.container.actual);
    } catch (err) { toast("删除失败：" + err); }
  };
});

let usageState = { installed: false, running: false, records: 0 };

function renderUsage() {
  const chip = $("#usageStatus");
  const btn = $("#usageToggle");
  if (!chip || !btn) return;
  const on = usageState.installed || usageState.running;
  chip.className = "status " + (on ? "ok" : "no");
  chip.textContent = on ? `已开启 · ${usageState.records} 个应用` : "未开启";
  btn.textContent = on ? "关闭" : "开启";
}

async function loadUsage() {
  if (!LIVE) { usageState = { installed: false, running: false, records: 0 }; renderUsage(); return; }
  try {
    const res = await invoke("usage", { action: "status" });
    usageState = { installed: !!res.installed, running: !!res.running, records: res.records || 0 };
  } catch (_) {
    usageState = { installed: false, running: false, records: 0 };
  }
  renderUsage();
}

$("#usageToggle") && $("#usageToggle").addEventListener("click", async () => {
  if (!LIVE) { toast("演示模式：需要运行桌面版才能开启"); return; }
  const on = usageState.installed || usageState.running;
  try {
    const res = await invoke("usage", { action: on ? "uninstall" : "install" });
    if (!res.ok) throw new Error(res.error || "操作失败");
    toast(on ? "已关闭使用记录，历史数据保留" : "已开启，之后的使用会被记录");
    await loadUsage();
    if (!on) loadApps();
  } catch (err) {
    toast("操作失败：" + err);
  }
});

async function loadDoctor() {
  if (!LIVE) return;
  try {
    const d = await invoke("doctor");
    const denied = (d.probes || []).filter((p) => p.status === "denied");
    const box = $("#fdaStatus");
    if (box) {
      box.innerHTML = denied.length
        ? '<span class="status no">未完全授权</span>'
        : '<span class="status ok">已授权</span>';
      box.title = denied.map((p) => p.label).join("、");
    }
    if ($("#ruleCount")) $("#ruleCount").textContent = `已加载 ${d.rules.total} 条规则`;
    if ($("#aboutRules")) $("#aboutRules").textContent = `${d.rules.total} 条白名单 · ${d.rules.redlines} 条红线`;
  } catch (err) {
    toast("环境检查失败：" + err);
  }
}

onTap($("#refreshApps"), async () => {
  if (!LIVE) { toast("演示模式：无需刷新"); return; }
  const btn = $("#refreshApps");
  btn.disabled = true;
  btn.textContent = "重新计算中…";
  try {
    const res = await invoke("apps", { enrich: true, fresh: true });
    state.apps = res.apps || [];
    renderApps(state.apps);
    toast("已重新计算体积");
  } catch (err) {
    toast("刷新失败：" + err);
  } finally {
    btn.disabled = false;
    btn.textContent = "刷新体积";
  }
});

$$("#appSort button").forEach((btn) => onTap(btn, () => {
  const key = btn.dataset.sort;
  if (state.appSort.key === key) state.appSort.dir *= -1;
  else state.appSort = { key, dir: key === "name" ? 1 : -1 };
  const q = ($("#appSearch") || {}).value || "";
  const needle = q.trim().toLowerCase();
  const list = state.apps.filter((a) => !needle || (a.name + " " + (a.bundle_id || "")).toLowerCase().includes(needle));
  renderApps(list);
}));

$("#appSearch") && $("#appSearch").addEventListener("input", (e) => {
  const q = e.target.value.trim().toLowerCase();
  const list = state.apps.filter((a) => !q || (a.name + " " + (a.bundle_id || "")).toLowerCase().includes(q));
  renderApps(list);
});

 $("#dExpand") && $("#dExpand").addEventListener("click", () => {
  if (!state.detailPath) return toast("请先选择一个项目");
  openContainer(state.detailPath, "clean");
});

$("#dReveal") && $("#dReveal").addEventListener("click", () => {
  const path = $("#dPath").textContent;
  if (!LIVE) return toast("演示模式：仅预览");
  invoke("reveal", { path }).catch((e) => toast("打开失败：" + e));
});
$("#dExclude") && $("#dExclude").addEventListener("click", () => toast("排除列表将在下一版接入"));

const initial = location.hash.replace("#", "");
go(initial && $("#v-" + initial) ? initial : "overview");
$("#v-clean").classList.add("noresult");
if (!LIVE) renderDisk({ total: 530e9, used: 460e9, free: 70e9, reclaimable: 0 });
loadApps();
loadDoctor();
loadUsage();
