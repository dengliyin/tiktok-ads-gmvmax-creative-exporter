# TikTok Ads GMV Max 创意数据导出（Python）

使用 [Playwright](https://playwright.dev/python/) 在 TikTok Ads GMV Max 后台自动导航，按「活跃商品广告系列」导出**昨日**创意素材表现数据。站点导出的 Excel 会在本地转换为 JSON。

## 环境要求

- Python 3.10+（建议）
- Chromium（通过 Playwright 安装）

## 安装

```bash
cd /path/to/tiktok-ads-gmvmax-exporter-py
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

## 配置

1. 复制示例配置并编辑：

   ```bash
   cp config.example.json config.json
   ```

2. 在 `config.json` 中至少设置 **`gmvMaxUrl`**：打开浏览器进入目标店铺的 GMV Max 仪表盘，将地址栏完整 URL 粘贴进去（需包含 `aadvid`、`oec_seller_id`、`bc_id` 等参数，可参考 `config.example.json`）。

### 常用配置项

| 字段 | 说明 |
|------|------|
| `gmvMaxUrl` | GMV Max 仪表盘 URL（单账号模式必填） |
| `downloadDir` | 下载与 JSON 输出根目录，默认 `./downloads` |
| `browserProfileDir` | 持久化浏览器用户数据目录，默认 `./browser-profile` |
| `storageStatePath` | 登录态（cookies 等）保存路径，默认 `./storage-state.json` |
| `headless` | 是否无头运行，默认 `false`（建议先可视化调试） |
| `exportTimeoutMs` | 导出相关超时（毫秒） |
| `navigationTimeoutMs` | 页面导航超时（毫秒） |
| `dateFormat` | 日期文件夹命名格式，默认 `yyyy-MM-dd` |
| `maxCampaigns` | 仅处理前 N 个活跃系列；`0` 表示不限制 |

## 登录（首次必做）

通过环境变量提供邮箱和密码，验证码 / 2FA 仍需人工处理。成功后会把会话写入 `storageStatePath`（与 `config.json` 中配置一致）。

```bash
# macOS / Linux
TIKTOK_ADS_EMAIL='your@email.com' TIKTOK_ADS_PASSWORD='your-password' python assisted_login.py

# Windows PowerShell
$env:TIKTOK_ADS_EMAIL='your@email.com'
$env:TIKTOK_ADS_PASSWORD='your-password'
python assisted_login.py
```

按终端提示完成验证；成功后同样会更新登录状态文件。

## 导出数据

```bash
# 无 accounts.json：仅用 config.json 里的 gmvMaxUrl 导出单账号
python export_gmvmax_creatives.py

# 存在 accounts.json：按文件内列表批量导出
python export_gmvmax_creatives.py

# 只导出指定账号（id 与 accounts.json 中一致）
python export_gmvmax_creatives.py --account 3

# 列出已配置的账号
python export_gmvmax_creatives.py --list-accounts
```

脚本会进入「数据分析 / Analytics（或直接点击推广系列名）→ 选择昨天 → 创意素材 / View creatives → 右侧导出图标」流程（界面支持中英文文案，并兼容抽屉式 Analytics 与新版广告系列详情页）。批量模式会先导出所有 Excel 表格，全部下载完成后再统一转换为 JSON。失败时可能在 `./debug-output/` 下生成截图便于排查。

## 多账号：`accounts.json`

若项目根目录存在 `accounts.json`，导出脚本会进入批量模式：依次打开每个账号的 `url`，并把文件写到按账号分区的子目录。可先复制脱敏模板：

```bash
cp accounts.example.json accounts.json
```

每条账号记录需包含脚本使用的字段，例如：`id`、`name`、`operator`、`region`、`shop_name`、`url`（完整 GMV Max 仪表盘链接）。可参考仓库内示例结构；`url` 必须使用目标店铺实际 GMV Max 页面的完整地址，并与对应广告账号和 `oec_seller_id` 一致。

**说明：** 批量模式共用同一浏览器配置目录与 `storage-state.json`，需保证当前登录身份对这些店铺均有权限；否则会导出失败或数据不完整。

## 输出目录结构

- **单账号：** `downloads/<日期>/`，其下为本次导出的文件及同名的 `.json`（由 xlsx 转换）。
- **多账号：** `downloads/<日期>/<两位id>_<账号名>/`，结构同上。

## 安全与隐私

- 不要将真实的 `config.json`、`storage-state.json`、`browser-profile/`、含敏感 ID 的 `accounts.json` 提交到公开仓库。
- `assisted_login.py` 的密码仅通过环境变量传入，避免写进配置文件。

## 项目文件一览

| 文件 | 作用 |
|------|------|
| `export_gmvmax_creatives.py` | 主流程：导航、导出、xlsx → JSON |
| `assisted_login.py` | 邮箱密码半自动登录 |
| `utils.py` | 配置、路径、日期、表格转换等工具 |
| `config.example.json` | 配置模板 |
| `accounts.example.json` | 多账号配置模板（脱敏） |
| `requirements.txt` | Python 依赖 |

## 许可

若本目录未附带许可证文件，使用前请自行确认是否符合 TikTok 服务条款及内部合规要求。
