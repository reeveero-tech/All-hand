import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, SPEC_LABELS, type TaskRecord } from "@/lib/api";
import { useBackend } from "@/hooks/useBackend";
import { useState } from "react";

export const Route = createFileRoute("/command")({
  component: CommandCenter,
});

function TaskRow({ task }: { task: TaskRecord }) {
  const [open, setOpen] = useState(false);
  const statusColor =
    task.status === "completed"
      ? "text-success"
      : task.status === "failed"
        ? "text-destructive"
        : "text-warning";
  const answer = task.result?.result?.answer;
  const toolOutput = task.result?.result?.tool_output;

  return (
    <div className="panel p-4">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center justify-between gap-3 text-right">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm text-foreground">{task.prompt}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {SPEC_LABELS[task.specialization] ?? task.specialization}
            {task.agent && (
              <span className="font-mono" dir="ltr">
                {" "}
                · {task.agent}
              </span>
            )}
          </p>
        </div>
        <span className={`text-xs font-medium ${statusColor}`}>
          {task.status === "completed" ? "منجزة" : task.status === "failed" ? "فشلت" : task.status === "running" ? "قيد التنفيذ" : "في الطابور"}
        </span>
      </button>
      {open && (answer || toolOutput || task.result?.result?.llm_error) && (
        <div className="mt-3 space-y-2 border-t border-border pt-3">
          {answer && <p className="whitespace-pre-wrap text-sm text-foreground">{answer}</p>}
          {toolOutput != null && (
            <pre className="overflow-x-auto rounded-md bg-background p-3 font-mono text-xs text-primary" dir="ltr">
              {JSON.stringify(toolOutput, null, 2)}
            </pre>
          )}
          {task.result?.result?.llm_error && (
            <p className="text-xs text-warning">⚠ {task.result.result.llm_error}</p>
          )}
        </div>
      )}
    </div>
  );
}

function CommandCenter() {
  const { connected, ready } = useBackend();
  const queryClient = useQueryClient();
  const [spec, setSpec] = useState("code_generator");
  const [prompt, setPrompt] = useState("");
  const [data, setData] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const tasksQuery = useQuery({
    queryKey: ["tasks"],
    queryFn: () => api.tasks(30),
    enabled: ready && connected,
    refetchInterval: 4000,
    retry: 1,
  });

  async function submit() {
    if (!prompt.trim()) return;
    setSending(true);
    setError(null);
    try {
      let payload: unknown = undefined;
      if (data.trim()) {
        try {
          payload = JSON.parse(data);
        } catch {
          payload = data;
        }
      }
      await api.createTask(spec, prompt, payload);
      setPrompt("");
      setData("");
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    } catch (e) {
      setError(e instanceof Error && e.message === "NO_BACKEND" ? "اربط الباكند أولاً من الإعدادات" : String(e));
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="font-display text-2xl font-bold">مركز الأوامر</h1>

      {ready && !connected && (
        <div className="panel border-warning/40 p-4 text-sm text-warning">
          وضع تجريبي — لإرسال مهام حقيقية،{" "}
          <Link to="/settings" className="underline underline-offset-2">
            اربط الباكند من الإعدادات
          </Link>
        </div>
      )}

      <div className="panel-glow space-y-4 p-5">
        <div>
          <label className="mb-1 block text-sm text-muted-foreground">التخصص</label>
          <select
            value={spec}
            onChange={(e) => setSpec(e.target.value)}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          >
            {Object.entries(SPEC_LABELS).map(([s, label]) => (
              <option key={s} value={s}>
                {label} — {s}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-sm text-muted-foreground">المهمة</label>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={3}
            placeholder="مثال: اكتب دالة بايثون لحساب الأعداد الأولية حتى n"
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <div>
          <label className="mb-1 block text-sm text-muted-foreground">
            بيانات إضافية (اختياري — أرقام لمعالج البيانات، كود لمشغّل الاختبارات، JSON للمدقّق)
          </label>
          <textarea
            value={data}
            onChange={(e) => setData(e.target.value)}
            rows={2}
            dir="ltr"
            placeholder='[1, 2, 3] أو print("hello")'
            className="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        {error && <p className="text-sm text-destructive">{error}</p>}
        <button
          onClick={submit}
          disabled={sending || !prompt.trim()}
          className="rounded-md bg-primary px-5 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
        >
          {sending ? "جارٍ الإرسال..." : "إرسال المهمة ▸"}
        </button>
      </div>

      <section>
        <h2 className="mb-3 font-display text-lg font-semibold">سجل المهام</h2>
        <div className="space-y-2">
          {(tasksQuery.data?.tasks ?? []).map((task) => (
            <TaskRow key={task.id} task={task} />
          ))}
          {connected && tasksQuery.data?.tasks.length === 0 && (
            <p className="py-8 text-center text-sm text-muted-foreground">لا مهام بعد — أرسل أول مهمة</p>
          )}
          {!connected && ready && (
            <p className="py-8 text-center text-sm text-muted-foreground">سجل المهام يظهر عند الاتصال بالباكند</p>
          )}
        </div>
      </section>
    </div>
  );
}
