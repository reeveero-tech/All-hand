const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const STATUS = {pending:"بانتظار", running:"قيد التنفيذ", done:"مُتحقق منها", failed:"فشلت", skipped:"لم تُنفذ"};
const EXAMPLES = [
  "ادرس سوق المقاهي المختصة في جدة واقترح خطة إطلاق بميزانية 200 ألف ريال",
  "اكتب دالة Python تتحقق من صحة رقم الآيبان السعودي مع اختبارات",
  "قارن بين أفضل 3 منصات متاجر إلكترونية للسوق السعودي من حيث التكلفة والمزايا",
  "اشرح لطالب ثانوي كيف يعمل التشفير بالمفتاح العام",
];

// ---------- settings ----------
const fields = ["apiKey", "baseUrl", "model", "accessCode", "maxAgents"];
function loadSettings() {
  const s = JSON.parse(localStorage.getItem("ah_settings") || "{}");
  fields.forEach((f) => { if (s[f]) $("#" + f).value = s[f]; });
  const p = s.provider || "free";
  document.querySelector(`input[name=provider][value=${p}]`).checked = true;
  syncProvider();
}
function saveSettings() {
  const s = {provider: provider()};
  fields.forEach((f) => (s[f] = $("#" + f).value.trim()));
  localStorage.setItem("ah_settings", JSON.stringify(s));
}
const provider = () => document.querySelector("input[name=provider]:checked").value;
function syncProvider() {
  document.querySelectorAll("[data-for]").forEach((el) => el.classList.toggle("hidden", !el.dataset.for.split(" ").includes(provider())));
}
document.querySelectorAll("input[name=provider]").forEach((r) => r.addEventListener("change", () => { syncProvider(); saveSettings(); }));
fields.forEach((f) => $("#" + f).addEventListener("change", saveSettings));
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

async function api(path, opts = {}) {
  const r = await fetch(path, {headers: {"Content-Type": "application/json"}, ...opts});
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
  return body;
}

$("#runBtn").onclick = async () => {
  const goal = $("#goal").value.trim();
  if (!goal) return $("#goal").focus();
  saveSettings();
  const s = JSON.parse(localStorage.getItem("ah_settings"));
  const body = {goal, provider: s.provider, api_key: s.apiKey || null, base_url: s.baseUrl || null,
    model: s.model || null, access_code: s.accessCode || null, max_agents: s.maxAgents ? Number(s.maxAgents) : null};
  $("#runBtn").disabled = true;
  try {
    const {id} = await api("/api/runs", {method: "POST", body: JSON.stringify(body)});
    open(id);
  } catch (e) {
    alert(e.message);
    if (/مفتاح|رمز|key/i.test(e.message)) $("#settings").classList.remove("hidden");
    $("#runBtn").disabled = false;
  }
};
$("#cancelBtn").onclick = () => current && api(`/api/runs/${current}/cancel`, {method: "POST"}).catch(() => {});

function open(id) {
  current = id;
  if (es) es.close();
  $("#live").classList.remove("hidden");
  $("#log").innerHTML = ""; $("#board").innerHTML = ""; $("#verdict").textContent = "";
  $("#result").className = "md muted"; $("#result").textContent = "قيد العمل...";
  refresh();
  es = new EventSource(`/api/runs/${id}/events`);
  es.onmessage = (m) => onEvent(JSON.parse(m.data));
  es.onerror = () => { es.close(); refresh(); };
}

const LABEL = {
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
  run_error: (e) => `خطأ: ${e.error}`,
  run_cancelled: () => "أُوقفت المهمة",
  run_finished: (e) => `انتهى: ${e.status} — ${e.agents_spawned} وكيلًا، ${e.usage.calls} استدعاء نموذج`,
};

