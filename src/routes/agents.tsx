import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { api, demoAgents, SPEC_LABELS, type AgentSnapshot } from "@/lib/api";
import { useBackend } from "@/hooks/useBackend";
import { useMemo, useState } from "react";

export const Route = createFileRoute("/agents")({
  validateSearch: (s: Record<string, unknown>) => ({
    spec: typeof s.spec === "string" ? s.spec : undefined,
  }),
  component: AgentsPage,
});

function AgentsPage() {
  const { spec } = Route.useSearch();
  const { connected, ready } = useBackend();
  const [filter, setFilter] = useState<string>(spec ?? "all");

  const query = useQuery({
    queryKey: ["agents"],
    queryFn: () => api.agents(),
    enabled: ready && connected,
    refetchInterval: 10000,
    retry: 1,
  });

  const agents: AgentSnapshot[] = useMemo(() => {
    const list = connected && query.data ? query.data.agents : demoAgents();
    return filter === "all" ? list : list.filter((a) => a.specialization === filter);
  }, [connected, query.data, filter]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="font-display text-2xl font-bold">سجل الوكلاء</h1>
        <span className="rounded-full border border-border bg-secondary px-3 py-1 text-sm text-muted-foreground">
          {agents.length} وكيل
        </span>
      </div>

      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => setFilter("all")}
          className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
            filter === "all" ? "bg-primary text-primary-foreground" : "bg-secondary text-muted-foreground hover:text-foreground"
          }`}
        >
          الكل
        </button>
        {Object.entries(SPEC_LABELS).map(([s, label]) => (
          <button
            key={s}
            onClick={() => setFilter(s)}
            className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
              filter === s ? "bg-primary text-primary-foreground" : "bg-secondary text-muted-foreground hover:text-foreground"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {agents.map((agent) => (
          <div key={agent.name} className="panel p-4">
            <div className="flex items-center justify-between">
              <p className="font-mono text-sm text-foreground" dir="ltr">
                {agent.name}
              </p>
              <span
                className={`status-dot ${
                  agent.status === "working"
                    ? "animate-pulse-glow bg-warning"
                    : "bg-success"
                }`}
                title={agent.status}
              />
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              {SPEC_LABELS[agent.specialization] ?? agent.specialization}
            </p>
            <div className="mt-3 flex gap-4 text-xs text-muted-foreground">
              <span>✓ {agent.stats.tasks_completed}</span>
              <span>✗ {agent.stats.tasks_failed}</span>
              <span>{agent.stats.total_time_s.toFixed(1)}s</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
