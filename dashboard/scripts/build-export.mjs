/**
 * Build estático del dashboard para empaquetarlo dentro del wheel.
 * Uso: npm run build:export  →  genera out/ servible por FastAPI.
 */
import { spawnSync } from "node:child_process";

const bin = process.platform === "win32" ? "next.cmd" : "next";
const r = spawnSync(bin, ["build"], {
  stdio: "inherit",
  env: { ...process.env, NOUS_EXPORT: "1" },
});
process.exit(r.status ?? 1);
