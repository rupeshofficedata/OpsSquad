const ROLE_ORDER = ["viewer", "dev", "admin"];

export function roleAtLeast(userRole, requiredRole) {
  return ROLE_ORDER.indexOf(userRole) >= ROLE_ORDER.indexOf(requiredRole);
}

// This is a convenience gate for the UI/UX — the real enforcement happens
// again in FastAPI/Flask, which never trust the BFF's check alone.
export function requireRole(minRole) {
  return (req, res, next) => {
    if (!req.user) return res.status(401).json({ error: "Not authenticated" });
    if (!roleAtLeast(req.user.role, minRole)) {
      return res.status(403).json({ error: `role '${req.user.role}' needs >= '${minRole}'` });
    }
    next();
  };
}
