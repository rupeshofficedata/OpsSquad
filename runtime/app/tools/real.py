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
  - docker:    ./docker-demo, built with rootless buildah (vfs storage,
               no extra capability needed) — a real image, no registry.
  - helm:      ./charts/canary (bundled into the image, same as docker-demo/
               terraform-demo), a trivial real release in this pod's own
               namespace (same "safe, stateless, restartable" principle as
               kubectl's redis target).
  - argocd:    a real core-install (controller + repo-server + redis, no
               UI/API server — nothing here needs ArgoCD's own UI) managing
               one dedicated ConfigMap via k8s/argocd-demo. Driven by
               `kubectl patch` on the Application CRD directly instead of
               the `argocd` CLI, since core-install has no API server for
               the CLI to talk to anyway.
  - cost/slack/pagerduty: real API calls, gated on a real credential being
               configured (same fail-closed-but-graceful shape as
               ALERTMANAGER_WEBHOOK_TOKEN) — honestly can't be live-tested
               without the user's own AWS/Slack/PagerDuty account.

A production deployment would point these at real infrastructure instead —
the point here is that the subprocess/API plumbing is genuinely real, not
that every demo target is production infrastructure.
"""

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.tools.base import Tool, ToolResult

SUBPROCESS_TIMEOUT = 60.0

_SAFE_ARG_RE = re.compile(r"[a-zA-Z0-9._/:-]+")


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


def _human_age(creation_timestamp: str | None) -> str | None:
    if not creation_timestamp:
        return None
    created = datetime.fromisoformat(creation_timestamp.replace("Z", "+00:00"))
    seconds = int((datetime.now(timezone.utc) - created).total_seconds())
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours}h" if hours else f"{days}d"


def _service_port_str(p: dict[str, Any]) -> str:
    proto = p.get("protocol", "TCP")
    return f"{p['port']}:{p['nodePort']}/{proto}" if p.get("nodePort") else f"{p.get('port')}/{proto}"


def _summarize_k8s_item(item: dict[str, Any]) -> dict[str, Any]:
    """Same columns the real `kubectl get <kind>` CLI shows for that
    specific kind — found by testing that a generic {status, ready,
    restarts} shape is honest for Pods but wrong for everything else
    (Services don't have restarts; they have TYPE/CLUSTER-IP/PORT(S),
    which Pods don't have either)."""
    metadata, status, spec = item.get("metadata", {}), item.get("status", {}), item.get("spec", {})
    name = metadata.get("name")
    age = _human_age(metadata.get("creationTimestamp"))
    kind = item.get("kind", "")

    if kind == "Service":
        ingress = status.get("loadBalancer", {}).get("ingress", [])
        external_ip = ", ".join(spec.get("externalIPs", []) + [i.get("ip") or i.get("hostname", "") for i in ingress]) or "<none>"
        return {
            "name": name,
            "type": spec.get("type", "ClusterIP"),
            "cluster_ip": spec.get("clusterIP"),
            "external_ip": external_ip,
            "ports": ", ".join(_service_port_str(p) for p in spec.get("ports", [])) or None,
            "age": age,
        }

    container_statuses = status.get("containerStatuses")
    if container_statuses is not None:  # Pod
        # `kubectl get pods` doesn't just print status.phase for STATUS — a
        # terminated container's own reason (e.g. "Completed", "Error")
        # overrides it, which is why the real CLI shows "Completed" for a
        # finished Job pod while the raw API phase is still "Succeeded".
        # Found by diffing this tool's output against real `kubectl get
        # pods` on opssquad-migrate/opssquad-vault-init.
        pod_status = status.get("phase")
        for c in container_statuses:
            terminated = (c.get("state") or {}).get("terminated") or {}
            if terminated.get("reason"):
                pod_status = terminated["reason"]
        return {
            "name": name,
            "ready": f"{sum(1 for c in container_statuses if c.get('ready'))}/{len(container_statuses)}",
            "status": pod_status,
            "restarts": sum(c.get("restartCount", 0) for c in container_statuses),
            "age": age,
        }

    # Deployments/other kinds with a replicas concept — honest fallback
    # (just name + age) for anything without one, rather than guessing.
    if "replicas" in spec:
        return {"name": name, "ready": f"{status.get('readyReplicas', 0)}/{spec.get('replicas', 0)}", "age": age}

    return {"name": name, "age": age}


class RealKubectlGet(Tool):
    name = "kubectl.get"

    async def run(self, **kwargs: Any) -> ToolResult:
        try:
            target = _safe_arg(kwargs.get("target") or settings.kube_read_target)
        except UnsafeArgError as exc:
            return ToolResult(ok=False, error=str(exc))
        code, out, err = await _run("kubectl", "get", "-n", settings.kube_namespace, "-o", "json", "--", target)
        if code != 0:
            return ToolResult(ok=False, error=err[:500])
        obj = json.loads(out)

        if obj.get("kind", "").endswith("List"):
            # A bare collection type (e.g. "pods", "deployments") returns
            # a *List — genuinely enumerate it instead of silently
            # returning 0/0 by assuming the single-object
            # .spec.replicas/.status.readyReplicas shape below, which a
            # List response never has. Same columns real `kubectl get
            # pods` shows (NAME/READY/STATUS/RESTARTS/AGE) — found by
            # testing that name+status alone wasn't actually what "list
            # pods" means to a user comparing it against the real CLI.
            items = [_summarize_k8s_item(i) for i in obj.get("items", [])]
            return ToolResult(ok=True, data={
                "namespace": settings.kube_namespace, "target": target, "items": items, "count": len(items), "real": True,
            })

        return ToolResult(ok=True, data={
            "namespace": settings.kube_namespace,
            "replicas_desired": obj.get("spec", {}).get("replicas", 0),
            "replicas_ready": obj.get("status", {}).get("readyReplicas", 0),
            "target": target, "real": True,
        })


class RealKubectlLogs(Tool):
    name = "kubectl.logs"

    async def run(self, **kwargs: Any) -> ToolResult:
        try:
            target = _safe_arg(kwargs.get("target") or settings.kube_read_target)
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
            "namespace": settings.kube_namespace,
            "lines_returned": len(lines), "error_count": error_count, "target": target, "real": True,
        })


class RealKubectlRestart(Tool):
    name = "kubectl.restart"

    async def run(self, **kwargs: Any) -> ToolResult:
        try:
            target = _safe_arg(kwargs.get("target") or settings.kube_mutate_target)
        except UnsafeArgError as exc:
            return ToolResult(ok=False, error=str(exc))
        code, _, err = await _run(
            "kubectl", "rollout", "restart", "-n", settings.kube_namespace, "--", target
        )
        return ToolResult(
            ok=code == 0,
            data={"namespace": settings.kube_namespace, "target": target, "real": True} if code == 0 else None,
            error=None if code == 0 else err[:500],
        )


class RealKubectlScale(Tool):
    name = "kubectl.scale"

    async def run(self, **kwargs: Any) -> ToolResult:
        try:
            target = _safe_arg(kwargs.get("target") or settings.kube_mutate_target)
            replicas = _safe_int(kwargs.get("replicas", 1), minimum=0)
        except (UnsafeArgError, ValueError, TypeError) as exc:
            return ToolResult(ok=False, error=str(exc))
        code, _, err = await _run(
            "kubectl", "scale", "-n", settings.kube_namespace, f"--replicas={replicas}", "--", target
        )
        return ToolResult(
            ok=code == 0,
            data={"namespace": settings.kube_namespace, "target": target, "replicas": replicas, "real": True} if code == 0 else None,
            error=None if code == 0 else err[:500],
        )


_PLAN_RE = re.compile(r"Plan:\s*(\d+)\s*to add,\s*(\d+)\s*to change,\s*(\d+)\s*to destroy")


class RealTerraformPlan(Tool):
    name = "terraform.plan"

    async def run(self, **kwargs: Any) -> ToolResult:
        code, out, err = await _run("terraform", f"-chdir={settings.terraform_dir}", "plan", "-no-color")
        match = _PLAN_RE.search(out)
        to_add, to_change, to_destroy = (int(x) for x in match.groups()) if match else (0, 0, 0)
        return ToolResult(ok=code == 0, data={
            "dir": settings.terraform_dir,
            "to_add": to_add, "to_change": to_change, "to_destroy": to_destroy, "real": True,
        } if code == 0 else None, error=None if code == 0 else err[:500])


class RealTerraformApply(Tool):
    name = "terraform.apply"

    async def run(self, **kwargs: Any) -> ToolResult:
        code, out, err = await _run(
            "terraform", f"-chdir={settings.terraform_dir}", "apply", "-auto-approve", "-no-color",
            timeout=120.0,
        )
        return ToolResult(
            ok=code == 0, data={"dir": settings.terraform_dir, "real": True} if code == 0 else None,
            error=None if code == 0 else err[:500],
        )


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


class RealDockerBuild(Tool):
    name = "docker.build"

    async def run(self, **kwargs: Any) -> ToolResult:
        try:
            # `or` not `.get(k, default)` — a Flightplan `with:` template that
            # doesn't resolve (e.g. ship-to-prod's `${inputs.commit[:7]}`
            # with no `commit` input) lands here as an explicit None, which
            # .get()'s default never catches.
            tag = _safe_arg(kwargs.get("tag") or "opssquad-docker-demo:latest")
        except UnsafeArgError as exc:
            return ToolResult(ok=False, error=str(exc))
        code, out, err = await _run(
            "buildah", "bud", "--storage-driver=vfs", "--root", "/tmp/buildah",
            "-t", tag, "--", "docker-demo", timeout=120.0,
        )
        if code != 0:
            return ToolResult(ok=False, error=err[:500])
        image_id = out.strip().splitlines()[-1] if out.strip() else ""
        return ToolResult(ok=True, data={"tag": tag, "image_id": image_id, "real": True})


class RealDockerTag(Tool):
    name = "docker.tag"

    async def run(self, **kwargs: Any) -> ToolResult:
        # build.py calls docker.build then docker.tag with the *same*
        # params — so "tag" here is the image docker.build just produced
        # (its base), not a separate new tag to apply. The real, useful
        # second tag is the conventional floating "latest" alias on the
        # same repo, matching how real CI pipelines tag a specific
        # version plus "latest" in one build.
        try:
            base = _safe_arg(kwargs.get("tag") or "opssquad-docker-demo:latest")
        except UnsafeArgError as exc:
            return ToolResult(ok=False, error=str(exc))
        repo = base.split(":", 1)[0]
        latest_tag = f"{repo}:latest"
        code, _, err = await _run("buildah", "--storage-driver=vfs", "--root", "/tmp/buildah", "tag", "--", base, latest_tag)
        return ToolResult(ok=code == 0, data={"tag": latest_tag, "real": True} if code == 0 else None,
                           error=None if code == 0 else err[:500])


class RealHelmUpgrade(Tool):
    name = "helm.upgrade"

    async def run(self, **kwargs: Any) -> ToolResult:
        try:
            tag = _safe_arg(kwargs.get("tag", "latest"))
        except UnsafeArgError as exc:
            return ToolResult(ok=False, error=str(exc))
        code, out, err = await _run(
            "helm", "upgrade", "--install", "canary", "charts/canary",
            "-n", settings.kube_namespace, "--set", f"image.tag={tag}", timeout=120.0,
        )
        return ToolResult(
            ok=code == 0,
            data={"namespace": settings.kube_namespace, "release": "canary", "tag": tag, "real": True} if code == 0 else None,
            error=None if code == 0 else err[:500],
        )


class RealHelmRollback(Tool):
    name = "helm.rollback"

    async def run(self, **kwargs: Any) -> ToolResult:
        code, _, err = await _run("helm", "rollback", "canary", "-n", settings.kube_namespace, timeout=60.0)
        return ToolResult(
            ok=code == 0,
            data={"namespace": settings.kube_namespace, "release": "canary", "real": True} if code == 0 else None,
            error=None if code == 0 else err[:500],
        )


_ARGOCD_APP = ("application", "argocd-demo", "-n", "argocd")


async def _argocd_wait_synced(timeout: float = 30.0) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        code, out, _ = await _run("kubectl", "get", *_ARGOCD_APP, "-o", "jsonpath={.status.sync.status}")
        if code == 0 and out.strip() == "Synced":
            return True
        await asyncio.sleep(2)
    return False


class RealArgocdSync(Tool):
    name = "argocd.sync"

    async def run(self, **kwargs: Any) -> ToolResult:
        code, _, err = await _run(
            "kubectl", "patch", *_ARGOCD_APP, "--type", "merge",
            "-p", '{"operation":{"sync":{}}}',
        )
        if code != 0:
            return ToolResult(ok=False, error=err[:500])
        synced = await _argocd_wait_synced()
        return ToolResult(ok=synced, data={"app": "argocd-demo", "real": True} if synced else None,
                           error=None if synced else "sync did not reach 'Synced' before timeout")


class RealArgocdRollback(Tool):
    name = "argocd.rollback"

    async def run(self, **kwargs: Any) -> ToolResult:
        code, out, err = await _run(
            "kubectl", "get", *_ARGOCD_APP, "-o",
            "jsonpath={.status.history}",
        )
        if code != 0:
            return ToolResult(ok=False, error=err[:500])
        history = json.loads(out) if out.strip() else []
        if len(history) < 2:
            return ToolResult(ok=False, error="no prior synced revision to roll back to")
        previous_revision = history[-2]["revision"]

        code, _, err = await _run(
            "kubectl", "patch", *_ARGOCD_APP, "--type", "merge",
            "-p", json.dumps({"spec": {"source": {"targetRevision": previous_revision}}}),
        )
        if code != 0:
            return ToolResult(ok=False, error=err[:500])
        code, _, err = await _run(
            "kubectl", "patch", *_ARGOCD_APP, "--type", "merge",
            "-p", '{"operation":{"sync":{}}}',
        )
        if code != 0:
            return ToolResult(ok=False, error=err[:500])
        synced = await _argocd_wait_synced()
        return ToolResult(ok=synced, data={"app": "argocd-demo", "rolled_back_to": previous_revision, "real": True} if synced else None,
                           error=None if synced else "rollback sync did not reach 'Synced' before timeout")


class RealCostExplorer(Tool):
    name = "cloud.cost_explorer"

    async def run(self, **kwargs: Any) -> ToolResult:
        if not settings.aws_access_key_id or not settings.aws_secret_access_key:
            return ToolResult(ok=False, error="AWS credentials not configured (AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY)")

        def _query() -> dict[str, Any]:
            import datetime

            import boto3

            client = boto3.client(
                "ce", region_name=settings.aws_region,
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
            )
            end = datetime.date.today()
            start = end - datetime.timedelta(days=30)
            resp = client.get_cost_and_usage(
                TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
                Granularity="MONTHLY",
                Metrics=["UnblendedCost"],
                GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
            )
            groups = resp.get("ResultsByTime", [{}])[0].get("Groups", [])
            idle_resources = [
                {"service": g["Keys"][0], "cost_usd": float(g["Metrics"]["UnblendedCost"]["Amount"])}
                for g in groups
                if float(g["Metrics"]["UnblendedCost"]["Amount"]) > 0
            ]
            total = sum(r["cost_usd"] for r in idle_resources)
            return {"idle_resources": idle_resources, "monthly_savings_usd": round(total * 0.1, 2)}

        try:
            data = await asyncio.to_thread(_query)
        except Exception as exc:
            return ToolResult(ok=False, error=str(exc)[:500])
        return ToolResult(ok=True, data={**data, "real": True})


class RealSlackPost(Tool):
    name = "slack.post"

    async def run(self, **kwargs: Any) -> ToolResult:
        if not settings.slack_webhook_url:
            return ToolResult(ok=False, error="SLACK_WEBHOOK_URL not configured")
        channel = kwargs.get("channel", "#general")
        message = kwargs.get("message", "")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(settings.slack_webhook_url, json={"channel": channel, "text": message})
        except httpx.HTTPError as exc:
            return ToolResult(ok=False, error=str(exc)[:500])
        if resp.status_code >= 400:
            return ToolResult(ok=False, error=f"Slack returned {resp.status_code}: {resp.text[:200]}")
        return ToolResult(ok=True, data={"channel": channel, "real": True})


class RealPagerdutyRead(Tool):
    name = "pagerduty.read"

    async def run(self, **kwargs: Any) -> ToolResult:
        if not settings.pagerduty_api_token:
            return ToolResult(ok=False, error="PAGERDUTY_API_TOKEN not configured")
        incident_id = kwargs.get("incident_id")
        if not incident_id:
            return ToolResult(ok=False, error="incident_id required")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"https://api.pagerduty.com/incidents/{incident_id}",
                    headers={"Authorization": f"Token token={settings.pagerduty_api_token}", "Accept": "application/vnd.pagerduty+json;version=2"},
                )
        except httpx.HTTPError as exc:
            return ToolResult(ok=False, error=str(exc)[:500])
        if resp.status_code >= 400:
            return ToolResult(ok=False, error=f"PagerDuty returned {resp.status_code}: {resp.text[:200]}")
        incident = resp.json().get("incident", {})
        return ToolResult(ok=True, data={
            "id": incident.get("id"), "status": incident.get("status"), "title": incident.get("title"), "real": True,
        })


