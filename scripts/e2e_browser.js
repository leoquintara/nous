const { chromium } = require('playwright-core');
const fs = require('fs');

const TOKEN = fs.readFileSync('.env', 'utf8')
  .split('\n').find(l => l.startsWith('NOUS_API_TOKEN='))
  ?.split('=')[1]?.trim();

(async () => {
  if (!TOKEN) { console.log('FAIL: sin NOUS_API_TOKEN en .env'); process.exit(1); }
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

  // 1. Login con token
  await page.goto('http://localhost:3000/login', { waitUntil: 'networkidle', timeout: 60000 });
  await page.fill('input[type="password"]', TOKEN);
  await page.click('button[type="submit"]');
  await page.waitForURL('http://localhost:3000/', { timeout: 30000 });
  await page.waitForTimeout(4000);
  await page.screenshot({ path: 'e2e_1_home.png' });
  console.log('1. login -> home OK');
  console.log('   canvas:', await page.locator('canvas').count(), 'elementos');

  // 2. Páginas
  for (const [ruta, nombre] of [['/memoria','memoria'], ['/proyeccion','proyeccion'], ['/benchmarks','benchmarks']]) {
    await page.goto('http://localhost:3000' + ruta, { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForTimeout(2500);
    await page.screenshot({ path: `e2e_2_${nombre}.png` });
    const texto = await page.locator('body').innerText();
    console.log(`2. ${ruta}: ${texto.length} chars, primeras 60: ${texto.replace(/\s+/g,' ').slice(0,60)}`);
  }

  // 3. Tema dark/light
  await page.goto('http://localhost:3000/', { waitUntil: 'networkidle', timeout: 60000 });
  const temaAntes = await page.locator('html').getAttribute('data-theme');
  const botonTema = page.locator('button[aria-label*="Tema"]').first();
  if (await botonTema.count()) {
    await botonTema.click();
    await page.waitForTimeout(800);
    const temaDespues = await page.locator('html').getAttribute('data-theme');
    await page.screenshot({ path: 'e2e_3_tema.png' });
    console.log('3. tema:', temaAntes, '->', temaDespues);
  } else {
    console.log('3. tema: boton no encontrado');
  }

  console.log('console errors:', errors.length ? errors.slice(0, 6) : 'ninguno');
  await browser.close();
})().catch(e => { console.log('FATAL', e.message); process.exit(1); });
