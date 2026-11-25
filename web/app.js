const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const STATUS = {pending:"بانتظار", running:"قيد التنفيذ", done:"مُتحقق منها", failed:"فشلت", skipped:"لم تُنفذ"};
const RUN_STATUS = {running: "يعمل", done: "اكتمل", partial: "اكتمل جزئيًا", failed: "فشل", cancelled: "أُوقف",
  interrupted: "انقطع"};
const RUN_CLASS = {running: "running", done: "done", partial: "pending", interrupted: "failed", cancelled: "pending"};
const EXAMPLES = [
  "ادرس سوق المقاهي المختصة في جدة واقترح خطة إطلاق بميزانية 200 ألف ريال",
  "اكتب دالة Python تتحقق من صحة رقم الآيبان السعودي مع اختبارات",
  "قارن بين أفضل 3 منصات متاجر إلكترونية للسوق السعودي من حيث التكلفة والمزايا",
  "اشرح لطالب ثانوي كيف يعمل التشفير بالمفتاح العام",
];

// ---------- settings ----------
const fields = ["apiKey", "baseUrl", "model", "accessCode", "maxAgents", "memSpace"];
function loadSettings() {
  const s = JSON.parse(localStorage.getItem("ah_settings") || "{}");
  fields.forEach((f) => { if (s[f]) $("#" + f).value = s[f]; });
  $("#memOn").checked = s.memOn !== false;
  if (!/^[A-Za-z0-9_-]{20,200}$/.test($("#memSpace").value)) $("#memSpace").value = newSpace();
  const p = s.provider || "free";
  document.querySelector(`input[name=provider][value=${p}]`).checked = true;
  syncProvider();
}
function saveSettings() {
  const s = {provider: provider()};
  fields.forEach((f) => (s[f] = $("#" + f).value.trim()));
  s.memOn = $("#memOn").checked;
  localStorage.setItem("ah_settings", JSON.stringify(s));
}
const provider = () => document.querySelector("input[name=provider]:checked").value;
function syncProvider() {
  document.querySelectorAll("[data-for]").forEach((el) => el.classList.toggle("hidden", !el.dataset.for.split(" ").includes(provider())));
}
document.querySelectorAll("input[name=provider]").forEach((r) => r.addEventListener("change", () => { syncProvider(); saveSettings(); }));
fields.forEach((f) => $("#" + f).addEventListener("change", saveSettings));
$("#memOn").addEventListener("change", () => { saveSettings(); loadMemory(); });
$("#memSpace").addEventListener("change", loadMemory);
function newSpace() {
  const b = crypto.getRandomValues(new Uint8Array(16));
  return "mem_" + Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
}
const memSpace = () => ($("#memOn").checked && /^[A-Za-z0-9_-]{20,200}$/.test($("#memSpace").value.trim()) ? $("#memSpace").value.trim() : null);
$("#settingsBtn").onclick = () => $("#settings").classList.toggle("hidden");

// ---------- markdown (small, safe: escape first) ----------
function md(src) {
  const blocks = [];
  let s = esc(src).replace(/```(\w*)\n([\s\S]*?)```/g, (_, l, c) => { blocks.push(`<pre><code>${c}</code></pre>`); return `\u0000${blocks.length - 1}\u0000`; });
  s = s.replace(/^### (.*)$/gm, "<h4>$1</h4>").replace(/^## (.*)$/gm, "<h3>$1</h3>").replace(/^# (.*)$/gm, "<h3>$1</h3>")
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
  s = s.replace(/(?:^\|.*\|\s*$\n?)+/gm, (tbl) => {
    const rows = tbl.trim().split("\n").filter((r) => !/^\|\s*:?-+/.test(r));
    return "<table>" + rows.map((r) => "<tr>" + r.replace(/^\||\|$/g, "").split("|").map((c) => `<td>${c.trim()}</td>`).join("") + "</tr>").join("") + "</table>\n";
  });
  s = s.replace(/(?:^\s*(?:[-*]|\d+\.) .*$\n?)+/gm, (list) => "<ul>" + list.trim().split("\n").map((l) => `<li>${l.replace(/^\s*(?:[-*]|\d+\.) /, "")}</li>`).join("") + "</ul>\n");
  s = s.split(/\n{2,}/).map((p) => (/^\s*<(h\d|ul|table|pre)/.test(p) || p.includes("\u0000") ? p : `<p>${p.replace(/\n/g, "<br>")}</p>`)).join("\n");
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => blocks[i]);
}

// ---------- run ----------
let current = null, es = null, refreshTimer = null;
const t0 = {v: 0};

