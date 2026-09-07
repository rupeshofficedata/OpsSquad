import { Router } from "express";
import { config } from "../config.js";
import { asyncHandler } from "../middleware/asyncHandler.js";
import { requireAuth } from "../middleware/requireAuth.js";

export const router = Router();

router.get("/", requireAuth, asyncHandler(async (req, res) => {
  const resp = await fetch(`${config.runtimeUrl}/agents`, {
    headers: { Authorization: req.headers.authorization },
  });
  const body = await resp.json();
  res.status(resp.status).json(body);
}));
