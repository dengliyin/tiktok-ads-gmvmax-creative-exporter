const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const {
  loadConfig,
  resolveFromProject,
  ensureDir,
  yesterday,
  formatDate,
  safeFilenamePart,
  timestampForFilename
} = require('./utils');

const TEXT = {
  active: '\u5df2\u751f\u6548',
  activeEn: 'Active',
  dataAnalysis: '\u6570\u636e\u5206\u6790',
  dataAnalysisEn: 'Analytics',
  viewCreative: '\u67e5\u770b\u521b\u610f\u7d20\u6750',
  viewCreativeEn: 'View creatives',
  creativeMaterial: '\u521b\u610f\u7d20\u6750',
  campaignList: '\u5e7f\u544a\u8ba1\u5212\u5217\u8868',
  campaignListEn: 'Campaigns',
  yesterday: '\u6628\u5929',
  yesterdayEn: 'Yesterday',
  today: '\u4eca\u5929',
  exportData: '\u5bfc\u51fa\u6570\u636e',
  exportDataEn: 'Export data',
  login: '\u767b\u5f55'
};

(async () => {
  const config = loadConfig();
  const targetDate = formatDate(yesterday(), config.dateFormat || 'yyyy-MM-dd');
  const baseDownloadDir = resolveFromProject(config.downloadDir || './downloads');
  const downloadDir = path.join(baseDownloadDir, targetDate);
  const debugDir = resolveFromProject('./debug-output');
  ensureDir(downloadDir);
  ensureDir(debugDir);

  const context = await launchContext(config);
  const page = context.pages()[0] || await context.newPage();
  page.setDefaultTimeout(30000);

  try {
    await openWithSavedState(context, page, config);
    await ensureDashboardReady(page);
    await selectActiveCampaigns(page);

    const campaigns = await collectActiveCampaigns(page);
    const limit = Number(config.maxCampaigns || 0);
    const selectedCampaigns = limit > 0 ? campaigns.slice(0, limit) : campaigns;

    if (selectedCampaigns.length === 0) {
      throw new Error('No active GMV Max campaign rows were found.');
    }

    console.log(`Found ${selectedCampaigns.length} active campaign(s).`);

    const results = [];
    for (let index = 0; index < selectedCampaigns.length; index += 1) {
      const campaign = selectedCampaigns[index];
      console.log(`Exporting ${index + 1}/${selectedCampaigns.length}: ${campaign.name}`);
      const exported = await exportOneCampaign(page, config, campaign, index, targetDate, downloadDir);
      results.push(...exported);
    }

    console.log(`Done. Exported ${results.length} file(s):`);
    for (const file of results) console.log(file);
  } catch (error) {
    const shot = path.join(debugDir, `failed_${targetDate}_${timestampForFilename()}.png`);
    await page.screenshot({ path: shot, fullPage: true }).catch(() => {});
    console.error(`Export failed: ${error.message}`);
    console.error(`Failure screenshot: ${shot}`);
    process.exitCode = 1;
  } finally {
    await context.storageState({ path: resolveFromProject(config.storageStatePath || './storage-state.json') }).catch(() => {});
    await context.close().catch(() => {});
  }
})();

async function launchContext(config) {
  const profileDir = resolveFromProject(config.browserProfileDir || './browser-profile');
  return chromium.launchPersistentContext(profileDir, {
    headless: Boolean(config.headless),
    acceptDownloads: true,
    viewport: { width: 1440, height: 900 }
  });
}

async function openWithSavedState(context, page, config) {
  const statePath = resolveFromProject(config.storageStatePath || './storage-state.json');
  if (fs.existsSync(statePath)) {
    const savedState = JSON.parse(fs.readFileSync(statePath, 'utf8'));
    if (Array.isArray(savedState.cookies) && savedState.cookies.length > 0) {
      await context.addCookies(savedState.cookies);
    }
  }

  await page.goto(config.gmvMaxUrl, { waitUntil: 'domcontentloaded', timeout: config.navigationTimeoutMs || 60000 });
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.waitForTimeout(2500);
  await dismissBlockingPopups(page);
}

async function ensureDashboardReady(page) {
  await dismissBlockingPopups(page);
  const loginInput = page.locator('input[type="password"]').first();
  if (await loginInput.isVisible().catch(() => false)) {
    throw new Error('TikTok Ads is showing a login page. Run npm run assisted-login first.');
  }

  await Promise.race([
    page.getByText(TEXT.campaignList).first().waitFor({ state: 'visible', timeout: 60000 }),
    page.getByText(TEXT.campaignListEn).first().waitFor({ state: 'visible', timeout: 60000 }),
    page.getByText(/GMV Max/i).first().waitFor({ state: 'visible', timeout: 60000 })
  ]);
  await dismissBlockingPopups(page);
}

