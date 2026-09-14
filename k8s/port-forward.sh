#!/usr/bin/env bash
# `kubectl port-forward` doesn't reconnect when the pod it's attached to
# is replaced (any rollout, or Vault's self-heal restarting runtime/bff) —
# it just dies, and the dashboard/API silently go unreachable until
# someone notices and restarts it by hand (hit this twice in one session).
# This keeps frontend/bff forwarded for good, restarting on any exit.
set -u
CTX="kind-opssquad"
NS="opssquad"

forward() {
  local svc=$1 port=$2
  while true; do
    kubectl --context "$CTX" -n "$NS" port-forward "svc/$svc" "$port:$port"
    echo "[port-forward] $svc:$port dropped — reconnecting in 2s" >&2
    sleep 2
  done
}

forward frontend 5173 &
forward bff 4000 &
wait
