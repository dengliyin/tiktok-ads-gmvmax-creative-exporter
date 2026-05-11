const { chromium } = require('playwright');
const { loadConfig, resolveFromProject } = require('./utils');

(async () => {
  const config = loadConfig();
  const profileDir = resolveFromProject(config.browserProfileDir || './browser-profile');

  const context = await chromium.launchPersistentContext(profileDir, {
    headless: false,
    acceptDownloads: true,
    viewport: { width: 1440, height: 900 }
  });

  const page = context.pages()[0] || await context.newPage();
  await page.goto(config.gmvMaxUrl, { waitUntil: 'domcontentloaded', timeout: config.navigationTimeoutMs || 60000 });

  console.log('Browser opened. Log in to TikTok Ads manually, then close the browser window.');
  await page.waitForEvent('close', { timeout: 0 }).catch(() => {});
  await context.storageState({ path: resolveFromProject(config.storageStatePath || './storage-state.json') }).catch(() => {});
  await context.close().catch(() => {});
})();
