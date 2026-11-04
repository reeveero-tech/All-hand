// R-176 (M8 P6 — أول دفعة من R-193): مولد حمل k6 — نموذج مفتوح
// (constant-arrival-rate): الوصول بمعدل مستقل عن زمن استجابة الخدمة.
// المعاملات كلها من البيئة (يضبطها scripts/r176_load_framework.py من
// benchmarks/r176/profiles.json) — لا قيم معلقة هنا.
//
// فصل المولد عن الخدمة: هذا السيناريو يخاطب SUT عبر HTTP فقط (TARGET_URL
// قد يكون خادمًا محليًا في عملية منفصلة أو عقدة مستقلة في البيئة المعزولة).

import http from 'k6/http';
import { check, sleep } from 'k6';

const TARGET_URL = __ENV.TARGET_URL || 'http://127.0.0.1:8000';
const DURATION = __ENV.DURATION || '20s';      // مدة التشغيل
const RATE = Number(__ENV.RATE || 5);           // طلبات/ثانية (open model)
const MAX_VUS = Number(__ENV.MAX_VUS || 8);     // سقف الوحدات الافتراضية
const API_KEY = __ENV.API_KEY || '';            // مفتاح المستأجر (SUT الحقيقي)
const CHAT_SHARE = Number(__ENV.CHAT_SHARE || 0.5);  // حصة مسار /api/chat
const PROFILE = __ENV.PROFILE || 'smoke';

// رسائل بلا LLM (مزيج R-131) — حمل تمثيلي بلا اعتماد خارجي
const CHAT_MESSAGES = [
  'sum these numbers: [1, 2, 3, 4, 5]',
  'validate this JSON: {"name": "string", "age": 3}',
  'pick a dark mode color palette from #3366ff for the dashboard',
  'review this code for bugs: def f(x): return x*2',
];

// العتبات تُحقن من الإطار (run_load) — النجاح/الفشل صريح لا جوفي.
// k6 يتطلب مصفوفة تعبيرات لكل مقياس؛ القيمة الواحدة تُغلّف تلقائيًا.
const rawThresholds = JSON.parse(__ENV.THRESHOLDS || '{}');
const thresholds = {};
for (const [metric, expr] of Object.entries(rawThresholds)) {
  thresholds[metric] = Array.isArray(expr) ? expr : [expr];
}

export const options = {
  scenarios: {
    load: {
      executor: 'constant-arrival-rate', // النموذج المفتوح — المطلوب في P6
      rate: RATE,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: Math.min(MAX_VUS, 5),
      maxVUs: MAX_VUS,
    },
  },
  thresholds,
};

let n = 0;

export default function () {
  // مزيج المسارات: (1-chat_share) لفحوص الصحة، والباقي محادثة فعلية
  const isChat = (Math.random() < CHAT_SHARE);
  if (isChat) {
    const msg = CHAT_MESSAGES[(n++) % CHAT_MESSAGES.length];
    const res = http.post(
      `${TARGET_URL}/api/chat`,
      JSON.stringify({ message: msg }),
      { headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
        tags: { profile: PROFILE, endpoint: 'chat' } });
    check(res, {
      'chat status 200': (r) => r.status === 200,
      'chat body ok': (r) => r.status === 200 && (r.json('outcome.ok') === true
        || r.json('ok') === true),
    });
  } else {
    const res = http.get(`${TARGET_URL}/health`,
      { tags: { profile: PROFILE, endpoint: 'health' } });
    check(res, {
      'health status 200': (r) => r.status === 200,
    });
  }
  sleep(0.05); // تنفس صغير بين الطلبات داخل الوحدة الوهمية
}
