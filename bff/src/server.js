import { createServer } from "node:http";
import cookieParser from "cookie-parser";
import cors from "cors";
import express from "express";
import rateLimit from "express-rate-limit";

import { config } from "./config.js";
import { connectRedis } from "./redis.js";
import { attachRunStream } from "./ws/streamRuns.js";

import { router as adminRouter } from "./routes/admin.js";
import { router as agentsRouter } from "./routes/agents.js";
import { router as authRouter } from "./routes/auth.js";
import { router as chatRouter } from "./routes/chat.js";
import { router as flightplansRouter } from "./routes/flightplans.js";
import { router as meRouter } from "./routes/me.js";
import { router as runsRouter } from "./routes/runs.js";

const app = express();

app.use(cors({ origin: config.frontendOrigin, credentials: true }));
app.use(express.json());
app.use(cookieParser());
app.use(rateLimit({ windowMs: 60_000, max: 120 }));

app.get("/health", (_req, res) => res.json({ status: "ok", service: "opssquad-bff" }));

app.use("/api/auth", authRouter);
app.use("/api/me", meRouter);
app.use("/api/agents", agentsRouter);
app.use("/api/chat", chatRouter);
app.use("/api/flightplans", flightplansRouter);
app.use("/api/runs", runsRouter);
app.use("/api/admin", adminRouter);

// Catches errors forwarded by asyncHandler (see middleware/asyncHandler.js).
// Without this, a rejected promise in a route handler crashes the process.
app.use((err, _req, res, _next) => {
  console.error("[bff] request error:", err);
  res.status(500).json({ error: "Internal server error" });
});

const server = createServer(app);
attachRunStream(server);

await connectRedis();

server.listen(config.port, () => {
  console.log(`[opssquad-bff] listening on :${config.port}`);
});
