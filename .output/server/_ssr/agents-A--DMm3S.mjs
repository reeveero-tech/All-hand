import { n as __toESM } from "../_runtime.mjs";
import { a as require_react, i as require_jsx_runtime, t as useQuery } from "../_libs/react+tanstack__react-query.mjs";
import { n as Route$4 } from "./router-JmfzJEID.mjs";
import { a as demoAgents, n as SPEC_LABELS, r as api } from "./api-D1T-sVu6.mjs";
import { t as useBackend } from "./useBackend-CTM-MADi.mjs";
//#region node_modules/.nitro/vite/services/ssr/assets/agents-A--DMm3S.js
var import_react = /* @__PURE__ */ __toESM(require_react());
var import_jsx_runtime = require_jsx_runtime();
function AgentsPage() {
	const { spec } = Route$4.useSearch();
	const { connected, ready } = useBackend();
	const [filter, setFilter] = (0, import_react.useState)(spec ?? "all");
	const query = useQuery({
		queryKey: ["agents"],
		queryFn: () => api.agents(),
		enabled: ready && connected,
		refetchInterval: 1e4,
		retry: 1
	});
	const agents = (0, import_react.useMemo)(() => {
		const list = connected && query.data ? query.data.agents : demoAgents();
		return filter === "all" ? list : list.filter((a) => a.specialization === filter);
	}, [
		connected,
		query.data,
		filter
	]);
	return /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
		className: "space-y-6",
		children: [
			/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
				className: "flex flex-wrap items-center justify-between gap-3",
				children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("h1", {
					className: "font-display text-2xl font-bold",
					children: "سجل الوكلاء"
				}), /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("span", {
					className: "rounded-full border border-border bg-secondary px-3 py-1 text-sm text-muted-foreground",
					children: [agents.length, " وكيل"]
				})]
			}),
			/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
				className: "flex flex-wrap gap-2",
				children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("button", {
					onClick: () => setFilter("all"),
					className: `rounded-md px-3 py-1.5 text-sm transition-colors ${filter === "all" ? "bg-primary text-primary-foreground" : "bg-secondary text-muted-foreground hover:text-foreground"}`,
					children: "الكل"
				}), Object.entries(SPEC_LABELS).map(([s, label]) => /* @__PURE__ */ (0, import_jsx_runtime.jsx)("button", {
					onClick: () => setFilter(s),
					className: `rounded-md px-3 py-1.5 text-sm transition-colors ${filter === s ? "bg-primary text-primary-foreground" : "bg-secondary text-muted-foreground hover:text-foreground"}`,
					children: label
				}, s))]
			}),
			/* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", {
				className: "grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4",
				children: agents.map((agent) => /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
					className: "panel p-4",
					children: [
						/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
							className: "flex items-center justify-between",
							children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
								className: "font-mono text-sm text-foreground",
								dir: "ltr",
								children: agent.name
							}), /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", {
								className: `status-dot ${agent.status === "working" ? "animate-pulse-glow bg-warning" : "bg-success"}`,
								title: agent.status
							})]
						}),
						/* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
							className: "mt-1 text-xs text-muted-foreground",
							children: SPEC_LABELS[agent.specialization] ?? agent.specialization
						}),
						/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
							className: "mt-3 flex gap-4 text-xs text-muted-foreground",
							children: [
								/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("span", { children: ["✓ ", agent.stats.tasks_completed] }),
								/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("span", { children: ["✗ ", agent.stats.tasks_failed] }),
								/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("span", { children: [agent.stats.total_time_s.toFixed(1), "s"] })
							]
						})
					]
				}, agent.name))
			})
		]
	});
}
//#endregion
export { AgentsPage as component };
