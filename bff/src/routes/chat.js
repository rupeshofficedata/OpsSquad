import { Router } from "express";
import { config } from "../config.js";
import { asyncHandler } from "../middleware/asyncHandler.js";
import { requireAuth } from "../middleware/requireAuth.js";

export const router = Router();

async function proxyPost(req, res, path) {
  const resp = await fetch(`${config.runtimeUrl}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: req.headers.authorization,
    },
    body: JSON.stringify(req.body),
  });
  const body = await resp.json();
  res.status(resp.status).json(body);
}

// No role gate here: which agent gets matched (and therefore its min_role)
// isn't known until FastAPI's intent router classifies the prompt, so the
// real RBAC check happens there — viewers are allowed through to reach
// read-only agents, and FastAPI 403s them on a write/deploy agent match.
router.post("/", requireAuth, asyncHandler((req, res) => proxyPost(req, res, "/chat")));
router.post("/:runId/reply", requireAuth, asyncHandler((req, res) => proxyPost(req, res, `/chat/${req.params.runId}/reply`)));
router.post("/:runId/approve-command", requireAuth, asyncHandler((req, res) => proxyPost(req, res, `/chat/${req.params.runId}/approve-command`)));
router.post("/:runId/deny-command", requireAuth, asyncHandler((req, res) => proxyPost(req, res, `/chat/${req.params.runId}/deny-command`)));