// Private key of this browser: the server keeps only its hash and shows you only your own tasks.
function clientKey() {
  let k = localStorage.getItem("ah_client_key");
  if (!/^[A-Za-z0-9_-]{20,200}$/.test(k || "")) { k = newSpace().replace("mem_", "cli_"); localStorage.setItem("ah_client_key", k); }
  return k;
}
async function api(path, opts = {}) {
  const r = await fetch(path, {...opts, headers: {"Content-Type": "application/json", "X-Client-Key": clientKey(), ...(opts.headers || {})}});
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
  return body;
}

$("#runBtn").onclick = () => startRun();
async function startRun(resumeFrom = null) {
  const goal = $("#goal").value.trim();
  if (!goal) return $("#goal").focus();
  saveSettings();
  const s = JSON.parse(localStorage.getItem("ah_settings"));
  const body = {goal, provider: s.provider, api_key: s.apiKey || null, base_url: s.baseUrl || null,
    model: s.model || null, access_code: s.accessCode || null, max_agents: s.maxAgents ? Number(s.maxAgents) : null, memory_space: memSpace(), resume_from: resumeFrom};
  $("#runBtn").disabled = true;
  try {
    const {id} = await api("/api/runs", {method: "POST", body: JSON.stringify(body)});
    open(id);
  } catch (e) {
    alert(e.message);
    if (/مفتاح|رمز|key/i.test(e.message)) $("#settings").classList.remove("hidden");
    $("#runBtn").disabled = false;
  }
}
$("#cancelBtn").onclick = () => current && api(`/api/runs/${current}/cancel`, {method: "POST"}).catch(() => {});

function open(id) {
  current = id;
  if (es) es.close();
  $("#live").classList.remove("hidden");
  $("#log").innerHTML = ""; $("#board").innerHTML = ""; $("#verdict").textContent = "";
  $("#result").className = "md muted"; $("#result").textContent = "قيد العمل...";
  refresh();
  es = streamEvents(id, onEvent, () => refresh());
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
          if (data) onMsg(JSON.parse(data));
        }
      }
    } catch (e) { if (e.name === "AbortError") return; }
    onEnd();
  })();
  return {close: () => ctl.abort()};
}

const LABEL = {
  run_interrupted: () => "انقطع التشغيل لأن الخادم يُعاد تشغيله؛ يمكنك استكماله لاحقًا",
  resumed: (e) => `استكمال المهمة ${e.from}: إعادة استخدام ${e.reused.length} مهمة مُتحقق منها، وتنفيذ ${e.remaining.length}`,
  run_started: (e) => `بدأ المنسق (${e.llm.provider}: ${e.llm.model})، ميزانية ${e.max_agents} وكيلًا`,
  planning: (e) => `المنسق يخطط${e.scope !== "root" ? " لفريق فرعي " + e.scope : ""}`,
  plan: (e) => `خطة${e.scope !== "root" ? " فرعية" : ""}: ${e.tasks.length} مهام — ${e.summary}`,
  agent_spawned: (e) => `أُنشئ الوكيل ${e.agent} (${e.role}) للمهمة ${e.task}${e.attempt > 1 ? " — محاولة " + e.attempt : ""}`,
  tool_call: (e) => `${e.agent} يستخدم ${e.tool}: ${JSON.stringify(e.input).slice(0, 140)}`,
  tool_result: (e) => `نتيجة ${e.tool}: ${e.preview.slice(0, 160)}`,
  verified: (e) => `المدقق: ${e.pass ? "قبل" : "رفض"} ناتج ${e.task} (${e.score ?? "-"}/10)${e.issues?.length ? " — " + e.issues[0] : ""}`,
  task_delegated: (e) => `المهمة ${e.task} كبيرة؛ أُسندت إلى فريق فرعي`,
  task_finished: (e) => `المهمة ${e.task}: ${STATUS[e.status]}${e.reason ? " — " + e.reason : ""}`,
  task_skipped: (e) => `تخطي ${e.task}: ${e.reason}`,
  agent_error: (e) => `خطأ عند ${e.agent}: ${e.error}`,
  synthesizing: () => "المنسق يجمع النتائج",
  final_check: (e) => `التحقق النهائي: ${e.pass ? "مقبول" : "مرفوض"} (${e.score}/10)`,
  memory_recalled: (e) => `استرجع المنسق ${e.count} معلومة من الذاكرة (من أصل ${e.total})`,
  memory_saved: (e) => `حُفظ في الذاكرة ${e.count} معلومة${e.superseded ? "، وحُذف " + e.superseded + " قديمة" : ""}`,
  memory_error: (e) => `تعذر الوصول إلى الذاكرة: ${e.error}`,
  run_error: (e) => `خطأ: ${e.error}`,
  run_cancelled: () => "أُوقفت المهمة",
  run_finished: (e) => `انتهى: ${RUN_STATUS[e.status] || e.status} — ${e.agents_spawned} وكيلًا، ${e.usage?.calls ?? 0} استدعاء نموذج${e.saved === false ? " — لم يُحفظ في السجل الدائم" : ""}`,
};