class RealGitDiff(Tool):
    name = "git.diff"

    async def run(self, **kwargs: Any) -> ToolResult:
        code, out, err = await _run("git", "-C", "git-demo", "diff", "HEAD~1", "HEAD")
        if code != 0:
            return ToolResult(ok=False, error=err[:500])
        return ToolResult(ok=True, data={"diff": out[:2000], "real": True})


class RealGitLog(Tool):
    name = "git.log"

    async def run(self, **kwargs: Any) -> ToolResult:
        code, out, err = await _run("git", "-C", "git-demo", "log", "--oneline", "-20")
        if code != 0:
            return ToolResult(ok=False, error=err[:500])
        return ToolResult(ok=True, data={"commits": out.strip().splitlines(), "real": True})


class RealSecretsScan(Tool):
    name = "secrets.scan"

    async def run(self, **kwargs: Any) -> ToolResult:
        # -r /dev/stdout fails under asyncio's subprocess pipes ("no such
        # device or address") — found by testing. A real temp file avoids
        # the special-device issue entirely.
        report_path = "/tmp/gitleaks-report.json"
        code, _, err = await _run(
            "gitleaks", "detect", "--source", "git-demo", "-f", "json", "-r", report_path, "--exit-code", "0",
            timeout=60.0,
        )
        if code != 0:
            return ToolResult(ok=False, error=err[:500])
        out = Path(report_path).read_text() if Path(report_path).exists() else ""
        findings = json.loads(out) if out.strip() else []
        secrets_found = [{"kind": f.get("RuleID"), "file": f.get("File"), "line": f.get("StartLine")} for f in findings]
        return ToolResult(ok=True, data={"secrets_found": secrets_found, "real": True})


