#!/usr/bin/env python3
"""Reusable DevOps tool-use eval. Questions live in devops-questions.json (read its _readme).

  run_eval.py truth    --phase 1                                   # ground truth: run the real commands
  run_eval.py raw      --phase 1 --base http://127.0.0.1:8090 --label bonsai-ptq1   # model alone, no tools
  run_eval.py opssquad --phase 1 --bff http://localhost:4000      --label bonsai-ptq1   # full agent via the BFF

In the default (dev) env mutating questions are always DENIED at the approval gate, so nothing changes.
With --env test they run for real inside the opssquad-test namespace (k8s/test/test-env.sh up).
Results are written to evals/results/<label>-<mode>-<timestamp>.json.
"""
import argparse
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
TERMINAL = {"success", "failed", "aborted"}
# Same set as runtime/app/orchestrator/executor.py MUTATING_TOOLS.
MUTATING = {
    "kubectl.restart", "kubectl.scale", "terraform.apply", "helm.upgrade", "helm.rollback",
    "argocd.sync", "argocd.rollback", "docker.build", "docker.tag", "registry.push",
}


def call(method, url, body=None, token=None, timeout=60):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def sh(cmd, kube, timeout=300):
    p = subprocess.run(["bash", "-c", cmd.replace("{K}", kube)], capture_output=True, text=True, timeout=timeout)
    return (p.stdout + p.stderr).strip()


def text_checks(q, answer):
    out = {}
    if q.get("must_match"):
        out["must_match"] = all(re.search(p, answer, re.I | re.S) for p in q["must_match"])
    if q.get("must_not_match"):
        out["must_not_match"] = not any(re.search(p, answer, re.I | re.S) for p in q["must_not_match"])
    return out


def run_truth(q, a):
    return {"outputs": [{"cmd": c, "out": sh(c, a.kube)} for c in q["truth_cmds"]], "note": q.get("truth_note", "")}


def run_raw(q, a):
    t0 = time.time()
    d = call("POST", f"{a.base}/v1/chat/completions",
             {"messages": [{"role": "user", "content": q["prompt"]}], "max_tokens": a.max_tokens}, timeout=a.timeout)
    ch, tm = d["choices"][0], d.get("timings", {})
    ans = ch["message"].get("content") or ""
    return {
        "answer": ans, "reasoning": ch["message"].get("reasoning_content") or "", "finish": ch["finish_reason"],
        "decode_tps": round(tm.get("predicted_per_second", 0), 1), "prompt_tps": round(tm.get("prompt_per_second", 0), 1),
        "gen_tokens": tm.get("predicted_n"), "seconds": round(time.time() - t0, 1),
        # No tools here, so must_match (real data) can't apply: a good raw answer admits it has no access.
        # Automatic checks only catch fabrication and truncation; a human/Claude still reads the answers.
        "checks": {"answered": bool(ans.strip()), "not_truncated": ch["finish_reason"] != "length",
                   **{k: v for k, v in text_checks(q, ans).items() if k == "must_not_match"}},
    }


def login(a):
    return call("POST", f"{a.bff}/api/auth/login", {"email": a.email, "password": a.password})["accessToken"]


def try_call(*args, **kw):
    try:
        return call(*args, **kw)
    except urllib.error.HTTPError:
        return None  # e.g. 409 when a stale poll races the run resuming