function onEvent(e) {
  const li = document.createElement("li");
  li.innerHTML = `<span class="t">${e.t.toFixed(1)}s</span>${esc((LABEL[e.type] || (() => e.type))(e))}`;
  if (/error|failed|skipped/.test(e.type) || e.pass === false) li.classList.add("err");
  $("#log").prepend(li);
  if (["plan", "task_finished", "task_skipped", "agent_spawned", "verified", "run_finished"].includes(e.type)) scheduleRefresh();
  if (e.type === "run_finished" && es) { es.close(); setTimeout(refresh, 300); }
}
function scheduleRefresh() { clearTimeout(refreshTimer); refreshTimer = setTimeout(refresh, 400); }

async function refresh() {
  if (!current) return;
  let r;
  try { r = await api(`/api/runs/${current}`); } catch { return; }
  const secs = ((r.finished_at || Date.now() / 1000) - r.created_at).toFixed(0);
  $("#stats").innerHTML = [["الحالة", RUN_STATUS[r.status] || r.status],
    ["وكلاء أُنشئوا", `${r.agents_spawned} / ${r.max_agents}`], ["مهام مُتحقق منها", `${r.tasks_done} / ${r.tasks_total}`],
    ["مهام فشلت", r.tasks_failed], ["الزمن", secs + " ث"], ["استدعاءات النموذج", r.usage?.calls ?? "…"]]
    .map(([k, v]) => `<div class="stat"><b>${esc(v)}</b><span>${k}</span></div>`).join("");
  $("#board").innerHTML = Object.values(r.tasks).map((t) => {
    const last = t.attempts.at(-1)?.verdict;
    return `<div class="task ${t.depth ? "sub" : ""}"><header><span class="badge">${esc(t.id)}</span><span class="title">${esc(t.title)}</span>
      <span class="badge">${esc(t.role)}</span><span class="badge s-${t.status}">${STATUS[t.status] || t.status}</span>${t.reused ? '<span class="badge">من التشغيل السابق</span>' : ""}</header>
      <div class="meta">${t.decompose ? "فريق فرعي · " : ""}${t.depends_on.length ? "يعتمد على: " + esc(t.depends_on.join("، ")) + " · " : ""}محاولات: ${t.attempts.length}${last && last.score != null ? ` · تقييم المدقق: ${last.score}/10` : ""}</div>
      ${last && !last.pass && last.issues?.length ? `<div class="meta err">${esc(last.issues.join(" · "))}</div>` : ""}
      ${t.output ? `<details><summary>الناتج</summary><div class="md">${md(t.output)}</div></details>` : ""}</div>`;
  }).join("") || '<p class="muted">المنسق يخطط...</p>';
  const busy = r.status === "running";
  $("#runBtn").disabled = busy; $("#cancelBtn").classList.toggle("hidden", !busy);
  if (!busy) {
    $("#result").className = "md";
    const notes = [];
    if (r.status === "interrupted") notes.push('<p class="err">انقطعت هذه المهمة قبل أن تكتمل، لأن الخادم أُعيد تشغيله أو توقف. ما يظهر أدناه هو آخر ما حُفظ منها.</p>');
    if (["interrupted", "failed", "partial", "cancelled"].includes(r.status)) {
      const kept = Object.values(r.tasks || {}).filter((t) => !t.parent && t.status === "done").length;
      notes.push(`<p><button id="resumeBtn">أكمل من حيث توقفت</button> <button id="rerunBtn" class="ghost">ابدأ من جديد</button>
        <small class="muted">${kept ? `الاستكمال يحتفظ بـ ${kept} من المهام التي اجتازت التحقق ويعيد الباقي.` : "لا توجد مهام مكتملة لإعادة استخدامها؛ الاستكمال سيعيد كل المهام بالخطة نفسها."}</small></p>`);
    }
    if (r.resumed) notes.push(`<p class="muted">استُكملت من المهمة ${esc(r.resumed.from)}، وأُعيد استخدام ${r.resumed.reused.length} مهمة مُتحقق منها.</p>`);
    if (r.saved === false) notes.push('<p class="err">تنبيه: تعذر حفظ هذه المهمة في السجل الدائم، وقد تختفي إذا أُعيد تشغيل الخادم. انسخ النتيجة إن احتجتها.</p>');
    $("#result").innerHTML = notes.join("") + (r.result ? md(r.result) : `<p class="err">${esc(r.error || "لا يوجد جواب")}</p>`);
    const rb = $("#rerunBtn");
    if (rb) rb.onclick = () => { $("#goal").value = r.goal; startRun(); };
    const sb = $("#resumeBtn");
    if (sb) sb.onclick = () => { $("#goal").value = r.goal; startRun(r.id); };
    if (!$("#log").children.length && r.events?.length) r.events.forEach(onEvent);
    const v = r.verdict || {};
    $("#verdict").innerHTML = v.score != null ? `<span class="badge ${v.pass ? "s-done" : "s-failed"}">تحقق نهائي ${v.score}/10</span>` : "";
    loadHistory();
    loadMemory();
  }
  const m = r.memory;
  $("#memRun").classList.toggle("hidden", !m?.enabled);
  if (m?.enabled) {
    const li = (xs) => xs?.length ? "<ul>" + xs.map((i) => `<li><span class="badge">${esc(KIND[i.kind] || i.kind)}</span>${esc(i.content)}</li>`).join("") + "</ul>" : '<p class="muted">لا شيء</p>';
    $("#memRunBody").innerHTML = `<h4>ما تذكّره المنسق</h4>${li(m.recalled)}<h4>ما حفظه بعد المهمة</h4>${busy ? '<p class="muted">بعد الانتهاء...</p>' : li(m.saved)}`;
  }
}

