/* AGOS web UI — plain JS, no build step. Talks only to this server's /api and /health. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const fmtN = (n) => Number(n || 0).toLocaleString("ar-SA");
const store = {
  get: (k, d = null) => { try { const v = localStorage.getItem(k); return v == null ? d : v; } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

const STATUS = {pending: "بانتظار", running: "قيد التنفيذ", done: "مُتحقق منها", failed: "فشلت", skipped: "لم تُنفذ"};
const RUN_STATUS = {running: "يعمل", done: "اكتمل", partial: "اكتمل جزئيًا", failed: "فشل", cancelled: "أُوقف",
  interrupted: "انقطع", paused_quota: "متوقف مؤقتًا: نفدت حصة النموذج", paused_provider: "متوقف مؤقتًا: المزود غير متاح",
  needs_input: "لم يبدأ: ينقص الطلب معلومات"};
const RUN_SHORT = {paused_quota: "متوقف: الحصة", paused_provider: "متوقف: المزود", needs_input: "ينتظر معلومات"};
const RUN_CLASS = {running: "running", done: "done", partial: "pending", interrupted: "failed", cancelled: "pending",
  paused_quota: "pending", paused_provider: "pending", needs_input: "pending"};
const RESUMABLE = ["interrupted", "failed", "partial", "cancelled", "paused_quota", "paused_provider"];
const ROLE = {researcher: "باحث", analyst: "محلل", writer: "كاتب", coder: "مبرمج", reviewer: "مراجع", planner: "مخطط", critic: "ناقد"};
const PHASE_NAME = {intake: "الاستقبال", planning: "التخطيط", research: "البحث", analysis: "التحليل", review: "المراجعة",
  finalization: "كتابة التقرير", final_review: "التدقيق النهائي", memory: "الذاكرة", other: "أخرى",
  agent: "التحليل", plan: "التخطيط", task: "التحليل", delivery: "التسليم"};
const phaseName = (p) => { const k = String(p || "").split(":")[0]; return PHASE_NAME[k] || k || "-"; };
const EXAMPLES = [
  "ادرس فرصة إطلاق منتج جديد في السوق السعودي، وابحث عن المنافسين، وحلل الأسعار والطلب، واكتب خطة تنفيذ وتقريرًا ماليًا، وأعد ملفًا نهائيًا",
  "ادرس سوق المقاهي المختصة في جدة واقترح خطة إطلاق بميزانية 200 ألف ريال",
  "قارن بين أفضل 3 منصات متاجر إلكترونية للسوق السعودي من حيث التكلفة والمزايا",
  "اكتب دالة Python تتحقق من صحة رقم الآيبان السعودي مع اختبارات",
];
const FREE_DAILY = 100;

// ---------- feedback: toasts and in-page dialogs (native alert/confirm/prompt can be blocked) ----------
function toast(msg, kind = "") {
  const t = document.createElement("div");
  t.className = "toast " + kind; t.textContent = msg; $("#toasts").append(t);
  setTimeout(() => t.remove(), kind === "bad" ? 7000 : 4000);
}
function ask({title, text = "", input = null, ok = "تأكيد", danger = false}) {
  return new Promise((resolve) => {
    const m = $("#modal"), inp = $("#modalInput");
    $("#modalTitle").textContent = title; $("#modalText").textContent = text;
    inp.classList.toggle("hidden", input == null); inp.value = input ?? "";
    $("#modalOk").textContent = ok; $("#modalOk").classList.toggle("danger", danger);
    m.classList.remove("hidden"); (input != null ? inp : $("#modalOk")).focus();
    const done = (v) => { m.classList.add("hidden"); document.removeEventListener("keydown", key); resolve(v); };
    const key = (e) => { if (e.key === "Escape") done(null); };
    document.addEventListener("keydown", key);
    $("#modalOk").onclick = () => done(input != null ? inp.value : true);
    $("#modalCancel").onclick = () => done(null);
    m.onclick = (e) => { if (e.target === m) done(null); };
  });
}

// ---------- theme ----------
$("#themeBtn").onclick = () => {
  const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = t; store.set("ah_theme", t);
};

// ---------- settings ----------
const fields = ["apiKey", "baseUrl", "model", "accessCode", "maxAgents", "memSpace"];
function loadSettings() {
  let s = {};
  try { s = JSON.parse(store.get("ah_settings", "{}")) || {}; } catch { s = {}; }
  fields.forEach((f) => { if (s[f]) $("#" + f).value = s[f]; });
  $("#memOn").checked = s.memOn !== false;
  if (!/^[A-Za-z0-9_-]{20,200}$/.test($("#memSpace").value)) $("#memSpace").value = newSpace();
  const p = ["free", "byok", "platform"].includes(s.provider) ? s.provider : "free";
  document.querySelector(`input[name=provider][value=${p}]`).checked = true;
  syncProvider();
}
function settings() {
  const s = {provider: provider()};
  fields.forEach((f) => (s[f] = $("#" + f).value.trim()));
  s.memOn = $("#memOn").checked;
  return s;
}
function saveSettings() { store.set("ah_settings", JSON.stringify(settings())); }
const provider = () => document.querySelector("input[name=provider]:checked").value;
function syncProvider() {
  $$("[data-for]").forEach((el) => el.classList.toggle("hidden", !el.dataset.for.split(" ").includes(provider())));
}
$$("input[name=provider]").forEach((r) => r.addEventListener("change", () => { syncProvider(); saveSettings(); renderHealth(); analyze(); }));
fields.forEach((f) => $("#" + f).addEventListener("change", saveSettings));
$("#memOn").addEventListener("change", () => { saveSettings(); loadMemory(); });
$("#memSpace").addEventListener("change", () => { saveSettings(); loadMemory(); });
function newSpace() {
  const b = crypto.getRandomValues(new Uint8Array(16));
  return "mem_" + Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
}
const memSpace = () => ($("#memOn").checked && /^[A-Za-z0-9_-]{20,200}$/.test($("#memSpace").value.trim()) ? $("#memSpace").value.trim() : null);
const openSettings = () => { $("#settings").classList.remove("hidden"); $("#settingsClose").focus(); };
const closeSettings = () => { $("#settings").classList.add("hidden"); $("#settingsBtn").focus(); };
$("#settingsBtn").onclick = openSettings;
$("#settingsClose").onclick = closeSettings;
$("#settings").addEventListener("click", (e) => { if (e.target.id === "settings") closeSettings(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#settings").classList.contains("hidden")) closeSettings(); });

// ---------- markdown (small, safe: escape first) ----------
function md(src) {
  const blocks = [];
  let n = 0;
  let s = esc(src).replace(/```(\w*)\n([\s\S]*?)```/g, (_, l, c) => { blocks.push(`<pre><code>${c}</code></pre>`); return `\u0000${blocks.length - 1}\u0000`; });
  s = s.replace(/^#{4,6} (.*)$/gm, "<h4>$1</h4>").replace(/^### (.*)$/gm, "<h4>$1</h4>")
    .replace(/^#{1,2} (.*)$/gm, (_, t) => `<h3 id="sec-${n++}">${t}</h3>`)
    .replace(/^&gt; ?(.*)$/gm, "<blockquote>$1</blockquote>")
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
  s = s.replace(/(?:^\|.*\|\s*$\n?)+/gm, (tbl) => {
    const rows = tbl.trim().split("\n").filter((r) => !/^\|[\s:|-]+\|?\s*$/.test(r));
    return '<div class="tbl"><table>' + rows.map((r) => "<tr>" + r.replace(/^\||\|\s*$/g, "").split("|").map((c) => `<td>${c.trim()}</td>`).join("") + "</tr>").join("") + "</table></div>\n";
  });
  s = s.replace(/(?:^\s*(?:[-*•]|\d+[.)]) .*$\n?)+/gm, (list) => "<ul>" + list.trim().split("\n").map((l) => `<li>${l.replace(/^\s*(?:[-*•]|\d+[.)]) /, "")}</li>`).join("") + "</ul>\n");
  s = s.split(/\n{2,}/).map((p) => (/^\s*<(h\d|ul|div|table|pre|blockquote)/.test(p) || p.includes("\u0000") ? p : `<p>${p.replace(/\n/g, "<br>")}</p>`)).join("\n");
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => blocks[i]);
}

// ---------- smart composer: reads the request locally (no model call, no quota spent) ----------
const CITIES = ["الرياض", "جدة", "مكة", "المدينة المنورة", "الدمام", "الخبر", "الظهران", "الطائف", "تبوك", "أبها", "خميس مشيط", "بريدة", "القصيم", "حائل", "جازان", "نجران", "ينبع", "الجبيل", "الأحساء", "العلا", "نيوم"];
const MARKETS = /السوق السعودي|السعودية|المملكة|الخليج|دول الخليج|الإمارات|دبي|الكويت|قطر|البحرين|عُمان|مصر|الأردن/;
const INTENTS = [
  ["research", "بحث سوق", /ادرس|ابحث|سوق|منافس|دراسة|استكشف|حلّ?ل الطلب|الطلب/],
  ["finance", "تحليل مالي", /ميزاني|تكلف|تكاليف|مالي|عائد|ربح|أسعار|سعر|تسعير|استثمار|تمويل|نقطة التعادل/],
  ["plan", "خطة تنفيذ", /خطة|تنفيذ|إطلاق|خارطة|جدول زمني|مراحل/],
  ["compare", "مقارنة", /قارن|مقارنة|أفضل \d|بين .* و/],
  ["code", "برمجة", /دالة|كود|برنامج|سكربت|Python|JavaScript|API|SQL|خوارزمية/i],
  ["explain", "شرح", /اشرح|ما هو|ما هي|كيف يعمل|بسّط/],
  ["file", "ملف نهائي", /ملف|تقرير|PDF|مستند|عرض تقديمي/],
];
const AMOUNT = /(\d[\d,٬.]*)\s*(ألف|الف|آلاف|مليون|ملايين|مليار|k|K|M)?\s*(ريال|ر\.س|SAR|دولار|\$|درهم)?/;
const toLatin = (s) => s.replace(/[٠-٩]/g, (d) => "٠١٢٣٤٥٦٧٨٩".indexOf(d)).replace(/٫/g, ".").replace(/٬/g, ",");
function budgetIn(text) {
  const t = toLatin(text || "");
  for (const line of t.split(/[\n،,.؛]/)) {
    if (!/ميزاني|budget|رأس المال/i.test(line)) continue;
    const m = line.match(AMOUNT);
    if (!m) continue;
    let v = parseFloat(m[1].replace(/[,٬]/g, ""));
    v *= {"ألف": 1e3, "الف": 1e3, "آلاف": 1e3, k: 1e3, K: 1e3, "مليون": 1e6, "ملايين": 1e6, M: 1e6, "مليار": 1e9}[m[2]] || 1;
    if (v >= 1000) return {value: v, unit: m[3] || "ريال", text: m[0].trim()};
  }
  return null;
}
const money = (v) => v >= 1e6 ? `${+(v / 1e6).toFixed(2)} مليون` : v >= 1e3 ? `${+(v / 1e3).toFixed(1)} ألف` : String(v);
function analyze() {
  const g = $("#goal").value.trim(), box = $("#insight");
  store.set("ah_draft", $("#goal").value);
  if (g.length < 12) { box.className = "insight"; box.innerHTML = ""; return; }
  const intents = INTENTS.filter(([, , re]) => re.test(g));
  const ids = new Set(intents.map((i) => i[0]));
  const budget = budgetIn(g);
  const city = CITIES.find((c) => g.includes(c));
  const market = g.match(MARKETS)?.[0];
  const period = g.match(/خلال \S+ (?:شهر|أشهر|شهرًا|سنة|سنوات|أسابيع)|\d+\s*(?:شهر|أشهر|شهرًا|سنة|سنوات)|الربع \S+|عام \d{4}/)?.[0];
  const business = ids.has("research") || ids.has("finance") || ids.has("plan");
  const found = [];
  if (budget) found.push(["الميزانية", `${money(budget.value)} ${budget.unit}`]);
  if (city || market) found.push(["النطاق", city || market]);
  if (period) found.push(["المدة", period]);
  const add = [];
  if (business && !budget) add.push(["بميزانية 200 ألف ريال", "حدد الميزانية لتُحسب الخطة المالية بأرقام حقيقية"]);
  if (business && !city && !market) add.push(["في السوق السعودي", "حدد السوق أو المدينة"]);
  if (ids.has("plan") && !period) add.push(["خلال 12 شهرًا", "حدد الأفق الزمني"]);
  if (business && !ids.has("file")) add.push(["وأعد ملفًا نهائيًا", "اطلب ملفًا جاهزًا للتنزيل"]);
  if (/منتج جديد|منتج ما|فكرة/.test(g) && !/(قهوة|عطر|تطبيق|ملابس|غذا|مشروب|منصة|خدمة|متجر)/.test(g))
    add.push(["(اذكر نوع المنتج)", "نوع المنتج غير محدد؛ تحديده يجعل البحث عن المنافسين والأسعار أدق"]);
  // clarity: what a team needs to work without guessing
  const clarity = Math.min(100, 25 + intents.length * 12 + (budget ? 18 : 0) + (city || market ? 15 : 0) + (period ? 8 : 0) + Math.min(14, g.length / 12)
    - (business ? (!budget ? 10 : 0) + (!city && !market ? 8 : 0) : 0));
  // rough model-reply estimate, so the free daily limit is not a surprise
  const tasks = Math.max(1, Math.min(6, intents.filter(([k]) => k !== "file").length + (business ? 1 : 0)));
  const research = ids.has("research") || ids.has("compare") ? 18 : 0;
  const lo = 1 + tasks * 2 + 3 + Math.round(research * 0.6), hi = 2 + tasks * 4 + 5 + research;
  const held = provider() === "free" && HEALTH.hold;
  box.className = "insight on";
  box.innerHTML = `
    <div class="insight-row"><b>فهمتُ أنها</b>${intents.length ? intents.map(([, n]) => `<span class="badge s-running">${n}</span>`).join("") : '<span class="badge">مهمة عامة</span>'}
      ${found.map(([k, v]) => `<span class="badge s-done">${esc(k)}: ${esc(v)}</span>`).join("")}</div>
    ${add.length ? `<div class="insight-row"><b>لنتيجة أدق</b>${add.map(([t, why]) => `<button type="button" class="chip add" data-add="${esc(t)}" title="${esc(why)}">+ ${esc(t)}</button>`).join("")}</div>` : ""}
    <div class="insight-row"><b>وضوح الطلب</b><span class="meter"><i><u style="width:${clarity}%"></u></i><small class="muted">${Math.round(clarity)}%</small></span>
      <small class="muted">· تقدير الاستهلاك: ${lo}–${hi} ردًا من النموذج${provider() === "free" ? ` (الحد المجاني نحو ${FREE_DAILY} يوميًا)` : ""}</small></div>
    ${held ? `<div class="hint warn">الحصة المجانية نفدت الآن${HEALTH.until ? " حتى " + esc(HEALTH.until) : ""}. يمكنك البدء: ستتوقف المهمة مؤقتًا دون فقدان أي عمل، ثم تكملها بزر «أكمل من حيث توقفت».</div>`
      : provider() === "free" && hi > FREE_DAILY * 0.6 ? `<div class="hint warn">المهمة كبيرة على الحد المجاني. إن نفدت الحصة يُحفظ العمل وتُستأنف من المرحلة التي توقفت عندها.</div>` : ""}`;
  $$("[data-add]", box).forEach((b) => (b.onclick = () => {
    const t = b.dataset.add, ta = $("#goal");
    if (t.startsWith("(")) { ta.focus(); toast("أضف نوع المنتج في نص الطلب، مثل: قهوة مختصة معلبة"); return; }
    ta.value = ta.value.trim().replace(/[.،]$/, "") + (/^و/.test(t) ? "، " : " ") + t; analyze(); ta.focus();
  }));
}
let analyzeTimer;
$("#goal").addEventListener("input", () => { clearTimeout(analyzeTimer); analyzeTimer = setTimeout(analyze, 180); });
$("#goal").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); startRun(); } });
$("#followGoal").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); $("#followBtn").click(); } });

// ---------- model availability (/health) ----------
const HEALTH = {hold: null, until: "", ok: null};
async function checkHealth() {
  try {
    const r = await fetch("/health", {cache: "no-store"});
    const h = await r.json();
    const holds = h.model_holds || [];
    HEALTH.ok = true;
    HEALTH.hold = holds.find((x) => /pollinations/i.test(x.endpoint || "")) || null;
    HEALTH.until = HEALTH.hold?.until ? new Date(HEALTH.hold.until * 1000).toLocaleString("ar-SA", {hour: "2-digit", minute: "2-digit", weekday: "short"}) : "";
  } catch { HEALTH.ok = false; }
  renderHealth(); analyze();
}
function renderHealth() {
  const pill = $("#modelPill"), txt = $("#modelPillText"), banner = $("#banner");
  const p = provider();
  let cls = "ok", label = {free: "النموذج المجاني متاح", byok: "مفتاحك الخاص", platform: "مفتاح المنصة"}[p];
  if (HEALTH.ok === false) { cls = "bad"; label = "الخادم لا يستجيب"; }
  else if (p === "free" && HEALTH.hold) { cls = "warn"; label = HEALTH.hold.kind === "quota_exhausted" ? `الحصة نفدت${HEALTH.until ? " حتى " + HEALTH.until : ""}` : "المزود معلّق مؤقتًا"; }
  pill.className = "pill " + cls; txt.textContent = label;
  pill.title = p === "free" ? "Pollinations المجاني: نحو 100 رد يوميًا لكل عنوان IP" : "";
  const show = p === "free" && HEALTH.hold;
  banner.classList.toggle("hidden", !show);
  if (show) banner.innerHTML = `حصة النموذج المجاني نفدت لهذا اليوم${HEALTH.until ? ` وتعود ${esc(HEALTH.until)}` : ""}. أي مهمة تبدأها الآن ستتوقف مؤقتًا ويُحفظ عملها، ثم تكملها من المرحلة نفسها. أو استخدم مفتاحك الخاص من <a href="#" id="bannerSettings">الإعدادات</a>.`;
  const bs = $("#bannerSettings"); if (bs) bs.onclick = (e) => { e.preventDefault(); openSettings(); };
}
$("#modelPill").onclick = () => { checkHealth(); openSettings(); };

// ---------- run ----------
let current = null, es = null, refreshTimer = null, lastRun = null;
function clientKey() {
  let k = store.get("ah_client_key");
  if (!/^[A-Za-z0-9_-]{20,200}$/.test(k || "")) { k = newSpace().replace("mem_", "cli_"); store.set("ah_client_key", k); }
  return k;
}
async function api(path, opts = {}) {
  const r = await fetch(path, {...opts, headers: {"Content-Type": "application/json", "X-Client-Key": clientKey(), ...(opts.headers || {})}});
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${r.status}`);
  return body;
}

$("#runBtn").onclick = () => startRun();
async function startRun(resumeFrom = null, followUpOf = null, followGoal = null) {
  const goal = (followGoal ?? $("#goal").value).trim();
  if (!goal && !resumeFrom) { toast("اكتب ما تريد إنجازه أولًا", "warn"); return (followUpOf ? $("#followGoal") : $("#goal")).focus(); }
  saveSettings();
  const s = settings();
  if (s.provider === "byok" && !s.apiKey) { toast("أدخل مفتاح API في الإعدادات أو اختر المجاني", "warn"); return openSettings(); }
  if (s.provider === "platform" && !s.accessCode) { toast("أدخل رمز الوصول في الإعدادات", "warn"); return openSettings(); }
  const body = {goal: goal || lastRun?.goal || "", provider: s.provider, api_key: s.apiKey || null, base_url: s.baseUrl || null,
    model: s.model || null, access_code: s.accessCode || null, max_agents: s.maxAgents ? Number(s.maxAgents) : null, memory_space: memSpace(),
    scenario: $("#scenario").checked, approve_plan: $("#approvePlan").checked && !resumeFrom && !followUpOf, resume_from: resumeFrom, follow_up_of: followUpOf};
  $("#runBtn").disabled = true;
  try {
    const {id} = await api("/api/runs", {method: "POST", body: JSON.stringify(body)});
    if (!followUpOf && !resumeFrom) store.set("ah_draft", "");
    toast(resumeFrom ? "يُستكمل العمل من حيث توقف" : followUpOf ? "بدأت المتابعة على نتيجة المهمة السابقة" : "بدأ الفريق العمل");
    openRun(id);
    loadHistory();
  } catch (e) {
    toast(e.message, "bad");
    if (/مفتاح|رمز|key/i.test(e.message)) openSettings();
    $("#runBtn").disabled = false;
  }
}
$("#cancelBtn").onclick = async () => {
  if (!current) return;
  if (!(await ask({title: "إيقاف المهمة؟", text: "يُحفظ ما أُنجز، ويمكنك استكمالها لاحقًا.", ok: "أوقف", danger: true}))) return;
  api(`/api/runs/${current}/cancel`, {method: "POST"}).then(() => toast("طُلب الإيقاف")).catch((e) => toast(e.message, "bad"));
};

function openRun(id) {
  current = id; lastRun = null;
  if (es) es.close();
  try { history.replaceState(null, "", "#run=" + id); } catch { /* sandboxed */ }
  $("#live").classList.remove("hidden");
  $("#log").innerHTML = ""; $("#board").innerHTML = '<div class="skeleton"></div><div class="skeleton" style="width:70%"></div>'; $("#verdict").textContent = "";
  $("#result").className = "md muted"; $("#result").textContent = "قيد العمل...";
  ["#followCard", "#exportBtn", "#copyBtn", "#toc", "#deltaCard", "#usageCard"].forEach((s) => $(s).classList.add("hidden"));
  $$("#history li").forEach((li) => li.classList.toggle("active", li.dataset.id === id));
  refresh();
  es = streamEvents(id, onEvent, () => refresh());
  $("#live").scrollIntoView({behavior: "smooth", block: "start"});
}

