// R-177 (M8 P6 — ثاني دفعة من R-193، D-133): مولد حمل لكل خدمة.
// نفس مبادئ R-176: نموذج مفتوح (constant-arrival-rate)، فصل المولد عن
// الخدمة (HTTP فقط إلى TARGET_URL)، كل المعاملات من البيئة (يضبطها
// scripts/r177_per_service.py من benchmarks/r177/services.json)، والعتبات
// تُحقن من الإطار — النجاح/الفشل صريح لا جوفاء.
//
// SERVICE يحدد شكل الطلب (جرد الخدمات في benchmarks/r177/services.json).
// الدفعة 3 (D-134): العتبات معتمدة بقرار المالك وتُحقن من العدّاء (لا تُرخى)؛
// حدود الإيقاف تُقيَّم حيًا في العدّاء من مخرجات --out json لهذا المولد.

import http from 'k6/http';
import { check, sleep } from 'k6';

const TARGET_URL = __ENV.TARGET_URL || 'http://127.0.0.1:8000';
const DURATION = __ENV.DURATION || '20s';
const RATE = Number(__ENV.RATE || 5);
// k6 يقبل أعدادًا صحيحة فقط في rate — المعدلات دون 1/ث تُعبر عنها
// بمضاعف لكل دقيقة (timeUnit=60s): 0.1/ث => 6 لكل دقيقة
const SUB_SECOND = !Number.isInteger(RATE) || RATE < 1;
const RATE_INT = SUB_SECOND ? Math.max(1, Math.round(RATE * 60)) : RATE;
const TIME_UNIT = SUB_SECOND ? '60s' : '1s';
const MAX_VUS = Number(__ENV.MAX_VUS || 8);
const API_KEY = __ENV.API_KEY || '';
const SERVICE = __ENV.SERVICE || 'api-health';
const PROFILE = __ENV.PROFILE || 'baseline';

const rawThresholds = JSON.parse(__ENV.THRESHOLDS || '{}');
const thresholds = {};
for (const [metric, expr] of Object.entries(rawThresholds)) {
  thresholds[metric] = Array.isArray(expr) ? expr : [expr];
}

export const options = {
  scenarios: {
    load: {
      executor: 'constant-arrival-rate',
      rate: RATE_INT,
      timeUnit: TIME_UNIT,
      duration: DURATION,
      preAllocatedVUs: Math.min(MAX_VUS, 5),
      maxVus: MAX_VUS,
    },
  },
  thresholds,
};

const AUTH = { 'Content-Type': 'application/json', 'X-API-Key': API_KEY };

// رسائل بلا LLM (مزيج R-131 — نفس مزيج R-176)
const CHAT_MESSAGES = [
  'sum these numbers: [1, 2, 3, 4, 5]',
  'validate this JSON: {"name": "string", "age": 3}',
  'pick a dark mode color palette from #3366ff for the dashboard',
  'review this code for bugs: def f(x): return x*2',
];

let n = 0;

function chatReq() {
  const msg = CHAT_MESSAGES[(n++) % CHAT_MESSAGES.length];
  const res = http.post(
    `${TARGET_URL}/api/chat`, JSON.stringify({ message: msg }),
    { headers: AUTH, tags: { profile: PROFILE, endpoint: 'chat' } });
  check(res, {
    'chat status 200': (r) => r.status === 200,
    'chat body ok': (r) => r.status === 200 && (r.json('outcome.ok') === true
      || r.json('ok') === true),
  });
}

function healthReq() {
  const paths = ['/health', '/health/ready', '/metrics', '/api/system/status', '/api'];
  const res = http.get(`${TARGET_URL}${paths[(n++) % paths.length]}`,
    { tags: { profile: PROFILE, endpoint: 'health' } });
  check(res, { 'health status 200': (r) => r.status === 200 });
}

function agentsReq() {
  const res = (n++ % 2 === 0)
    ? http.get(`${TARGET_URL}/api/agents`, { tags: { profile: PROFILE, endpoint: 'agents' } })
    : http.get(`${TARGET_URL}/api/specializations`, { tags: { profile: PROFILE, endpoint: 'specializations' } });
  check(res, { 'agents status 200': (r) => r.status === 200 });
}

function tasksReadReq() {
  const res = http.get(`${TARGET_URL}/api/tasks?limit=50`,
    { headers: AUTH, tags: { profile: PROFILE, endpoint: 'tasks-list' } });
  check(res, { 'tasks list 200': (r) => r.status === 200 });
  // كل تكرار رابع: تفاصيل أول مهمة من القائمة (إن وُجدت) — مسار القراءة المفرد
  if (n++ % 4 === 0 && res.status === 200) {
    const first = (res.json('tasks') || [])[0];
    if (first && first.id) {
      const det = http.get(`${TARGET_URL}/api/tasks/${first.id}`,
        { headers: AUTH, tags: { profile: PROFILE, endpoint: 'task-detail' } });
      // الدفعة 3: مهمة أُدرجت لمستأجرها يجب أن تُقرأ — 200 فقط (كان يقبل 404)
      check(det, { 'task detail 200': (r) => r.status === 200 });
    }
  }
}

