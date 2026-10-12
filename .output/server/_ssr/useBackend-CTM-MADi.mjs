import { n as __toESM } from "../_runtime.mjs";
import { a as require_react, t as useQuery } from "../_libs/react+tanstack__react-query.mjs";
import { o as getApiUrl, r as api, t as DEMO_STATUS } from "./api-D1T-sVu6.mjs";
//#region node_modules/.nitro/vite/services/ssr/assets/useBackend-CTM-MADi.js
var import_react = /* @__PURE__ */ __toESM(require_react());
function useBackend() {
	const [url, setUrl] = (0, import_react.useState)(null);
	const [ready, setReady] = (0, import_react.useState)(false);
	(0, import_react.useEffect)(() => {
		setUrl(getApiUrl());
		setReady(true);
	}, []);
	return {
		url,
		ready,
		connected: !!url
	};
}
function useSystemStatus() {
	const { connected, ready } = useBackend();
	const query = useQuery({
		queryKey: ["system-status"],
		queryFn: () => api.systemStatus(),
		enabled: ready && connected,
		refetchInterval: 1e4,
		retry: 1
	});
	return {
		status: connected ? query.data : DEMO_STATUS,
		isDemo: ready && !connected,
		isError: query.isError,
		ready
	};
}
//#endregion
export { useSystemStatus as n, useBackend as t };