// Server-sent events over fetch, so the private X-Client-Key header can be sent (EventSource cannot).
function streamEvents(id, onMsg, onEnd) {
  const ctl = new AbortController();
  (async () => {
    try {
      const r = await fetch(`/api/runs/${id}/events`, {headers: {"X-Client-Key": clientKey()}, signal: ctl.signal});
      if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);
      const reader = r.body.getReader(), dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const {value, done} = await reader.read();
        if (done) break;
        buf += dec.decode(value, {stream: true});
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
          const data = chunk.split("\n").filter((l) => l.startsWith("data: ")).map((l) => l.slice(6)).join("\n");
          if (data) { try { onMsg(JSON.parse(data)); } catch { /* malformed event: skip */ } }
        }
      }
    } catch (e) { if (e.name === "AbortError") return; }
    onEnd();
  })();
  return {close: () => ctl.abort()};
}

// Labels return plain text; onEvent escapes once.
const LABEL = {
  run_interrupted: () => "انقطع التشغيل لأن الخادم يُعاد تشغيله؛ يمكنك استكماله لاحقًا",
  resumed: (e) => `استكمال المهمة ${e.from}: إعادة استخدام ${e.reused?.length ?? 0} مهمة مُتحقق منها، وتنفيذ ${e.remaining?.length ?? 0}${e.research_reused ? "، والبحث محفوظ" : ""}`,
  run_started: (e) => `بدأ المنسق (${e.llm?.provider}${e.llm?.model ? ": " + e.llm.model : ""})، ميزانية ${e.max_agents} وكيلًا`,
  planning: (e) => `المنسق يخطط${e.scope && e.scope !== "root" ? " لفريق فرعي " + e.scope : ""}`,
  goal_checked: (e) => {
    const bad = (e.checks || []).filter((c) => !c.ok);
    return bad.length ? "تدقيق حسابي لأرقام طلبك: " + bad.map((c) => `«${c.text}» — الصحيح ${c.expr} = ${Number(c.correct).toLocaleString("ar")} ${c.unit || ""}`).join("؛ ")
      : `تدقيق حسابي لأرقام طلبك: ${(e.checks || []).length} علاقة صحيحة`;
  },
  intake_brief: (e) => e.blocking?.length ? `تحليل الطلب: ينقصه ${e.blocking.map((k) => CLARIFY_LABEL[k] || k).join("، ")}` : "تحليل الطلب: المعطيات الأساسية موجودة",
  clarification_needed: () => "ينتظر إجابتك عن أسئلة الطلب؛ لن يُستهلك أي رد قبلها",
  clarification_answered: (e) => `أجبتَ: ${Object.entries(e.answers || {}).map(([k, v]) => `${CLARIFY_LABEL[k] || k}: ${v}`).join("، ") || "—"}${e.assumed?.length ? `؛ وافتراض: ${e.assumed.map((k) => CLARIFY_LABEL[k] || k).join("، ")}` : ""}`,
  clarification_timeout: () => "انتهت مهلة الانتظار دون إجابة؛ لم يُنفَّذ شيء ولم يُستهلك أي رد",
  plan_adjusted: (e) => `حُذفت من الخطة مهمة التجميع ${(e.dropped || []).join("، ")}: ${e.reason || ""}`,
  plan_awaiting_approval: () => "الخطة جاهزة وتنتظر اعتمادك؛ لن يعمل أي وكيل قبل قرارك",
  plan_decision: (e) => ({approve: `اعتمدتَ الخطة${e.dropped?.length ? "، وأزلت " + e.dropped.join("، ") : ""}${e.note ? "، مع ملاحظة: " + e.note : ""}`,
    reject: "رفضتَ الخطة؛ لن يُنفَّذ شيء", timeout: "انتهت مهلة الاعتماد دون قرار؛ لم يُنفَّذ شيء"})[e.action] || e.action,
  plan: (e) => `خطة${e.scope && e.scope !== "root" ? " فرعية" : ""}: ${e.tasks?.length ?? 0} مهام — ${e.summary || ""}`,
  research_started: (e) => e.resumed ? "البحث: أُعيد استخدام ما جُمع وقُرئ سابقًا" : "بدأ البحث في المصادر",
  research_round: (e) => `جولة بحث ${e.round}: ${(e.facets || []).length} محاور`,
  research_round_done: (e) => `انتهت جولة البحث ${e.round}`,
  agent_spawned: (e) => `أُنشئ الوكيل ${e.agent} (${ROLE[e.role] || e.role}) للمهمة ${e.task}${e.attempt > 1 ? " — محاولة " + e.attempt : ""}`,
  tool_call: (e) => `${e.agent} يستخدم ${e.tool}: ${JSON.stringify(e.input ?? "").slice(0, 140)}`,
  tool_result: (e) => `نتيجة ${e.tool}: ${String(e.preview || "").slice(0, 160)}`,
  verified: (e) => `المدقق: ${e.pass ? "قبل" : "رفض"} ناتج ${e.task} (${e.score ?? "-"}/10)${e.issues?.length ? " — " + e.issues[0] : ""}`,
  task_delegated: (e) => `المهمة ${e.task} كبيرة؛ أُسندت إلى فريق فرعي`,
  task_finished: (e) => `المهمة ${e.task}: ${STATUS[e.status] || e.status}${e.reason ? " — " + e.reason : ""}`,
  task_skipped: (e) => `تخطي ${e.task}: ${e.reason}`,
  agent_error: (e) => `خطأ عند ${e.agent}: ${e.error}`,
  prior_checked: (e) => `فُحصت النتيجة السابقة: ${Object.entries(e.counts || {}).map(([k, n]) => `${k}: ${n}`).join("، ")}` + ((e.issues || []).length ? ` — مرفوض: ${e.issues.join("؛ ")}` : ""),
  research_stats: (e) => `قياس البحث: ${e.search_calls} بحثًا، ${e.search_ok} بنتائج، ${e.pages_read} صفحة قُرئت فعلًا، ${e.sources_in_answer} مصدرًا في الجواب`,
  memory_excluded: (e) => `استُبعد من الذاكرة ${e.count}: ${e.reason}`,
  memory_skipped: (e) => `لم يُحفظ شيء في الذاكرة: ${e.reason}`,
  synthesizing: (e) => e.unverified ? "لم تجتز أي مهمة التحقق؛ المنسق يكتب مسودة موسومة بأنها غير متحقق منها" : "المنسق يجمع النتائج ويكتب التقرير",
  final_check: (e) => `التحقق النهائي: ${e.pass ? "مقبول" : "مرفوض"} (${e.score}/10)`,
  memory_recalled: (e) => `استرجع المنسق ${e.count} معلومة من الذاكرة (من أصل ${e.total})`,
  memory_saved: (e) => `حُفظ في الذاكرة ${e.count} معلومة${e.superseded ? "، وحُذف " + e.superseded + " قديمة" : ""}`,
  memory_error: (e) => `تعذر ${({recall: "استرجاع", save: "حفظ", extract: "استخراج"})[e.stage] || "عمل"} الذاكرة: ${e.error}`,
  run_error: (e) => `خطأ: ${e.error}`,
  run_cancelled: () => "أُوقفت المهمة",
  file_created: (e) => `أُنشئ الملف النهائي ${e.name} (${fmtN(e.bytes)} بايت) وتُحقق منه`,
  file_failed: (e) => `تعذر إنشاء الملف النهائي: ${e.error || "فشل التحقق"}`,
  review_reused: (e) => `أُعيد استخدام مراجعة سابقة لـ ${e.task} (المدخلات لم تتغير)`,
  review_resumed: (e) => `مراجعة ناتج ${e.task} المحفوظ دون إعادة كتابته`,
  section_reused: (e) => `أُعيد استخدام قسم ${e.section} المحفوظ`,
  report_revision: (e) => `تصحيح أقسام التقرير المرفوضة فقط: ${(e.sections || []).join("، ")}`,
  retries_stopped: (e) => `أُوقفت إعادة المحاولة لـ ${e.task}: ${e.reason}`,
  budget_delta: (e) => `تغيير الميزانية: يُعاد حساب ${(e.affected || []).join("، ") || "لا شيء"}، ويبقى ${(e.reused || []).join("، ") || "لا شيء"} كما هو دون بحث جديد`,
  run_paused: (e) => `توقف مؤقت (${e.kind}) في مرحلة ${phaseName(e.phase || e.step)}: ${e.message || ""}`,
  run_finished: (e) => `انتهى: ${RUN_STATUS[e.status] || e.status} — ${e.agents_spawned ?? 0} وكيلًا، ${e.usage?.calls ?? 0} استدعاء نموذج${e.saved === false ? " — لم يُحفظ في السجل الدائم" : ""}`,
};
const MINOR = new Set(["tool_call", "tool_result", "research_round_done", "planning", "agent_spawned", "memory_recalled", "memory_excluded", "memory_skipped"]);
const GOOD = new Set(["file_created", "final_check", "review_reused", "section_reused", "research_started", "budget_delta"]);

