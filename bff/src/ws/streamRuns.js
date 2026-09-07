import jwt from "jsonwebtoken";
import { WebSocketServer } from "ws";
import { config } from "../config.js";
import { pool } from "../db.js";

const TERMINAL_STATUSES = new Set(["success", "failed", "aborted"]);
const POLL_INTERVAL_MS = 2000;

// No message broker is wired up in this scaffold, so live updates are done
// by polling the DB on an interval and pushing a diff-free snapshot. Swap
// this for a Redis pub/sub subscription (published to by the runtime after
// each step) once run volume makes polling too slow.
export function attachRunStream(server) {
  const wss = new WebSocketServer({ noServer: true });

  server.on("upgrade", (req, socket, head) => {
    const url = new URL(req.url, "http://localhost");
    const match = url.pathname.match(/^\/ws\/runs\/([^/]+)$/);
    if (!match) return socket.destroy();

    const token = url.searchParams.get("token");
    try {
      jwt.verify(token, config.jwtSecret);
    } catch {
      socket.destroy();
      return;
    }

    wss.handleUpgrade(req, socket, head, (ws) => {
      wss.emit("connection", ws, req, match[1]);
    });
  });

  wss.on("connection", (ws, _req, runId) => {
    let closed = false;
    ws.on("close", () => {
      closed = true;
    });

    (async function poll() {
      try {
        while (!closed) {
          const { rows: runRows } = await pool.query("SELECT * FROM runs WHERE id = $1", [runId]);
          const run = runRows[0];
          if (!run) {
            ws.send(JSON.stringify({ error: "Run not found" }));
            return ws.close();
          }

          const { rows: steps } = await pool.query(
            "SELECT * FROM run_steps WHERE run_id = $1 ORDER BY step_order", [runId]
          );
          ws.send(JSON.stringify({ run, steps }));

          if (TERMINAL_STATUSES.has(run.status)) return ws.close();
          await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
        }
      } catch (err) {
        console.error(`[ws] run stream error for ${runId}:`, err.message);
        ws.close();
      }
    })();
  });

  return wss;
}
