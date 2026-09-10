"""Real tool implementations — actually shell out, no fake data.

Grounded against safe, self-contained targets since there's no real cloud
account or "payments-api" deployment anywhere to point at:
  - kubectl:   this pod's own namespace (opssquad), via a least-privilege
               ServiceAccount (see k8s/05-rbac.yaml) — read targets default
               to the runtime deployment, mutating targets default to redis
               (stateless, safe to restart/scale, never the pod answering
               the request that triggered the action).
  - terraform: ./terraform-demo, a local-only `local` provider config — no
               cloud credentials needed, safe to actually apply.
  - trivy:     this container's own filesystem (real CVE data against the
               real installed OS/Python packages).

A production deployment would point KUBE_NAMESPACE/TERRAFORM_DIR/the trivy
scan target at real infrastructure instead — the point here is that the
subprocess/parsing plumbing is genuinely real, not that the demo targets are.
"""

import asyncio
import json
import re
from typing import Any

from app.config import settings
from app.tools.base import Tool, ToolResult

SUBPROCESS_TIMEOUT = 60.0

_SAFE_ARG_RE = re.compile(r"[a-zA-Z0-9._/-]+")


class UnsafeArgError(Exception):
    pass


def _safe_arg(value: str) -> str:
    """Caller-controlled values (Flightplan `with:` params, direct agent-run
    request params) land as bare positional argv elements to kubectl/trivy —
    a value starting with '-' would be parsed as a flag instead of a
    resource name/path (argument injection), even though there's no shell
    involved. Reject anything outside a known-safe charset, on top of always
    passing `--` before the positional (belt and suspenders)."""
    value = str(value)
    if not _SAFE_ARG_RE.fullmatch(value) or value.startswith("-"):
        raise UnsafeArgError(f"unsafe argument: {value!r}")
    return value


def _safe_int(value: Any, *, minimum: int = 0) -> int:
    n = int(value)
    if n < minimum:
        raise UnsafeArgError(f"value must be >= {minimum}: {n}")
    return n


async def _run(*args: str, timeout: float = SUBPROCESS_TIMEOUT) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 1, "", f"timed out after {timeout}s"
    return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")


class RealKubectlGet(Tool):
    name = "kubectl.get"

    async def run(self, **kwargs: Any) -> ToolResult:
        try:
            target = _safe_arg(kwargs.get("k8s_target", settings.kube_read_target))
        except UnsafeArgError as exc:
            return ToolResult(ok=False, error=str(exc))
        code, out, err = await _run("kubectl", "get", "-n", settings.kube_namespace, "-o", "json", "--", target)
        if code != 0:
            return ToolResult(ok=False, error=err[:500])
        obj = json.loads(out)
        return ToolResult(ok=True, data={
            "replicas_desired": obj.get("spec", {}).get("replicas", 0),
            "replicas_ready": obj.get("status", {}).get("readyReplicas", 0),
            "target": target, "real": True,
        })


class RealKubectlLogs(Tool):
    name = "kubectl.logs"

    async def run(self, **kwargs: Any) -> ToolResult:
        try:
            target = _safe_arg(kwargs.get("k8s_target", settings.kube_read_target))
            tail = _safe_int(kwargs.get("tail", 200), minimum=1)
        except (UnsafeArgError, ValueError, TypeError) as exc:
            return ToolResult(ok=False, error=str(exc))
        code, out, err = await _run(
            "kubectl", "logs", "-n", settings.kube_namespace, f"--tail={tail}", "--", target
        )
        if code != 0:
            return ToolResult(ok=False, error=err[:500])
        lines = out.splitlines()
        error_count = sum(1 for line in lines if "error" in line.lower())
        return ToolResult(ok=True, data={
            "lines_returned": len(lines), "error_count": error_count, "target": target, "real": True,
        })


class RealKubectlRestart(Tool):
    name = "kubectl.restart"

    async def run(self, **kwargs: Any) -> ToolResult:
        try:
            target = _safe_arg(kwargs.get("k8s_target", settings.kube_mutate_target))
        except UnsafeArgError as exc:
            return ToolResult(ok=False, error=str(exc))
        code, _, err = await _run(
            "kubectl", "rollout", "restart", "-n", settings.kube_namespace, "--", target
        )
        return ToolResult(ok=code == 0, data={"target": target, "real": True} if code == 0 else None,
                           error=None if code == 0 else err[:500])


class RealKubectlScale(Tool):
    name = "kubectl.scale"

    async def run(self, **kwargs: Any) -> ToolResult:
        try:
            target = _safe_arg(kwargs.get("k8s_target", settings.kube_mutate_target))
            replicas = _safe_int(kwargs.get("replicas", 1), minimum=0)
        except (UnsafeArgError, ValueError, TypeError) as exc:
            return ToolResult(ok=False, error=str(exc))
        code, _, err = await _run(
            "kubectl", "scale", "-n", settings.kube_namespace, f"--replicas={replicas}", "--", target
        )
        return ToolResult(ok=code == 0, data={"target": target, "replicas": replicas, "real": True} if code == 0 else None,
                           error=None if code == 0 else err[:500])


_PLAN_RE = re.compile(r"Plan:\s*(\d+)\s*to add,\s*(\d+)\s*to change,\s*(\d+)\s*to destroy")


class RealTerraformPlan(Tool):
    name = "terraform.plan"

    async def run(self, **kwargs: Any) -> ToolResult:
        code, out, err = await _run("terraform", f"-chdir={settings.terraform_dir}", "plan", "-no-color")
        match = _PLAN_RE.search(out)
        to_add, to_change, to_destroy = (int(x) for x in match.groups()) if match else (0, 0, 0)
        return ToolResult(ok=code == 0, data={
            "to_add": to_add, "to_change": to_change, "to_destroy": to_destroy, "real": True,
        } if code == 0 else None, error=None if code == 0 else err[:500])


class RealTerraformApply(Tool):
    name = "terraform.apply"

    async def run(self, **kwargs: Any) -> ToolResult:
        code, out, err = await _run(
            "terraform", f"-chdir={settings.terraform_dir}", "apply", "-auto-approve", "-no-color",
            timeout=120.0,
        )
        return ToolResult(ok=code == 0, data={"real": True} if code == 0 else None, error=None if code == 0 else err[:500])


_SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


class RealTrivyScan(Tool):
    name = "trivy.scan"

    async def run(self, **kwargs: Any) -> ToolResult:
        try:
            path = _safe_arg(kwargs.get("scan_path", settings.trivy_scan_path))
        except UnsafeArgError as exc:
            return ToolResult(ok=False, error=str(exc))
        code, out, err = await _run(
            "trivy", "fs", "--format", "json", "--scanners", "vuln", "--quiet", "--", path,
            timeout=120.0,
        )
        if code != 0:
            return ToolResult(ok=False, error=err[:500])
        report = json.loads(out)
        cves = [
            {
                "id": v.get("VulnerabilityID"),
                "severity": v.get("Severity"),
                "package": v.get("PkgName"),
                "fixed_in": v.get("FixedVersion") or "unfixed",
            }
            for result in (report.get("Results") or [])
            for v in (result.get("Vulnerabilities") or [])
        ]
        counts = {s: sum(1 for c in cves if c["severity"] == s) for s in _SEVERITIES}
        return ToolResult(ok=True, data={"cves": cves, "counts": counts, "real": True})


REAL_TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        RealKubectlGet(), RealKubectlLogs(), RealKubectlRestart(), RealKubectlScale(),
        RealTerraformPlan(), RealTerraformApply(), RealTrivyScan(),
    )
}
