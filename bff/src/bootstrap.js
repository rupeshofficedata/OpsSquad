// Real entrypoint (see Dockerfile CMD). config.js and db.js both read
// process.env at import time (db.js's pool is created the moment it's
// imported) — a static `import` at the top of server.js is hoisted and
// would already lock in stale env vars before an async Vault fetch could
// run. Dynamic import() isn't hoisted, so: fetch first, then import.
import { loadSecretsFromVault } from "./vault.js";

const INSECURE_DEFAULT_JWT_SECRET = "change-me-to-a-long-random-string";

await loadSecretsFromVault(); // no-op if VAULT_ADDR isn't set

if (!process.env.JWT_SECRET || process.env.JWT_SECRET === INSECURE_DEFAULT_JWT_SECRET) {
  console.error(
    "JWT_SECRET is not configured (or still the old public placeholder). " +
    "Set it via .env or Vault — refusing to start with an unsafe default."
  );
  process.exit(1);
}

await import("./server.js");