function onEvent(e) {
  const li = document.createElement("li");
  let text;
  try { text = (LABEL[e.type] || (() => e.type))(e); } catch { text = e.type; }
  li.innerHTML = `<span class="t">${Number(e.t || 0).toFixed(1)}ث</span><span>${esc(text)}</span>`;
  if (MINOR.has(e.type)) li.classList.add("minor");
  if (/error|failed|skipped/.test(e.type) || e.pass === false) li.classList.add("err");
  else if (e.type === "run_paused" || e.type === "retries_stopped") li.classList.add("warn");
  else if (GOOD.has(e.type) && e.pass !== false) li.classList.add("good");
  $("#log").prepend(li);
  if (["clarification_needed", "clarification_answered", "plan", "plan_awaiting_approval", "plan_decision", "task_finished", "task_skipped", "agent_spawned", "verified", "run_finished",
    "run_paused", "research_started", "synthesizing", "file_created", "budget_delta", "final_check"].includes(e.type)) scheduleRefresh();
  if (e.type === "run_finished" && es) { es.close(); setTimeout(refresh, 300); }
}
function scheduleRefresh() { clearTimeout(refreshTimer); refreshTimer = setTimeout(refresh, 400); }
$$("[data-log]").forEach((b) => (b.onclick = () => {
  $$("[data-log]").forEach((x) => x.classList.toggle("on", x === b));
  $("#log").classList.toggle("key", b.dataset.log === "key");
}));
$("#log").classList.add("key");