async function dismissBlockingPopups(page) {
  const selectors = [
    page.getByRole('button', { name: /^Got it$/i }).first(),
    page.getByText(/^Got it$/i).first(),
    page.getByRole('button', { name: /^知道了$|^我知道了$/ }).first(),
    page.getByText(/^知道了$|^我知道了$/).first()
  ];

  for (let round = 0; round < 4; round += 1) {
    let clicked = false;
    for (const selector of selectors) {
      try {
        if (await selector.isVisible({ timeout: 1000 }).catch(() => false)) {
          await selector.click({ timeout: 3000 });
          await page.waitForTimeout(800);
          clicked = true;
        }
      } catch {}
    }

    const clickedByDom = await page.evaluate(() => {
      const isVisible = (el) => {
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
      };
      const candidates = Array.from(document.querySelectorAll('button,[role="button"],div,span,a')).filter(isVisible);
      const target = candidates.find((el) => /^(Got it|知道了|我知道了)$/i.test(el.innerText.trim()));
      if (!target) return false;
      target.click();
      return true;
    }).catch(() => false);

    if (clickedByDom) {
      clicked = true;
      await page.waitForTimeout(800);
    }

    await page.keyboard.press('Escape').catch(() => {});
    if (!clicked) break;
  }
}

async function selectActiveCampaigns(page) {
  const activeFilter = page.getByText(TEXT.active, { exact: true }).first();
  if (await activeFilter.isVisible().catch(() => false)) return;

  const dropdownCandidates = [
    page.locator('button').filter({ hasText: /全部|状态|All|Status/ }).first(),
    page.locator('[role="button"]').filter({ hasText: /全部|状态|All|Status/ }).first()
  ];

  for (const candidate of dropdownCandidates) {
    try {
      await candidate.click({ timeout: 5000 });
      await page.getByText(TEXT.active, { exact: true }).first().click({ timeout: 5000 });
      await page.waitForLoadState('networkidle').catch(() => {});
      await page.waitForTimeout(1500);
      return;
    } catch {}
  }
}

async function collectActiveCampaigns(page) {
  await scrollCampaignListIntoView(page);
  await dismissBlockingPopups(page);
  await page.waitForTimeout(1500);

  return page.evaluate((text) => {
    const isVisible = (el) => {
      const style = window.getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
    };

    return Array.from(document.querySelectorAll('tr')).filter(isVisible).map((row, rowIndex) => {
      const rowText = row.innerText.replace(/\s+/g, ' ').trim();
      const buttons = Array.from(row.querySelectorAll('button,[role="button"],a')).filter(isVisible);
      const hasDataAnalysis = buttons.some((button) => button.innerText.includes(text.dataAnalysis) || button.innerText.includes(text.dataAnalysisEn));
      const isActive = rowText.includes(text.active) || rowText.includes(text.activeEn);
      const cells = Array.from(row.querySelectorAll('td')).map((cell) => cell.innerText.replace(/\s+/g, ' ').trim());
      const name = cells.find((cell) => cell && !cell.includes(text.active) && !cell.includes(text.activeEn) && !cell.includes(text.dataAnalysis) && !cell.includes(text.dataAnalysisEn)) || rowText.split(text.active)[0] || `campaign-${rowIndex + 1}`;
      return { rowIndex, name, rowText, hasDataAnalysis, isActive };
    }).filter((row) => row.hasDataAnalysis && row.isActive);
  }, TEXT);
}

async function scrollCampaignListIntoView(page) {
  await dismissBlockingPopups(page);
  const labels = [
    TEXT.campaignList,
    'Ad campaign list',
    'Campaign list',
    'Product GMV Max',
    '\u5546\u54c1\u0020GMV Max'
  ];

  for (const label of labels) {
    const target = page.getByText(label).first();
    if (await target.isVisible().catch(() => false)) {
      await target.scrollIntoViewIfNeeded();
      await page.waitForTimeout(1000);
      return;
    }
  }

  await page.evaluate(() => window.scrollTo({ top: document.body.scrollHeight, behavior: 'instant' }));
  await page.waitForTimeout(2000);
}

