# عامل المتصفح (Browser Worker)

الحالة: `interface only`. العقد والعميل والعامل الوهمي موجودة في `app/research/browser.py` ومختبرة. **لا يوجد عامل مستضاف**، ولا يُثبَّت Playwright أو Chromium داخل خدمة Render (512MB لا تكفي بأمان، ويكبر حجم الصورة وسطح الهجوم). الاستضافة تحتاج موافقة المالك.

## متى يُستخدم

للصفحات التي لا يظهر محتواها إلا بـ JavaScript، بعد أن يفشل `fetch_raw` وقارئ المستندات. دون `BROWSER_WORKER_URL` و`BROWSER_WORKER_SECRET` يكون العامل معطلًا، ويظهر ذلك في `/health` (`research.browser_worker`).

## العقد

`POST {BROWSER_WORKER_URL}/render` بجسم JSON من `BrowserRequest`:

| الحقل | المعنى |
|---|---|
| `url` | رابط عام http(s) فقط |
| `action` | `render` أو `screenshot_text` (قراءة فقط: لا ملء نماذج ولا نقر على جدران الموافقة أو الدخول) |
| `wait_s` | حتى 30 ثانية |
| `session_scope` | معرف المشروع؛ الجلسات معزولة لكل مشروع |
| `use_authorized_session` + `approval_id` | فقط إن سجل المالك الدخول بنفسه ووافق |

الترويسات: `x-agos-ts` و`x-agos-signature` = HMAC-SHA256 للنص `timestamp.body` بالسر المشترك. العامل يرفض التوقيع الخاطئ أو الأقدم من 120 ثانية (`verify_signature`).

الرد `BrowserResult`: `ok` و`status` و`html` و`text` و`failure` (`blocked`, `login_required`, `captcha`, `mfa_required`, `timeout`, `worker_unavailable`...) و`needs_human` (`login`/`mfa`/`captcha`) يُعرض على المالك ولا يُحل آليًا.

## قواعد ملزمة

- العميل يتحقق من SSRF قبل الإرسال، والعامل يطبق القواعد نفسها (لا عناوين خاصة أو loopback أو metadata).
- لا تجاوز لـ CAPTCHA أو MFA أو حواجز الدفع أو الدخول.
- لا كلمات مرور أو رموز أو كوكيز في الطلبات أو السجلات؛ `redact()` يحذف ما يشبه الأسرار من الرد قبل تخزينه.

## خيارات الاستضافة (قرار المالك)

| الخيار | التكلفة | ملاحظات |
|---|---|---|
| جهاز المالك | مجاني | يعمل عند تشغيل الجهاز فقط |
| GitHub Actions | ضمن الحصة المجانية | للمهام غير التفاعلية، بطيء البدء |
| خدمة Render ثانية | تحتاج خطة أكبر من المجانية | لا يُنفذ دون موافقة |