// ---------- phase timeline ----------
const PHASES = [["planning", "التخطيط"], ["research", "البحث"], ["analysis", "التحليل والمراجعة"], ["finalization", "كتابة التقرير"], ["delivery", "التسليم"]];
const PHASE_IDX = {intake: 0, planning: 0, plan: 0, start: 0, research: 1, analysis: 2, review: 2, task: 2, finalization: 3, final_review: 3, memory: 4, delivery: 4};
function phaseOf(r) {
  const types = new Set((r.events || []).map((e) => e.type));
  let i = 0;
  if (types.has("research_started")) i = 1;
  if (types.has("agent_spawned") || types.has("verified") || types.has("review_resumed")) i = 2;
  if (types.has("synthesizing") || types.has("section_reused") || types.has("final_check")) i = 3;
  if (types.has("file_created") || types.has("file_failed")) i = 4;
  const cp = r.checkpoint?.phase;
  if (cp in PHASE_IDX) i = Math.max(i, PHASE_IDX[cp]);
  if (r.pause) { const p = String(r.pause.phase || r.pause.step || "").split(":")[0]; if (p in PHASE_IDX) i = PHASE_IDX[p]; }
  return {i, research: types.has("research_started")};
}
function renderPhases(r) {
  const {i, research} = phaseOf(r);
  const finished = ["done", "partial"].includes(r.status);
  const stopped = String(r.status).startsWith("paused") || ["interrupted", "cancelled"].includes(r.status);
  $("#phases").innerHTML = PHASES.map(([, name], k) => {
    let cls = finished || k < i ? "done" : k === i ? (stopped ? "stop" : r.status === "failed" ? "fail" : "now") : "";
    if (k === 1 && !research && (k < i || finished)) return `<li class="done" title="لم تحتج المهمة بحثًا">${name} <small>(لا حاجة)</small></li>`;
    return `<li class="${cls}"${k === i && stopped ? ' title="توقفت هنا؛ الاستكمال يبدأ من هذه المرحلة"' : ""}>${name}</li>`;
  }).join("");
}

