# TikTok Ads GMV Max Creative Exporter

Use Playwright to export yesterday's creative performance spreadsheets for active TikTok Ads Product GMV Max campaigns. Exported Excel files are saved locally and can be converted to JSON.

This repository includes two implementations:

- **Python workflow**: `export_gmvmax_creatives.py`, `assisted_login.py`, `utils.py`
- **Node.js workflow**: `scripts/`, `start-mac.command`, `start-windows.ps1`

The Python workflow supports multi-account export through `accounts.json` and is the currently maintained workflow for batch exports.

## Python Quick Start

Requirements:

- Python 3.10+ recommended
- Chromium installed by Playwright

Install:

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

Create local config:

```bash
cp config.example.json config.json
```

Edit `config.json` and set `gmvMaxUrl` to a real GMV Max dashboard URL. The URL must include the matching `aadvid`, `oec_seller_id`, and `bc_id`.

First login:

```bash
TIKTOK_ADS_EMAIL='your@email.com' TIKTOK_ADS_PASSWORD='your-password' python assisted_login.py
```

Captcha / 2FA still requires manual handling. After login, the browser profile and storage state are saved locally.

Export:

```bash
# Single-account mode, using config.json
python export_gmvmax_creatives.py

# Multi-account mode, if accounts.json exists
python export_gmvmax_creatives.py

# Export one account by id
python export_gmvmax_creatives.py --account 3

# List configured accounts
python export_gmvmax_creatives.py --list-accounts
```

The Python script follows:

```text
Analytics / campaign detail -> select yesterday -> View creatives -> right-side export icon
```

It supports both the drawer-style Analytics UI and the newer full campaign detail page. In batch mode it downloads all Excel files first, then converts the downloaded files to JSON.

## Multi-Account Config

Create a local account file from the sanitized template:

```bash
cp accounts.example.json accounts.json
```

Each record should include fields such as:

```text
id, name, operator, region, shop_name, url
```

The `url` must be copied from the actual target shop's GMV Max dashboard and must match the corresponding ad account and `oec_seller_id`.

## Node.js Workflow

Requirements:

- Node.js 20+
- A TikTok Ads account that can access the configured GMV Max page

Install:

```bash
npm install
npm run install-browser
```

First login:

```bash
export TIKTOK_ADS_EMAIL='your-email@example.com'
export TIKTOK_ADS_PASSWORD='your-password'
npm run assisted-login
```

Export:

```bash
npm run export-gmvmax-creatives
```

macOS helper:

```bash
chmod +x start-mac.command
./start-mac.command
```

Windows helper:

```powershell
.\start-windows.ps1
```

## Output

Files are saved under:

```text
downloads/yyyy-MM-dd/
```

In Python multi-account mode:

```text
downloads/yyyy-MM-dd/<account-id>_<account-name>/
```

Each `.xlsx` export is converted to a same-name `.json` file.

## Security

Do not commit local credentials, browser profiles, login state, exported data, or debug screenshots.

Ignored local files include:

- `config.json`
- `accounts.json`
- `storage-state.json`
- `browser-profile/`
- `downloads/`
- `debug-output/`
- `inspect-output/`
- `node_modules/`
- `.venv/`

## Files

| File | Purpose |
|------|---------|
| `export_gmvmax_creatives.py` | Python export workflow and xlsx-to-JSON batch flow |
| `assisted_login.py` | Python assisted login with email/password autofill |
| `utils.py` | Python config, path, date, and spreadsheet helpers |
| `scripts/export-gmvmax-creatives.js` | Node.js export workflow |
| `scripts/assisted-login.js` | Node.js assisted login |
| `scripts/inspect-page.js` | Node.js page inspection helper |
| `config.example.json` | Sanitized local config template |
| `accounts.example.json` | Sanitized multi-account template |
| `requirements.txt` | Python dependencies |
| `package.json` | Node.js dependencies and scripts |
