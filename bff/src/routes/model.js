import { Router } from "express";
import { config } from "../config.js";
import { asyncHandler } from "../middleware/asyncHandler.js";
import { requireAuth } from "../middleware/requireAuth.js";

export const router = Router();

// Thin proxy to local-model-control/agent.py — same shape as routes/agents.js.
// If the host-side agent itself isn't reachable (not started, or this isn't
// running against the machine that has a local model), that's
// indistinguishable from "stopped" to a caller — same honest default state.
async function controlAgentFetch(path, options) {
  try {
    const resp = await fetch(`${config.modelControlUrl}${path}`, options);
    return await resp.json();
  } catch {
    return { state: "stopped" };
  }
}

router.get("/status", requireAuth, asyncHandler(async (_req, res) => {
  res.json(await controlAgentFetch("/status"));
}));

router.post("/start", requireAuth, asyncHandler(async (_req, res) => {
  res.json(await controlAgentFetch("/start", { method: "POST" }));
}));
