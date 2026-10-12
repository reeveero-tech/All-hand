import { n as __toESM } from "../_runtime.mjs";
import { a as require_react, i as require_jsx_runtime } from "../_libs/react+tanstack__react-query.mjs";
import { i as clearApiUrl, o as getApiUrl, r as api, s as setApiUrl } from "./api-D1T-sVu6.mjs";
//#region node_modules/.nitro/vite/services/ssr/assets/settings-Ddu_WzgK.js
var import_react = /* @__PURE__ */ __toESM(require_react());
var import_jsx_runtime = require_jsx_runtime();
function SettingsPage() {
	const [url, setUrl] = (0, import_react.useState)("");
	const [saved, setSaved] = (0, import_react.useState)(null);
	const [testing, setTesting] = (0, import_react.useState)(false);
	const [testResult, setTestResult] = (0, import_react.useState)(null);
	(0, import_react.useEffect)(() => {
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
				message: `متصل ✓ — ${status.total_agents} وكيل، ${status.specializations} تخصصاً${status.llm_configured ? "، الذكاء مفعّل" : "، ⚠ مفتاح LLM غير مضبوط على الخادم"}`
			});
		} catch (e) {
			setTestResult({
				ok: false,
				message: `فشل الاتصال: ${String(e).slice(0, 150)}`
			});
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
	return /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
		className: "mx-auto max-w-2xl space-y-6",
		children: [
			/* @__PURE__ */ (0, import_jsx_runtime.jsx)("h1", {
				className: "font-display text-2xl font-bold",
				children: "الإعدادات"
			}),
			/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
				className: "panel-glow space-y-4 p-5",
				children: [
					/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("h2", {
						className: "font-display font-semibold",
						children: "رابط باكند الحضارة"
					}), /* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
						className: "mt-1 text-sm text-muted-foreground",
						children: "بعد نشر الباكند على Render، الصق الرابط هنا (مثال: https://agos-civilization.onrender.com)"
					})] }),
					/* @__PURE__ */ (0, import_jsx_runtime.jsx)("input", {
						value: url,
						onChange: (e) => setUrl(e.target.value),
						dir: "ltr",
						placeholder: "https://your-app.onrender.com",
						className: "w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
					}),
					/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
						className: "flex gap-2",
						children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("button", {
							onClick: saveAndTest,
							disabled: testing || !url.trim(),
							className: "rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50",
							children: testing ? "جارٍ الاختبار..." : "حفظ واختبار الاتصال"
						}), saved && /* @__PURE__ */ (0, import_jsx_runtime.jsx)("button", {
							onClick: disconnect,
							className: "rounded-md border border-border px-4 py-2 text-sm text-muted-foreground transition-colors hover:text-destructive",
							children: "قطع الاتصال"
						})]
					}),
					testResult && /* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
						className: `text-sm ${testResult.ok ? "text-success" : "text-destructive"}`,
						children: testResult.message
					})
				]
			}),
			/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
				className: "panel space-y-3 p-5 text-sm",
				children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("h2", {
					className: "font-display font-semibold",
					children: "خطوات النشر على Render (مجاناً)"
				}), /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("ol", {
					className: "list-inside list-decimal space-y-2 text-muted-foreground",
					children: [
						/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("li", { children: [
							"افتح",
							" ",
							/* @__PURE__ */ (0, import_jsx_runtime.jsx)("a", {
								href: "https://render.com",
								target: "_blank",
								rel: "noreferrer",
								className: "text-primary underline underline-offset-2",
								children: "render.com"
							}),
							" ",
							"وأنشئ حساباً مجانياً"
						] }),
						/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("li", { children: [
							"اضغط ",
							/* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", {
								className: "text-foreground",
								children: "New → Blueprint"
							}),
							" واربط مستودع",
							" ",
							/* @__PURE__ */ (0, import_jsx_runtime.jsx)("a", {
								href: "https://github.com/reeveero-tech/All-hand",
								target: "_blank",
								rel: "noreferrer",
								className: "font-mono text-primary underline underline-offset-2",
								dir: "ltr",
								children: "reeveero-tech/All-hand"
							})
						] }),
						/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("li", { children: [
							"أنشئ مفتاحاً مجانياً من",
							" ",
							/* @__PURE__ */ (0, import_jsx_runtime.jsx)("a", {
								href: "https://openrouter.ai/keys",
								target: "_blank",
								rel: "noreferrer",
								className: "text-primary underline underline-offset-2",
								children: "openrouter.ai/keys"
							}),
							" ",
							"وضعه في متغير ",
							/* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", {
								className: "font-mono text-foreground",
								dir: "ltr",
								children: "OPENROUTER_API_KEY"
							})
						] }),
						/* @__PURE__ */ (0, import_jsx_runtime.jsx)("li", { children: "بعد اكتمال النشر، الصق رابط الخدمة في الحقل أعلاه" })
					]
				})]
			})
		]
	});
}
//#endregion
export { SettingsPage as component };
