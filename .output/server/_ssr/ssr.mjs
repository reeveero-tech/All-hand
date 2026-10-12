//#region node_modules/.nitro/vite/services/ssr/index.js
/**
* Error capture shim for the root TanStack UI.
*
* Another module that src/server.ts imports but was never committed
* (R-013). Minimal dependency-free implementation.
*/
var captured = [];
function consumeLastCapturedError() {
	return captured.pop();
}
/**
* Error page renderer shim for the root TanStack UI.
*
* Imported by src/server.ts but never committed (R-013). Minimal
* dependency-free implementation.
*/
function renderErrorPage(error) {
	return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>AGOS — Error</title>
</head>
<body>
  <h1>Something went wrong</h1>
  <pre>${(error instanceof Error ? error.message : String(error)).replace(/</g, "<")}</pre>
</body>
</html>`;
}
var serverEntryPromise;
async function getServerEntry() {
	if (!serverEntryPromise) serverEntryPromise = import("./server-BDrj-jka.mjs").then((n) => n.t).then((m) => m.default ?? m);
	return serverEntryPromise;
}
async function normalizeCatastrophicSsrResponse(response) {
	if (response.status < 500) return response;
	if (!(response.headers.get("content-type") ?? "").includes("application/json")) return response;
	const body = await response.clone().text();
	if (!isH3SwallowedErrorBody(body)) return response;
	console.error(consumeLastCapturedError() ?? /* @__PURE__ */ new Error(`h3 swallowed SSR error: ${body}`));
	return new Response(renderErrorPage(), {
		status: 500,
		headers: { "content-type": "text/html; charset=utf-8" }
	});
}
function isH3SwallowedErrorBody(body) {
	try {
		const payload = JSON.parse(body);
		return payload.unhandled === true && payload.message === "HTTPError";
	} catch {
		return false;
	}
}
var server_default = { async fetch(request, env, ctx) {
	try {
		return await normalizeCatastrophicSsrResponse(await (await getServerEntry()).fetch(request, env, ctx));
	} catch (error) {
		console.error(error);
		return new Response(renderErrorPage(), {
			status: 500,
			headers: { "content-type": "text/html; charset=utf-8" }
		});
	}
} };
//#endregion
export { server_default as default, renderErrorPage as t };
