import { Router } from "express";
import { config } from "../config.js";
import { asyncHandler } from "../middleware/asyncHandler.js";
import { requireAuth } from "../middleware/requireAuth.js";
import { requireRole } from "../middleware/requireRole.js";

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

// Models the host can serve (every .gguf the control agent finds) + which one is loaded.
router.get("/list", requireAuth, asyncHandler(async (_req, res) => {
  const r = await controlAgentFetch("/models");
  res.json({ models: [], active: null, ...r });
}));

// Switching or stopping the model affects every user's runs, so dev+ only.
router.post("/load", requireAuth, requireRole("dev"), asyncHandler(async (req, res) => {
  const model = req.body?.model;
  if (typeof model !== "string" || !model) return res.status(400).json({ error: "model is required" });
  const r = await controlAgentFetch("/load", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model }),
  });
  res.status(r.error ? 400 : 200).json(r);
}));

router.post("/stop", requireAuth, requireRole("dev"), asyncHandler(async (_req, res) => {
  res.json(await controlAgentFetch("/stop", { method: "POST" }));
}));
