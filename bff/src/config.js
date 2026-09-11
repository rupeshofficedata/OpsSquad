export const config = {
  port: process.env.BFF_PORT || 4000,
  databaseUrl: process.env.DATABASE_URL || "postgresql://opssquad:change-me@localhost:5432/opssquad",
  redisUrl: process.env.REDIS_URL || "redis://localhost:6379",
  // No usable default on purpose — bootstrap.js (the real entrypoint,
  // see Dockerfile) sets this from Vault before server.js/config.js are
  // ever imported, in k8s. Plain docker-compose reads it straight from
  // .env. bootstrap.js refuses to start if it's still empty/the old
  // public placeholder either way.
  jwtSecret: process.env.JWT_SECRET || "",
  jwtAccessTtl: process.env.JWT_ACCESS_TTL || "15m",
  jwtRefreshTtl: process.env.JWT_REFRESH_TTL || "7d",
  runtimeUrl: process.env.RUNTIME_URL || "http://localhost:8000",
  modelControlUrl: process.env.MODEL_CONTROL_URL || "http://localhost:8081",
  frontendOrigin: process.env.FRONTEND_ORIGIN || "http://localhost:5173",
};
