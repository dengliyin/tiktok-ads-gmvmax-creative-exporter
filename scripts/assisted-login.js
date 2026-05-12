const { chromium } = require('playwright');
const { loadConfig, resolveFromProject } = require('./utils');

const TEXT = {
  gmvMax: '\u0047\u004d\u0056\u0020\u004d\u0061\u0078',
  campaignList: '\u5e7f\u544a\u8ba1\u5212\u5217\u8868',
  emailLogin: '\u90ae\u7bb1',
  password: '\u5bc6\u7801'
};

(async () => {
  const config = loadConfig();
  const email = process.env.TIKTOK_ADS_EMAIL;
  const password = process.env.TIKTOK_ADS_PASSWORD;

  if (!email || !password) {
    throw new Error('Missing TIKTOK_ADS_EMAIL or TIKTOK_ADS_PASSWORD environment variable.');
  }

  const profileDir = resolveFromProject(config.browserProfileDir || './browser-profile');
  const statePath = resolveFromProject(config.storageStatePath || './storage-state.json');
  const context = await chromium.launchPersistentContext(profileDir, {
    headless: false,
    acceptDownloads: true,
    viewport: { width: 1440, height: 900 }
  });

  const page = context.pages()[0] || await context.newPage();
  page.setDefaultTimeout(30000);
  await page.goto(config.gmvMaxUrl, { waitUntil: 'domcontentloaded', timeout: config.navigationTimeoutMs || 60000 });
  await page.waitForLoadState('networkidle').catch(() => {});

  await chooseEmailLoginIfNeeded(page);
  await fillLoginForm(page, email, password);
  await clickLoginButton(page);

  console.log('Email and password filled when matching fields were found.');
  console.log('Login button clicked. Please finish captcha/2FA if TikTok asks for it. The script will save login state after the GMV Max page is visible.');

  await waitForLoggedIn(page);
  await context.storageState({ path: statePath });
  console.log(`Login state saved: ${statePath}`);
  await context.close();
})();

async function chooseEmailLoginIfNeeded(page) {
  const choices = [
    page.getByText(TEXT.emailLogin).first(),
    page.getByText(/Email|email|邮箱|账号/).first()
  ];

  for (const choice of choices) {
    try {
      await choice.click({ timeout: 5000 });
      await page.waitForTimeout(1000);
      return;
    } catch {}
  }
}

async function fillLoginForm(page, email, password) {
  await fillFirstMatchingInput(page, [
    'input[type="email"]',
    'input[name*="email" i]',
    'input[placeholder*="email" i]',
    'input[placeholder*="\u90ae\u7bb1"]',
    'input[placeholder*="\u8d26\u53f7"]',
    'input:not([type="password"])'
  ], email);

  await fillFirstMatchingInput(page, [
    'input[type="password"]',
    'input[name*="password" i]',
    'input[placeholder*="password" i]',
    'input[placeholder*="\u5bc6\u7801"]'
  ], password);
}

async function fillFirstMatchingInput(page, selectors, value) {
  for (const selector of selectors) {
    const input = page.locator(selector).filter({ hasNotText: '' }).first();
    try {
      await input.waitFor({ state: 'visible', timeout: 5000 });
      await input.click({ clickCount: 3 });
      await input.fill(value);
      return;
    } catch {}
  }
}

async function clickLoginButton(page) {
  const candidates = [
    page.getByRole('button', { name: /^Log in$/i }).first(),
    page.getByRole('button', { name: /登录|登入|Log in/i }).first(),
    page.locator('button').filter({ hasText: /登录|登入|Log in/i }).first()
  ];

  for (const candidate of candidates) {
    try {
      await candidate.waitFor({ state: 'visible', timeout: 5000 });
      await candidate.click();
      return;
    } catch {}
  }

  throw new Error('Could not find the Log in button.');
}

async function waitForLoggedIn(page) {
  await Promise.race([
    page.getByText(TEXT.campaignList).first().waitFor({ state: 'visible', timeout: 20 * 60 * 1000 }),
    page.getByText(TEXT.gmvMax).first().waitFor({ state: 'visible', timeout: 20 * 60 * 1000 })
  ]);
}
