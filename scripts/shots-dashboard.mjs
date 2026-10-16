// Screenshots del dashboard Nous (login + sidebar) con chromium bundled.
// Uso: node scripts/shots-dashboard.mjs [baseUrl] [token]
import { chromium } from "playwright-core";

const EXE =
  "C:\\Users\\spice\\AppData\\Local\\ms-playwright\\chromium_headless_shell-1234\\chrome-headless-shell-win64\\chrome-headless-shell.exe";
const BASE = process.argv[2] ?? "http://localhost:8720";
const TOKEN = process.argv[3];

const browser = await chromium.launch({ executablePath: EXE });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on("pageerror", (e) => console.log("[pageerror]", e.message));
page.on("console", (m) => {
  if (m.type() === "error") console.log("[console.error]", m.text());
});

// 1) login con fondo universo
await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
await page.waitForTimeout(2500);
const painted = await page.evaluate(() => {
  const c = document.querySelector("canvas");
  if (!c) return "no canvas";
  const x = c.getContext("2d");
  const d = x.getImageData(0, 0, c.width, c.height).data;
  let n = 0;
  for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++;
  return `${n} px con alpha`;
});
console.log("login canvas:", painted);
await page.screenshot({ path: "shot-login.png" });

// 2) sidebar expandido (autenticado via localStorage token)
if (TOKEN) {
  await page.evaluate((t) => localStorage.setItem("nous.token", t), TOKEN);
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.waitForTimeout(3000);
  await page.screenshot({ path: "shot-sidebar.png" });

  // 3) sidebar colapsado
  await page.evaluate(() =>
    localStorage.setItem("nous.sidebar-colapsado", "1"),
  );
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: "shot-sidebar-min.png" });
}

await browser.close();
console.log("ok");