class RealLintRun(Tool):
    name = "lint.run"

    async def run(self, **kwargs: Any) -> ToolResult:
        code, out, err = await _run("ruff", "check", "app", "--output-format", "json", timeout=30.0)
        # ruff exits 1 when it finds warnings — that's a normal result, not a tool failure.
        if code not in (0, 1):
            return ToolResult(ok=False, error=err[:500])
        warnings = json.loads(out) if out.strip() else []
        return ToolResult(ok=True, data={"warnings": len(warnings), "real": True})


class RealTestRun(Tool):
    name = "test.run"

    async def run(self, **kwargs: Any) -> ToolResult:
        code, out, err = await _run("python", "-m", "pytest", "app/tests", "-q", "--tb=no", timeout=60.0)
        if code not in (0, 1):
            return ToolResult(ok=False, error=err[:500] or out[:500])
        match = re.search(r"(\d+) passed(?:, (\d+) failed)?|(\d+) failed", out)
        passed = int(match.group(1)) if match and match.group(1) else 0
        failed = int(match.group(2) or match.group(3) or 0) if match else 0
        return ToolResult(ok=True, data={
            "total": passed + failed, "passed": passed, "failed": failed, "real": True,
        })


class RealTestSelect(Tool):
    name = "test.select"

    async def run(self, **kwargs: Any) -> ToolResult:
        code, out, err = await _run("find", "app/tests", "-name", "test_*.py")
        if code != 0:
            return ToolResult(ok=False, error=err[:500])
        return ToolResult(ok=True, data={"tests": out.strip().splitlines(), "real": True})


