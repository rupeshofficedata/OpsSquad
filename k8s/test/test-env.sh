#!/usr/bin/env bash
# Disposable test namespace `opssquad-test` inside the existing kind-opssquad
# cluster: full RBAC in that namespace only, no approval pause, mock
# PagerDuty/Slack/Cost Explorer. See k8s/test/test-env.yaml.
#   k8s/test/test-env.sh up      build :test images, deploy, seed fixtures, port-forward BFF to :4100
#   k8s/test/test-env.sh down    delete the namespace and the Argo fixture
#   k8s/test/test-env.sh status
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CTX=kind-opssquad NS=opssquad-test PIDFILE=/tmp/opssquad-test-forward.pid
k() { kubectl --context "$CTX" "$@"; }
kt() { k -n "$NS" "$@"; }

up() {
  docker build -q -t opssquad/runtime:test "$ROOT/runtime"
  docker build -q -t opssquad/bff:test "$ROOT/bff"
  kind load docker-image opssquad/runtime:test opssquad/bff:test --name opssquad

  k create namespace "$NS" --dry-run=client -o yaml | k apply -f -
  if ! kt get secret opssquad-secrets >/dev/null 2>&1; then
    pw=$(openssl rand -hex 12)
    kt create secret generic opssquad-secrets \
      --from-literal=POSTGRES_PASSWORD="$pw" \
      --from-literal=DATABASE_URL="postgresql://opssquad:$pw@postgres:5432/opssquad" \
      --from-literal=JWT_SECRET="$(openssl rand -hex 32)"
  fi
  kt create configmap opssquad-sql --from-file="$ROOT/db/migrations/001_init.sql" --from-file="$ROOT/db/seed.sql" \
    --dry-run=client -o yaml | k apply -f -
  kt create configmap mocks-src --from-file="$ROOT/k8s/test/mocks.py" --dry-run=client -o yaml | k apply -f -

  kt delete job opssquad-migrate --ignore-not-found
  k apply -f "$ROOT/k8s/test/test-env.yaml" -f "$ROOT/k8s/test/argocd.yaml"
  kt wait --for=condition=complete job/opssquad-migrate --timeout=180s
  for d in runtime bff mocks registry prometheus alertmanager redis; do kt rollout status deploy/$d --timeout=180s; done

  # Use the self-hosted model on the host (LOCAL_LLM_BASE_URL) rather than falling back to the simulator.
  kt exec deploy/postgres -- psql -U opssquad -qc "UPDATE users SET model_provider='local'"

  # helm.rollback needs >=2 revisions; argocd.rollback needs >=2 synced revisions.
  rt=$(kt get pod -l app=runtime -o name | head -1)
  for _ in 1 2; do kt exec "$rt" -- helm upgrade --install canary /app/charts/canary -n "$NS" >/dev/null; done
  argo_fixture

  forward
  echo "test env ready: BFF http://localhost:4100  (admin@opssquad.dev / Admin@123)"
}

# Sync at an older revision, then at HEAD, so history has two entries.
argo_fixture() {
  full=$(gh api "repos/argoproj/argocd-example-apps/commits/5c2d89b897" --jq .sha)
  patch() { k -n argocd patch application argocd-test --type merge -p "$1" >/dev/null; }
  wait_synced() { for _ in $(seq 30); do [ "$(k -n argocd get application argocd-test -o jsonpath='{.status.sync.status}')" = Synced ] && return; sleep 2; done; return 1; }
  patch "{\"spec\":{\"source\":{\"targetRevision\":\"$full\"}},\"operation\":{\"sync\":{}}}"; wait_synced
  patch '{"spec":{"source":{"targetRevision":"HEAD"}},"operation":{"sync":{}}}'; wait_synced
}

forward() {
  [ -f "$PIDFILE" ] && kill "$(cat "$PIDFILE")" 2>/dev/null || true
  nohup kubectl --context "$CTX" -n "$NS" port-forward svc/bff 4100:4000 >/tmp/opssquad-test-forward.log 2>&1 &
  echo $! >"$PIDFILE"
}

down() {
  [ -f "$PIDFILE" ] && kill "$(cat "$PIDFILE")" 2>/dev/null || true
  rm -f "$PIDFILE"
  k -n argocd delete application/argocd-test rolebinding/runtime-test-argocd-sync role/runtime-test-argocd-sync --ignore-not-found
  k delete namespace "$NS" --ignore-not-found
}

case "${1:-}" in
  up) up ;;
  down) down ;;
  status) kt get deploy,pods; k -n argocd get application argocd-test ;;
  *) echo "usage: $0 up|down|status" >&2; exit 2 ;;
esac