// ---------- refresh / render ----------
async function refresh() {
  if (!current) return;
  let r;
  try { r = await api(`/api/runs/${current}`); } catch (e) {
    if (!lastRun) $("#result").innerHTML = `<div class="note bad">تعذر فتح هذه المهمة: ${esc(e.message)}</div>`;
    return;
  }
  if (r.id !== current) return;
  lastRun = r;
  const busy = r.status === "running";
  $("#runStatus").className = `badge s-${RUN_CLASS[r.status] || "failed"}`; $("#runStatus").textContent = RUN_SHORT[r.status] || RUN_STATUS[r.status] || r.status;
  $("#runGoal").textContent = r.goal; $("#runGoal").title = r.goal;
  renderPhases(r);
  const secs = Math.max(0, ((r.finished_at || Date.now() / 1000) - r.created_at));
  const dur = secs >= 3600 ? `${(secs / 3600).toFixed(1)} س` : secs >= 90 ? `${Math.round(secs / 60)} د` : `${Math.round(secs)} ث`;
  const u = r.usage || {};
  $("#stats").innerHTML = [["مهام مُتحقق منها", `${r.tasks_done} / ${r.tasks_total}`], ["مهام لم تكتمل", r.tasks_failed],
    ["وكلاء", `${r.agents_spawned} / ${r.max_agents}`], ["الزمن", dur], ["ردود النموذج", r.usage ? fmtN(u.calls) : "…"],
    ...(u.avoided ? [["وُفّرت بإعادة الاستخدام", fmtN(u.avoided)]] : [])]
    .map(([k, v]) => `<div class="stat"><b>${esc(v)}</b><span>${k}</span></div>`).join("");
  renderBoard(r);
  renderClarify(r);
  renderApproval(r);
  renderDelta(r);
  renderUsage(r);
  $("#runBtn").disabled = busy; $("#cancelBtn").classList.toggle("hidden", !busy);
  if (!busy) renderResult(r);
  else if (r.result) { $("#result").className = "md"; $("#result").innerHTML = md(r.result); }
  const m = r.memory;
  $("#memRun").classList.toggle("hidden", !m?.enabled);
  if (m?.enabled) {
    const li = (xs) => xs?.length ? '<ul class="mem">' + xs.map((i) => `<li>${memLine(i)}</li>`).join("") + "</ul>" : '<p class="muted">لا شيء</p>';
    $("#memRunBody").innerHTML = `${m.error ? `<div class="note bad">تعذر عمل الذاكرة في هذه المهمة (${esc(m.error)}). الجواب محفوظ، لكن لم يُحفظ شيء جديد في الذاكرة.</div>` : ""}<h4>ما تذكّره المنسق</h4>${li(m.recalled)}<h4>ما حفظه بعد المهمة</h4>${busy ? '<p class="muted">بعد الانتهاء...</p>' : li(m.saved)}`;
  }
}

function renderBoard(r) {
  const open = new Set($$("#board details[open]").map((d) => d.dataset.id));
  const tasks = Object.values(r.tasks || {});
  const done = tasks.filter((t) => t.status === "done").length;
  $("#boardSummary").textContent = tasks.length ? `${done} من ${tasks.length}` : "";
  $("#board").innerHTML = tasks.map((t) => {
    const last = t.attempts?.at(-1)?.verdict;
    const sc = last?.score != null ? Number(last.score) : null;
    const scoreBar = sc != null ? `<span class="score" title="تقييم المدقق ${sc}/10"><u style="width:${sc * 10}%;background:${sc >= 7 ? "var(--ok)" : sc >= 5 ? "var(--warn)" : "var(--bad)"}"></u></span> ${sc}/10` : "";
    return `<div class="task ${t.depth ? "sub" : ""} ${t.status === "running" ? "is-running" : ""}"><div class="task-h"><span class="badge">${esc(t.id)}</span><span class="title">${esc(t.title)}</span>
      <span class="badge">${esc(ROLE[t.role] || t.role)}</span><span class="badge s-${t.status}">${STATUS[t.status] || esc(t.status)}</span>
      ${t.reused ? '<span class="badge" title="نتيجة محفوظة أُعيد استخدامها دون استدعاء جديد">محفوظة</span>' : ""}${t.budget_dependent ? '<span class="badge s-pending">يعتمد على الميزانية</span>' : ""}${t.unreviewed ? '<span class="badge s-pending">بانتظار المراجعة</span>' : ""}</div>
      <div class="meta">${t.decompose ? "فريق فرعي · " : ""}${t.depends_on?.length ? "يعتمد على: " + esc(t.depends_on.join("، ")) + " · " : ""}محاولات: ${t.attempts?.length ?? 0}${scoreBar ? " · " + scoreBar : ""}</div>
      ${last && !last.pass && last.issues?.length ? `<div class="meta err">${esc(last.issues.slice(0, 2).join(" · "))}</div>` : ""}
      ${t.output ? `<details data-id="${esc(t.id)}"${open.has(t.id) ? " open" : ""}><summary>الناتج</summary><div class="md">${md(t.output)}</div></details>` : ""}</div>`;
  }).join("") || (r.status === "running" ? '<div class="skeleton"></div><div class="skeleton" style="width:70%"></div><p class="muted small">المنسق يخطط…</p>' : '<p class="muted">لا توجد مهام.</p>');
}

