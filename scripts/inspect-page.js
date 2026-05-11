const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const { loadConfig, resolveFromProject, ensureDir, safeFilenamePart } = require('./utils');

(async () => {
  const config = loadConfig();
  const profileDir = resolveFromProject(config.browserProfileDir || './browser-profile');
  const statePath = resolveFromProject(config.storageStatePath || './storage-state.json');
  const outputDir = resolveFromProject('./inspect-output');
  ensureDir(outputDir);

  const context = await chromium.launchPersistentContext(profileDir, {
    headless: Boolean(config.headless),
    acceptDownloads: true,
    viewport: { width: 1440, height: 900 }
  });

  if (fs.existsSync(statePath)) {
    const savedState = JSON.parse(fs.readFileSync(statePath, 'utf8'));
    if (Array.isArray(savedState.cookies)) await context.addCookies(savedState.cookies);
  }

  const page = context.pages()[0] || await context.newPage();
  await page.goto(config.gmvMaxUrl, { waitUntil: 'domcontentloaded', timeout: config.navigationTimeoutMs || 60000 });
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.waitForTimeout(3000);
  await page.evaluate(() => {
    const candidates = Array.from(document.querySelectorAll('button,[role="button"],div,span,a'));
    const target = candidates.find((el) => /^(Got it|知道了|我知道了)$/i.test((el.innerText || '').trim()));
    if (target) target.click();
  }).catch(() => {});
  await page.waitForTimeout(1000);
  await page.evaluate(() => window.scrollTo({ top: document.body.scrollHeight, behavior: 'instant' }));
  await page.waitForTimeout(2500);

  const data = await page.evaluate(() => {
    const isVisible = (el) => {
      const style = window.getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
    };

    return {
      url: location.href,
      title: document.title,
      bodyTextSample: document.body.innerText.slice(0, 8000),
      inputs: Array.from(document.querySelectorAll('input')).filter(isVisible).map((el, index) => ({
        index,
        type: el.type,
        value: el.value,
        placeholder: el.getAttribute('placeholder'),
        ariaLabel: el.getAttribute('aria-label'),
        outerHTML: el.outerHTML.slice(0, 500)
      })),
      buttons: Array.from(document.querySelectorAll('button,[role="button"]')).filter(isVisible).map((el, index) => ({
        index,
        text: el.innerText.trim(),
        title: el.getAttribute('title'),
        ariaLabel: el.getAttribute('aria-label'),
        outerHTML: el.outerHTML.slice(0, 500)
      })),
      rows: Array.from(document.querySelectorAll('tr,[role="row"],.table-row')).filter(isVisible).map((el, index) => ({
        index,
        text: el.innerText.trim().slice(0, 1000),
        outerHTML: el.outerHTML.slice(0, 1000)
      })),
      tables: Array.from(document.querySelectorAll('table')).filter(isVisible).map((table, index) => ({
        index,
        text: table.innerText.slice(0, 4000),
        rowCount: table.querySelectorAll('tr').length
      }))
    };
  });

  const safeUrl = safeFilenamePart(new URL(page.url()).pathname || 'page');
  const jsonPath = path.join(outputDir, `${safeUrl || 'page'}-inspect.json`);
  const pngPath = path.join(outputDir, `${safeUrl || 'page'}-inspect.png`);
  fs.writeFileSync(jsonPath, JSON.stringify(data, null, 2), 'utf8');
  await page.screenshot({ path: pngPath, fullPage: true });
  console.log(`Inspection saved: ${jsonPath}`);
  console.log(`Screenshot saved: ${pngPath}`);
  await context.close();
})();
