//#region ../../vue-tui/src/cli/node-module.ts
function isNodeRuntime() {
	return typeof globalThis.process?.versions?.node === "string";
}
function importNodeModule(specifier) {
	if (!isNodeRuntime()) return Promise.resolve(null);
	try {
		return new Function("specifier", "return import(specifier)")(specifier).catch(() => null);
	} catch {
		return Promise.resolve(null);
	}
}
//#endregion
//#region ../../vue-tui/src/utils/path.ts
function normalizeSeparators(input) {
	let out = "";
	for (let i = 0; i < input.length; i++) {
		const ch = input[i];
		out += ch === "\\" ? "/" : ch;
	}
	return out;
}
function isAbsolutePath(path) {
	const p = normalizeSeparators(path);
	if (p.startsWith("/")) return true;
	return /^[A-Z]:\//i.test(p);
}
function joinPath(base, next) {
	const a = normalizeSeparators(base);
	const b = normalizeSeparators(next);
	if (!a) return b;
	if (!b) return a;
	if (a.endsWith("/")) return `${a}${b.startsWith("/") ? b.slice(1) : b}`;
	return `${a}/${b.startsWith("/") ? b.slice(1) : b}`;
}
function normalizePath(path) {
	const raw = normalizeSeparators(path);
	if (!raw) return "";
	const drive = raw.match(/^([A-Z]:)(\/|$)/i)?.[1] ?? null;
	const rest = drive ? raw.slice(drive.length) : raw;
	const absolute = rest.startsWith("/");
	const parts = rest.split("/").filter(Boolean);
	const stack = [];
	for (const part of parts) {
		if (part === ".") continue;
		if (part === "..") {
			if (stack.length > 0 && stack[stack.length - 1] !== "..") {
				stack.pop();
				continue;
			}
			if (!absolute) stack.push("..");
			continue;
		}
		stack.push(part);
	}
	return `${drive ? `${drive}${absolute ? "/" : ""}` : absolute ? "/" : ""}${stack.join("/")}` || (absolute ? drive ? `${drive}/` : "/" : "");
}
function resolvePath(baseAbs, input) {
	const b = normalizePath(baseAbs);
	const i = normalizePath(input);
	if (!b) return i;
	if (isAbsolutePath(i)) return i;
	return normalizePath(joinPath(b, i));
}
//#endregion
export { importNodeModule as i, normalizePath as n, resolvePath as r, isAbsolutePath as t };
