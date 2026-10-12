import { n as __toESM } from "../_runtime.mjs";
import { a as require_react, i as require_jsx_runtime, r as useQueryClient, t as useQuery } from "../_libs/react+tanstack__react-query.mjs";
import { b as Link } from "../_libs/@tanstack/react-router+[...].mjs";
import { n as SPEC_LABELS, r as api } from "./api-D1T-sVu6.mjs";
import { t as useBackend } from "./useBackend-CTM-MADi.mjs";
//#region node_modules/.nitro/vite/services/ssr/assets/command-D-hBUhQa.js
var import_react = /* @__PURE__ */ __toESM(require_react());
var import_jsx_runtime = require_jsx_runtime();
function TaskRow({ task }) {
	const [open, setOpen] = (0, import_react.useState)(false);
	const statusColor = task.status === "completed" ? "text-success" : task.status === "failed" ? "text-destructive" : "text-warning";
	const answer = task.result?.result?.answer;
	const toolOutput = task.result?.result?.tool_output;
	return /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
		className: "panel p-4",
		children: [/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("button", {
			onClick: () => setOpen(!open),
			className: "flex w-full items-center justify-between gap-3 text-right",
			children: [/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
				className: "min-w-0 flex-1",
				children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
					className: "truncate text-sm text-foreground",
					children: task.prompt
				}), /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("p", {
					className: "mt-1 text-xs text-muted-foreground",
					children: [SPEC_LABELS[task.specialization] ?? task.specialization, task.agent && /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("span", {
						className: "font-mono",
						dir: "ltr",
						children: [
							" ",
							"· ",
							task.agent
						]
					})]
				})]
			}), /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", {
				className: `text-xs font-medium ${statusColor}`,
				children: task.status === "completed" ? "منجزة" : task.status === "failed" ? "فشلت" : task.status === "running" ? "قيد التنفيذ" : "في الطابور"
			})]
		}), open && (answer || toolOutput || task.result?.result?.llm_error) && /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
			className: "mt-3 space-y-2 border-t border-border pt-3",
			children: [
				answer && /* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
					className: "whitespace-pre-wrap text-sm text-foreground",
					children: answer
				}),
				toolOutput != null && /* @__PURE__ */ (0, import_jsx_runtime.jsx)("pre", {
					className: "overflow-x-auto rounded-md bg-background p-3 font-mono text-xs text-primary",
					dir: "ltr",
					children: JSON.stringify(toolOutput, null, 2)
				}),
				task.result?.result?.llm_error && /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("p", {
					className: "text-xs text-warning",
					children: ["⚠ ", task.result.result.llm_error]
				})
			]
		})]
	});
}
function CommandCenter() {
	const { connected, ready } = useBackend();
	const queryClient = useQueryClient();
	const [spec, setSpec] = (0, import_react.useState)("code_generator");
	const [prompt, setPrompt] = (0, import_react.useState)("");
	const [data, setData] = (0, import_react.useState)("");
	const [sending, setSending] = (0, import_react.useState)(false);
	const [error, setError] = (0, import_react.useState)(null);
	const tasksQuery = useQuery({
		queryKey: ["tasks"],
		queryFn: () => api.tasks(30),
		enabled: ready && connected,
		refetchInterval: 4e3,
		retry: 1
	});
	async function submit() {
		if (!prompt.trim()) return;
		setSending(true);
		setError(null);
		try {
			let payload = void 0;
			if (data.trim()) try {
				payload = JSON.parse(data);
			} catch {
				payload = data;
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
	return /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
		className: "space-y-6",
		children: [
			/* @__PURE__ */ (0, import_jsx_runtime.jsx)("h1", {
				className: "font-display text-2xl font-bold",
				children: "مركز الأوامر"
			}),
			ready && !connected && /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
				className: "panel border-warning/40 p-4 text-sm text-warning",
				children: [
					"وضع تجريبي — لإرسال مهام حقيقية،",
					" ",
					/* @__PURE__ */ (0, import_jsx_runtime.jsx)(Link, {
						to: "/settings",
						className: "underline underline-offset-2",
						children: "اربط الباكند من الإعدادات"
					})
				]
			}),
			/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
				className: "panel-glow space-y-4 p-5",
				children: [
					/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("label", {
						className: "mb-1 block text-sm text-muted-foreground",
						children: "التخصص"
					}), /* @__PURE__ */ (0, import_jsx_runtime.jsx)("select", {
						value: spec,
						onChange: (e) => setSpec(e.target.value),
						className: "w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring",
						children: Object.entries(SPEC_LABELS).map(([s, label]) => /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("option", {
							value: s,
							children: [
								label,
								" — ",
								s
							]
						}, s))
					})] }),
					/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("label", {
						className: "mb-1 block text-sm text-muted-foreground",
						children: "المهمة"
					}), /* @__PURE__ */ (0, import_jsx_runtime.jsx)("textarea", {
						value: prompt,
						onChange: (e) => setPrompt(e.target.value),
						rows: 3,
						placeholder: "مثال: اكتب دالة بايثون لحساب الأعداد الأولية حتى n",
						className: "w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
					})] }),
					/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("label", {
						className: "mb-1 block text-sm text-muted-foreground",
						children: "بيانات إضافية (اختياري — أرقام لمعالج البيانات، كود لمشغّل الاختبارات، JSON للمدقّق)"
					}), /* @__PURE__ */ (0, import_jsx_runtime.jsx)("textarea", {
						value: data,
						onChange: (e) => setData(e.target.value),
						rows: 2,
						dir: "ltr",
						placeholder: "[1, 2, 3] أو print(\"hello\")",
						className: "w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
					})] }),
					error && /* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
						className: "text-sm text-destructive",
						children: error
					}),
					/* @__PURE__ */ (0, import_jsx_runtime.jsx)("button", {
						onClick: submit,
						disabled: sending || !prompt.trim(),
						className: "rounded-md bg-primary px-5 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50",
						children: sending ? "جارٍ الإرسال..." : "إرسال المهمة ▸"
					})
				]
			}),
			/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("section", { children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("h2", {
				className: "mb-3 font-display text-lg font-semibold",
				children: "سجل المهام"
			}), /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
				className: "space-y-2",
				children: [
					(tasksQuery.data?.tasks ?? []).map((task) => /* @__PURE__ */ (0, import_jsx_runtime.jsx)(TaskRow, { task }, task.id)),
					connected && tasksQuery.data?.tasks.length === 0 && /* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
						className: "py-8 text-center text-sm text-muted-foreground",
						children: "لا مهام بعد — أرسل أول مهمة"
					}),
					!connected && ready && /* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
						className: "py-8 text-center text-sm text-muted-foreground",
						children: "سجل المهام يظهر عند الاتصال بالباكند"
					})
				]
			})] })
		]
	});
}
//#endregion
export { CommandCenter as component };