function renderDelta(r) {
  const d = r.delta;
  $("#deltaCard").classList.toggle("hidden", !d?.change);
  if (!d?.change) return;
  const title = (id) => esc(r.tasks?.[id]?.title || id);
  const unit = d.change.unit || d.old?.unit || "";
  $("#deltaBody").innerHTML = `<p>الميزانية: ${d.old?.value ? `<b>${money(d.old.value)}</b> ${esc(unit)}` : "لم تُذكر سابقًا"} ← <b>${money(d.change.new)}</b> ${esc(unit)}
    ${d.old?.source === "assumed_by_study" ? '<small class="muted">(السابقة استُنتجت من نتائج الدراسة)</small>' : ""}</p>
    <div class="insight-row"><b>يُعاد حسابه</b>${(d.recomputed || []).map((i) => `<span class="badge s-pending">${title(i)}</span>`).join("") || '<span class="muted small">…</span>'}</div>
    <div class="insight-row"><b>يبقى كما هو</b>${(d.reused || []).map((i) => `<span class="badge s-done">${title(i)}</span>`).join("") || '<span class="muted small">لا شيء</span>'}</div>
    <p class="muted small">لا يُعاد التخطيط ولا البحث، ولا يُفتح أي مصدر من جديد. أي نتيجة تبقى على الميزانية القديمة تُرفض تلقائيًا.</p>`;
}

function renderUsage(r) {
  const u = r.usage, bp = u?.by_phase || {};
  const rows = Object.entries(bp).filter(([, v]) => v.calls || v.failed || v.avoided);
  $("#usageCard").classList.toggle("hidden", !rows.length);
  if (!rows.length) return;
  const max = Math.max(1, ...rows.map(([, v]) => v.calls));
  $("#usageNote").textContent = `${fmtN(u.calls)} ردًا${u.failed ? ` · ${fmtN(u.failed)} فاشلة` : ""}${u.retried ? ` · ${fmtN(u.retried)} معادة` : ""}${u.avoided ? ` · وُفّر ${fmtN(u.avoided)}` : ""}`;
  const free = r.llm?.provider === "free";
  $("#usageBody").innerHTML = `<div class="bars">${rows.map(([k, v]) => `<div class="bar"><span>${esc(PHASE_NAME[k] || k)}</span><i><u style="width:${(v.calls / max) * 100}%"></u></i><b>${fmtN(v.calls)}</b></div>`).join("")}</div>
    ${free ? `<div class="quota muted">من الحد المجاني اليومي (نحو ${FREE_DAILY} رد): استهلكت هذه المهمة ${Math.round((u.calls / FREE_DAILY) * 100)}%.</div>` : ""}`;
}

function suggestFollowUps(r) {
  const out = [];
  const b = budgetIn(r.goal) || budgetIn(r.result);
  if (b) {
    const unit = b.unit || "ريال";
    [0.75, 1.5].forEach((f) => out.push(`غيّر الميزانية إلى ${money(Math.round((b.value * f) / 1000) * 1000)} ${unit}، وحدّث النتائج المتأثرة فقط`));
  }
  const res = r.result || "";
  if (!/مخاطر/.test(res)) out.push("أضف تحليلًا للمخاطر وخطط التخفيف");
  if (/منافس/.test(res + r.goal)) out.push("قارن أقوى منافسَين بالتفصيل في جدول");
  if (/خطة|تنفيذ/.test(res + r.goal) && !/جدول زمني|أسبوع/.test(res)) out.push("حوّل خطة التنفيذ إلى جدول زمني أسبوعي");
  out.push("لخّص التقرير في نقاط لمتخذ القرار");
  return out.slice(0, 5);
}
function followHint() {
  const g = $("#followGoal").value, h = $("#followHint");
  const b = /ميزاني/.test(g) && /غيّ?ر|عدّ?ل|حدّ?ث|اجعل|ارفع|خفّ?ض|إلى/.test(g) ? budgetIn(g) : null;
  h.classList.toggle("hidden", !b);
  if (b) h.innerHTML = `ستُعامل كمتابعة لتغيير الميزانية إلى <b>${money(b.value)} ${esc(b.unit)}</b>: يُعاد حساب المهام المعتمدة على الميزانية فقط، ويبقى البحث والمنافسون والأسعار كما هي. هذا أقل استهلاكًا بكثير من إعادة الدراسة.`;
}
$("#followGoal").addEventListener("input", followHint);

function renderResult(r) {
  $("#result").className = "md";
  const notes = [];
  if (r.status === "interrupted") notes.push('<div class="note bad">انقطعت هذه المهمة قبل أن تكتمل لأن الخادم أُعيد تشغيله أو توقف. ما يظهر أدناه هو آخر ما حُفظ منها.</div>');
  if (r.pause && String(r.status).startsWith("paused")) {
    const when = r.pause.resume_after ? new Date(r.pause.resume_after * 1000).toLocaleString("ar-SA") : "";
    const k = r.pause.kept || {};
    notes.push(`<div class="note warn"><b>توقفت المهمة مؤقتًا ولم تفشل.</b> ${esc(r.pause.message || "")}<br>
      العمل المنجز محفوظ: ${(k.tasks_done || []).length} مهام مكتملة، ${k.readings ?? 0} مصادر مقروءة. توقفت عند مرحلة «${esc(phaseName(r.pause.phase || r.pause.step))}».${when ? ` يُتوقع توفر النموذج بعد ${esc(when)}.` : ""}</div>`);
  }
  if (RESUMABLE.includes(r.status)) {
    const kept = Object.values(r.tasks || {}).filter((t) => !t.parent && t.status === "done").length;
    const sections = (r.report?.sections || []).length;
    notes.push(`<p class="row-actions"><button id="resumeBtn" class="primary" type="button">أكمل من حيث توقفت</button> <button id="rerunBtn" class="ghost" type="button">ابدأ من جديد</button>
      <small class="muted">${kept ? `الاستكمال يحتفظ بـ ${kept} مهام مكتملة${sections ? ` و${sections} أقسام من التقرير` : ""}، ولا يعيد البحث المحفوظ.` : "لا توجد مهام مكتملة؛ الاستكمال يعيد الخطة نفسها دون تخطيط جديد."}</small></p>`);
  }
  if (r.follow_up_of) notes.push(`<p class="muted small">متابعة للمهمة <a href="#" data-open="${esc(r.follow_up_of)}">${esc(r.follow_up_of)}</a>، وقد بُنيت على نتيجتها.</p>`);
  if (r.resumed) notes.push(`<p class="muted small">استُكملت من المهمة <a href="#" data-open="${esc(r.resumed.from)}">${esc(r.resumed.from)}</a>، وأُعيد استخدام ${r.resumed.reused?.length ?? 0} مهمة مُتحقق منها.</p>`);
  const file = (r.files || [])[0];
  if (file) notes.push(file.verified
    ? `<div class="note ok">الملف النهائي <b>${esc(file.name)}</b> جاهز (${(file.bytes / 1024).toFixed(1)} ك.ب)، وتحقق النظام من وجوده وسلامته (SHA-256 <code>${esc(String(file.sha256).slice(0, 12))}…</code>).</div>`
    : '<div class="note bad">تعذر إنشاء الملف النهائي أو التحقق منه؛ التقرير محفوظ ويمكن إعادة بنائه بزر «تنزيل التقرير».</div>');
  if (r.saved === false) notes.push('<div class="note bad">تنبيه: تعذر حفظ هذه المهمة في السجل الدائم، وقد تختفي إذا أُعيد تشغيل الخادم. انسخ النتيجة إن احتجتها.</div>');
  $("#result").innerHTML = notes.join("") + (r.result ? md(r.result) : `<p class="err">${esc(r.error || "لا يوجد جواب بعد")}</p>`);
  $$("#result a[data-open]").forEach((a) => (a.onclick = (ev) => { ev.preventDefault(); openRun(a.dataset.open); }));
  // table of contents from the report's headings, marking sections that failed review
  const heads = $$("#result h3[id]");
  const failed = new Set((r.report?.sections || []).filter((s) => s.kind === "task" && s.status !== "verified").map((s) => s.title));
  $("#toc").classList.toggle("hidden", heads.length < 3);
  $("#toc").innerHTML = heads.map((h) => `<a href="#${h.id}" class="${failed.has(h.textContent) ? "warn" : ""}">${esc(h.textContent.slice(0, 40))}</a>`).join("");
  $$("#toc a").forEach((a) => (a.onclick = (ev) => { ev.preventDefault(); document.getElementById(a.getAttribute("href").slice(1))?.scrollIntoView({behavior: "smooth"}); }));
  const rb = $("#rerunBtn");
  if (rb) rb.onclick = () => { $("#goal").value = r.goal; analyze(); startRun(); };
  const sb = $("#resumeBtn");
  if (sb) sb.onclick = () => startRun(r.id);
  const canFollow = !!r.result || Object.values(r.tasks || {}).some((t) => t.status === "done");
  $("#followCard").classList.toggle("hidden", !canFollow);
  if (canFollow) {
    const sug = suggestFollowUps(r);
    $("#followSuggest").innerHTML = sug.map((x) => `<button type="button" class="chip">${esc(x)}</button>`).join("");
    $$("#followSuggest button").forEach((b, i) => (b.onclick = () => { $("#followGoal").value = sug[i]; followHint(); $("#followGoal").focus(); }));
  }
  $("#followBtn").onclick = () => { const g = $("#followGoal").value; if (g.trim()) { $("#followGoal").value = ""; followHint(); startRun(null, r.id, g); } else $("#followGoal").focus(); };
  $("#exportBtn").classList.toggle("hidden", !r.result);
  $("#copyBtn").classList.toggle("hidden", !r.result);
  $("#copyBtn").onclick = () => navigator.clipboard?.writeText(r.result).then(() => toast("نُسخ التقرير"), () => toast("تعذر النسخ", "bad"));
  $("#exportBtn").onclick = async () => {
    try {
      const res = await fetch(`/api/runs/${r.id}/export`, {headers: {"X-Client-Key": clientKey()}});
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const name = (res.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/)?.[1] || `agos-report-${r.id}.md`;
      const url = URL.createObjectURL(await res.blob()), a = document.createElement("a");
      a.href = url; a.download = name; document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      toast(res.headers.get("X-AGOS-File") === "stored" ? "نُزّل الملف المحفوظ والمتحقق منه" : "نُزّل التقرير (أُعيد بناؤه من البيانات المحفوظة)");
    } catch (e) { toast("تعذر تنزيل التقرير: " + e.message, "bad"); }
  };
  if (!$("#log").children.length && r.events?.length) r.events.forEach(onEvent);
  const v = r.verdict || {};
  $("#verdict").innerHTML = v.draft ? '<span class="badge s-failed">مسودة غير متحقق منها</span>' : v.score != null ? `<span class="badge ${v.pass ? "s-done" : "s-failed"}">تحقق نهائي ${v.score}/10</span>` : "";
  loadHistory();
  loadMemory();
}