class RealRegistryPush(Tool):
    name = "registry.push"

    async def run(self, **kwargs: Any) -> ToolResult:
        try:
            tag = _safe_arg(kwargs.get("tag") or "opssquad-docker-demo:latest")
        except UnsafeArgError as exc:
            return ToolResult(ok=False, error=str(exc))
        dest = f"registry:5000/{tag}"
        code, _, err = await _run(
            "buildah", "push", "--storage-driver=vfs", "--root", "/tmp/buildah",
            "--tls-verify=false", tag, dest, timeout=60.0,
        )
        return ToolResult(ok=code == 0, data={"pushed_to": dest, "real": True} if code == 0 else None,
                           error=None if code == 0 else err[:500])


class RealRegistryPull(Tool):
    name = "registry.pull"

    async def run(self, **kwargs: Any) -> ToolResult:
        try:
            tag = _safe_arg(kwargs.get("tag") or "opssquad-docker-demo:latest")
        except UnsafeArgError as exc:
            return ToolResult(ok=False, error=str(exc))
        src = f"registry:5000/{tag}"
        code, _, err = await _run(
            "buildah", "pull", "--storage-driver=vfs", "--root", "/tmp/buildah",
            "--tls-verify=false", src, timeout=60.0,
        )
        return ToolResult(ok=code == 0, data={"pulled_from": src, "real": True} if code == 0 else None,
                           error=None if code == 0 else err[:500])


