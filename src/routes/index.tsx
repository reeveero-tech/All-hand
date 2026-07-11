import { createFileRoute, Link } from "@tanstack/react-router";
import { useSystemStatus } from "@/hooks/useBackend";
import { SPEC_LABELS } from "@/lib/api";

export const Route = createFileRoute("/")({
  component: Dashboard,
});

function StatCard({ label, value, accent }: { label: string; value: string | number; accent?: boolean }) {
  return (
    <div className={accent ? "panel-glow p-5" : "panel p-5"}>
      <p className="text-sm text-muted-foreground">{label}</p>
      <p className={`mt-1 font-display text-3xl font-bold ${accent ? "text-primary text-glow" : "text-foreground"}`}>
        {value}
      </p>
    </div>
  );
}

function Dashboard() {
  const { status, isDemo, ready } = useSystemStatus();

  return (
    <div className="space-y-8">
      <section className="py-6 text-center">
        <h1 className="font-display text-4xl font-bold text-foreground md:text-5xl">
          حضارة <span className="text-glow text-primary">AGOS</span>
        </h1>
        <p className="mx-auto mt-3 max-w-2xl text-muted-foreground">
          20 تخصصاً من وكلاء الذكاء الاصطناعي الحقيقيين — يحللون، يبرمجون، يبحثون، ويختبرون في خدمتك
        </p>
        {ready && isDemo && (
          <div className="mx-auto mt-4 inline-flex items-center gap-2 rounded-full border border-warning/40 bg-warning/10 px-4 py-1.5 text-sm text-warning">
            <span className="status-dot animate-pulse-glow bg-warning" />
            وضع تجريبي — اربط الباكند من{" "}
            <Link to="/settings" className="underline underline-offset-2">
              الإعدادات
            </Link>
          </div>
        )}
        {ready && !isDemo && (
          <div className="mx-auto mt-4 inline-flex items-center gap-2 rounded-full border border-success/40 bg-success/10 px-4 py-1.5 text-sm text-success">
            <span className="status-dot animate-pulse-glow bg-success" />
            متصل بالحضارة — {status?.llm_configured ? "الذكاء مفعّل" : "بانتظار مفتاح LLM"}
          </div>
        )}
      </section>

      <section className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="إجمالي الوكلاء" value={status?.total_agents ?? "—"} accent />
        <StatCard label="التخصصات" value={status?.specializations ?? "—"} />
        <StatCard label="مهام منجزة" value={status?.tasks_completed ?? "—"} />
        <StatCard label="مهام فاشلة" value={status?.tasks_failed ?? "—"} />
      </section>

      <section>
        <h2 className="mb-4 font-display text-xl font-semibold">التخصصات العشرون</h2>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          {Object.entries(SPEC_LABELS).map(([spec, label]) => (
            <Link
              key={spec}
              to="/agents"
              search={{ spec }}
              className="panel group p-4 transition-all hover:border-primary/50 hover:shadow-[0_0_16px_var(--color-glow)]"
            >
              <p className="font-medium text-foreground group-hover:text-primary">{label}</p>
              <p className="mt-1 font-mono text-xs text-muted-foreground" dir="ltr">
                {spec}
              </p>
            </Link>
          ))}
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-2">
        <Link to="/command" className="panel-glow group p-6 transition-transform hover:scale-[1.01]">
          <h3 className="font-display text-lg font-semibold text-primary">▸ مركز الأوامر</h3>
          <p className="mt-2 text-sm text-muted-foreground">
            أرسل مهمة محددة لأي تخصص وتابع تنفيذها لحظياً
          </p>
        </Link>
        <Link to="/chat" className="panel-glow group p-6 transition-transform hover:scale-[1.01]">
          <h3 className="font-display text-lg font-semibold text-primary">✦ دردشة الحضارة</h3>
          <p className="mt-2 text-sm text-muted-foreground">
            اكتب أمراً بلغة طبيعية — الحضارة توجهه للوكيل الأنسب تلقائياً
          </p>
        </Link>
      </section>
    </div>
  );
}
