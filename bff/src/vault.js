// Mirrors runtime/app/vault.py — Kubernetes-auth login with this pod's own
// ServiceAccount token, no static Vault credential stored anywhere.
import { readFileSync, existsSync } from "node:fs";

const SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token";

// Same rationale as runtime/app/vault.py's MAX_LOGIN_ATTEMPTS: dev-mode
// Vault is in-memory and self-heals via a 2-minute CronJob after a
// restart, and a pod can also boot before cluster DNS resolves "vault"
// yet — both transient, so retry instead of crashing.
const MAX_LOGIN_ATTEMPTS = 40;
const LOGIN_RETRY_MS = 5000;

async function login(vaultAddr, role, jwt) {
  for (let attempt = 0; attempt < MAX_LOGIN_ATTEMPTS; attempt++) {
    try {
      const resp = await fetch(`${vaultAddr}/v1/auth/kubernetes/login`, {
        method: "POST",
        body: JSON.stringify({ role, jwt }),
      });
      if (!resp.ok) throw new Error(`Vault login failed: ${resp.status}`);
      return resp;
    } catch (err) {
      if (attempt === MAX_LOGIN_ATTEMPTS - 1) throw err;
      await new Promise((r) => setTimeout(r, LOGIN_RETRY_MS));
    }
  }
}

export async function loadSecretsFromVault() {
  const vaultAddr = process.env.VAULT_ADDR;
  if (!vaultAddr || !existsSync(SA_TOKEN_PATH)) return;

  const jwt = readFileSync(SA_TOKEN_PATH, "utf8").trim();
  const role = process.env.VAULT_ROLE || "opssquad-bff";

  const loginResp = await login(vaultAddr, role, jwt);
  const { auth } = await loginResp.json();

  const jwtSecretResp = await fetch(`${vaultAddr}/v1/secret/data/opssquad/jwt`, {
    headers: { "X-Vault-Token": auth.client_token },
  });
  if (!jwtSecretResp.ok) throw new Error(`Vault read failed: ${jwtSecretResp.status}`);
  const { data } = await jwtSecretResp.json();
  if (data.data.secret) process.env.JWT_SECRET = data.data.secret;
}
