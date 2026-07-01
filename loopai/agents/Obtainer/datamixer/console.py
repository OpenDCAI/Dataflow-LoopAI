"""L6 Web console -- a visual interface over the *exact same CLI operations*.

Design principle: **the UI runs the CLI.** Every operational action posts an
argv (or a raw command line) to ``/api/cli``, which executes
the package CLI in-process and returns the captured JSON. The UI also shows the
equivalent data-lake command for each action, so what you click is exactly what
you'd type. Read-only chart endpoints under ``/api/*`` exist for fast dashboard
rendering.

Single-threaded on purpose (one local user; keeps the SQLite connection simple).
Charts use ECharts from a CDN with graceful degradation to tables when offline.
The UI ships with English/Chinese (en/zh) i18n, toggled from the sidebar.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from . import cli as cli_mod
from . import recipe as R


def run_cli(root, argv=None, line=None, files=None):
    """Execute a CLI command in-process and capture its JSON output.

    ``files`` maps a token (e.g. ``"@DATA"``) appearing in argv to file content;
    each is written to a temp file and the token is substituted with its path,
    so file-taking commands (``ingest --file``, ``recipe export``) work from the
    browser. Returns ``{command, exit, output, raw}``.
    """
    if line is not None:
        argv = shlex.split(line)
    argv = list(argv or [])
    if argv and argv[0] in ("serve",):
        return {"command": "datamixer " + " ".join(argv), "exit": 2,
                "output": {"error": "serve cannot be launched from the console"},
                "raw": ""}
    display = "datamixer --json " + " ".join(shlex.quote(a) for a in argv)

    tmp_paths = []
    resolved = ["--root", str(root), "--json", *argv]
    files = files or {}
    if files:
        for i, a in enumerate(resolved):
            if a in files:
                fd, path = tempfile.mkstemp(suffix=".tmp", prefix="dm-console-")
                os.close(fd)
                Path(path).write_text(files[a], encoding="utf-8")
                tmp_paths.append(path)
                resolved[i] = path

    buf = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = cli_mod.main(resolved)
    except SystemExit as e:  # argparse errors
        code = int(e.code or 0)
    except Exception as e:  # noqa: BLE001 - surface to UI
        return {"command": display, "exit": 1,
                "output": {"error": str(e)}, "raw": str(e)}
    finally:
        for p in tmp_paths:
            try:
                os.unlink(p)
            except OSError:
                pass

    raw = buf.getvalue()
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        parsed = None
    return {"command": display, "exit": code, "output": parsed, "raw": raw}


# commands that only read state -- the only ones allowed in --read-only mode
READ_ONLY_COMMANDS = {
    "schema", "dataset", "query", "sample", "stats", "dist", "hist", "columns",
    "grade", "op", "recipe", "lineage", "snapshot", "index", "recall",
    "model", "contam", "codex-check",
}
# op/recipe/index/model/contam have read-only subcommands only-ish; we further
# block the mutating leaf subcommands below.
READ_ONLY_BLOCK_SUB = {
    ("op", "run"), ("recipe", "export"), ("index", "build"),
    ("model", "add"), ("model", "remove"), ("contam", "add"),
    ("snapshot", "create"), ("dataset", "add"),
}


def _is_read_only(argv) -> bool:
    if not argv:
        return True
    cmd = argv[0]
    if cmd not in READ_ONLY_COMMANDS:
        return False
    sub = argv[1] if len(argv) > 1 else None
    return (cmd, sub) not in READ_ONLY_BLOCK_SUB


def _make_handler(store, base_recipe, read_only=False, token=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, obj, status=200, ctype="application/json"):
            body = obj if isinstance(obj, bytes) else json.dumps(
                obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # -- routing -------------------------------------------------------
        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if u.path in ("/", "/index.html"):
                    return self._send(PAGE.encode("utf-8"),
                                      ctype="text/html; charset=utf-8")
                if u.path == "/api/overview":
                    return self._send(self.overview())
                if u.path == "/api/dist":
                    return self._send(store.catalog.distribution(
                        q.get("column", "domain"), where=q.get("filter")))
                if u.path == "/api/hist":
                    return self._send(store.catalog.histogram(
                        q.get("column", "quality_score"),
                        bins=int(q.get("bins", 12)), where=q.get("filter")))
                if u.path == "/api/recipe":
                    return self._send(self.recipe_doc())
                if u.path == "/api/columns":
                    return self._send(store.catalog.columns_overview())
                if u.path == "/api/grade":
                    tiers = ([float(x) for x in q["tiers"].split(",")]
                             if q.get("tiers") else None)
                    return self._send(R.grade(
                        store, where=q.get("filter") or None,
                        column=q.get("column", "quality_score"), thresholds=tiers))
                if u.path == "/api/exports":
                    return self._send(self.exports())
                if u.path == "/api/download":
                    return self.download(q.get("path", ""))
                if u.path == "/api/download_zip":
                    return self.download_zip(q.get("id", ""))
                return self._send({"error": "not found"}, 404)
            except Exception as e:  # noqa: BLE001
                return self._send({"error": str(e)}, 400)

        def do_POST(self):
            u = urlparse(self.path)
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            try:
                if u.path == "/api/cli":
                    if token and self.headers.get("X-DM-Token") != token:
                        return self._send({"error": "unauthorized"}, 401)
                    import shlex as _shlex
                    argv = (payload.get("argv")
                            or _shlex.split(payload.get("line") or ""))
                    if read_only and not _is_read_only(argv):
                        return self._send(
                            {"command": "datamixer " + " ".join(argv), "exit": 1,
                             "output": {"error": "console is read-only; "
                                        f"'{argv[0] if argv else ''}' is blocked"},
                             "raw": ""})
                    res = run_cli(store.root, argv=payload.get("argv"),
                                  line=payload.get("line"),
                                  files=payload.get("files"))
                    self._refresh()       # drop stale in-memory caches
                    return self._send(res)
                return self._send({"error": "not found"}, 404)
            except Exception as e:  # noqa: BLE001
                return self._send({"error": str(e)}, 400)

        # -- helpers -------------------------------------------------------
        def _refresh(self):
            # the CLI wrote through a separate connection / index file; reload
            if store._index is not None:
                with contextlib.suppress(Exception):
                    store._index.close()
                store._index = None

        def overview(self):
            return {
                "storage": store.storage_stats(),
                "index": store.index.stats(),
                "datasets": store.catalog.list_datasets(),
            }

        # -- exports / downloads ------------------------------------------
        def _safe(self, rel: str) -> Path:
            root = store.root.resolve()
            p = (root / rel).resolve()
            if root not in p.parents and p != root:
                raise ValueError("path escapes warehouse")
            return p

        def exports(self):
            out = []
            d = store.root / "exports"
            if d.is_dir():
                for m in sorted(d.glob("*/manifest.json")):
                    try:
                        doc = json.loads(m.read_text())
                    except (json.JSONDecodeError, OSError):
                        continue
                    out.append({
                        "export_id": doc.get("export_id"),
                        "format": doc.get("format"),
                        "dir": str(m.parent.relative_to(store.root)),
                        "samples": (doc.get("summary") or {}).get("selected_samples"),
                        "files": doc.get("files", []),
                    })
            return {"root": str(store.root), "exports": out}

        def _send_file(self, data, filename, ctype="application/octet-stream"):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Disposition",
                             f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def download(self, rel):
            p = self._safe(rel)
            if not p.is_file():
                return self._send({"error": "not a file"}, 404)
            self._send_file(p.read_bytes(), p.name)

        def download_zip(self, export_id):
            import io
            import zipfile
            d = self._safe(f"exports/{export_id}")
            if not d.is_dir():
                return self._send({"error": "export not found"}, 404)
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for f in sorted(d.rglob("*")):
                    if f.is_file():
                        z.write(f, f.relative_to(d))
            self._send_file(buf.getvalue(), f"{export_id}.zip", "application/zip")

        def recipe_doc(self):
            r = base_recipe
            return {
                "name": r.name, "stage": r.stage,
                "total_tokens": r.total_tokens, "total_samples": r.total_samples,
                "strategy": r.strategy,
                "buckets": [{"name": b.name, "weight": b.weight,
                             "filter": b.filter, "query": b.semantic_query,
                             "keyword": b.keyword} for b in r.buckets],
            }

    return Handler


def serve(store, host="127.0.0.1", port=8848, recipe_path=None,
          read_only=False, token=None):
    if recipe_path:
        base_recipe = R.load_recipe(recipe_path)
    else:
        dims = store.catalog.distribution("domain")
        buckets = [{"name": d["value"] or "unknown", "weight": 1.0,
                    "filter": f"domain = '{d['value']}'"}
                   for d in dims if d["value"]][:6] or \
                  [{"name": "all", "weight": 1.0, "filter": ""}]
        base_recipe = R.parse_recipe({"recipe": {
            "name": "interactive-mix", "total_samples": 1000,
            "buckets": buckets,
            "sampling": {"strategy": "weighted_sample"},
            "export": {"format": "jsonl"}}})
    # safety: a non-loopback bind without a token is refused unless explicitly
    # opted out, since /api/cli drives the CLI.
    if host not in ("127.0.0.1", "localhost", "::1") and not token and not read_only:
        raise ValueError(
            f"refusing to bind {host} without --token or --read-only "
            "(/api/cli executes commands)")
    handler = _make_handler(store, base_recipe, read_only=read_only, token=token)
    httpd = HTTPServer((host, port), handler)
    print(f"DataMixer console on http://{host}:{port}  (Ctrl-C to stop)")
    httpd.serve_forever()


# ---------------------------------------------------------------------------
# Single-page app. Every action mirrors a CLI command and shows it.
# Static text carries data-i18n keys; JS-built text uses t(); language toggle
# lives in the sidebar and persists to localStorage.
# ---------------------------------------------------------------------------

PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>DataMixer Console</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
 :root{--bg:#0f1117;--card:#181b24;--fg:#e6e6e6;--mut:#8b93a7;--acc:#5b8def;
   --ok:#46c2a0;--err:#e5736b;--bd:#232838;--in:#0f1117}
 *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--fg);
   font:14px/1.5 system-ui,Segoe UI,Roboto,"PingFang SC","Microsoft YaHei",sans-serif;
   display:flex;min-height:100vh}
 nav{width:208px;background:#11141c;border-right:1px solid var(--bd);padding:14px 0;
   position:sticky;top:0;height:100vh}
 nav h1{font-size:17px;margin:0 16px 4px} nav .sub{font-size:11px;color:var(--mut);
   margin:0 16px 10px}
 nav a{display:block;padding:9px 16px;color:var(--mut);cursor:pointer;
   border-left:3px solid transparent;text-decoration:none}
 nav a:hover{color:var(--fg);background:#161a24} nav a.on{color:var(--fg);
   border-left-color:var(--acc);background:#161a24}
 #langbtn{margin:0 16px 12px;padding:4px 10px;font-size:12px;background:#2a3142;
   color:var(--fg);border:1px solid var(--bd);border-radius:6px;cursor:pointer}
 main{flex:1;padding:20px;max-width:1200px}
 .panel{display:none} .panel.on{display:block}
 .grid{display:grid;gap:16px;grid-template-columns:repeat(12,1fr)}
 .card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:14px}
 .card h2{font-size:13px;margin:0 0 10px;color:var(--mut);text-transform:uppercase;
   letter-spacing:.05em}
 .c3{grid-column:span 3}.c4{grid-column:span 4}.c6{grid-column:span 6}
 .c8{grid-column:span 8}.c12{grid-column:span 12}
 .stat{display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px dashed var(--bd)}
 .stat b{color:var(--acc)} .chart{height:240px}
 table{width:100%;border-collapse:collapse;font-size:12px} th,td{text-align:left;
   padding:4px 6px;border-bottom:1px solid var(--bd);white-space:nowrap;
   overflow:hidden;text-overflow:ellipsis;max-width:280px}
 th{color:var(--mut)} label.f{display:block;color:var(--mut);font-size:12px;margin:8px 0 2px}
 input,select,textarea{width:100%;background:var(--in);color:var(--fg);
   border:1px solid #2a3142;border-radius:6px;padding:7px;font:inherit}
 textarea{min-height:120px;font-family:ui-monospace,monospace;font-size:12px}
 .row{display:flex;gap:10px;flex-wrap:wrap} .row>div{flex:1;min-width:140px}
 button{background:var(--acc);color:#fff;border:0;border-radius:6px;padding:8px 14px;
   cursor:pointer;font:inherit;margin-top:10px} button.alt{background:#2a3142}
 button:hover{filter:brightness(1.1)}
 .muted{color:var(--mut)} .pill{font-size:11px;color:var(--mut)}
 pre{white-space:pre-wrap;word-break:break-word;margin:0;font-size:12px;color:#b9c2d6}
 .cmd{font-family:ui-monospace,monospace;font-size:12px;background:#0b0d13;
   border:1px solid var(--bd);border-radius:6px;padding:8px;color:#9fd1a0;margin-top:10px}
 .out{margin-top:8px;max-height:360px;overflow:auto;background:#0b0d13;border-radius:6px;
   padding:10px;border:1px solid var(--bd)}
 .brow{display:flex;align-items:center;gap:8px;margin:6px 0}
 .brow label{width:130px;color:var(--mut)} .brow input[type=range]{flex:1}
 .brow .w{width:46px;text-align:right;color:var(--acc)}
 .ok{color:var(--ok)} .err{color:var(--err)} h3{margin:14px 0 4px;font-size:14px}
 .lnk{color:var(--acc);cursor:pointer;text-decoration:underline;font-size:12px}
 .modal{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;
   align-items:center;justify-content:center;z-index:50}
 .modal.on{display:flex}
 .modal .box{background:var(--card);border:1px solid var(--bd);border-radius:10px;
   padding:18px;width:min(560px,92vw);max-height:88vh;overflow:auto}
 .modal .x{float:right;cursor:pointer;color:var(--mut);font-size:18px}
 /* responsive / adaptive layout */
 @media (max-width:1100px){ .c3{grid-column:span 6}.c4{grid-column:span 6}
   .c8{grid-column:span 12} }
 @media (max-width:820px){
   body{flex-direction:column}
   nav{width:100%;height:auto;position:sticky;top:0;z-index:10;display:flex;
     flex-wrap:wrap;align-items:center;gap:2px;padding:8px 10px}
   nav h1{margin:0 8px 0 0;font-size:16px} nav .sub{display:none}
   nav #langbtn{margin:0 4px 0 auto;order:2}
   nav a{border-left:0;border-bottom:2px solid transparent;padding:6px 9px;
     border-radius:6px}
   nav a.on{border-left:0;border-bottom-color:var(--acc)}
   main{padding:12px;max-width:100%}
   .c3,.c4,.c6,.c8{grid-column:span 12}
 }
 @media (max-width:560px){ .row>div{min-width:100%} .chart{height:200px}
   .out{max-height:300px} }
</style></head><body>
<nav>
  <h1>DataMixer</h1><div class="sub" id="nsub">console</div>
  <button id="langbtn" onclick="toggleLang()">中文</button>
  <a data-p="dash" class="on" data-i18n="nav.dash">Dashboard</a>
  <a data-p="datasets" data-i18n="nav.datasets">Datasets &amp; Ingest</a>
  <a data-p="query" data-i18n="nav.query">Query</a>
  <a data-p="ops" data-i18n="nav.ops">Operators &amp; Pipeline</a>
  <a data-p="models" data-i18n="nav.models">Models &amp; Classify</a>
  <a data-p="index" data-i18n="nav.index">Index &amp; Recall</a>
  <a data-p="recipe" data-i18n="nav.recipe">Recipe Mix</a>
  <a data-p="lineage" data-i18n="nav.lineage">Lineage</a>
  <a data-p="cli" data-i18n="nav.cli">CLI Console</a>
</nav>
<main>
  <!-- DASHBOARD -->
  <section class="panel on" id="p-dash"><div class="grid">
    <div class="card c3"><h2 data-i18n="h.storage">Storage</h2><div id="storage"></div></div>
    <div class="card c3"><h2 data-i18n="h.index">Index</h2><div id="idxstat"></div></div>
    <div class="card c3"><h2 data-i18n="h.domainMix">Domain mix</h2><div id="pie_domain" class="chart" style="height:160px"></div></div>
    <div class="card c3"><h2 data-i18n="h.langMix">Language mix</h2><div id="pie_lang" class="chart" style="height:160px"></div></div>
    <div class="card c6"><h2 data-i18n="h.qualityDist">Quality score distribution</h2><div id="hist_q" class="chart"></div></div>
    <div class="card c6"><h2 data-i18n="h.stageDist">Stage distribution</h2><div id="bar_stage" class="chart"></div></div>
  </div></section>

  <!-- DATASETS -->
  <section class="panel" id="p-datasets"><div class="grid">
    <div class="card c12"><h2 data-i18n="h.datasets">Datasets</h2><div id="dstable"></div></div>
    <div class="card c6"><h2 data-i18n="h.registerDs">Register dataset</h2>
      <label class="f" data-i18n="f.name">name</label><input id="ds_name">
      <div class="row"><div><label class="f" data-i18n="f.source">source</label><input id="ds_src"></div>
        <div><label class="f" data-i18n="f.license">license</label><input id="ds_lic"></div></div>
      <label class="f" data-i18n="f.desc">description</label><input id="ds_desc">
      <button onclick="dsAdd()" data-i18n="btn.dsAdd">dataset add</button>
      <div id="ds_res"></div></div>
    <div class="card c6"><h2 data-i18n="h.ingest">Ingest data</h2>
      <div class="row"><div><label class="f" data-i18n="f.dsNameId">dataset (name/id)</label><input id="ig_ds" list="dslist"></div>
        <div><label class="f" data-i18n="f.stage">stage</label><select id="ig_stage">
          <option value="">-</option><option>pretrain</option><option>sft</option>
          <option>preference</option><option>agent_trajectory</option></select></div></div>
      <div class="row"><div><label class="f" data-i18n="f.domain">domain</label><input id="ig_dom"></div>
        <div><label class="f" data-i18n="f.lang">lang</label><input id="ig_lang"></div>
        <div><label class="f" data-i18n="f.contentKey">content-key</label><input id="ig_ck" value="content"></div></div>
      <label class="f" data-i18n="f.jsonl">JSONL data (one record per line)</label>
      <textarea id="ig_data" placeholder='{"content":{"text":"hello"},"domain":"web"}'></textarea>
      <button onclick="ingest()" data-i18n="btn.ingest">ingest</button>
      <div id="ig_res"></div></div>
    <div class="card c12"><h2 data-i18n="h.agentIngest">Agent ingest - any format</h2>
      <p class="muted" data-i18n="help.agentIngest">An LLM reviews the file's format
        and tags, then ingests it. Paste any text format (CSV/JSON/JSONL/TXT/...).</p>
      <div class="row">
        <div><label class="f" data-i18n="f.filename">filename (for format hint)</label>
          <input id="ag_name" placeholder="data.csv"></div>
        <div><label class="f" data-i18n="f.engine">engine</label>
          <select id="ag_engine"><option value="auto">auto</option>
            <option value="codex">codex (LoopAI runner)</option>
            <option value="builtin">builtin (offline)</option></select></div>
        <div><label class="f" data-i18n="f.model">runner model (pool)</label>
          <select id="ag_model"><option value="">(heuristic, no model)</option></select></div>
        <div><label class="f" data-i18n="f.dataset">dataset</label><input id="ag_ds" list="dslist"></div></div>
      <label class="f" data-i18n="f.fileContent">file content</label>
      <textarea id="ag_data" placeholder="question,answer,category&#10;2+2,four,math"></textarea>
      <label class="brow"><input type="checkbox" id="ag_dry">
        <span data-i18n="f.dryRun">dry-run (review &amp; plan only)</span></label>
      <button onclick="agentIngest()" data-i18n="btn.agentIngest">agent-ingest</button>
      <div id="ag_res"></div></div>
  </div></section>

  <!-- QUERY -->
  <section class="panel" id="p-query"><div class="card c12">
    <h2 data-i18n="h.querySamples">Query samples</h2>
    <div class="row">
      <div style="flex:3"><label class="f" data-i18n="f.filterSql">filter (SQL predicate)</label>
        <input id="q_filter" placeholder="domain='code' AND quality_score>0.6"></div>
      <div><label class="f" data-i18n="f.dataset">dataset</label><input id="q_ds" list="dslist"></div>
      <div><label class="f" data-i18n="f.limit">limit</label><input id="q_limit" type="number" value="25"></div>
    </div>
    <label class="f" data-i18n="f.columns">columns</label>
    <input id="q_cols" value="sample_id,dataset_id,domain,lang,stage,quality_score,n_tokens,cid">
    <button onclick="runQuery()" data-i18n="btn.query">query</button>
    <div id="q_res"></div></div></section>

  <!-- OPERATORS -->
  <section class="panel" id="p-ops"><div class="grid">
    <div class="card c12"><h2 data-i18n="h.regOps">Registered operators</h2><div id="oplist"></div>
      <button class="alt" onclick="opList()" data-i18n="btn.opList">op list</button></div>
    <div class="card c6"><h2 data-i18n="h.runOp">Run operator</h2>
      <div class="row"><div><label class="f" data-i18n="f.operator">operator</label><select id="op_name"></select></div>
        <div><label class="f" data-i18n="f.dataset">dataset</label><input id="op_ds" list="dslist"></div></div>
      <label class="f" data-i18n="f.filterOpt">filter (optional)</label><input id="op_filter">
      <label class="f" data-i18n="f.args">args (key=value per line, value is JSON)</label>
      <textarea id="op_args" style="min-height:60px" placeholder="k=3"></textarea>
      <button onclick="opRun()" data-i18n="btn.opRun">op run</button><div id="op_res"></div></div>
    <div class="card c6"><h2 data-i18n="h.runPipe">Run pipeline</h2>
      <label class="f" data-i18n="f.pipelineYaml">pipeline YAML</label>
      <textarea id="pl_yaml" style="min-height:200px"></textarea>
      <button onclick="pipeRun()" data-i18n="btn.pipeRun">pipeline run</button><div id="pl_res"></div></div>
  </div></section>

  <!-- MODELS & CLASSIFY -->
  <section class="panel" id="p-models"><div class="grid">
    <div class="card c12"><h2 data-i18n="h.modelPool">Model pool</h2><div id="mptable"></div>
      <button class="alt" onclick="modelList()" data-i18n="btn.modelRefresh">model list</button></div>
    <div class="card c6"><h2 data-i18n="h.regModel">Register model</h2>
      <div class="row"><div><label class="f" data-i18n="f.name">name</label><input id="m_name"></div>
        <div><label class="f" data-i18n="f.format">response format</label><select id="m_fmt">
          <option value="openaichat">openaichat</option><option value="response">response</option>
        </select></div></div>
      <label class="f" data-i18n="f.apiUrl">api url</label><input id="m_url" placeholder="https://api.openai.com/v1/chat/completions">
      <div class="row"><div><label class="f" data-i18n="f.apiKey">api key (or env:VAR)</label><input id="m_key" placeholder="env:OPENAI_API_KEY"></div>
        <div><label class="f" data-i18n="f.modelId">provider model id</label><input id="m_model" placeholder="gpt-4o-mini"></div></div>
      <label class="f" data-i18n="f.note">note</label><input id="m_note">
      <div class="row"><div><label class="f" data-i18n="f.maxConc">max concurrency</label><input id="m_conc" type="number" value="64"></div>
        <div><label class="f" data-i18n="f.temperature">temperature</label><input id="m_temp" type="number" step="0.1" value="0"></div></div>
      <button onclick="modelAdd()" data-i18n="btn.modelAdd">model add</button>
      <div id="m_res"></div></div>
    <div class="card c6"><h2 data-i18n="h.classify">Domain classify (LLM)</h2>
      <div class="row"><div><label class="f" data-i18n="f.dataset">dataset</label><input id="cl_ds" list="dslist"></div>
        <div><label class="f" data-i18n="f.model">model</label><select id="cl_model"></select></div></div>
      <label class="f" data-i18n="f.labels">labels (comma-separated)</label>
      <input id="cl_labels" placeholder="code,math,web,science,agent">
      <div class="row"><div><label class="f" data-i18n="f.chunkSize">chunk size (&le;10)</label><input id="cl_chunk" type="number" value="10"></div>
        <div><label class="f" data-i18n="f.maxConc">max concurrency</label><input id="cl_conc" type="number" value="64"></div></div>
      <label class="f" data-i18n="f.filterOpt">filter (optional)</label><input id="cl_filter">
      <button onclick="classify()" data-i18n="btn.classify">run domain_classify</button>
      <div id="cl_res"></div></div>
  </div></section>

  <!-- INDEX & RECALL -->
  <section class="panel" id="p-index"><div class="grid">
    <div class="card c4"><h2 data-i18n="h.indexL3">Index (L3)</h2><div id="ixstat2"></div>
      <button onclick="ixBuild('')" data-i18n="btn.ixBuild">index build</button>
      <button class="alt" onclick="ixBuild('--vector-only')" data-i18n="btn.ixVec">vector only</button>
      <button class="alt" onclick="ixBuild('--fulltext-only')" data-i18n="btn.ixFt">fulltext only</button>
      <div id="ix_res"></div></div>
    <div class="card c8"><h2 data-i18n="h.recall">Recall</h2>
      <div class="row"><div style="flex:3"><label class="f" data-i18n="f.queryText">query / match text</label>
        <input id="rc_q" data-i18n-ph="ph.recallText" placeholder="function that returns a value"></div>
        <div><label class="f" data-i18n="f.mode">mode</label><select id="rc_mode">
          <option value="query" data-i18n="opt.semantic">semantic</option>
          <option value="match" data-i18n="opt.keyword">keyword</option></select></div>
        <div><label class="f" data-i18n="f.limit">limit</label><input id="rc_limit" type="number" value="8"></div></div>
      <label class="f" data-i18n="f.restrictFilter">restrict filter (optional)</label><input id="rc_filter">
      <button onclick="recall()" data-i18n="btn.recall">recall</button><div id="rc_res"></div></div>
  </div></section>

  <!-- RECIPE -->
  <section class="panel" id="p-recipe"><div class="grid">
    <div class="card c6"><h2 data-i18n="h.mixEditor">Mix editor</h2>
      <div id="buckets"></div>
      <div class="row"><div><label class="f" data-i18n="f.budget">budget</label><input id="r_budget" type="number" value="1000"></div>
        <div><label class="f" data-i18n="f.budgetKind">budget kind</label><select id="r_kind">
          <option value="total_samples" data-i18n="opt.samples">samples</option>
          <option value="total_tokens" data-i18n="opt.tokens">tokens</option></select></div>
        <div><label class="f" data-i18n="f.strategy">strategy</label><select id="r_strat">
          <option>weighted_sample</option><option>weighted_token</option>
          <option>temperature</option><option>epoch_repeat</option></select></div></div>
      <div class="brow" style="margin-top:8px">
        <label style="width:auto"><input type="checkbox" id="q_enable" onchange="renderTiers()">
          <span data-i18n="f.qgrade">quality grading (applies to all buckets)</span></label></div>
      <div id="q_tiers"></div>
      <div id="q_extra" style="display:none">
        <button class="alt" onclick="addTier()" data-i18n="btn.addTier">+ tier</button>
        <label class="brow"><input type="checkbox" id="q_best">
          <span data-i18n="f.qbest">draw best-quality first</span></label></div>
      <button onclick="rPlan()" data-i18n="btn.rPlan">recipe plan</button>
      <button class="alt" onclick="rPreview()" data-i18n="btn.rPreview">recipe preview</button>
      <button class="alt" onclick="rExport()" data-i18n="btn.rExport">recipe export</button>
      <span class="pill" id="r_fp"></span>
      <div id="plan_chart" class="chart"></div>
      <div id="r_warn" class="muted"></div><div id="r_res"></div></div>
    <div class="card c6"><h2 data-i18n="h.rawRecipe">Raw recipe (YAML/JSON)</h2>
      <textarea id="r_yaml" style="min-height:240px"></textarea>
      <button onclick="rRaw('validate')" data-i18n="btn.validate">validate</button>
      <button class="alt" onclick="rRaw('plan')" data-i18n="btn.plan">plan</button>
      <button class="alt" onclick="rRaw('preview')" data-i18n="btn.preview">preview</button>
      <button class="alt" onclick="rRaw('export')" data-i18n="btn.export">export</button>
      <div id="rr_res"></div></div>
    <div class="card c6"><h2 data-i18n="h.columns">Available columns (what you can export/filter)</h2>
      <button class="alt" onclick="loadColumns()" data-i18n="btn.columns">columns</button>
      <div id="cols_res"></div></div>
    <div class="card c6"><h2 data-i18n="h.quality">Quality-graded recall</h2>
      <div class="row"><div><label class="f" data-i18n="f.filterOpt">filter (optional)</label><input id="g_filter"></div>
        <div><label class="f" data-i18n="f.tiers">tiers (descending)</label><input id="g_tiers" value="0.8,0.6,0.4"></div></div>
      <button onclick="grade()" data-i18n="btn.grade">grade</button>
      <div id="grade_chart" class="chart"></div><div id="g_res"></div></div>
  </div></section>

  <!-- LINEAGE -->
  <section class="panel" id="p-lineage"><div class="grid">
    <div class="card c12"><h2 data-i18n="h.exports">Exports (download)</h2>
      <div class="muted" id="exp_root"></div>
      <button class="alt" onclick="listExports()" data-i18n="btn.exportsRefresh">refresh</button>
      <div id="exp_res"></div></div>
    <div class="card c12"><h2 data-i18n="h.lineageExports">Lineage</h2>
      <button class="alt" onclick="lineage()" data-i18n="btn.lineageList">lineage list</button>
      <div id="ln_res"></div></div>
  </div></section>

  <!-- CLI -->
  <section class="panel" id="p-cli"><div class="card c12">
    <h2 data-i18n="h.cliConsole">CLI console</h2>
    <p class="muted" data-i18n="help.cli">Type any datamixer command (without
      datamixer / --root / --json). Same engine as the terminal.</p>
    <input id="cli_line" placeholder="query --filter &quot;domain='code'&quot; --limit 5"
      onkeydown="if(event.key==='Enter')cliRun()">
    <button onclick="cliRun()" data-i18n="btn.run">run</button>
    <div class="muted" style="margin-top:8px"><span data-i18n="help.examples">examples:</span>
      <code>stats</code> · <code>dataset list</code> · <code>op list</code> ·
      <code>dist domain</code> · <code>index stats</code></div>
    <div id="cli_res"></div></div></section>

  <!-- OPERATOR EDIT MODAL -->
  <div class="modal" id="opmodal"><div class="box">
    <span class="x" onclick="closeOpModal()">&#10005;</span>
    <h3 id="opm_title"></h3><p class="muted" id="opm_desc"></p>
    <div class="row"><div><label class="f" data-i18n="f.dataset">dataset</label><input id="opm_ds" list="dslist"></div>
      <div><label class="f" data-i18n="f.filterOpt">filter (optional)</label><input id="opm_filter"></div></div>
    <div id="opm_fields"></div>
    <button onclick="opmRun()" data-i18n="btn.opRun">op run</button>
    <div id="opm_res"></div>
  </div></div>
  <datalist id="dslist"></datalist>
</main>
<script>
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const E=window.echarts, charts={};
let OPS=[], MODELS=[];

// ---- i18n ----
const I18N={
 en:{'nav.dash':'Dashboard','nav.datasets':'Datasets & Ingest','nav.query':'Query',
   'nav.ops':'Operators & Pipeline','nav.models':'Models & Classify',
   'nav.index':'Index & Recall','nav.recipe':'Recipe Mix',
   'nav.lineage':'Lineage','nav.cli':'CLI Console',
   'h.modelPool':'Model pool','h.regModel':'Register model','h.classify':'Domain classify (LLM)',
   'h.agentIngest':'Agent ingest - any format',
   'help.agentIngest':"The LoopAI runner (kernel model from the pool) reviews the "
     +"file and drives the CLI to ingest it; 'builtin' is an offline heuristic. "
     +"Paste any text format (CSV/JSON/JSONL/TXT/...).",
   'f.filename':'filename (for format hint)','f.fileContent':'file content',
   'f.dryRun':'dry-run (review & plan only)','opt.heuristic':'(heuristic, no model)',
   'f.engine':'engine','btn.agentIngest':'Agent ingest',
   'f.note':'note','f.apiUrl':'api url','f.apiKey':'api key (or env:VAR)','f.format':'response format',
   'f.modelId':'provider model id','f.model':'model','f.labels':'labels (comma-separated)',
   'f.chunkSize':'chunk size (<=10)','f.maxConc':'max concurrency','f.temperature':'temperature',
   'btn.modelAdd':'Add model','btn.modelRefresh':'Refresh','btn.classify':'Run domain_classify',
   'h.storage':'Storage','h.index':'Index','h.domainMix':'Domain mix','h.langMix':'Language mix',
   'h.qualityDist':'Quality score distribution','h.stageDist':'Stage distribution',
   'h.datasets':'Datasets','h.registerDs':'Register dataset','h.ingest':'Ingest data',
   'h.querySamples':'Query samples','h.regOps':'Registered operators','h.runOp':'Run operator',
   'h.runPipe':'Run pipeline','h.indexL3':'Index (L3)','h.recall':'Recall','h.mixEditor':'Mix editor',
   'h.rawRecipe':'Raw recipe (YAML/JSON)','h.lineageExports':'Lineage & exports','h.cliConsole':'CLI console',
   'f.name':'name','f.source':'source','f.license':'license','f.desc':'description',
   'f.dsNameId':'dataset (name/id)','f.stage':'stage','f.domain':'domain','f.lang':'lang',
   'f.contentKey':'content-key','f.jsonl':'JSONL data (one record per line)',
   'f.filterSql':'filter (SQL predicate)','f.dataset':'dataset','f.limit':'limit','f.columns':'columns',
   'f.operator':'operator','f.filterOpt':'filter (optional)','f.args':'args (key=value per line, value is JSON)',
   'f.pipelineYaml':'pipeline YAML','f.queryText':'query / match text','f.mode':'mode',
   'f.restrictFilter':'restrict filter (optional)','f.budget':'budget','f.budgetKind':'budget kind',
   'f.strategy':'strategy','opt.samples':'samples','opt.tokens':'tokens','opt.semantic':'semantic',
   'opt.keyword':'keyword','ph.recallText':'function that returns a value',
   'help.cli':'Type any datamixer command (without datamixer / --root / --json). Same engine as the terminal.',
   'help.examples':'examples:',
   'w.samples':'samples','w.uniqueBlobs':'unique blobs','w.stored':'stored','w.catalogDb':'catalog db',
   'w.dedup':'dedup','w.compression':'compression','w.contentSavings':'content savings',
   'w.vectors':'vectors','w.dim':'dim','w.fulltextDocs':'fulltext docs','w.available':'available',
   'w.target':'target','w.id':'id','w.name':'name','w.samplesCol':'samples','w.licenseCol':'license',
   'w.version':'v','w.kind':'kind','w.descCol':'desc','w.path':'path','w.rows':'rows','w.opName':'name',
   'btn.dsAdd':'Add dataset','btn.ingest':'Ingest','btn.query':'Query',
   'btn.opList':'List operators','btn.opRun':'Run operator','btn.pipeRun':'Run pipeline',
   'btn.ixBuild':'Build index','btn.ixVec':'Vectors only','btn.ixFt':'Fulltext only',
   'btn.recall':'Recall','btn.rPlan':'Plan','btn.rPreview':'Preview','btn.rExport':'Export',
   'btn.validate':'Validate','btn.plan':'Plan','btn.preview':'Preview','btn.export':'Export',
   'btn.lineageList':'List lineage','btn.run':'Run',
   'h.columns':'Available columns (what you can export/filter)',
   'h.quality':'Quality-graded recall','h.exports':'Exports (download)',
   'f.tiers':'tiers (descending)','f.qgrade':'quality grading (applies to all buckets)',
   'f.qbest':'draw best-quality first','btn.addTier':'+ tier',
   'btn.columns':'columns','btn.grade':'grade','btn.exportsRefresh':'refresh',
   'btn.edit':'edit','btn.downloadZip':'download .zip',
   'w.tags':'custom tags','w.tier':'tier','w.range':'range',
   'msg.exportLoc':'Exports are written to:',
   'msg.noHits':'no hits — build the index first'},
 zh:{'nav.dash':'仪表盘','nav.datasets':'数据集与入库','nav.query':'查询',
   'nav.ops':'算子与流水线','nav.models':'模型与分类',
   'nav.index':'索引与召回','nav.recipe':'配比混合',
   'nav.lineage':'血缘','nav.cli':'命令行',
   'h.modelPool':'模型池','h.regModel':'注册模型','h.classify':'领域分类（LLM）',
   'h.agentIngest':'智能入库 - 任意格式',
   'help.agentIngest':'LoopAI runner（内核模型取自模型池）审查文件并调用 CLI 入库；builtin 为离线启发式。可粘贴任意文本格式（CSV/JSON/JSONL/TXT/…）。',
   'f.filename':'文件名（用于格式识别）','f.fileContent':'文件内容',
   'f.dryRun':'试运行（仅审查与方案，不入库）','opt.heuristic':'（启发式，不用模型）',
   'f.engine':'引擎','btn.agentIngest':'智能入库',
   'f.note':'备注','f.apiUrl':'API 地址','f.apiKey':'API 密钥（或 env:变量名）','f.format':'响应格式',
   'f.modelId':'提供方模型名','f.model':'模型','f.labels':'标签（逗号分隔）',
   'f.chunkSize':'每批条数（≤10）','f.maxConc':'最大并发','f.temperature':'温度',
   'btn.modelAdd':'添加模型','btn.modelRefresh':'刷新','btn.classify':'运行 domain_classify',
   'h.storage':'存储','h.index':'索引','h.domainMix':'领域分布','h.langMix':'语言分布',
   'h.qualityDist':'质量分分布','h.stageDist':'阶段分布',
   'h.datasets':'数据集','h.registerDs':'注册数据集','h.ingest':'数据入库',
   'h.querySamples':'查询样本','h.regOps':'已注册算子','h.runOp':'运行算子',
   'h.runPipe':'运行流水线','h.indexL3':'索引 (L3)','h.recall':'召回','h.mixEditor':'配比编辑器',
   'h.rawRecipe':'原始配比 (YAML/JSON)','h.lineageExports':'血缘与导出','h.cliConsole':'命令行控制台',
   'f.name':'名称','f.source':'来源','f.license':'许可证','f.desc':'描述',
   'f.dsNameId':'数据集（名称/ID）','f.stage':'训练阶段','f.domain':'领域','f.lang':'语言',
   'f.contentKey':'内容字段名','f.jsonl':'JSONL 数据（每行一条记录）',
   'f.filterSql':'过滤条件（SQL 谓词）','f.dataset':'数据集','f.limit':'上限','f.columns':'列',
   'f.operator':'算子','f.filterOpt':'过滤条件（可选）','f.args':'参数（每行 key=value，值为 JSON）',
   'f.pipelineYaml':'流水线 YAML','f.queryText':'查询 / 匹配文本','f.mode':'模式',
   'f.restrictFilter':'限定过滤（可选）','f.budget':'预算','f.budgetKind':'预算类型',
   'f.strategy':'采样策略','opt.samples':'按条数','opt.tokens':'按 token','opt.semantic':'语义',
   'opt.keyword':'关键词','ph.recallText':'返回某个值的函数',
   'help.cli':'输入任意 datamixer 命令（无需 datamixer / --root / --json），与终端同一引擎。',
   'help.examples':'示例：',
   'w.samples':'样本','w.uniqueBlobs':'唯一块','w.stored':'已存储','w.catalogDb':'目录库',
   'w.dedup':'去重','w.compression':'压缩','w.contentSavings':'内容节省',
   'w.vectors':'向量','w.dim':'维度','w.fulltextDocs':'全文文档','w.available':'可用',
   'w.target':'目标','w.id':'ID','w.name':'名称','w.samplesCol':'样本数','w.licenseCol':'许可证',
   'w.version':'版本','w.kind':'类型','w.descCol':'描述','w.path':'路径','w.rows':'行','w.opName':'名称',
   'btn.dsAdd':'添加数据集','btn.ingest':'入库','btn.query':'查询',
   'btn.opList':'刷新算子','btn.opRun':'运行算子','btn.pipeRun':'运行流水线',
   'btn.ixBuild':'构建索引','btn.ixVec':'仅向量','btn.ixFt':'仅全文',
   'btn.recall':'召回','btn.rPlan':'规划','btn.rPreview':'预览','btn.rExport':'导出',
   'btn.validate':'校验','btn.plan':'规划','btn.preview':'预览','btn.export':'导出',
   'btn.lineageList':'列出血缘','btn.run':'运行',
   'h.columns':'可用列（可导出/过滤的字段）',
   'h.quality':'质量分级召回','h.exports':'导出包（下载）',
   'f.tiers':'分级阈值（降序）','f.qgrade':'质量分级（应用于所有桶）',
   'f.qbest':'优先选取高质量','btn.addTier':'+ 档位',
   'btn.columns':'列预览','btn.grade':'分级','btn.exportsRefresh':'刷新',
   'btn.edit':'编辑','btn.downloadZip':'下载 .zip',
   'w.tags':'自定义标签','w.tier':'等级','w.range':'区间',
   'msg.exportLoc':'导出包写入：',
   'msg.noHits':'无结果 — 请先构建索引'}
};
let LANG=localStorage.getItem('dm_lang')||((navigator.language||'en').toLowerCase().startsWith('zh')?'zh':'en');
function t(k){return (I18N[LANG]&&I18N[LANG][k])||I18N.en[k]||k;}
function applyI18n(){
  document.documentElement.lang=LANG;
  $$('[data-i18n]').forEach(e=>e.textContent=t(e.getAttribute('data-i18n')));
  $$('[data-i18n-ph]').forEach(e=>e.setAttribute('placeholder',t(e.getAttribute('data-i18n-ph'))));
  $('#langbtn').textContent=LANG==='zh'?'EN':'中文';
}
function toggleLang(){LANG=(LANG==='zh')?'en':'zh';localStorage.setItem('dm_lang',LANG);
  applyI18n(); boot();}

function chart(id,opt){if(!E)return;if(!charts[id])charts[id]=E.init($('#'+id),'dark');
  charts[id].setOption(opt,true);}
async function get(u){return (await fetch(u)).json();}
async function api(argv,files){return (await fetch('/api/cli',{method:'POST',
  body:JSON.stringify({argv,files})})).json();}
async function apiLine(line){return (await fetch('/api/cli',{method:'POST',
  body:JSON.stringify({line})})).json();}
function fmtB(n){const u=['B','KB','MB','GB','TB'];let i=0;while(n>=1024&&i<4){n/=1024;i++;}
  return n.toFixed(1)+u[i];}
function esc(s){return (s+'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function render(el,res){
  const ok=res.exit===0;
  let h=`<div class="cmd">$ ${esc(res.command)}</div>`;
  h+=`<div class="out"><span class="${ok?'ok':'err'}">exit ${res.exit}</span><pre>`+
     esc(res.output?JSON.stringify(res.output,null,2):res.raw)+`</pre></div>`;
  $(el).innerHTML=h; return res;
}
// nav
$$('nav a').forEach(a=>a.onclick=()=>{$$('nav a').forEach(x=>x.classList.remove('on'));
  $$('.panel').forEach(x=>x.classList.remove('on'));a.classList.add('on');
  $('#p-'+a.dataset.p).classList.add('on');
  setTimeout(()=>Object.values(charts).forEach(c=>c.resize()),50);});

// ---- dashboard ----
async function boot(){
  const o=await get('/api/overview'), s=o.storage;
  $('#nsub').textContent=s.samples+' '+t('w.samples')+' · '+o.index.vectors+' '+t('w.vectors');
  $('#storage').innerHTML=[[t('w.samples'),s.samples],[t('w.uniqueBlobs'),s.unique_blobs],
    [t('w.stored'),fmtB(s.stored_blob_bytes)],[t('w.catalogDb'),fmtB(s.catalog_db_bytes)],
    [t('w.dedup'),s.dedup_ratio+'x'],[t('w.compression'),s.compression_ratio+'x'],
    [t('w.contentSavings'),s.content_savings_ratio+'x']]
    .map(r=>`<div class="stat"><span>${r[0]}</span><b>${r[1]}</b></div>`).join('');
  $('#idxstat').innerHTML=[[t('w.vectors'),o.index.vectors],[t('w.dim'),o.index.dim],
    [t('w.fulltextDocs'),o.index.fulltext_docs]]
    .map(r=>`<div class="stat"><span>${r[0]}</span><b>${r[1]}</b></div>`).join('');
  pie('pie_domain','domain'); pie('pie_lang','lang');
  const h=await get('/api/hist?column=quality_score&bins=12');
  chart('hist_q',{grid:{left:40,right:10,top:10,bottom:24},
    xAxis:{type:'category',data:h.bins.map(b=>b.lo.toFixed(2)),axisLabel:{fontSize:9}},
    yAxis:{type:'value'},series:[{type:'bar',data:h.bins.map(b=>b.n),itemStyle:{color:'#5b8def'}}]});
  const st=await get('/api/dist?column=stage');
  chart('bar_stage',{grid:{left:90,right:10,top:10,bottom:24},
    yAxis:{type:'category',data:st.map(d=>d.value||'?')},xAxis:{type:'value'},
    series:[{type:'bar',data:st.map(d=>d.n),itemStyle:{color:'#46c2a0'}}]});
  dsRefresh(); opList(); modelList(); loadRecipe(); listExports();
  if(!$('#pl_yaml').value)$('#pl_yaml').value=DEFAULT_PIPE;
}
async function pie(id,col){const d=await get('/api/dist?column='+col);
  chart(id,{tooltip:{},series:[{type:'pie',radius:['38%','70%'],
    data:d.filter(x=>x.value).map(x=>({name:x.value,value:x.n}))}]});}

// ---- datasets ----
async function dsRefresh(){const r=await api(['dataset','list']);
  const rows=(r.output&&r.output.datasets)||[];
  $('#dstable').innerHTML='<table><tr><th>'+t('w.id')+'</th><th>'+t('w.name')+'</th><th>'+
    t('w.samplesCol')+'</th><th>'+t('w.licenseCol')+'</th></tr>'+rows.map(d=>`<tr><td class=muted>${d.id}</td>`+
    `<td>${esc(d.name)}</td><td>${d.n_samples}</td><td>${esc(d.license||'-')}</td></tr>`).join('')+'</table>';
  $('#dslist').innerHTML=rows.map(d=>`<option value="${esc(d.name)}">`).join('');}   // searchable dataset names
async function agentIngest(){
  const a=['agent-ingest','@FILE','--engine',$('#ag_engine').value];
  if($('#ag_name').value)a.push('--filename',$('#ag_name').value);
  if($('#ag_model').value)a.push('--model',$('#ag_model').value);
  if($('#ag_ds').value)a.push('--dataset',$('#ag_ds').value);
  if($('#ag_dry').checked)a.push('--dry-run');
  render('#ag_res',await api(a,{'@FILE':$('#ag_data').value})); boot();}
async function dsAdd(){const a=['dataset','add','--name',$('#ds_name').value];
  if($('#ds_src').value)a.push('--source',$('#ds_src').value);
  if($('#ds_lic').value)a.push('--license',$('#ds_lic').value);
  if($('#ds_desc').value)a.push('--description',$('#ds_desc').value);
  render('#ds_res',await api(a)); dsRefresh();}
async function ingest(){
  const a=['ingest',$('#ig_ds').value,'--file','@DATA','--content-key',$('#ig_ck').value];
  if($('#ig_stage').value)a.push('--stage',$('#ig_stage').value);
  if($('#ig_dom').value)a.push('--domain',$('#ig_dom').value);
  if($('#ig_lang').value)a.push('--lang',$('#ig_lang').value);
  render('#ig_res',await api(a,{'@DATA':$('#ig_data').value})); boot();}

// ---- query ----
async function runQuery(){const a=['query'];
  if($('#q_filter').value)a.push('--filter',$('#q_filter').value);
  if($('#q_ds').value)a.push('--dataset',$('#q_ds').value);
  a.push('--limit',$('#q_limit').value,'--columns',$('#q_cols').value);
  const r=await api(a); const rows=(r.output&&r.output.rows)||[];
  let h=`<div class="cmd">$ ${esc(r.command)}</div>`;
  if(rows.length){const cols=Object.keys(rows[0]);
    h+='<div class="out"><table><tr>'+cols.map(c=>`<th>${c}</th>`).join('')+'</tr>'+
      rows.map(rw=>'<tr>'+cols.map(c=>`<td title="${esc(rw[c])}">${esc(rw[c])}</td>`).join('')+'</tr>').join('')+
      `</table><p class=muted>${r.output.returned}/${r.output.total} ${t('w.rows')}</p></div>`;
  } else h+='<div class="out"><pre>'+esc(r.output?JSON.stringify(r.output,null,2):r.raw)+'</pre></div>';
  $('#q_res').innerHTML=h;}

// ---- operators ----
async function opList(){const r=await api(['op','list']);
  OPS=(r.output&&r.output.operators)||[];
  $('#oplist').innerHTML='<table><tr><th>'+t('w.opName')+'</th><th>'+t('w.version')+'</th><th>'+
    t('w.kind')+'</th><th>'+t('w.descCol')+'</th><th></th></tr>'+
    OPS.map((o,i)=>`<tr><td>${o.name}</td><td>${o.version}</td><td>${o.kind}</td>`+
    `<td>${esc(o.description)}</td><td><span class=lnk onclick="openOpModal(${i})" `+
    `data-i18n="btn.edit">edit</span></td></tr>`).join('')+'</table>';
  applyI18n();
  $('#op_name').innerHTML=OPS.map(o=>`<option>${o.name}</option>`).join('');}

// ---- operator edit modal ----
function openOpModal(i){const o=OPS[i]; window._opm=o;
  $('#opm_title').textContent=o.name+'  ('+o.kind+' v'+o.version+')';
  $('#opm_desc').textContent=o.description||'';
  $('#opm_ds').value=''; $('#opm_filter').value=''; $('#opm_res').innerHTML='';
  const fields=(o.params||[]).map(p=>{
    const def=p.default===null?'':(typeof p.default==='object'?JSON.stringify(p.default):p.default);
    if(p.name==='model'){
      return `<label class="f">model</label><select id="opm_${p.name}">`+
        '<option value=""></option>'+MODELS.map(m=>`<option>${esc(m)}</option>`).join('')+'</select>';
    }
    const ph=p.name==='labels'?'code,math,web (comma-separated)':('default: '+def);
    return `<label class="f">${p.name}${p.kind==='int'?' (int)':''}</label>`+
      `<input id="opm_${p.name}" value="${esc(def)}" placeholder="${esc(ph)}">`;
  }).join('');
  $('#opm_fields').innerHTML=fields||'<p class=muted>no parameters</p>';
  $('#opmodal').classList.add('on');}
function closeOpModal(){$('#opmodal').classList.remove('on');}
async function opmRun(){const o=window._opm; const a=['op','run',o.name];
  if($('#opm_ds').value)a.push('--dataset',$('#opm_ds').value);
  if($('#opm_filter').value)a.push('--filter',$('#opm_filter').value);
  (o.params||[]).forEach(p=>{
    const el=$('#opm_'+p.name); if(!el)return; let v=el.value.trim(); if(v==='')return;
    if(p.name==='labels'&&v[0]!=='[') v=JSON.stringify(v.split(',').map(s=>s.trim()).filter(Boolean));
    a.push('--arg',p.name+'='+v);
  });
  render('#opm_res',await api(a)); boot();}
async function opRun(){const a=['op','run',$('#op_name').value];
  if($('#op_ds').value)a.push('--dataset',$('#op_ds').value);
  if($('#op_filter').value)a.push('--filter',$('#op_filter').value);
  $('#op_args').value.split('\n').map(s=>s.trim()).filter(Boolean)
    .forEach(kv=>a.push('--arg',kv));
  render('#op_res',await api(a)); boot();}
async function pipeRun(){
  render('#pl_res',await api(['pipeline','run','@PIPE'],{'@PIPE':$('#pl_yaml').value})); boot();}

// ---- models & classify ----
async function modelList(){const r=await api(['model','list']);
  const ms=(r.output&&r.output.models)||[];
  $('#mptable').innerHTML='<table><tr><th>'+t('w.name')+'</th><th>'+t('f.format')+'</th>'+
    '<th>'+t('f.modelId')+'</th><th>'+t('f.apiUrl')+'</th><th>'+t('f.apiKey')+'</th></tr>'+
    ms.map(m=>`<tr><td>${esc(m.name)}</td><td>${m.response_format}</td>`+
    `<td>${esc(m.model)}</td><td class=muted>${esc(m.api_url)}</td>`+
    `<td class=muted>${esc(m.api_key)}</td></tr>`).join('')+'</table>';
  MODELS=ms.map(m=>m.name);
  $('#cl_model').innerHTML=ms.map(m=>`<option>${esc(m.name)}</option>`).join('');
  $('#ag_model').innerHTML='<option value="">'+t('opt.heuristic')+'</option>'+
    ms.map(m=>`<option>${esc(m.name)}</option>`).join('');}
async function modelAdd(){
  const a=['model','add','--name',$('#m_name').value,'--api-url',$('#m_url').value,
    '--format',$('#m_fmt').value];
  if($('#m_key').value)a.push('--key',$('#m_key').value);
  if($('#m_model').value)a.push('--model',$('#m_model').value);
  if($('#m_note').value)a.push('--note',$('#m_note').value);
  if($('#m_conc').value)a.push('--max-concurrency',$('#m_conc').value);
  if($('#m_temp').value)a.push('--temperature',$('#m_temp').value);
  render('#m_res',await api(a)); modelList();}
async function classify(){
  const labels=$('#cl_labels').value.split(',').map(s=>s.trim()).filter(Boolean);
  const a=['op','run','domain_classify','--arg','model='+$('#cl_model').value,
    '--arg','labels='+JSON.stringify(labels),
    '--arg','chunk_size='+$('#cl_chunk').value,
    '--arg','max_concurrency='+$('#cl_conc').value];
  if($('#cl_ds').value)a.push('--dataset',$('#cl_ds').value);
  if($('#cl_filter').value)a.push('--filter',$('#cl_filter').value);
  render('#cl_res',await api(a)); boot();}

// ---- index & recall ----
async function ixBuild(flag){const a=['index','build'];if(flag)a.push(flag);
  render('#ix_res',await api(a)); ixStat(); boot();}
async function ixStat(){const r=await api(['index','stats']);
  const o=r.output||{};$('#ixstat2').innerHTML=[[t('w.vectors'),o.vectors],[t('w.dim'),o.dim],
    [t('w.fulltextDocs'),o.fulltext_docs]].map(x=>
    `<div class=stat><span>${x[0]}</span><b>${x[1]}</b></div>`).join('');}
async function recall(){const a=['recall'];const m=$('#rc_mode').value;
  a.push(m==='query'?'--query':'--match',$('#rc_q').value);
  if($('#rc_filter').value)a.push('--filter',$('#rc_filter').value);
  a.push('--limit',$('#rc_limit').value,'--preview');
  const r=await api(a); const hits=(r.output&&r.output.results)||[];
  let h=`<div class="cmd">$ ${esc(r.command)}</div><div class="out">`;
  h+=hits.map(x=>`<div class=stat><b>${x.score}</b>`+
    `<span class=pill>${esc(x.domain||'')}/${esc(x.lang||'')}</span></div>`+
    `<pre>${esc(x.preview||'')}</pre>`).join('')||'<span class=muted>'+t('msg.noHits')+'</span>';
  $('#rc_res').innerHTML=h+'</div>';}

// ---- recipe ----
let RECIPE=null;
async function loadRecipe(){RECIPE=await get('/api/recipe');
  $('#buckets').innerHTML=RECIPE.buckets.map((b,i)=>
    `<div class="brow"><label title="${esc(b.filter)}">${esc(b.name)}</label>`+
    `<input type=range min=0 max=1 step=0.01 value=${b.weight} `+
    `oninput="w${i}.textContent=this.value" id=r${i}>`+
    `<span class=w id=w${i}>${b.weight}</span></div>`).join('');
  if(RECIPE.total_samples)$('#r_budget').value=RECIPE.total_samples;
  $('#r_yaml').value=JSON.stringify(buildRecipe(),null,2);}
// quality-tier policy (applied to every bucket), edited with sliders
let TIERS=[{label:'high',min:0.8,weight:0.6},{label:'mid',min:0.6,weight:0.3},
           {label:'low',min:0.0,weight:0.1}];
function renderTiers(){
  const on=$('#q_enable').checked;
  $('#q_extra').style.display=on?'block':'none';
  if(!on){$('#q_tiers').innerHTML='';return;}
  $('#q_tiers').innerHTML=TIERS.map((t,i)=>
    `<div class="brow"><input style="width:64px" value="${esc(t.label)}" id="tl${i}">`+
    `<span class=muted>min</span><input type="number" step="0.05" min="0" max="1" `+
    `style="width:70px" value="${t.min}" id="tm${i}">`+
    `<input type="range" min="0" max="1" step="0.05" value="${t.weight}" id="tw${i}" `+
    `oninput="tlw${i}.textContent=this.value"><span class=w id="tlw${i}">${t.weight}</span>`+
    `<span class=lnk onclick="rmTier(${i})">&#10005;</span></div>`).join('');}
function syncTiers(){TIERS=TIERS.map((t,i)=>({
  label:($('#tl'+i)||{}).value||t.label,
  min:parseFloat(($('#tm'+i)||{}).value||t.min),
  weight:parseFloat(($('#tw'+i)||{}).value||t.weight)}));}
function addTier(){syncTiers();TIERS.push({label:'tier',min:0.0,weight:0.1});renderTiers();}
function rmTier(i){syncTiers();TIERS.splice(i,1);renderTiers();}
function buildRecipe(){
  const grade=$('#q_enable').checked; if(grade)syncTiers();
  const best=$('#q_best')&&$('#q_best').checked;
  const buckets=RECIPE.buckets.map((b,i)=>{const o={name:b.name,
    weight:parseFloat($('#r'+i).value)}; if(b.filter)o.filter=b.filter;
    if(b.query)o.query=b.query; if(b.keyword)o.keyword=b.keyword;
    if(grade){o.quality_tiers=TIERS.map(t=>({min:t.min,weight:t.weight,label:t.label}));
      if(best)o.quality_grade=true;} return o;});
  const r={name:RECIPE.name||'console-mix',buckets,
    sampling:{strategy:$('#r_strat').value,seed:42},export:{format:'jsonl',shard_size:'1MB'}};
  if(RECIPE.stage)r.stage=RECIPE.stage;
  r[$('#r_kind').value]=parseFloat($('#r_budget').value);
  return {recipe:r};}
async function rPlan(){$('#r_yaml').value=JSON.stringify(buildRecipe(),null,2);
  const r=await api(['recipe','plan','@R'],{'@R':$('#r_yaml').value});
  render('#r_res',r); drawPlan(r.output);}
async function rPreview(){$('#r_yaml').value=JSON.stringify(buildRecipe(),null,2);
  render('#r_res',await api(['recipe','preview','@R','--per-bucket','2'],{'@R':$('#r_yaml').value}));}
async function rExport(){$('#r_yaml').value=JSON.stringify(buildRecipe(),null,2);
  render('#r_res',await api(['recipe','export','@R'],{'@R':$('#r_yaml').value}));
  boot(); listExports();}
function drawPlan(p){if(!p||!p.buckets)return;$('#r_fp').textContent='fingerprint '+p.fingerprint;
  $('#r_warn').innerHTML=(p.warnings||[]).map(w=>'⚠ '+esc(w)).join('<br>');
  const tk=p.budget_kind==='token';
  chart('plan_chart',{tooltip:{},legend:{data:[t('w.available'),t('w.target')],
    textStyle:{color:'#8b93a7'},top:0},
    grid:{left:90,right:10,top:30,bottom:24},xAxis:{type:'value'},
    yAxis:{type:'category',data:p.buckets.map(b=>b.name)},series:[
      {name:t('w.available'),type:'bar',data:p.buckets.map(b=>tk?b.available_tokens:b.available_samples),itemStyle:{color:'#2a3142'}},
      {name:t('w.target'),type:'bar',data:p.buckets.map(b=>tk?b.target_tokens:b.target_samples),itemStyle:{color:'#5b8def'}}]});}
async function rRaw(verb){const a=['recipe',verb,'@R'];
  if(verb==='preview')a.push('--per-bucket','2');
  const r=await api(a,{'@R':$('#r_yaml').value}); render('#rr_res',r);
  if(verb==='plan')drawPlan(r.output); if(verb==='export'){boot(); listExports();}}

// ---- columns preview ----
async function loadColumns(){const d=await get('/api/columns');
  let h='<table><tr><th>'+t('w.name')+'</th><th>'+t('w.kind')+'</th><th>non_null</th></tr>'+
    d.core.map(c=>`<tr><td>${c.name}</td><td class=muted>${c.type}${c.indexed?' (idx)':''}</td>`+
    `<td>${c.non_null}</td></tr>`).join('')+'</table>';
  if(d.tags&&d.tags.length){h+='<h3>'+t('w.tags')+'</h3><table>'+d.tags.map(tg=>
    `<tr><td>${esc(tg.key)}</td><td class=muted>${esc(JSON.stringify(tg.example))}</td></tr>`).join('')+'</table>';}
  $('#cols_res').innerHTML='<div class="out">'+h+'<p class=muted>'+esc(d.note)+'</p></div>';}

// ---- quality-graded recall ----
async function grade(){let u='/api/grade?column=quality_score';
  if($('#g_filter').value)u+='&filter='+encodeURIComponent($('#g_filter').value);
  if($('#g_tiers').value)u+='&tiers='+encodeURIComponent($('#g_tiers').value);
  const d=await get(u); if(d.error){$('#g_res').innerHTML='<div class=out><span class=err>'+esc(d.error)+'</span></div>';return;}
  const ts=d.tiers||[];
  chart('grade_chart',{tooltip:{},grid:{left:60,right:10,top:10,bottom:24},
    xAxis:{type:'category',data:ts.map(x=>x.tier)},yAxis:{type:'value'},
    series:[{type:'bar',data:ts.map(x=>x.samples),itemStyle:{color:'#46c2a0'}}]});
  $('#g_res').innerHTML='<div class="out"><table><tr><th>'+t('w.tier')+'</th><th>'+t('w.range')+
    '</th><th>'+t('w.samplesCol')+'</th><th>tokens</th></tr>'+ts.map(x=>{
      const rng=x.min===null?'unscored':(x.max===null?'>='+x.min:'['+x.min+','+x.max+')');
      return `<tr><td>${x.tier}</td><td class=muted>${rng}</td><td>${x.samples}</td><td>${x.tokens}</td></tr>`;
    }).join('')+'</table></div>';}

// ---- exports / download ----
async function listExports(){const d=await get('/api/exports');
  $('#exp_root').textContent=t('msg.exportLoc')+' '+d.root+'/exports/';
  const xs=d.exports||[];
  $('#exp_res').innerHTML=xs.length?xs.map(x=>{
    const files=(x.files||[]).map(f=>`<span class=lnk onclick="dl('${x.dir}/${f.name}')">${f.name}</span>`+
      ` <span class=muted>(${fmtB(f.bytes||0)})</span>`).join(' · ');
    return `<div class="out"><b>${x.export_id}</b> <span class=pill>${x.format}, ${x.samples} ${t('w.samples')}</span>`+
      ` <span class=lnk onclick="dlzip('${x.export_id}')" data-i18n="btn.downloadZip">download .zip</span>`+
      `<div style="margin-top:6px">${files}</div></div>`;
  }).join(''):'<p class=muted>no exports yet</p>'; applyI18n();}
function dl(path){window.location='/api/download?path='+encodeURIComponent(path);}
function dlzip(id){window.location='/api/download_zip?id='+encodeURIComponent(id);}

// ---- lineage / cli ----
async function lineage(){const r=await api(['lineage','list']);
  const items=(r.output&&r.output.lineage)||[];
  $('#ln_res').innerHTML=`<div class="cmd">$ ${esc(r.command)}</div><div class="out">`+
    '<table><tr><th>'+t('w.kind')+'</th><th>'+t('w.id')+'</th><th>'+t('w.path')+'</th></tr>'+items.map(i=>
    `<tr><td>${i.kind}</td><td>${i.id||''}</td><td class=muted>${esc(i.path)}</td></tr>`).join('')+
    '</table></div>';}
async function cliRun(){render('#cli_res',await apiLine($('#cli_line').value)); boot();}

const DEFAULT_PIPE=`pipeline:
  name: clean-v1
  source: { dataset: "" }
  operators:
    - { name: token_count }
    - { name: lang_detect }
    - { name: quality_score }
    - { name: pii_detect }
    - { name: minhash_dedup, args: { k: 4 } }`;
window.addEventListener('resize',()=>Object.values(charts).forEach(c=>c.resize()));
applyI18n(); boot();
</script></body></html>"""