function tasksSubmitReq() {
  const res = http.post(
    `${TARGET_URL}/api/tasks`,
    JSON.stringify({ specialization: 'validator', prompt: 'validate this JSON: {"ok": true, "n": 1}' }),
    { headers: AUTH, tags: { profile: PROFILE, endpoint: 'tasks-submit' } });
  check(res, { 'task accepted 202': (r) => r.status === 202 });
}

function taskLifecycleReq() {
  const created = http.post(
    `${TARGET_URL}/api/tasks`,
    JSON.stringify({ specialization: 'validator', prompt: 'validate this JSON: {"ok": true, "n": 1}' }),
    { headers: AUTH, tags: { profile: PROFILE, endpoint: 'lifecycle-create' } });
  const okCreate = check(created, { 'task accepted 202': (r) => r.status === 202 });
  if (okCreate) {
    const id = created.json('task_id');
    if (id) {
      const cancel = http.post(
        `${TARGET_URL}/api/tasks/${id}/cancel`, '',
        { headers: AUTH, tags: { profile: PROFILE, endpoint: 'lifecycle-cancel' } });
      // 200 = أُلغيت فعليًا؛ 409 = انتهت قبل الإلغاء (المسار نُفّذ كلاهما دليل)
      check(cancel, { 'cancel handled': (r) => r.status === 200 || r.status === 409 });
    }
  }
}

function memoryReq() {
  const res = (n++ % 2 === 0)
    ? http.get(`${TARGET_URL}/api/memory?limit=20`, { headers: AUTH, tags: { profile: PROFILE, endpoint: 'memory' } })
    : http.get(`${TARGET_URL}/api/learning`, { headers: AUTH, tags: { profile: PROFILE, endpoint: 'learning' } });
  check(res, { 'memory status 200': (r) => r.status === 200 });
}

function adminToolsReq() {
  const res = http.get(`${TARGET_URL}/api/admin/tools`,
    { headers: AUTH, tags: { profile: PROFILE, endpoint: 'admin-tools' } });
  check(res, { 'admin tools 200': (r) => r.status === 200 });
}

function sseReq() {
  // استطلاع الأحداث فقط — تيار SSE لا يقاس هنا: k6 ينتظر جسم الرد
  // كاملًا (مثبت تجريبيًا — تيار حتى timeout)، فزمن اتصال التيار (TTFB
  // حتى الرؤوس) يقيسه مسبار العدّاء _sse_ttfb_probe في عملية منفصلة.
  const res = http.get(`${TARGET_URL}/api/events`,
    { headers: AUTH, tags: { profile: PROFILE, endpoint: 'events-poll' } });
  check(res, { 'events poll 200': (r) => r.status === 200 });
}

function authReq() {
  // المسار السالب: بلا مفتاح — بوابة الرفض نفسها هي المقياس
  const res = http.post(
    `${TARGET_URL}/api/chat`, JSON.stringify({ message: 'auth gate probe' }),
    { headers: { 'Content-Type': 'application/json' },
      tags: { profile: PROFILE, endpoint: 'auth-negative' } });
  check(res, { 'auth rejected 401/403': (r) => r.status === 401 || r.status === 403 });
}

// الدفعة 3: مسارات الواجهة الحقيقية من web/dist المبني (يمررها العدّاء) —
// 200 فقط. بلا بناء لا يُشغَّل هذا المولد أصلًا (التصنيف not_measured في العدّاء):
// الرد 404 ليس قياس واجهة (تصحيح الدفعة 2 التي قبلت 404).
const FRONTEND_PATHS = JSON.parse(__ENV.FRONTEND_PATHS || '["/"]');

function frontendReq() {
  const path = FRONTEND_PATHS[(n++) % FRONTEND_PATHS.length];
  const res = http.get(`${TARGET_URL}${path}`,
    { tags: { profile: PROFILE, endpoint: path === '/' ? 'frontend-root'
      : (path.startsWith('/assets/') ? 'frontend-asset' : 'frontend-spa') } });
  check(res, {
    'frontend 200': (r) => r.status === 200,
    'frontend body non-empty': (r) => r.status === 200 && r.body && r.body.length > 0,
  });
}

const OPS = {
  'api-health': healthReq,
  'api-agents': agentsReq,
  'api-tasks-read': tasksReadReq,
  'api-tasks-submit': tasksSubmitReq,
  'api-task-lifecycle': taskLifecycleReq,
  'api-memory': memoryReq,
  'api-chat': chatReq,
  'api-admin-tools': adminToolsReq,
  'api-sse': sseReq,
  'api-auth': authReq,
  'frontend': frontendReq,
};

// حالات الاستجابة المتوقعة لكل خدمة: خارجها يُعد فشلًا (http_req_failed).
// الإلغاء يقبل 409 (انتهت المهمة قبل الإلغاء)؛ المصادقة تتوقع 401/403؛
// الواجهة 200 فقط (الدفعة 3). تيار SSE لا يُقاس هنا إطلاقًا (مسبار العدّاء).
const EXPECTED = {
  'api-auth': [401, 403],
  'api-task-lifecycle': [200, 202, 409],
};

if (EXPECTED[SERVICE]) {
  http.setResponseCallback(http.expectedStatuses(...EXPECTED[SERVICE]));
}

export default function () {
  const op = OPS[SERVICE];
  if (op) op();
  sleep(0.05);
}
