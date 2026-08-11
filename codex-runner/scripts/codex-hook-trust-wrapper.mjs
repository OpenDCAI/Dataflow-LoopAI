#!/usr/bin/env node
/**
 * Codex CLI wrapper that enables hooks without a persisted trust record.
 *
 * `@openai/codex-sdk` spawns the bundled `codex` binary with a fixed argv and
 * exposes no way to append CLI flags. Hooks declared in `$CODEX_HOME/hooks.json`
 * are silently skipped until they are trusted, so the guardrail never runs on
 * the SDK path. Pointing `CodexOptions.codexPathOverride` at this file inserts
 * `--dangerously-bypass-hook-trust` and then execs the real binary.
 *
 * The SDK skips its own PATH setup when the path is overridden, so the vendored
 * `codex-path` directory (ripgrep) is prepended here instead.
 */

import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import { statSync } from "node:fs";

const FLAG = "--dangerously-bypass-hook-trust";
const FORWARDED_SIGNALS = ["SIGINT", "SIGTERM", "SIGHUP"];
const CODEX_NPM_NAME = "@openai/codex";
const PLATFORM_PACKAGE_BY_TARGET = {
    "x86_64-unknown-linux-musl": "@openai/codex-linux-x64",
    "aarch64-unknown-linux-musl": "@openai/codex-linux-arm64",
    "x86_64-apple-darwin": "@openai/codex-darwin-x64",
    "aarch64-apple-darwin": "@openai/codex-darwin-arm64",
    "x86_64-pc-windows-msvc": "@openai/codex-win32-x64",
    "aarch64-pc-windows-msvc": "@openai/codex-win32-arm64",
};

const moduleRequire = createRequire(import.meta.url);

function isFile(p) {
    try {
        return statSync(p).isFile();
    } catch {
        return false;
    }
}

function isDirectory(p) {
    try {
        return statSync(p).isDirectory();
    } catch {
        return false;
    }
}

function targetTriple() {
    const { platform, arch } = process;
    if (platform === "linux" || platform === "android") {
        if (arch === "x64") return "x86_64-unknown-linux-musl";
        if (arch === "arm64") return "aarch64-unknown-linux-musl";
    } else if (platform === "darwin") {
        if (arch === "x64") return "x86_64-apple-darwin";
        if (arch === "arm64") return "aarch64-apple-darwin";
    } else if (platform === "win32") {
        if (arch === "x64") return "x86_64-pc-windows-msvc";
        if (arch === "arm64") return "aarch64-pc-windows-msvc";
    }
    throw new Error(`Unsupported platform: ${platform} (${arch})`);
}

/** Mirrors the resolution `@openai/codex-sdk` performs internally. */
function resolveCodex() {
    const triple = targetTriple();
    const platformPackage = PLATFORM_PACKAGE_BY_TARGET[triple];
    let vendorRoot;
    try {
        const codexPackageJsonPath = moduleRequire.resolve(`${CODEX_NPM_NAME}/package.json`);
        const codexRequire = createRequire(codexPackageJsonPath);
        const platformPackageJsonPath = codexRequire.resolve(`${platformPackage}/package.json`);
        vendorRoot = path.join(path.dirname(platformPackageJsonPath), "vendor");
    } catch {
        throw new Error(
            `Unable to locate Codex CLI binaries. Ensure ${CODEX_NPM_NAME} is installed with optional dependencies.`
        );
    }

    const binaryName = process.platform === "win32" ? "codex.exe" : "codex";
    const packageRoot = path.join(vendorRoot, triple);

    const packageBinary = path.join(packageRoot, "bin", binaryName);
    if (isFile(packageBinary) && isFile(path.join(packageRoot, "codex-package.json"))) {
        return {
            executablePath: packageBinary,
            pathDirs: [path.join(packageRoot, "codex-path")].filter(isDirectory),
        };
    }

    const legacyBinary = path.join(packageRoot, "codex", binaryName);
    if (isFile(legacyBinary)) {
        return {
            executablePath: legacyBinary,
            pathDirs: [path.join(packageRoot, "path")].filter(isDirectory),
        };
    }

    throw new Error(`Unable to locate Codex CLI binary for ${triple} under ${vendorRoot}`);
}

/**
 * The SDK always emits `exec` first; `resume` is appended after the flags, so
 * index 1 keeps the flag attached to `exec` in both shapes.
 */
function injectFlag(argv) {
    if (argv.includes(FLAG)) {
        return argv;
    }
    const at = argv[0] === "exec" ? 1 : 0;
    return [...argv.slice(0, at), FLAG, ...argv.slice(at)];
}

function withPathDirs(env, pathDirs) {
    if (pathDirs.length === 0) {
        return env;
    }
    const key = process.platform === "win32"
        ? (Object.keys(env).find((k) => k.toUpperCase() === "PATH") ?? "PATH")
        : "PATH";
    const existing = (env[key] ?? "")
        .split(path.delimiter)
        .filter((entry) => entry.length > 0 && !pathDirs.includes(entry));
    return { ...env, [key]: [...pathDirs, ...existing].join(path.delimiter) };
}

// CODEX_REAL_BIN swaps in a stub binary so argv injection, PATH restoration and
// exit-code propagation can be asserted without spending a model call. It is
// honoured only under NODE_ENV=test — never a deployment knob.
const overrideBin = process.env.NODE_ENV === "test"
    ? process.env.CODEX_REAL_BIN?.trim()
    : undefined;
if (overrideBin && !isFile(overrideBin)) {
    throw new Error(`CODEX_REAL_BIN does not point at a file: ${overrideBin}`);
}

let resolved;
try {
    resolved = resolveCodex();
} catch (err) {
    if (!overrideBin) {
        throw err;
    }
    resolved = { executablePath: overrideBin, pathDirs: [] };
}

const executablePath = overrideBin || resolved.executablePath;
const args = injectFlag(process.argv.slice(2));

const child = spawn(executablePath, args, {
    env: withPathDirs(process.env, resolved.pathDirs),
    stdio: "inherit",
});

// The SDK aborts a run by killing this process; without forwarding, the real
// codex would keep running detached.
for (const signal of FORWARDED_SIGNALS) {
    process.on(signal, () => {
        if (!child.killed) {
            child.kill(signal);
        }
    });
}

child.on("error", (err) => {
    process.stderr.write(`codex-hook-trust-wrapper: failed to spawn codex: ${err.message}\n`);
    process.exit(127);
});

child.on("exit", (code, signal) => {
    if (signal) {
        // Re-raise rather than exiting 128+N, so anyone reading this process's
        // wait status sees a real signal death, same as the child's.
        for (const s of FORWARDED_SIGNALS) {
            process.removeAllListeners(s);
        }
        process.kill(process.pid, signal);
        return;
    }
    process.exit(code ?? 1);
});