const KIND = {profile: "عنك", project: "مشروع", decision: "قرار", finding: "معلومة", task: "مهمة سابقة"};
async function loadMemory() {
  const sp = memSpace();
  if (!sp) { $("#memList").innerHTML = '<li class="muted">الذاكرة معطلة من الإعدادات</li>'; $("#memCount").textContent = ""; return; }
  let d;
  try { d = await api("/api/memory", {headers: {"X-Memory-Space": sp}}); } catch (e) { $("#memList").innerHTML = `<li class="err">${esc(e.message)}</li>`; return; }
  $("#memCount").textContent = `(${d.count} عنصر)`;
  $("#memList").innerHTML = d.items.map((i) => `<li><span class="badge">${esc(KIND[i.kind] || i.kind)}</span>${esc(i.content)}
    <small class="muted">${new Date(i.created_at * 1000).toLocaleDateString("ar-SA")}</small><button class="ghost del" data-id="${esc(i.id)}">حذف</button></li>`).join("")
    || '<li class="muted">لا شيء بعد. سيبدأ المنسق بالتذكر بعد أول مهمة.</li>';
  document.querySelectorAll("#memList .del").forEach((b) => (b.onclick = async () => {
    await api(`/api/memory/${encodeURIComponent(b.dataset.id)}`, {method: "DELETE", headers: {"X-Memory-Space": sp}}).catch(() => {});
    loadMemory();
  }));
}
$("#memRefresh").onclick = loadMemory;
$("#memClear").onclick = async () => {
  const sp = memSpace();
  if (!sp || !confirm("مسح كل ذاكرة المنسق؟ لا يمكن التراجع.")) return;
  await api("/api/memory", {method: "DELETE", headers: {"X-Memory-Space": sp}}).catch(() => {});
  loadMemory();
};

async function loadHistory() {
  const runs = await api("/api/runs").catch(() => []);
  $("#history").innerHTML = runs.map((r) => `<li data-id="${esc(r.id)}"><span class="badge s-${RUN_CLASS[r.status] || "failed"}">${esc(RUN_STATUS[r.status] || r.status)}</span>${esc(r.goal.slice(0, 120))}<small class="muted">${r.agents_spawned} وكيل</small></li>`).join("") || '<li class="muted">لا شيء بعد</li>';
  document.querySelectorAll("#history li[data-id]").forEach((li) => (li.onclick = () => open(li.dataset.id)));
}

$("#examples").innerHTML = EXAMPLES.map((x) => `<button>${esc(x.slice(0, 38))}…</button>`).join("");
document.querySelectorAll("#examples button").forEach((b, i) => (b.onclick = () => ($("#goal").value = EXAMPLES[i])));
loadSettings();
saveSettings();
$("#clientKeyField").value = clientKey();
$("#clientKeyField").addEventListener("change", (ev) => {
  const v = ev.target.value.trim();
  if (/^[A-Za-z0-9_-]{20,200}$/.test(v)) { localStorage.setItem("ah_client_key", v); loadHistory(); }
  else { alert("المفتاح غير صالح (20 حرفًا على الأقل: حروف إنجليزية وأرقام و_ و-)."); ev.target.value = clientKey(); }
});
loadHistory();
loadMemory();
