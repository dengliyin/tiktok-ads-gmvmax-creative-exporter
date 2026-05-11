# TikTok Ads GMV Max Creative Exporter

This tool uses Playwright to export yesterday's creative performance spreadsheets for all active product GMV Max campaigns in a TikTok Ads account.

It was built for the SIMCMY GMV Max workflow:

1. Open the GMV Max dashboard URL in `config.json`.
2. Find active campaign rows.
3. Open each campaign's `Analytics` page.
4. Open `View creatives`.
5. Select yesterday.
6. Scroll to the creative table and export the spreadsheet.

## Requirements

- Node.js 20 or newer
- A TikTok Ads account that can access the configured GMV Max page
- A normal desktop browser session for login verification/captcha

## Quick Start on macOS

Download the GitHub ZIP, unzip it, then open Terminal in the project folder and run:

```bash
chmod +x start-mac.command
./start-mac.command
```

Choose:

- `1` for first login or refreshing login
- `2` for exporting yesterday's active GMV Max creative reports
- `3` for troubleshooting page structure

If macOS blocks the file, right-click `start-mac.command`, choose `Open`, then confirm.

## Quick Start on Windows

Open PowerShell in the project folder and run:

```powershell
.\start-windows.ps1
```

If PowerShell blocks scripts, run this once in the same PowerShell window:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Then run `.\start-windows.ps1` again.

## Manual Commands

Install dependencies:

```bash
npm install
npm run install-browser
```

Create local config:

```bash
cp config.example.json config.json
```

On Windows PowerShell:

```powershell
Copy-Item config.example.json config.json
```

First login:

```bash
export TIKTOK_ADS_EMAIL='your-email@example.com'
export TIKTOK_ADS_PASSWORD='your-password'
npm run assisted-login
```

On Windows PowerShell:

```powershell
$env:TIKTOK_ADS_EMAIL='your-email@example.com'
$env:TIKTOK_ADS_PASSWORD='your-password'
npm run assisted-login
```

Export yesterday's reports:

```bash
npm run export-gmvmax-creatives
```

## Configuration

Edit `config.json` after copying it from `config.example.json`.

Important fields:

- `gmvMaxUrl`: GMV Max dashboard URL.
- `downloadDir`: output folder. The default `./downloads` works on Windows and macOS.
- `browserProfileDir`: local browser profile folder. Do not commit it.
- `storageStatePath`: saved login cookies. Do not commit it.
- `maxCampaigns`: set to `0` to export all active campaigns, or a number for testing.

## Output

Files are saved under:

```text
downloads/yyyy-MM-dd/
```

For example:

```text
downloads/2026-05-10/GMVMax_creatives_2026-05-10_01_campaign_product.xlsx
```

## Troubleshooting

If TikTok Ads changes the page layout, run:

```bash
npm run inspect-page
```

It saves a screenshot and visible page structure under `inspect-output/`.

If an export fails, the script saves a screenshot under `debug-output/`.

## Security

The repository intentionally ignores these local files:

- `config.json`
- `storage-state.json`
- `browser-profile/`
- `node_modules/`
- `downloads/`
- `debug-output/`
- `inspect-output/`

Do not upload saved login state, browser profiles, or passwords to GitHub.
