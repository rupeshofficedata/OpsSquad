import jwt from "jsonwebtoken";
import { config } from "../config.js";

export function requireAuth(req, res, next) {
  const header = req.headers.authorization || "";
  if (!header.startsWith("Bearer ")) {
    return res.status(401).json({ error: "Missing bearer token" });
  }
  const token = header.slice("Bearer ".length);
  try {
    req.user = jwt.verify(token, config.jwtSecret);
    req.accessToken = token;
    next();
  } catch (err) {
    return res.status(401).json({ error: `Invalid token: ${err.message}` });
  }
}