const CLARIFY_LABEL = {subject: "المنتج أو المشروع", budget: "الميزانية", market: "السوق"};
function renderClarify(r) {
  const card = $("#clarifyCard"), waiting = r.status === "running" && r.awaiting === "clarification";
  const qs = (r.brief?.missing || []);
  card.classList.toggle("hidden", !waiting && r.status !== "needs_input");
  if (r.status === "needs_input") {  // timed out: nothing ran; offer to put the answers into a new request
    card.dataset.run = "";
    $("#clarifyQs").innerHTML = `<p>${esc(r.result || "")}</p>`;
    $("#clarifyBtn").textContent = "عدّل الطلب وأعد البدء"; $("#assumeBtn").classList.add("hidden");
    $("#clarifyBtn").onclick = () => { $("#goal").value = r.goal; analyze(); $("#goal").scrollIntoView({behavior: "smooth", block: "center"}); $("#goal").focus(); };
    return;
  }
  if (!waiting || card.dataset.run === r.id) return;
  card.dataset.run = r.id; $("#clarifyMsg").textContent = ""; $("#assumeBtn").classList.remove("hidden");
  $("#clarifyBtn").textContent = "ابدأ بهذه المعلومات";
  $("#clarifyQs").innerHTML = qs.map((q) => `<label class="field"><b>${esc(q.question)}${q.blocking ? "" : ' <small class="muted">(اختياري)</small>'}</b>
      <small class="muted">${esc(q.why)}</small>
      <input type="text" maxlength="300" data-key="${esc(q.key)}" placeholder="${esc(q.example || "")}" ${q.blocking ? "required" : ""}></label>`).join("");
  const send = async (assume) => {
    const answers = Object.fromEntries($$("#clarifyQs input").map((i) => [i.dataset.key, i.value.trim()]).filter(([, v]) => v));
    if (!assume && qs.some((q) => q.blocking && !answers[q.key])) { $("#clarifyMsg").textContent = "أجب عن الأسئلة الأساسية، أو اختر المتابعة بافتراضات."; return; }
    $("#clarifyBtn").disabled = $("#assumeBtn").disabled = true;
    try {
      await api(`/api/runs/${r.id}/clarify`, {method: "POST", body: JSON.stringify({answers, assume})});
      card.classList.add("hidden"); scheduleRefresh(); toast(assume ? "يتابع الفريق بافتراضات موسومة" : "بدأ الفريق بمعلوماتك");
    } catch (e) { $("#clarifyMsg").textContent = e.message; }
    $("#clarifyBtn").disabled = $("#assumeBtn").disabled = false;
  };
  $("#clarifyBtn").onclick = () => send(false);
  $("#assumeBtn").onclick = () => send(true);
  $("#clarifyQs input")?.focus();
}

function renderApproval(r) {
  const card = $("#approvalCard"), waiting = r.status === "running" && r.awaiting === "plan";
  card.classList.toggle("hidden", !waiting);
  if (!waiting || card.dataset.run === r.id) return;
  card.dataset.run = r.id; $("#approvalNote").value = ""; $("#approvalMsg").textContent = "";
  $("#approvalTasks").innerHTML = Object.values(r.tasks).filter((t) => !t.parent).map((t) =>
    `<li><label class="check"><input type="checkbox" checked data-id="${esc(t.id)}"> <b>${esc(t.id)}</b> ${esc(t.title)} <span class="badge">${esc(ROLE[t.role] || t.role)}</span>${t.depends_on?.length ? ` <small class="muted">يعتمد على ${esc(t.depends_on.join("، "))}</small>` : ""}</label>
      <details><summary class="muted">التعليمات ومعيار القبول</summary><p>${esc(t.instructions)}</p><p class="muted">القبول: ${esc(t.acceptance)}</p></details></li>`).join("");
  const send = async (action) => {
    const drop = $$("#approvalTasks input:not(:checked)").map((i) => i.dataset.id);
    $("#approveBtn").disabled = $("#rejectBtn").disabled = true;
    try {
      await api(`/api/runs/${r.id}/plan`, {method: "POST", body: JSON.stringify({action, drop, note: $("#approvalNote").value || null})});
      card.classList.add("hidden"); scheduleRefresh(); toast(action === "approve" ? "اعتُمدت الخطة" : "رُفضت الخطة");
    } catch (e) { $("#approvalMsg").textContent = e.message; }
    $("#approveBtn").disabled = $("#rejectBtn").disabled = false;
  };
  $("#approveBtn").onclick = () => send("approve");
  $("#rejectBtn").onclick = () => send("reject");
}

// ---------- memory ----------
const KIND = {profile: "عنك", project: "مشروع", decision: "قرار", finding: "معلومة", task: "مهمة سابقة", note: "تنبيه"};
const NATURE = {user_stated: ["قلتَه أنت", "s-done"], user_confirmed: ["أكدتَه", "s-done"], external_source: ["مصدر خارجي", "s-running"],
  task_output: ["نتيجة/توصية الفريق", "s-running"], inferred: ["استنتاج", "s-pending"], assumption: ["افتراض/تقدير", "s-pending"],
  scenario: ["اختباري", "s-failed"], unverified: ["مصدر غير مؤكد", "s-pending"]};