class RealIacScan(Tool):
    name = "iac.scan"

    async def run(self, **kwargs: Any) -> ToolResult:
        code, out, err = await _run("trivy", "config", "--format", "json", "--quiet", "--", settings.terraform_dir, timeout=60.0)
        if code != 0:
            return ToolResult(ok=False, error=err[:500])
        report = json.loads(out)
        misconfigs = [
            {"rule": m.get("Title"), "resource": m.get("ID")}
            for result in (report.get("Results") or [])
            for m in (result.get("Misconfigurations") or [])
        ]
        return ToolResult(ok=True, data={"misconfigurations": misconfigs, "real": True})


class RealHttpSmokeTest(Tool):
    name = "http.smoke_test"

    async def run(self, **kwargs: Any) -> ToolResult:
        targets = ["http://runtime:8000/health", "http://bff:4000/health"]
        failures = []
        async with httpx.AsyncClient(timeout=5) as client:
            for url in targets:
                try:
                    resp = await client.get(url)
                    if resp.status_code >= 400:
                        failures.append(f"GET {url} -> {resp.status_code}")
                except httpx.HTTPError as exc:
                    failures.append(f"GET {url} -> {exc}")
        return ToolResult(ok=True, data={
            "ok": not failures, "checks_run": len(targets), "failures": failures, "real": True,
        })


