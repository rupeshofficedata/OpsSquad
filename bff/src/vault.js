// Mirrors runtime/app/vault.py — Kubernetes-auth login with this pod's own
// ServiceAccount token, no static Vault credential stored anywhere.
import { readFileSync, existsSync } from "node:fs";

const SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token";

export async function loadSecretsFromVault() {
  const vaultAddr = process.env.VAULT_ADDR;
  if (!vaultAddr || !existsSync(SA_TOKEN_PATH)) return;

  const jwt = readFileSync(SA_TOKEN_PATH, "utf8").trim();
  const role = process.env.VAULT_ROLE || "opssquad-bff";

  const login = await fetch(`${vaultAddr}/v1/auth/kubernetes/login`, {
    method: "POST",
    body: JSON.stringify({ role, jwt }),
  });
  if (!login.ok) throw new Error(`Vault login failed: ${login.status}`);
  const { auth } = await login.json();

  const jwtSecretResp = await fetch(`${vaultAddr}/v1/secret/data/opssquad/jwt`, {
    headers: { "X-Vault-Token": auth.client_token },
  });
  if (!jwtSecretResp.ok) throw new Error(`Vault read failed: ${jwtSecretResp.status}`);
  const { data } = await jwtSecretResp.json();
  if (data.data.secret) process.env.JWT_SECRET = data.data.secret;
}