const conf = (i) => i.confidence != null ? `<small class="muted" title="درجة الثقة تُحسب من طبيعة المعلومة">ثقة ${Math.round(i.confidence * 100)}%</small>` : "";
const safeUrl = (u) => /^https?:\/\//i.test(u || "") ? u : null;
const proof = (i) => !i.evidence ? "" : i.nature === "external_source" && safeUrl(i.evidence)
  ? `<small class="muted">المصدر: <a href="${esc(i.evidence)}" target="_blank" rel="noopener noreferrer">${esc(i.evidence.slice(0, 60))}</a></small>`
  : `<small class="muted">نصك: «${esc(i.evidence)}»</small>`;
const natureBadge = (n) => `<span class="badge ${(NATURE[n] || NATURE.unverified)[1]}">${(NATURE[n] || NATURE.unverified)[0]}</span>`;
const memLine = (i) => `${natureBadge(i.nature)}<span class="badge">${esc(KIND[i.kind] || i.kind)}</span>${i.subject ? `<span class="badge">${esc(i.subject)}</span>` : ""}<span>${esc(i.content)}</span>`;
async function loadMemory() {
  const sp = memSpace();
  if (!sp) { $("#memList").innerHTML = '<li class="empty"><b>الذاكرة معطلة</b>فعّلها من الإعدادات ليتذكر المنسق مشاريعك.</li>'; $("#memCount").textContent = ""; return; }
  let d;
  try { d = await api("/api/memory", {headers: {"X-Memory-Space": sp}}); } catch (e) { $("#memList").innerHTML = `<li class="err">${esc(e.message)}</li>`; return; }
  $("#memCount").textContent = d.count ? `(${fmtN(d.count)})` : "";
  $("#memList").innerHTML = d.items.map((i) => `<li>${memLine(i)}
    <small class="muted" title="${esc(i.source_goal || "")}">${new Date(i.created_at * 1000).toLocaleDateString("ar-SA")}${i.run_id ? " · مهمة " + esc(i.run_id) : ""}</small>
    ${conf(i)} ${proof(i)}
    <span class="mem-actions"><button class="ghost sm edit" type="button" data-id="${esc(i.id)}">تعديل</button>${i.nature !== "scenario" ? `<button class="ghost sm test" type="button" data-id="${esc(i.id)}">بيانات اختبار</button>` : ""}<button class="ghost sm danger del" type="button" data-id="${esc(i.id)}">حذف</button></span></li>`).join("")
    || '<li class="empty"><b>لا شيء بعد</b>سيبدأ المنسق بالتذكر بعد أول مهمة.</li>';
  $$("#memList .del").forEach((b) => (b.onclick = async () => {
    await api(`/api/memory/${encodeURIComponent(b.dataset.id)}`, {method: "DELETE", headers: {"X-Memory-Space": sp}}).then(() => toast("حُذفت المعلومة"), (e) => toast("لم يُحذف: " + e.message, "bad"));
    loadMemory();
  }));
  const patch = (id, body) => api(`/api/memory/${encodeURIComponent(id)}`, {method: "PATCH", body: JSON.stringify(body),
    headers: {"X-Memory-Space": sp}}).then(() => toast("حُفظ التعديل"), (e) => toast(e.message, "bad")).then(loadMemory);
  $$("#memList .edit").forEach((b) => (b.onclick = async () => {
    const it = d.items.find((x) => x.id === b.dataset.id);
    const v = await ask({title: "صحّح المعلومة", text: "ستُعلَّم بأنك أكدتها.", input: it.content, ok: "احفظ"});
    if (v && v.trim() !== it.content) patch(it.id, {content: v.trim()});
  }));
  $$("#memList .test").forEach((b) => (b.onclick = () => patch(b.dataset.id, {nature: "scenario"})));
}
$("#memRefresh").onclick = loadMemory;
$("#memClear").onclick = async () => {
  const sp = memSpace();
  if (!sp) return;
  if (!(await ask({title: "مسح كل ذاكرة المنسق؟", text: "لا يمكن التراجع عن ذلك.", ok: "امسح الكل", danger: true}))) return;
  await api("/api/memory", {method: "DELETE", headers: {"X-Memory-Space": sp}}).then(() => toast("مُسحت الذاكرة"), (e) => toast("لم يُمسح: " + e.message, "bad"));
  loadMemory();
};

// ---------- history ----------
let RUNS = [], histFilter = "all";
const FILTERS = {all: () => true, done: (r) => r.status === "done", paused: (r) => String(r.status).startsWith("paused"),
  running: (r) => r.status === "running", bad: (r) => ["failed", "partial", "interrupted", "cancelled"].includes(r.status)};
async function loadHistory() {
  RUNS = await api("/api/runs").catch(() => RUNS);
  renderHistory();
}
function ago(ts) {
  if (!ts) return "";
  const s = Date.now() / 1000 - ts;
  return s < 3600 ? `قبل ${Math.max(1, Math.round(s / 60))} د` : s < 86400 ? `قبل ${Math.round(s / 3600)} س` : new Date(ts * 1000).toLocaleDateString("ar-SA");
}
function renderHistory() {
  const q = $("#histSearch").value.trim();
  const list = RUNS.filter(FILTERS[histFilter]).filter((r) => !q || (r.goal || "").includes(q) || r.id.includes(q));
  $("#history").innerHTML = list.map((r) => `<li data-id="${esc(r.id)}" class="${r.id === current ? "active" : ""}"><button type="button">
      <span class="badge s-${RUN_CLASS[r.status] || "failed"}">${esc(RUN_SHORT[r.status] || RUN_STATUS[r.status] || r.status)}</span>
      <span class="g">${r.follow_up_of ? "↩ " : ""}${esc((r.goal || "").slice(0, 140))}</span>
      <span class="m">${ago(r.created_at)} · ${r.tasks_done ?? 0}/${r.tasks_total ?? 0} مهام${String(r.status).startsWith("paused") ? " · قابلة للاستكمال" : ""}</span></button></li>`).join("")
    || (RUNS.length ? '<li class="empty">لا توجد مهام تطابق البحث.</li>' : '<li class="empty"><b>لا مهام بعد</b>اكتب طلبك في الأعلى وسيظهر هنا.</li>');
  $$("#history li[data-id] button").forEach((b) => (b.onclick = () => openRun(b.parentElement.dataset.id)));
}
$("#histSearch").addEventListener("input", renderHistory);
$$("#histFilter button").forEach((b) => (b.onclick = () => {
  histFilter = b.dataset.f; $$("#histFilter button").forEach((x) => x.classList.toggle("on", x === b)); renderHistory();
}));

// ---------- start ----------
$("#examples").innerHTML = EXAMPLES.map((x) => `<button type="button" class="chip" title="${esc(x)}">${esc(x.slice(0, 42))}…</button>`).join("");
$$("#examples button").forEach((b, i) => (b.onclick = () => { $("#goal").value = EXAMPLES[i]; analyze(); $("#goal").focus(); }));
loadSettings();
saveSettings();
$("#clientKeyField").value = clientKey();
$("#clientKeyField").addEventListener("change", (ev) => {
  const v = ev.target.value.trim();
  if (/^[A-Za-z0-9_-]{20,200}$/.test(v)) { store.set("ah_client_key", v); loadHistory(); toast("تم تغيير مفتاح المهام"); }
  else { toast("المفتاح غير صالح (20 حرفًا على الأقل: حروف إنجليزية وأرقام و_ و-).", "bad"); ev.target.value = clientKey(); }
});
const draft = store.get("ah_draft", "");
if (draft) $("#goal").value = draft;
checkHealth();
setInterval(checkHealth, 60000);
loadHistory();
loadMemory();
analyze();
const hashRun = () => (location.hash.match(/run=([A-Za-z0-9_-]{6,64})/) || [])[1];
if (hashRun()) openRun(hashRun());
window.addEventListener("hashchange", () => { const id = hashRun(); if (id && id !== current) openRun(id); });
setInterval(() => { if (current && lastRun?.status === "running") scheduleRefresh(); }, 15000);
