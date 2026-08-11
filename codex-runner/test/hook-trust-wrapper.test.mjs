/**
 * Tests for the codex hook-trust wrapper.
 *
 * The wrapper mirrors two things it does not own: how `@openai/codex-sdk`
 * resolves the bundled binary, and the shape of the argv the SDK builds. An SDK
 * upgrade can change either without any error surfacing, so the last group of
 * tests asserts those contracts directly against the installed SDK.
 *
 * Run with: yarn test
 */

import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawn, execFile } from "node:child_process";
import { fileURLToPath } from "node:url";
import { mkdtempSync, rmSync, writeFileSync, chmodSync, existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const WRAPPER = fileURLToPath(new URL("../scripts/codex-hook-trust-wrapper.mjs", import.meta.url));
const FLAG = "--dangerously-bypass-hook-trust";

let workDir;

before(() => {
    // realpathSync via mkdtempSync on macOS still yields /var -> /private/var;
    // tests never compare paths, so the symlink is harmless here.
    workDir = mkdtempSync(path.join(tmpdir(), "hook-trust-test-"));
});

after(() => {
    rmSync(workDir, { recursive: true, force: true });
});

/** Writes an executable POSIX stub that stands in for the real codex binary. */
function stub(name, body) {
    const file = path.join(workDir, name);
    writeFileSync(file, `#!/bin/sh\n${body}\n`);
    chmodSync(file, 0o755);
    return file;
}

/** Runs the wrapper with a stub binary; resolves with exit code and output. */
function runWrapper(realBin, args) {
    return new Promise((resolve) => {
        execFile(
            process.execPath,
            [WRAPPER, ...args],
            { env: { ...process.env, NODE_ENV: "test", CODEX_REAL_BIN: realBin } },
            (err, stdout, stderr) => {
                resolve({ code: err?.code ?? 0, stdout, stderr });
            }
        );
    });
}

// ---------------------------------------------------------------- flag position

test("injects the flag directly after `exec`", async () => {
    const bin = stub("echo-argv.sh", 'echo "$@"');
    const { stdout } = await runWrapper(bin, ["exec", "--experimental-json", "--sandbox", "read-only"]);
    assert.equal(
        stdout.trim(),
        `exec ${FLAG} --experimental-json --sandbox read-only`
    );
});

test("keeps `resume` as the last subcommand", async () => {
    const bin = stub("echo-argv-resume.sh", 'echo "$@"');
    const { stdout } = await runWrapper(bin, [
        "exec", "--experimental-json", "--cd", "/w", "--skip-git-repo-check", "resume", "tid-1",
    ]);
    const argv = stdout.trim().split(/\s+/);
    assert.equal(argv[0], "exec");
    assert.equal(argv[1], FLAG, "flag must attach to exec, before the resume subcommand");
    assert.deepEqual(argv.slice(-2), ["resume", "tid-1"]);
});

test("does not duplicate an already-present flag", async () => {
    const bin = stub("echo-argv-dup.sh", 'echo "$@"');
    const { stdout } = await runWrapper(bin, ["exec", FLAG, "--experimental-json"]);
    const occurrences = stdout.trim().split(/\s+/).filter((a) => a === FLAG).length;
    assert.equal(occurrences, 1);
});

// ------------------------------------------------------------------------- PATH

test("restores the vendored PATH entry the SDK skips when the binary is overridden", async () => {
    const bin = stub("which-rg.sh", 'command -v rg || echo NOT_FOUND');
    const { stdout } = await runWrapper(bin, ["exec"]);
    const resolvedRg = stdout.trim();
    assert.notEqual(resolvedRg, "NOT_FOUND", "codex ships its own rg; the wrapper must keep it on PATH");
    assert.ok(
        resolvedRg.includes("codex-path"),
        `expected rg to resolve inside the vendored codex-path dir, got: ${resolvedRg}`
    );
});

// -------------------------------------------------------------------- exit code

test("propagates the child exit code", async () => {
    const bin = stub("exit-42.sh", "exit 42");
    const { code } = await runWrapper(bin, ["exec"]);
    assert.equal(code, 42);
});

test("propagates a zero exit code", async () => {
    const bin = stub("exit-0.sh", "exit 0");
    const { code } = await runWrapper(bin, ["exec"]);
    assert.equal(code, 0);
});

// ---------------------------------------------------------------------- signals

test("forwards SIGTERM so the child dies with the wrapper", async () => {
    const readyFile = path.join(workDir, "ready");
    const bin = stub("sleeper.sh", `echo ready > ${readyFile}\nsleep 60`);

    const child = spawn(process.execPath, [WRAPPER, "exec"], {
        env: { ...process.env, NODE_ENV: "test", CODEX_REAL_BIN: bin },
        stdio: "ignore",
    });

    // Wait for the grandchild to actually be running before signalling.
    await new Promise((resolve, reject) => {
        const deadline = Date.now() + 10_000;
        const poll = setInterval(() => {
            if (existsSync(readyFile)) {
                clearInterval(poll);
                resolve();
            } else if (Date.now() > deadline) {
                clearInterval(poll);
                reject(new Error("stub never started"));
            }
        }, 20);
    });

    const grandchildPid = await new Promise((resolve, reject) => {
        execFile("pgrep", ["-P", String(child.pid)], (err, stdout) => {
            if (err) return reject(new Error("could not find grandchild pid"));
            resolve(Number(stdout.trim().split("\n")[0]));
        });
    });

    const exited = new Promise((resolve) => child.once("exit", (code, signal) => resolve({ code, signal })));
    child.kill("SIGTERM");
    const status = await exited;

    // Re-raised, so the wrapper dies by signal rather than exiting 128+N.
    assert.equal(status.signal, "SIGTERM");

    let alive = true;
    for (let i = 0; i < 100 && alive; i++) {
        try {
            process.kill(grandchildPid, 0);
            await new Promise((r) => setTimeout(r, 20));
        } catch {
            alive = false;
        }
    }
    assert.equal(alive, false, `grandchild ${grandchildPid} outlived the wrapper (orphaned codex)`);
});

// ------------------------------------------------- CODEX_REAL_BIN is test-only

test("ignores CODEX_REAL_BIN outside NODE_ENV=test", async () => {
    const bin = stub("should-not-run.sh", 'echo "STUB_RAN"');
    const { stdout } = await new Promise((resolve) => {
        execFile(
            process.execPath,
            [WRAPPER, "--version"],
            { env: { ...process.env, NODE_ENV: "production", CODEX_REAL_BIN: bin } },
            (err, stdout, stderr) => resolve({ stdout, stderr })
        );
    });
    assert.ok(!stdout.includes("STUB_RAN"), "the test-only override must not apply in production");
    assert.match(stdout, /codex-cli/, "should fall through to the real binary");
});

// -------------------------------------------------- contracts owned by the SDK

test("canary: the real codex binary still accepts the flag", async () => {
    const { stdout } = await new Promise((resolve, reject) => {
        execFile(WRAPPER, ["--version"], (err, stdout) => (err ? reject(err) : resolve({ stdout })));
    });
    assert.match(stdout, /codex-cli/, "wrapper must resolve a working codex binary");

    // If a codex upgrade removes the flag, clap rejects it and every hooked run
    // fails at startup — assert it is still advertised.
    const help = await new Promise((resolve, reject) => {
        execFile(WRAPPER, ["exec", "--help"], { maxBuffer: 4 << 20 }, (err, stdout) =>
            err ? reject(err) : resolve(stdout)
        );
    });
    assert.ok(help.includes(FLAG), `codex exec no longer advertises ${FLAG}`);
});

test("canary: the SDK still builds an argv starting with `exec`", async () => {
    // The wrapper inserts at index 1 on the assumption that argv[0] is `exec`.
    // Drive the real SDK with a recording stub to verify that still holds.
    const recorded = path.join(workDir, "sdk-argv.txt");
    const recorder = stub("record-argv.sh", `printf '%s\\n' "$@" > ${recorded}\nexit 0`);

    const { Codex } = await import("@openai/codex-sdk");
    const codex = new Codex({ codexPathOverride: recorder, env: { ...process.env } });
    const thread = codex.startThread({ sandboxMode: "read-only", skipGitRepoCheck: true });

    // The stub exits without emitting events, so the SDK errors out; the argv it
    // wrote before that is the whole point of this test.
    await thread.runStreamed("noop").then(
        async ({ events }) => {
            try {
                for await (const _ of events) { /* drain */ }
            } catch { /* expected */ }
        },
        () => { /* expected */ }
    );

    assert.ok(existsSync(recorded), "SDK never spawned the overridden path");
    const argv = readFileSync(recorded, "utf8").trim().split("\n");
    assert.equal(argv[0], "exec", `SDK argv no longer starts with 'exec': ${JSON.stringify(argv)}`);
    assert.ok(
        argv.includes("--experimental-json"),
        `SDK argv no longer contains --experimental-json: ${JSON.stringify(argv)}`
    );
});