function onEvent(e) {
  const li = document.createElement("li");
  li.innerHTML = `<span class="t">${e.t.toFixed(1)}s</span>${esc((LABEL[e.type] || (() => e.type))(e))}`;
  if (/error|failed|skipped/.test(e.type) || e.pass === false) li.classList.add("err");
  $("#log").prepend(li);
  if (["plan", "task_finished", "task_skipped", "agent_spawned", "verified", "run_finished"].includes(e.type)) scheduleRefresh();
  if (e.type === "run_finished") { es.close(); setTimeout(refresh, 300); }
}
function scheduleRefresh() { clearTimeout(refreshTimer); refreshTimer = setTimeout(refresh, 400); }

async function refresh() {
  if (!current) return;
  let r;
  try { r = await api(`/api/runs/${current}`); } catch { return; }
  const secs = ((r.finished_at || Date.now() / 1000) - r.created_at).toFixed(0);
  $("#stats").innerHTML = [["الحالة", {running: "يعمل", done: "اكتمل", partial: "اكتمل جزئيًا", failed: "فشل", cancelled: "أُوقف"}[r.status] || r.status],
    ["وكلاء أُنشئوا", `${r.agents_spawned} / ${r.max_agents}`], ["مهام مُتحقق منها", `${r.tasks_done} / ${r.tasks_total}`],
    ["مهام فشلت", r.tasks_failed], ["الزمن", secs + " ث"], ["استدعاءات النموذج", r.usage?.calls ?? "…"]]
    .map(([k, v]) => `<div class="stat"><b>${esc(v)}</b><span>${k}</span></div>`).join("");
  $("#board").innerHTML = Object.values(r.tasks).map((t) => {
    const last = t.attempts.at(-1)?.verdict;
    return `<div class="task ${t.depth ? "sub" : ""}"><header><span class="badge">${esc(t.id)}</span><span class="title">${esc(t.title)}</span>
      <span class="badge">${esc(t.role)}</span><span class="badge s-${t.status}">${STATUS[t.status] || t.status}</span></header>
      <div class="meta">${t.decompose ? "فريق فرعي · " : ""}${t.depends_on.length ? "يعتمد على: " + esc(t.depends_on.join("، ")) + " · " : ""}محاولات: ${t.attempts.length}${last && last.score != null ? ` · تقييم المدقق: ${last.score}/10` : ""}</div>
      ${last && !last.pass && last.issues?.length ? `<div class="meta err">${esc(last.issues.join(" · "))}</div>` : ""}
      ${t.output ? `<details><summary>الناتج</summary><div class="md">${md(t.output)}</div></details>` : ""}</div>`;
  }).join("") || '<p class="muted">المنسق يخطط...</p>';
  const busy = r.status === "running";
  $("#runBtn").disabled = busy; $("#cancelBtn").classList.toggle("hidden", !busy);
  if (!busy) {
    $("#result").className = "md";
    $("#result").innerHTML = r.result ? md(r.result) : `<p class="err">${esc(r.error || "لا يوجد جواب")}</p>`;
    const v = r.verdict || {};
    $("#verdict").innerHTML = v.score != null ? `<span class="badge ${v.pass ? "s-done" : "s-failed"}">تحقق نهائي ${v.score}/10</span>` : "";
    loadHistory();
  }
}

async function loadHistory() {
  const runs = await api("/api/runs").catch(() => []);
  $("#history").innerHTML = runs.map((r) => `<li data-id="${r.id}"><span class="badge s-${r.status === "done" ? "done" : r.status === "running" ? "running" : "failed"}">${esc(r.status)}</span>${esc(r.goal.slice(0, 120))}<small class="muted">${r.agents_spawned} وكيل</small></li>`).join("") || '<li class="muted">لا شيء بعد</li>';
  document.querySelectorAll("#history li[data-id]").forEach((li) => (li.onclick = () => open(li.dataset.id)));
}

$("#examples").innerHTML = EXAMPLES.map((x) => `<button>${esc(x.slice(0, 38))}…</button>`).join("");
document.querySelectorAll("#examples button").forEach((b, i) => (b.onclick = () => ($("#goal").value = EXAMPLES[i])));
loadSettings();
loadHistory();