async function exportOneCampaign(page, config, campaign, campaignIndex, targetDate, downloadDir) {
  await page.goto(config.gmvMaxUrl, { waitUntil: 'domcontentloaded', timeout: config.navigationTimeoutMs || 60000 });
  await page.waitForLoadState('networkidle').catch(() => {});
  await dismissBlockingPopups(page);
  await selectActiveCampaigns(page);
  await scrollCampaignListIntoView(page);

  const dataButtons = page.locator('tr').filter({ hasText: campaign.name }).getByText(new RegExp(`^(${TEXT.dataAnalysis}|${TEXT.dataAnalysisEn})$`, 'i'));
  let dataButton = dataButtons.first();
  if (!(await dataButton.isVisible().catch(() => false))) {
    dataButton = page.getByText(new RegExp(`^(${TEXT.dataAnalysis}|${TEXT.dataAnalysisEn})$`, 'i')).nth(campaignIndex);
  }

  await dataButton.scrollIntoViewIfNeeded();
  await dataButton.click();
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.waitForTimeout(2500);
  await dismissBlockingPopups(page);

  await page.getByText(new RegExp(`^(${TEXT.viewCreative}|${TEXT.viewCreativeEn})$`, 'i')).first().scrollIntoViewIfNeeded();
  const creativeButtons = await getVisibleTextCount(page, TEXT.viewCreative);
  if (creativeButtons === 0) {
    const englishCreativeButtons = await getVisibleTextCount(page, TEXT.viewCreativeEn);
    if (englishCreativeButtons === 0) {
      throw new Error(`No "${TEXT.viewCreative}" button found for campaign: ${campaign.name}`);
    }
  }

  const totalCreativeButtons = Math.max(
    await getVisibleTextCount(page, TEXT.viewCreative),
    await getVisibleTextCount(page, TEXT.viewCreativeEn)
  );
  const exported = [];
  for (let productIndex = 0; productIndex < totalCreativeButtons; productIndex += 1) {
    const productLabel = await getCreativeProductLabel(page, productIndex);
    await page.getByText(new RegExp(`^(${TEXT.viewCreative}|${TEXT.viewCreativeEn})$`, 'i')).nth(productIndex).click();
    await page.waitForLoadState('networkidle').catch(() => {});
    await page.waitForTimeout(2000);
    await dismissBlockingPopups(page);

    await setCreativeDateToYesterday(page, targetDate);
    await scrollToCreativeExportArea(page);
    const filePath = await clickExportAndSave(page, {
      config,
      downloadDir,
      campaignIndex,
      productIndex,
      campaignName: campaign.name,
      productLabel,
      targetDate
    });
    exported.push(filePath);

    if (productIndex + 1 < totalCreativeButtons) {
      await closeCreativeTabOrReturnToProduct(page);
    }
  }

  return exported;
}

async function getVisibleTextCount(page, text) {
  return page.evaluate((targetText) => {
    const isVisible = (el) => {
      const style = window.getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
    };
    return Array.from(document.querySelectorAll('button,[role="button"],a')).filter((el) => isVisible(el) && el.innerText.trim().toLowerCase() === targetText.toLowerCase()).length;
  }, text);
}

async function getCreativeProductLabel(page, productIndex) {
  return page.evaluate(({ viewCreative, viewCreativeEn, productIndex }) => {
    const isVisible = (el) => {
      const style = window.getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
    };
    const buttons = Array.from(document.querySelectorAll('button,[role="button"],a')).filter((el) => {
      const label = el.innerText.trim().toLowerCase();
      return isVisible(el) && (label === viewCreative.toLowerCase() || label === viewCreativeEn.toLowerCase());
    });
    const button = buttons[productIndex];
    const row = button ? button.closest('tr') : null;
    return row ? row.innerText.replace(/\s+/g, ' ').trim().slice(0, 80) : `product-${productIndex + 1}`;
  }, { viewCreative: TEXT.viewCreative, viewCreativeEn: TEXT.viewCreativeEn, productIndex });
}

async function setCreativeDateToYesterday(page, targetDate) {
  const dateInput = await findDateInput(page);
  await dateInput.scrollIntoViewIfNeeded();
  await dateInput.click({ force: true });
  await page.waitForTimeout(500);

  const yesterdayButton = page.getByText(new RegExp(`^(${TEXT.yesterday}|${TEXT.yesterdayEn})$`, 'i')).first();
  if (await yesterdayButton.isVisible().catch(() => false)) {
    await yesterdayButton.click();
  } else {
    await dateInput.click({ clickCount: 3, force: true });
    await page.keyboard.press('Control+A');
    await page.keyboard.type(`${targetDate} - ${targetDate}`);
    await page.keyboard.press('Enter').catch(() => {});
  }

  await forceSetDateInput(page, targetDate);
  await page.mouse.click(20, 20).catch(() => {});
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.waitForTimeout(1800);
}

