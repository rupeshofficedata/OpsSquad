#!/usr/bin/env bash
# Counterpart to bootstrap.sh: stops the local model server (GPU + ~9GB RAM,
# see local-model-control/agent.py), kills the dashboard port-forwards, and
# docker-stops the kind cluster's node container(s) — so the whole stack
# (kubelet, etcd, apiserver, every pod) stops costing RAM. Nothing is
# deleted: images, volumes and the cluster's own state (Postgres PVC,
# Vault, DB data) survive stopped, exactly as docker/kind leave a stopped
# container. Bring it back with `k8s/bootstrap.sh`, which docker-starts a
# stopped node before reusing it.
set -euo pipefail

CLUSTER_NAME="opssquad"
PIDFILE="/tmp/opssquad-k8s-port-forward.pids"

log() { echo "==> $*"; }

log "Stopping the local model server (if running)"
curl -sf -X POST http://localhost:8081/stop >/dev/null 2>&1 && log "  stopped" || log "  not running / control agent unreachable — nothing to stop"

log "Stopping port-forwards"
if [ -f "$PIDFILE" ]; then
  while read -r pid; do kill "$pid" 2>/dev/null || true; done < "$PIDFILE"
  rm -f "$PIDFILE"
fi
fuser -k -TERM 5173/tcp 4000/tcp 2>/dev/null || true

nodes=$(docker ps -q --filter "label=io.x-k8s.kind.cluster=$CLUSTER_NAME")
if [ -z "$nodes" ]; then
  log "kind cluster '$CLUSTER_NAME' has no running node containers — already stopped"
else
  log "Stopping kind cluster '$CLUSTER_NAME' node container(s)"
  docker stop $nodes >/dev/null
fi

log "Done. RAM and GPU freed. Bring it back up with: k8s/bootstrap.sh"
