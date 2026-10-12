import { i as require_jsx_runtime } from "../_libs/react+tanstack__react-query.mjs";
import { b as Link } from "../_libs/@tanstack/react-router+[...].mjs";
import { n as SPEC_LABELS } from "./api-D1T-sVu6.mjs";
import { n as useSystemStatus } from "./useBackend-CTM-MADi.mjs";
//#region node_modules/.nitro/vite/services/ssr/assets/routes-NvKu0P0g.js
var import_jsx_runtime = require_jsx_runtime();
function StatCard({ label, value, accent }) {
	return /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
		className: accent ? "panel-glow p-5" : "panel p-5",
		children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
			className: "text-sm text-muted-foreground",
			children: label
		}), /* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
			className: `mt-1 font-display text-3xl font-bold ${accent ? "text-primary text-glow" : "text-foreground"}`,
			children: value
		})]
	});
}
function Dashboard() {
	const { status, isDemo, ready } = useSystemStatus();
	return /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
		className: "space-y-8",
		children: [
			/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("section", {
				className: "py-6 text-center",
				children: [
					/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("h1", {
						className: "font-display text-4xl font-bold text-foreground md:text-5xl",
						children: ["حضارة ", /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", {
							className: "text-glow text-primary",
							children: "AGOS"
						})]
					}),
					/* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
						className: "mx-auto mt-3 max-w-2xl text-muted-foreground",
						children: "20 تخصصاً من وكلاء الذكاء الاصطناعي الحقيقيين — يحللون، يبرمجون، يبحثون، ويختبرون في خدمتك"
					}),
					ready && isDemo && /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
						className: "mx-auto mt-4 inline-flex items-center gap-2 rounded-full border border-warning/40 bg-warning/10 px-4 py-1.5 text-sm text-warning",
						children: [
							/* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "status-dot animate-pulse-glow bg-warning" }),
							"وضع تجريبي — اربط الباكند من",
							" ",
							/* @__PURE__ */ (0, import_jsx_runtime.jsx)(Link, {
								to: "/settings",
								className: "underline underline-offset-2",
								children: "الإعدادات"
							})
						]
					}),
					ready && !isDemo && /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
						className: "mx-auto mt-4 inline-flex items-center gap-2 rounded-full border border-success/40 bg-success/10 px-4 py-1.5 text-sm text-success",
						children: [
							/* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "status-dot animate-pulse-glow bg-success" }),
							"متصل بالحضارة — ",
							status?.llm_configured ? "الذكاء مفعّل" : "بانتظار مفتاح LLM"
						]
					})
				]
			}),
			/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("section", {
				className: "grid grid-cols-2 gap-4 md:grid-cols-4",
				children: [
					/* @__PURE__ */ (0, import_jsx_runtime.jsx)(StatCard, {
						label: "إجمالي الوكلاء",
						value: status?.total_agents ?? "—",
						accent: true
					}),
					/* @__PURE__ */ (0, import_jsx_runtime.jsx)(StatCard, {
						label: "التخصصات",
						value: status?.specializations ?? "—"
					}),
					/* @__PURE__ */ (0, import_jsx_runtime.jsx)(StatCard, {
						label: "مهام منجزة",
						value: status?.tasks_completed ?? "—"
					}),
					/* @__PURE__ */ (0, import_jsx_runtime.jsx)(StatCard, {
						label: "مهام فاشلة",
						value: status?.tasks_failed ?? "—"
					})
				]
			}),
			/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("section", { children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("h2", {
				className: "mb-4 font-display text-xl font-semibold",
				children: "التخصصات العشرون"
			}), /* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", {
				className: "grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5",
				children: Object.entries(SPEC_LABELS).map(([spec, label]) => /* @__PURE__ */ (0, import_jsx_runtime.jsxs)(Link, {
					to: "/agents",
					search: { spec },
					className: "panel group p-4 transition-all hover:border-primary/50 hover:shadow-[0_0_16px_var(--color-glow)]",
					children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
						className: "font-medium text-foreground group-hover:text-primary",
						children: label
					}), /* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
						className: "mt-1 font-mono text-xs text-muted-foreground",
						dir: "ltr",
						children: spec
					})]
				}, spec))
			})] }),
			/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("section", {
				className: "grid gap-4 md:grid-cols-2",
				children: [/* @__PURE__ */ (0, import_jsx_runtime.jsxs)(Link, {
					to: "/command",
					className: "panel-glow group p-6 transition-transform hover:scale-[1.01]",
					children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("h3", {
						className: "font-display text-lg font-semibold text-primary",
						children: "▸ مركز الأوامر"
					}), /* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
						className: "mt-2 text-sm text-muted-foreground",
						children: "أرسل مهمة محددة لأي تخصص وتابع تنفيذها لحظياً"
					})]
				}), /* @__PURE__ */ (0, import_jsx_runtime.jsxs)(Link, {
					to: "/chat",
					className: "panel-glow group p-6 transition-transform hover:scale-[1.01]",
					children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("h3", {
						className: "font-display text-lg font-semibold text-primary",
						children: "✦ دردشة الحضارة"
					}), /* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
						className: "mt-2 text-sm text-muted-foreground",
						children: "اكتب أمراً بلغة طبيعية — الحضارة توجهه للوكيل الأنسب تلقائياً"
					})]
				})]
			})
		]
	});
}
//#endregion
export { Dashboard as component };