async function findDateInput(page) {
  const candidates = [
    page.locator('input').filter({ hasText: /202\d/ }).first(),
    page.locator('input[value*="202"]').first(),
    page.locator('input[placeholder*="YYYY"],input[placeholder*="yyyy"]').first(),
    page.locator('input').first()
  ];

  for (const candidate of candidates) {
    try {
      await candidate.waitFor({ state: 'visible', timeout: 5000 });
      const value = await candidate.inputValue().catch(() => '');
      if (/202\d|YYYY|yyyy|-\s*/.test(value) || candidate === candidates[candidates.length - 1]) return candidate;
    } catch {}
  }

  throw new Error('Could not find a date range input.');
}

async function forceSetDateInput(page, targetDate) {
  await page.evaluate((dateText) => {
    const isVisible = (el) => {
      const style = window.getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
    };
    const inputs = Array.from(document.querySelectorAll('input')).filter((input) => isVisible(input) && /202\d/.test(input.value));
    const input = inputs[0];
    if (!input) return;
    const descriptor = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
    descriptor.set.call(input, `${dateText} - ${dateText}`);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    input.dispatchEvent(new Event('blur', { bubbles: true }));
  }, targetDate);
}

async function scrollToCreativeExportArea(page) {
  const labels = [
    TEXT.creativeMaterial,
    TEXT.viewCreativeEn,
    'Creatives',
    'Product and creatives reporting',
    '\u5546\u54c1\u548c\u521b\u610f\u7d20\u6750\u62a5\u544a'
  ];

  for (const label of labels) {
    const target = page.getByText(label).first();
    if (await target.isVisible().catch(() => false)) {
      await target.scrollIntoViewIfNeeded();
      await page.waitForTimeout(800);
      break;
    }
  }

  await page.evaluate(() => {
    const exportLike = Array.from(document.querySelectorAll('button,[role="button"],a')).find((el) => {
      const style = window.getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      const visible = style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
      return visible && /导出|Export/i.test(`${el.innerText} ${el.title} ${el.getAttribute('aria-label')}`);
    });

    if (exportLike) {
      exportLike.scrollIntoView({ block: 'center', inline: 'center' });
      return;
    }

    window.scrollTo({ top: document.body.scrollHeight, behavior: 'instant' });
  });
  await page.waitForTimeout(1000);
}

async function clickExportAndSave(page, options) {
  const download = await Promise.all([
    page.waitForEvent('download', { timeout: options.config.exportTimeoutMs || 120000 }),
    clickExportButton(page)
  ]).then(([download]) => download);

  const suggested = download.suggestedFilename();
  const ext = path.extname(suggested) || '.xlsx';
  const filename = [
    'GMVMax_creatives',
    options.targetDate,
    String(options.campaignIndex + 1).padStart(2, '0'),
    safeFilenamePart(options.campaignName),
    safeFilenamePart(options.productLabel)
  ].filter(Boolean).join('_');

  let finalPath = path.join(options.downloadDir, `${filename}${ext}`);
  if (fs.existsSync(finalPath)) {
    finalPath = path.join(options.downloadDir, `${filename}_${timestampForFilename()}${ext}`);
  }

  await download.saveAs(finalPath);
  return finalPath;
}

async function clickExportButton(page) {
  const candidates = [
    page.getByText(new RegExp(`^(${TEXT.exportData}|${TEXT.exportDataEn})$`, 'i')).first(),
    page.locator('[title*="\u5bfc\u51fa"],[aria-label*="\u5bfc\u51fa"]').first(),
    page.locator('button').filter({ hasText: /导出|Export/i }).first()
  ];

  for (const candidate of candidates) {
    try {
      await candidate.waitFor({ state: 'visible', timeout: 5000 });
      await candidate.click();
      return;
    } catch {}
  }

  const clicked = await page.evaluate(() => {
    const isVisible = (el) => {
      const style = window.getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
    };
    const buttons = Array.from(document.querySelectorAll('button,[role="button"]')).filter(isVisible);
    const exportLike = buttons.find((button) => /导出|Export/i.test(`${button.innerText} ${button.title} ${button.getAttribute('aria-label')}`));
    const squareIconButtons = buttons.filter((button) => {
      const rect = button.getBoundingClientRect();
      return rect.width <= 48 && rect.height <= 48 && rect.top > window.innerHeight * 0.45;
    });
    const target = exportLike || squareIconButtons[squareIconButtons.length - 1];
    if (!target) return false;
    target.click();
    return true;
  });

  if (!clicked) throw new Error('Could not find the export button on the creative material table.');
}

async function closeCreativeTabOrReturnToProduct(page) {
  const closeButton = page.locator('button,[role="button"]').filter({ hasText: 'x' }).first();
  if (await closeButton.isVisible().catch(() => false)) {
    await closeButton.click();
    await page.waitForTimeout(1000);
    return;
  }

  await page.getByText('\u5546\u54c1', { exact: true }).first().click().catch(() => {});
  await page.waitForTimeout(1000);
}
