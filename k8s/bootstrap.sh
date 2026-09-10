#!/usr/bin/env bash
# Bootstraps OpsSquad onto a local kind cluster: builds images, creates the
# cluster if needed, loads images, applies manifests, runs the DB migration
# Job, waits for every Deployment to be ready, and port-forwards the
# frontend + BFF to localhost so the dashboard is reachable at
# http://localhost:5173. Safe to re-run — every step is idempotent.
#
# Uses `kubectl --context kind-opssquad` throughout instead of switching the
# user's current kube context, so it won't disturb work against any other
# cluster (e.g. a different kind cluster already in use).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
K8S_DIR="$ROOT_DIR/k8s"
CLUSTER_NAME="opssquad"
NAMESPACE="opssquad"
CTX="kind-$CLUSTER_NAME"
PIDFILE="/tmp/opssquad-k8s-port-forward.pids"

k() { kubectl --context "$CTX" "$@"; }
log() { echo "==> $*"; }

for bin in docker kind kubectl; do
  command -v "$bin" >/dev/null || { echo "missing required tool: $bin"; exit 1; }
done

log "Building images"
docker build -t opssquad/runtime:local  "$ROOT_DIR/runtime"
docker build -t opssquad/bff:local      "$ROOT_DIR/bff"
docker build -t opssquad/frontend:local "$ROOT_DIR/frontend"

# `kind create cluster` switches the global current-context as a side
# effect; remember it so we can put it back afterwards and not disturb
# whatever cluster the user was already working against.
ORIGINAL_CTX="$(kubectl config current-context 2>/dev/null || true)"

if kind get clusters | grep -qx "$CLUSTER_NAME"; then
  log "kind cluster '$CLUSTER_NAME' already exists — reusing it"
else
  log "Creating kind cluster '$CLUSTER_NAME'"
  kind create cluster --name "$CLUSTER_NAME"
fi

log "Loading images into the cluster"
kind load docker-image \
  opssquad/runtime:local opssquad/bff:local opssquad/frontend:local \
  --name "$CLUSTER_NAME"

log "Applying namespace, config, secrets, and data stores"
k apply -f "$K8S_DIR/00-namespace.yaml"
k apply -f "$K8S_DIR/01-configmap.yaml"
k apply -f "$K8S_DIR/02-secret.yaml"
k apply -f "$K8S_DIR/10-postgres.yaml"
k apply -f "$K8S_DIR/11-redis.yaml"

log "Waiting for postgres and redis"
k -n "$NAMESPACE" rollout status deployment/postgres --timeout=120s
k -n "$NAMESPACE" rollout status deployment/redis --timeout=60s

log "Syncing db/migrations + db/seed.sql into a ConfigMap and running the migration Job"
k -n "$NAMESPACE" create configmap opssquad-sql \
  --from-file=001_init.sql="$ROOT_DIR/db/migrations/001_init.sql" \
  --from-file=seed.sql="$ROOT_DIR/db/seed.sql" \
  --dry-run=client -o yaml | k apply -f -
k -n "$NAMESPACE" delete job opssquad-migrate --ignore-not-found
k apply -f "$K8S_DIR/20-migration-job.yaml"
if ! k -n "$NAMESPACE" wait --for=condition=complete job/opssquad-migrate --timeout=120s; then
  log "Migration job failed — logs:"
  k -n "$NAMESPACE" logs job/opssquad-migrate
  exit 1
fi

log "Applying application deployments"
k apply -f "$K8S_DIR/30-runtime.yaml"
k apply -f "$K8S_DIR/32-bff.yaml"
k apply -f "$K8S_DIR/33-frontend.yaml"

# Images are always rebuilt above but keep the same :local tag, so `apply`
# sees no spec diff and won't restart already-running pods even though the
# node's image content changed. Force it every run.
log "Restarting deployments to pick up the freshly built images"
k -n "$NAMESPACE" rollout restart deployment/runtime deployment/bff deployment/frontend

log "Waiting for application rollouts"
k -n "$NAMESPACE" rollout status deployment/runtime  --timeout=120s
k -n "$NAMESPACE" rollout status deployment/bff      --timeout=120s
k -n "$NAMESPACE" rollout status deployment/frontend --timeout=120s

log "Stopping any previous port-forwards"
if [ -f "$PIDFILE" ]; then
  while read -r pid; do kill "$pid" 2>/dev/null || true; done < "$PIDFILE"
  rm -f "$PIDFILE"
fi
fuser -k -TERM 5173/tcp 4000/tcp 2>/dev/null || true
# kill is async — wait for the ports to actually free before rebinding them,
# otherwise the new port-forward can lose the race and fail to listen.
for port in 5173 4000; do
  for _ in $(seq 1 20); do
    ss -tln | grep -q ":$port " || break
    sleep 0.25
  done
done

log "Starting port-forwards (frontend :5173, bff :4000)"
k -n "$NAMESPACE" port-forward svc/frontend 5173:5173 >/tmp/opssquad-k8s-pf-frontend.log 2>&1 &
echo $! >> "$PIDFILE"
k -n "$NAMESPACE" port-forward svc/bff 4000:4000 >/tmp/opssquad-k8s-pf-bff.log 2>&1 &
echo $! >> "$PIDFILE"
sleep 3

log "Health checks"
curl -sf http://localhost:4000/health && echo
curl -sf -o /dev/null -w "frontend HTTP %{http_code}\n" http://localhost:5173/

if [ -n "$ORIGINAL_CTX" ] && [ "$ORIGINAL_CTX" != "$CTX" ]; then
  kubectl config use-context "$ORIGINAL_CTX" >/dev/null
  log "Restored kubectl current-context to '$ORIGINAL_CTX' (this script used --context=$CTX throughout)"
fi

log "Done. Dashboard: http://localhost:5173  (login: admin@opssquad.dev / Admin@123)"
log "Port-forward PIDs recorded in $PIDFILE — stop them with: kill \$(cat $PIDFILE)"
