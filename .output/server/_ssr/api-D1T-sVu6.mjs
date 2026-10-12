//#region node_modules/.nitro/vite/services/ssr/assets/api-D1T-sVu6.js
/**
* API client for the AGOS Agent Civilization backend.
* Backend URL is configurable (Render deployment) and stored in localStorage.
* When no backend is configured, the UI runs in demo mode with sample data.
*/
var STORAGE_KEY = "agos_api_url";
function getApiUrl() {
	if (typeof window === "undefined") return null;
	return localStorage.getItem(STORAGE_KEY);
}
function setApiUrl(url) {
	localStorage.setItem(STORAGE_KEY, url.replace(/\/+$/, ""));
}
function clearApiUrl() {
	localStorage.removeItem(STORAGE_KEY);
}
async function request(path, init) {
	const base = getApiUrl();
	if (!base) throw new Error("NO_BACKEND");
	const res = await fetch(`${base}${path}`, {
		...init,
		headers: {
			"Content-Type": "application/json",
			...init?.headers
		}
	});
	if (!res.ok) {
		const body = await res.text().catch(() => "");
		throw new Error(`API ${res.status}: ${body.slice(0, 200)}`);
	}
	return res.json();
}
var api = {
	systemStatus: () => request("/system/status"),
	agents: () => request("/agents"),
	specializations: () => request("/specializations"),
	tasks: (limit = 50) => request(`/tasks?limit=${limit}`),
	task: (id) => request(`/tasks/${id}`),
	createTask: (specialization, prompt, data) => request("/tasks", {
		method: "POST",
		body: JSON.stringify({
			specialization,
			prompt,
			data
		})
	}),
	chat: (message) => request("/chat", {
		method: "POST",
		body: JSON.stringify({ message })
	})
};
var SPEC_LABELS = {
	analyst: "محلل",
	api_integrator: "مكامل واجهات",
	architect: "مهندس معماري",
	builder: "بنّاء",
	code_generator: "مولّد أكواد",
	communicator: "متواصل",
	data_processor: "معالج بيانات",
	db_manager: "مدير قواعد بيانات",
	designer: "مصمم",
	educator: "معلّم",
	modifier: "معدِّل أكواد",
	monitor: "مراقب",
	network_client: "خبير شبكات",
	researcher: "باحث",
	reviewer: "مراجع",
	self_developer: "مطوّر ذاتي",
	strategist: "استراتيجي",
	surgeon: "جرّاح أعطال",
	test_runner: "مشغّل اختبارات",
	validator: "مدقّق"
};
var DEMO_STATUS = {
	status: "demo",
	uptime_s: 0,
	llm_configured: false,
	llm_model: "meta-llama/llama-3.3-70b-instruct:free",
	specializations: 20,
	total_agents: 100,
	external_agents: 0,
	tasks_completed: 0,
	tasks_failed: 0,
	tasks_in_memory: 0
};
function demoAgents() {
	return Object.keys(SPEC_LABELS).flatMap((spec, si) => Array.from({ length: 5 }, (_, i) => ({
		id: si * 5 + i,
		name: `${spec}_${String(si * 5 + i).padStart(4, "0")}`,
		specialization: spec,
		status: "idle",
		stats: {
			tasks_completed: 0,
			tasks_failed: 0,
			total_time_s: 0
		}
	})));
}
//#endregion
export { demoAgents as a, clearApiUrl as i, SPEC_LABELS as n, getApiUrl as o, api as r, setApiUrl as s, DEMO_STATUS as t };
