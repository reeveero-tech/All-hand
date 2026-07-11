import { createFileRoute } from "@tanstack/react-router";
import { getApiUrl, setApiUrl, clearApiUrl, api } from "@/lib/api";
import { useEffect, useState } from "react";

export const Route = createFileRoute("/settings")({
  component: SettingsPage,
});

function SettingsPage() {
  const [url, setUrl] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);

  useEffect(() => {
    const current = getApiUrl();
    if (current) {
      setUrl(current);
      setSaved(current);
    }
  }, []);

  async function saveAndTest() {
    const clean = url.trim().replace(/\/+$/, "");
    if (!clean) return;
    setApiUrl(clean);
    setSaved(clean);
    setTesting(true);
    setTestResult(null);
    try {
      const status = await api.systemStatus();
      setTestResult({
        ok: true,
        message: `متصل ✓ — ${status.total_agents} وكيل، ${status.specializations} تخصصاً${status.llm_configured ? "، الذكاء مفعّل" : "، ⚠ مفتاح LLM غير مضبوط على الخادم"}`,
      });
    } catch (e) {
      setTestResult({ ok: false, message: `فشل الاتصال: ${String(e).slice(0, 150)}` });
    } finally {
      setTesting(false);
    }
  }

  function disconnect() {
    clearApiUrl();
    setSaved(null);
    setUrl("");
    setTestResult(null);
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <h1 className="font-display text-2xl font-bold">الإعدادات</h1>

      <div className="panel-glow space-y-4 p-5">
        <div>
          <h2 className="font-display font-semibold">رابط باكند الحضارة</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            بعد نشر الباكند على Render، الصق الرابط هنا (مثال: https://agos-civilization.onrender.com)
          </p>
        </div>
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          dir="ltr"
          placeholder="https://your-app.onrender.com"
          className="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
        />
        <div className="flex gap-2">
          <button
            onClick={saveAndTest}
            disabled={testing || !url.trim()}
            className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
          >
            {testing ? "جارٍ الاختبار..." : "حفظ واختبار الاتصال"}
          </button>
          {saved && (
            <button
              onClick={disconnect}
              className="rounded-md border border-border px-4 py-2 text-sm text-muted-foreground transition-colors hover:text-destructive"
            >
              قطع الاتصال
            </button>
          )}
        </div>
        {testResult && (
          <p className={`text-sm ${testResult.ok ? "text-success" : "text-destructive"}`}>
            {testResult.message}
          </p>
        )}
      </div>

      <div className="panel space-y-3 p-5 text-sm">
        <h2 className="font-display font-semibold">خطوات النشر على Render (مجاناً)</h2>
        <ol className="list-inside list-decimal space-y-2 text-muted-foreground">
          <li>
            افتح{" "}
            <a href="https://render.com" target="_blank" rel="noreferrer" className="text-primary underline underline-offset-2">
              render.com
            </a>{" "}
            وأنشئ حساباً مجانياً
          </li>
          <li>
            اضغط <span className="text-foreground">New → Blueprint</span> واربط مستودع{" "}
            <a
              href="https://github.com/reeveero-tech/All-hand"
              target="_blank"
              rel="noreferrer"
              className="font-mono text-primary underline underline-offset-2"
              dir="ltr"
            >
              reeveero-tech/All-hand
            </a>
          </li>
          <li>
            أنشئ مفتاحاً مجانياً من{" "}
            <a href="https://openrouter.ai/keys" target="_blank" rel="noreferrer" className="text-primary underline underline-offset-2">
              openrouter.ai/keys
            </a>{" "}
            وضعه في متغير <span className="font-mono text-foreground" dir="ltr">OPENROUTER_API_KEY</span>
          </li>
          <li>بعد اكتمال النشر، الصق رابط الخدمة في الحقل أعلاه</li>
        </ol>
      </div>
    </div>
  );
}
