import { n as __toESM } from "../_runtime.mjs";
import { a as require_react, i as require_jsx_runtime } from "../_libs/react+tanstack__react-query.mjs";
import { b as Link } from "../_libs/@tanstack/react-router+[...].mjs";
import { n as SPEC_LABELS, r as api } from "./api-D1T-sVu6.mjs";
import { t as useBackend } from "./useBackend-CTM-MADi.mjs";
//#region node_modules/.nitro/vite/services/ssr/assets/chat-FxcUdkYS.js
var import_react = /* @__PURE__ */ __toESM(require_react());
var import_jsx_runtime = require_jsx_runtime();
function ChatPage() {
	const { connected, ready } = useBackend();
	const [messages, setMessages] = (0, import_react.useState)([]);
	const [input, setInput] = (0, import_react.useState)("");
	const [busy, setBusy] = (0, import_react.useState)(false);
	const bottomRef = (0, import_react.useRef)(null);
	const inputRef = (0, import_react.useRef)(null);
	(0, import_react.useEffect)(() => {
		bottomRef.current?.scrollIntoView({ behavior: "smooth" });
	}, [messages, busy]);
	(0, import_react.useEffect)(() => {
		inputRef.current?.focus();
	}, [busy]);
	async function send() {
		const text = input.trim();
		if (!text || busy) return;
		setInput("");
		setMessages((m) => [...m, {
			role: "user",
			content: text
		}]);
		setBusy(true);
		try {
			const res = await api.chat(text);
			const answer = res.outcome?.result?.answer ?? (res.outcome?.result?.llm_error ? `⚠ ${res.outcome.result.llm_error}` : "لم يصل رد من الوكيل");
			setMessages((m) => [...m, {
				role: "civilization",
				content: answer,
				routedTo: res.routed_to,
				agent: res.agent
			}]);
		} catch (e) {
			setMessages((m) => [...m, {
				role: "civilization",
				content: e instanceof Error && e.message === "NO_BACKEND" ? "الحضارة غير متصلة بعد — اربط الباكند من صفحة الإعدادات ثم عد للدردشة." : `خطأ في الاتصال: ${String(e)}`
			}]);
		} finally {
			setBusy(false);
		}
	}
	return /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
		className: "flex h-[calc(100vh-12rem)] flex-col",
		children: [
			/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
				className: "mb-4 flex items-center justify-between",
				children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("h1", {
					className: "font-display text-2xl font-bold",
					children: "دردشة الحضارة"
				}), ready && !connected && /* @__PURE__ */ (0, import_jsx_runtime.jsx)(Link, {
					to: "/settings",
					className: "text-sm text-warning underline underline-offset-2",
					children: "غير متصل — الإعدادات"
				})]
			}),
			/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
				className: "panel flex-1 space-y-4 overflow-y-auto p-4",
				children: [
					messages.length === 0 && /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
						className: "flex h-full flex-col items-center justify-center text-center",
						children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
							className: "text-glow font-display text-lg text-primary",
							children: "✦"
						}), /* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
							className: "mt-2 text-sm text-muted-foreground",
							children: "اكتب أمراً بأي لغة — الحضارة توجّهه تلقائياً للوكيل المتخصص الأنسب"
						})]
					}),
					messages.map((msg, i) => /* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", {
						className: `flex ${msg.role === "user" ? "justify-start" : "justify-end"}`,
						children: /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
							className: `max-w-[85%] rounded-lg px-4 py-3 text-sm ${msg.role === "user" ? "bg-primary text-primary-foreground" : "bg-secondary text-foreground"}`,
							children: [msg.routedTo && /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("p", {
								className: "mb-1 text-xs opacity-70",
								children: [
									"وُجّه إلى: ",
									SPEC_LABELS[msg.routedTo] ?? msg.routedTo,
									msg.agent && /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("span", {
										className: "font-mono",
										dir: "ltr",
										children: [
											" ",
											"(",
											msg.agent,
											")"
										]
									})
								]
							}), /* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
								className: "whitespace-pre-wrap",
								children: msg.content
							})]
						})
					}, i)),
					busy && /* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", {
						className: "flex justify-end",
						children: /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
							className: "rounded-lg bg-secondary px-4 py-3 text-sm text-muted-foreground",
							children: ["الحضارة تفكّر", /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", {
								className: "animate-pulse",
								children: "..."
							})]
						})
					}),
					/* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", { ref: bottomRef })
				]
			}),
			/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
				className: "mt-4 flex gap-2",
				children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("textarea", {
					ref: inputRef,
					value: input,
					onChange: (e) => setInput(e.target.value),
					onKeyDown: (e) => {
						if (e.key === "Enter" && !e.shiftKey) {
							e.preventDefault();
							send();
						}
					},
					rows: 2,
					placeholder: "مثال: ابحث لي عن أحدث تقنيات الذكاء الاصطناعي...",
					className: "flex-1 resize-none rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
				}), /* @__PURE__ */ (0, import_jsx_runtime.jsx)("button", {
					onClick: send,
					disabled: busy || !input.trim(),
					className: "rounded-md bg-primary px-5 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50",
					children: "إرسال"
				})]
			})
		]
	});
}
//#endregion
export { ChatPage as component };
