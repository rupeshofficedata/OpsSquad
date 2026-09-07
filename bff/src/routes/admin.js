import { Router } from "express";
import { config } from "../config.js";
import { asyncHandler } from "../middleware/asyncHandler.js";
import { requireAuth } from "../middleware/requireAuth.js";
import { requireRole } from "../middleware/requireRole.js";

export const router = Router();

router.use(requireAuth, requireRole("admin"));

async function proxy(req, res, path, method = req.method) {
  const resp = await fetch(`${config.controlUrl}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      Authorization: req.headers.authorization,
    },
    body: ["GET", "HEAD"].includes(method) ? undefined : JSON.stringify(req.body),
  });
  const body = await resp.json();
  res.status(resp.status).json(body);
}

router.get("/users", asyncHandler((req, res) => proxy(req, res, "/admin/users")));
router.post("/users", asyncHandler((req, res) => proxy(req, res, "/admin/users")));
router.patch("/users/:id/role", asyncHandler((req, res) => proxy(req, res, `/admin/users/${req.params.id}/role`)));
router.get("/audit", asyncHandler((req, res) => {
  const qs = new URLSearchParams(req.query).toString();
  return proxy(req, res, `/admin/audit${qs ? `?${qs}` : ""}`, "GET");
}));
