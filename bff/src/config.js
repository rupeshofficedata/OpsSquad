export const config = {
  port: process.env.BFF_PORT || 4000,
  databaseUrl: process.env.DATABASE_URL || "postgresql://opssquad:change-me@localhost:5432/opssquad",
  redisUrl: process.env.REDIS_URL || "redis://localhost:6379",
  jwtSecret: process.env.JWT_SECRET || "change-me-to-a-long-random-string",
  jwtAccessTtl: process.env.JWT_ACCESS_TTL || "15m",
  jwtRefreshTtl: process.env.JWT_REFRESH_TTL || "7d",
  runtimeUrl: process.env.RUNTIME_URL || "http://localhost:8000",
  controlUrl: process.env.CONTROL_URL || "http://localhost:6001",
  frontendOrigin: process.env.FRONTEND_ORIGIN || "http://localhost:5173",
};