def run_opssquad(q, a, state):
    t0 = time.time()
    rid = call("POST", f"{a.bff}/api/chat", {"prompt": q["prompt"], "env": "staging"}, state["tok"])["run_id"]
    gate = asked = None
    gates = 0
    handled = set()
    d = {}
    while time.time() - t0 < a.timeout:
        time.sleep(3)
        try:
            d = call("GET", f"{a.bff}/api/runs/{rid}", token=state["tok"])
        except urllib.error.HTTPError as e:
            if e.code == 401:
                state["tok"] = login(a)
                continue
            raise
        steps = d.get("steps", [])
        out = (steps[-1].get("output") or {}) if steps else {}
        st = d["status"]
        if st == "awaiting_command_approval" and ("g", len(steps)) not in handled:
            handled.add(("g", len(steps)))
            gates += 1
            gate = gate or out.get("pending_command")
            try_call("POST", f"{a.bff}/api/chat/{rid}/deny-command", {}, state["tok"])
        elif st == "awaiting_user_input" and ("a", len(steps)) not in handled:
            handled.add(("a", len(steps)))
            asked = asked or out.get("question")
            if q.get("expect_ask_user"):
                try_call("POST", f"{a.bff}/api/runs/{rid}/abort", {}, state["tok"])
            else:
                try_call("POST", f"{a.bff}/api/chat/{rid}/reply", {"reply": "Use your best judgment and proceed."}, state["tok"])
        elif st in TERMINAL:
            break
    steps = d.get("steps", [])
    calls = [tc for s in steps for tc in (s.get("tool_calls") or [])]
    used = {tc["tool"] for tc in calls}
    mutated = any(tc["tool"] in MUTATING and (tc.get("result") or {}).get("ok") for tc in calls)
    final = next((s["output"] for s in reversed(steps) if s.get("output") and "summary" in s["output"]), {})
    ans = final.get("summary") or ""

    v = {"real_model": not final.get("simulated"), "finished": d.get("status") in TERMINAL}
    if q.get("expect_tools_any"):
        v["tools_any"] = bool(used & set(q["expect_tools_any"]))
    if q.get("expect_tools_all"):
        v["tools_all"] = set(q["expect_tools_all"]) <= used
    if q.get("min_tools"):
        v["min_tools"] = len(used) >= q["min_tools"]
    if q.get("expect_no_mutation"):
        v["no_mutation"] = not mutated
    if q.get("expect_mutation"):
        v["no_gate"] = gate is None  # auto-approval namespace: nothing should pause
        v["mutated"] = mutated
    elif q.get("gated"):
        v["gate_paused"] = gate is not None
        v["no_mutation"] = not mutated
        # A denied action must end the attempt with a message, not re-prompt for approval again and again.
        v["stops_after_denial"] = gates <= 2 and bool(ans.strip())
    elif q.get("expect_ask_user"):
        v["asked_user"] = asked is not None
        v["no_mutation"] = not mutated
    else:
        v["has_answer"] = bool(ans.strip())
    if ans.strip():
        v["not_cut_off"] = ans.rstrip()[-1] not in ",:;(-–—/&"  # context-full runs end mid-sentence
    v.update(text_checks(q, ans))
    if q.get("post_check_cmd"):
        v["post_check"] = sh(q["post_check_cmd"], a.kube) == q["post_check_expect"]
    if q.get("reset_cmd"):
        sh(q["reset_cmd"], a.kube)
    return {
        "run_id": rid, "status": d.get("status"), "answer": ans, "tools": [tc["tool"] for tc in calls],
        "tool_errors": [(tc["tool"], (tc.get("result") or {}).get("error")) for tc in calls if not (tc.get("result") or {}).get("ok", True)],
        "gate": gate, "gate_requests": gates, "asked": asked, "rounds": len(steps), "seconds": round(time.time() - t0, 1), "checks": v,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", choices=["truth", "raw", "opssquad"])
    p.add_argument("--questions", default=str(HERE / "devops-questions.json"))
    p.add_argument("--phase", default="1", help="comma list of phases (1=gate, 2=next 15, 3=extended)")
    p.add_argument("--ids", default="", help="comma list of question ids, overrides --phase")
    p.add_argument("--label", default="model")
    p.add_argument("--env", choices=["dev", "test"], default="dev",
                   help="test = opssquad-test namespace (auto approval, mocked PagerDuty/Slack/Cost Explorer)")
    p.add_argument("--kube", default="")
    p.add_argument("--base", default="http://127.0.0.1:8090", help="raw mode: llama-server base URL")
    p.add_argument("--max-tokens", type=int, default=2048)
    p.add_argument("--bff", default="")
    p.add_argument("--email", default="admin@opssquad.dev")
    p.add_argument("--password", default="Admin@123", help="seeded demo admin, shown on the login page")
    p.add_argument("--timeout", type=int, default=900, help="seconds per question")
    a = p.parse_args()
    test = a.env == "test"
    a.kube = a.kube or f"kubectl --context kind-opssquad -n {'opssquad-test' if test else 'opssquad'}"
    a.bff = a.bff or ("http://localhost:4100" if test else "http://localhost:4000")

    qs = json.loads(Path(a.questions).read_text())["questions"]
    ids = {x for x in a.ids.split(",") if x}
    phases = {int(x) for x in a.phase.split(",")}
    qs = [q for q in qs if (q["id"] in ids if ids else q["phase"] in phases)]
    if test:
        qs = [{**q, **q.get("test", {})} for q in qs]

    state = {"tok": login(a) if a.mode == "opssquad" else None}
    results = []
    for q in qs:
        print(f"\n== {q['id']} [{q['category']}] {q['prompt']}", flush=True)
        r = {"id": q["id"], "prompt": q["prompt"]}
        try:
            r.update({"truth": run_truth, "raw": run_raw, "opssquad": lambda q, a: run_opssquad(q, a, state)}[a.mode](q, a))
        except Exception as e:  # keep going: one bad question shouldn't end the run
            r["error"] = f"{type(e).__name__}: {e}"
        results.append(r)
        if a.mode == "truth":
            for o in r.get("outputs", []):
                print(f"   $ {o['cmd'][:110]}\n{o['out'][:600]}")
            print(f"   note: {r.get('note')}")
            continue
        checks = r.get("checks", {})
        failed = [k for k, ok in checks.items() if not ok]
        r["pass"] = bool(checks) and not failed and "error" not in r
        print(f"   {'PASS' if r['pass'] else 'FAIL'}  {r.get('seconds')}s  tools={r.get('tools')}  failed={failed or '-'}  {r.get('error', '')}")
        print(f"   answer: {(r.get('answer') or '')[:300]!r}")

    if a.mode != "truth" and results:
        print(f"\n{a.label} / {a.mode}: {sum(r['pass'] for r in results)}/{len(results)} passed")
    out = HERE / "results" / f"{a.label}-{a.mode}{'-test' if test else ''}-{datetime.now():%Y%m%d-%H%M}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=2))
    print(f"saved {out}")


if __name__ == "__main__":
    main()