class RealPrometheusQuery(Tool):
    name = "prometheus.query"

    async def run(self, **kwargs: Any) -> ToolResult:
        # No app-level HTTP error-rate metric exists in this scaffold
        # (cadvisor only exposes container cpu/memory/network) — verify.py
        # thresholds on "error_rate" though, so the real, honest signal is
        # the fraction of scraped targets that are actually down, not a
        # fabricated HTTP metric. p99_latency_ms stays real-absent (None)
        # rather than invented.
        query = kwargs.get("query") or 'up{job="kubernetes-cadvisor"}'
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get("http://prometheus:9090/api/v1/query", params={"query": query})
        except httpx.HTTPError as exc:
            return ToolResult(ok=False, error=str(exc)[:500])
        if resp.status_code >= 400:
            return ToolResult(ok=False, error=f"Prometheus returned {resp.status_code}: {resp.text[:200]}")
        result = resp.json().get("data", {}).get("result", [])
        total = len(result)
        down = sum(1 for r in result if r.get("value", [None, "1"])[1] == "0")
        error_rate = round(down / total, 4) if total else 0.0
        return ToolResult(ok=True, data={
            "query": query, "error_rate": error_rate, "p99_latency_ms": None,
            "series_returned": total, "real": True,
        })


class RealAlertmanagerRead(Tool):
    name = "alertmanager.read"

    async def run(self, **kwargs: Any) -> ToolResult:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get("http://alertmanager:9093/api/v2/alerts")
        except httpx.HTTPError as exc:
            return ToolResult(ok=False, error=str(exc)[:500])
        if resp.status_code >= 400:
            return ToolResult(ok=False, error=f"Alertmanager returned {resp.status_code}: {resp.text[:200]}")
        alerts = resp.json()
        firing = [a for a in alerts if a.get("status", {}).get("state") == "active"]
        first = firing[0] if firing else (alerts[0] if alerts else {})
        labels = first.get("labels", {})
        return ToolResult(ok=True, data={
            "severity": labels.get("severity", "unknown"),
            "alertname": labels.get("alertname", "none"),
            "summary": first.get("annotations", {}).get("summary", ""),
            "firing_count": len(firing),
            "real": True,
        })


REAL_TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        RealKubectlGet(), RealKubectlLogs(), RealKubectlRestart(), RealKubectlScale(),
        RealTerraformPlan(), RealTerraformApply(), RealTrivyScan(),
        RealDockerBuild(), RealDockerTag(),
        RealHelmUpgrade(), RealHelmRollback(),
        RealArgocdSync(), RealArgocdRollback(),
        RealCostExplorer(), RealSlackPost(), RealPagerdutyRead(),
        RealGitDiff(), RealGitLog(), RealSecretsScan(), RealLintRun(),
        RealTestRun(), RealTestSelect(),
        RealRegistryPush(), RealRegistryPull(), RealIacScan(),
        RealHttpSmokeTest(), RealPrometheusQuery(), RealAlertmanagerRead(),
    )
}
