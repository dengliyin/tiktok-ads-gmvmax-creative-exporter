"""
TikTok Ads GMV Max Creative Exporter — Python 版
===================================================
用 Playwright 自动登录 TikTok Ads GMV Max 后台，遍历所有活跃商品广告系列，
导出每个产品下的昨日素材表现数据，输出为 JSON 文件。

依赖:
    pip install playwright openpyxl
    playwright install chromium

使用:
    # 首次先登录
    python assisted_login.py

    # 批量导出所有账号（如果 accounts.json 存在）
    python export_gmvmax_creatives.py

    # 只导出指定账号
    python export_gmvmax_creatives.py --account 3

    # 列出所有账号
    python export_gmvmax_creatives.py --list-accounts
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import (
    BrowserContext,
    Download,
    Page,
    async_playwright,
)

from utils import (
    ensure_dir,
    format_date,
    load_config,
    resolve_from_project,
    safe_filename_part,
    save_json_output,
    timestamp_for_filename,
    xlsx_to_json,
    yesterday,
)

# ── 界面文字（中英双语） ──────────────────────────────────────────────────

TEXT = {
    "active": "已生效",
    "activeEn": "Active",
    "dataAnalysis": "数据分析",
    "dataAnalysisEn": "Analytics",
    "viewCreative": "查看创意素材",
    "viewCreativeEn": "View creatives",
    "creativeMaterial": "创意素材",
    "campaignList": "广告计划列表",
    "campaignListEn": "Campaign list",
    "yesterday": "昨天",
    "yesterdayEn": "Yesterday",
    "today": "今天",
    "exportData": "导出数据",
    "exportDataEn": "Export data",
    "login": "登录",
}


# ── 账号加载 ─────────────────────────────────────────────────────────────


def load_accounts() -> list[dict[str, Any]]:
    """加载 accounts.json 中的账号列表。"""
    acct_path = resolve_from_project("./accounts.json")
    if not acct_path.exists():
        return []
    return json.loads(acct_path.read_text(encoding="utf-8"))


def list_accounts(accounts: list[dict]) -> None:
    """打印账号列表。"""
    print(f"共 {len(accounts)} 个账号:")
    for a in accounts:
        print(f"  [{a['id']:02d}] {a['name']:20s} ({a['region']}) 运营: {a['operator']} 店铺: {a['shop_name']}")
    print()


# ── 主入口 ────────────────────────────────────────────────────────────────


async def main() -> None:
    parser = argparse.ArgumentParser(description="TikTok Ads GMV Max Creative Exporter")
    parser.add_argument(
        "--account", type=int, default=None,
        help="只导出指定 ID 的账号（使用 --list-accounts 查看 ID）",
    )
    parser.add_argument(
        "--list-accounts", action="store_true",
        help="列出 accounts.json 中的所有账号并退出",
    )
    args = parser.parse_args()

    accounts = load_accounts()

    if args.list_accounts and accounts:
        list_accounts(accounts)
        return

    if not accounts:
        await run_single_account()
    elif args.account is not None:
        account = next((a for a in accounts if a["id"] == args.account), None)
        if not account:
            print(f"错误: 未找到 ID 为 {args.account} 的账号。使用 --list-accounts 查看所有账号。")
            sys.exit(1)
        await run_account_batch([account])
    else:
        print(f"批量导出模式: {len(accounts)} 个账号")
        await run_account_batch(accounts)


# ── 单账号模式（兼容旧版 config.json） ──────────────────────────────────


async def run_single_account() -> None:
    """使用 config.json 中的 gmvMaxUrl 导出单个账号。"""
    config = load_config()
    target_date = format_date(yesterday(), config.get("dateFormat", "yyyy-MM-dd"))
    base_download_dir = resolve_from_project(config.get("downloadDir", "./downloads"))
    download_dir = base_download_dir / target_date
    debug_dir = resolve_from_project("./debug-output")
    ensure_dir(download_dir)
    ensure_dir(debug_dir)

    async with async_playwright() as p:
        context = await _launch_context(config, p)
        page = context.pages[0] if context.pages else await context.new_page()
        page.set_default_timeout(30000)

        try:
            await _open_with_saved_state(context, page, config)
            await _ensure_dashboard_ready(page)
            await _select_active_campaigns(page)

            campaigns = await _collect_active_campaigns(page)
            limit = int(config.get("maxCampaigns", 0))
            selected = campaigns[:limit] if limit > 0 else campaigns

            if not selected:
                raise RuntimeError("未找到活跃的 GMV Max 广告系列。")

            print(f"找到 {len(selected)} 个活跃广告系列。")

            results: list[Path] = []
            for idx, campaign in enumerate(selected):
                print(f"导出中 {idx + 1}/{len(selected)}: {campaign['name']}")
                exported = await _export_one_campaign(
                    page, config, campaign, idx, target_date, download_dir
                )
                results.extend(exported)

            results = _convert_exported_files(results)

            print(f"\n完成！共导出 {len(results)} 个文件:")
            for f in results:
                size = f.stat().st_size
                size_str = f"{size / 1024:.1f} KB" if size > 1024 else f"{size} B"
                print(f"  {f.name:50s} {size_str:>8s}")

        except Exception as exc:
            shot = debug_dir / f"failed_{target_date}_{timestamp_for_filename()}.png"
            try:
                await page.screenshot(path=str(shot), full_page=True)
            except Exception:
                pass
            print(f"[FAIL] 导出失败: {exc}", file=sys.stderr)
            if shot.exists():
                print(f"[FAIL] 失败截图: {shot}", file=sys.stderr)
            sys.exit(1)

        finally:
            state_path = resolve_from_project(
                config.get("storageStatePath", "./storage-state.json")
            )
            try:
                await context.storage_state(path=str(state_path))
            except Exception:
                pass
            await context.close()


# ── 批量导出 ──────────────────────────────────────────────────────────────


async def run_account_batch(accounts: list[dict]) -> None:
    """批量导出多个账号。

    逐个遍历 accounts 列表，每个账号使用独立的 URL 和下载子目录。
    单个账号失败不会中断整体流程。
    """
    config = load_config()
    target_date = format_date(yesterday(), config.get("dateFormat", "yyyy-MM-dd"))
    base_download_dir = resolve_from_project(config.get("downloadDir", "./downloads"))
    debug_dir = resolve_from_project("./debug-output")
    ensure_dir(debug_dir)

    async with async_playwright() as p:
        context = await _launch_context(config, p)
        page = context.pages[0] if context.pages else await context.new_page()
        page.set_default_timeout(30000)

        all_results: list[tuple[dict, list[Path]]] = []

        for acct in accounts:
            print(f"\n{'='*60}")
            print(f"[{acct['id']:02d}/{len(accounts)}] {acct['name']}")
            print(f"  运营: {acct['operator']} | 地区: {acct['region']} | 店铺: {acct['shop_name']}")
            print(f"{'='*60}")

            acct_config = dict(config)
            acct_config["gmvMaxUrl"] = acct["url"]

            acct_dir = (
                base_download_dir
                / target_date
                / f"{acct['id']:02d}_{safe_filename_part(acct['name'], max_length=30)}"
            )
            ensure_dir(acct_dir)

            try:
                await _open_with_saved_state(context, page, acct_config)
                await _ensure_dashboard_ready(page)
                await _select_active_campaigns(page)

                campaigns = await _collect_active_campaigns(page)
                limit = int(config.get("maxCampaigns", 0))
                selected = campaigns[:limit] if limit > 0 else campaigns

                if not selected:
                    print(f"  [WARN] 未找到活跃广告系列，跳过。")
                    await _print_campaign_detection_debug(page)
                    shot = debug_dir / f"{acct['id']:02d}_{safe_filename_part(acct['name'])}_{target_date}_no_active.png"
                    try:
                        await page.screenshot(path=str(shot), full_page=True)
                        print(f"  [WARN] 诊断截图已保存: {shot}")
                    except Exception:
                        pass
                    all_results.append((acct, []))
                    continue

                print(f"  找到 {len(selected)} 个活跃广告系列。")

                results: list[Path] = []
                for idx, campaign in enumerate(selected):
                    print(f"  导出中 {idx + 1}/{len(selected)}: {campaign['name']}")
                    try:
                        exported = await _export_one_campaign(
                            page, acct_config, campaign, idx, target_date, acct_dir
                        )
                        results.extend(exported)
                    except Exception as exc:
                        print(f"  [WARN] 广告系列导出失败，继续下一个: {campaign['name']} - {exc}")
                        shot = debug_dir / (
                            f"{acct['id']:02d}_{safe_filename_part(acct['name'])}_"
                            f"{target_date}_campaign_{idx + 1}.png"
                        )
                        try:
                            await page.screenshot(path=str(shot), full_page=True)
                            print(f"  [WARN] 系列诊断截图已保存: {shot}")
                        except Exception:
                            pass

                print(f"  [OK] 导出 {len(results)} 个文件")
                all_results.append((acct, results))

            except Exception as exc:
                print(f"  [FAIL] 账号导出失败: {exc}")
                shot = debug_dir / f"{acct['id']:02d}_{safe_filename_part(acct['name'])}_{target_date}.png"
                try:
                    await page.screenshot(path=str(shot), full_page=True)
                    print(f"  [FAIL] 截图已保存: {shot}")
                except Exception:
                    pass
                all_results.append((acct, []))

        all_results = [
            (acct, _convert_exported_files(results) if results else [])
            for acct, results in all_results
        ]

        # 汇总
        total_files = sum(len(r) for _, r in all_results)
        success_count = sum(1 for _, r in all_results if r)
        failed_count = len(accounts) - success_count

        print(f"\n{'='*60}")
        print(f"批量导出完成!")
        print(f"  账号: {success_count}/{len(accounts)} 成功" + (f", {failed_count} 失败" if failed_count else ""))
        print(f"  文件: {total_files} 个")
        print(f"{'='*60}")
        for acct, results in all_results:
            status = f"{len(results)} 文件" if results else "失败"
            tag = "OK" if results else "FAIL"
            print(f"  [{tag}] [{acct['id']:02d}] {acct['name']:20s} → {status}")
        print(f"{'='*60}")

        # 保存登录状态
        state_path = resolve_from_project(
            config.get("storageStatePath", "./storage-state.json")
        )
        try:
            await context.storage_state(path=str(state_path))
        except Exception:
            pass
        await context.close()


# ── 启动 & 登录恢复 ───────────────────────────────────────────────────────


async def _launch_context(config: dict, playwright) -> BrowserContext:
    """启动持久浏览器上下文。"""
    profile_dir = resolve_from_project(
        config.get("browserProfileDir", "./browser-profile")
    )
    return await playwright.chromium.launch_persistent_context(
        str(profile_dir),
        headless=bool(config.get("headless", False)),
        accept_downloads=True,
        viewport={"width": 1440, "height": 900},
    )


async def _open_with_saved_state(
    context: BrowserContext, page: Page, config: dict
) -> None:
    """加载已保存的 cookies 并导航到 GMV Max 页面。"""
    state_path = resolve_from_project(
        config.get("storageStatePath", "./storage-state.json")
    )
    if state_path.exists():
        saved_state = json.loads(state_path.read_text(encoding="utf-8"))
        cookies = saved_state.get("cookies", [])
        if cookies:
            await context.add_cookies(cookies)

    await _goto_dashboard(page, config)
    await page.wait_for_timeout(2500)
    await _dismiss_blocking_popups(page)


async def _goto_dashboard(page: Page, config: dict) -> None:
    """导航到 GMV Max 页面；账号切换时 TikTok 偶发 ERR_ABORTED，给页面一次恢复机会。"""
    try:
        await page.goto(
            config["gmvMaxUrl"],
            wait_until="domcontentloaded",
            timeout=config.get("navigationTimeoutMs", 60000),
        )
    except Exception as exc:
        msg = str(exc)
        if "ERR_ABORTED" not in msg and "Timeout" not in msg:
            raise
        await page.wait_for_timeout(5000)
        if "gmv-max" not in page.url:
            await page.goto(
                config["gmvMaxUrl"],
                wait_until="commit",
                timeout=config.get("navigationTimeoutMs", 60000),
            )
        await page.wait_for_timeout(3000)


async def _ensure_dashboard_ready(page: Page) -> None:
    """确保已登录且仪表盘已加载。"""
    await _dismiss_blocking_popups(page)

    # 检测是否停留在登录页
    login_input = page.locator('input[type="password"]').first
    try:
        if await login_input.is_visible():
            raise RuntimeError(
                "TikTok Ads 显示的是登录页。请先运行: python assisted_login.py"
            )
    except RuntimeError:
        raise
    except Exception:
        pass

    # 等待仪表盘加载完成
    ready_text = re.compile(
        rf"^({re.escape(TEXT['campaignList'])}|{re.escape(TEXT['campaignListEn'])}|Product GMV Max|商品 GMV Max)$",
        re.IGNORECASE,
    )
    await page.get_by_text(ready_text).first.wait_for(state="visible", timeout=60000)
    await _dismiss_blocking_popups(page)


# ── 弹窗驱逐 ──────────────────────────────────────────────────────────────


async def _dismiss_blocking_popups(page: Page) -> None:
    """多层循环尝试关闭所有阻塞弹窗。"""
    known_selectors = [
        page.get_by_role("button", name=re.compile(r"^Got it$", re.IGNORECASE)).first,
        page.get_by_text(re.compile(r"^Got it$", re.IGNORECASE)).first,
        page.get_by_role("button", name=re.compile(r"^知道了$|^我知道了$")).first,
        page.get_by_text(re.compile(r"^知道了$|^我知道了$")).first,
    ]

    for _round in range(4):
        clicked = False

        # 尝试已知选择器
        for sel in known_selectors:
            try:
                if await sel.is_visible(timeout=1000):
                    await sel.click(timeout=3000)
                    await page.wait_for_timeout(800)
                    clicked = True
            except Exception:
                pass

        # DOM 回退：遍历所有可见元素找匹配文本
        if not clicked:
            dom_clicked = await page.evaluate("""() => {
                const isVisible = (el) => {
                    const s = window.getComputedStyle(el);
                    const r = el.getBoundingClientRect();
                    return s.visibility !== 'hidden' && s.display !== 'none'
                        && r.width > 0 && r.height > 0;
                };
                const candidates = Array.from(
                    document.querySelectorAll('button,[role="button"],div,span,a')
                ).filter(isVisible);
                const target = candidates.find(el =>
                    /^(Got it|知道了|我知道了)$/i.test(el.innerText.trim())
                );
                if (!target) return false;
                target.click();
                return true;
            }""")
            if dom_clicked:
                clicked = True
                await page.wait_for_timeout(800)

        if not await _wait_campaign_analytics_drawer(page, timeout=500):
            await page.keyboard.press("Escape")
        if not clicked:
            break


# ── 广告系列筛选 ───────────────────────────────────────────────────────────


async def _select_active_campaigns(page: Page) -> None:
    """将表格上方 Status 筛选切换为 Active。"""
    await _scroll_campaign_list_into_view(page)
    already_active = await page.evaluate(
        """() => {
            const visible = (el) => {
                const s = window.getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none'
                    && r.width > 0 && r.height > 0;
            };
            return Array.from(document.querySelectorAll('button,[role="button"],div'))
                .filter(visible)
                .some((el) => {
                    const r = el.getBoundingClientRect();
                    const text = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                    return r.top > window.innerHeight * 0.45
                        && r.top < window.innerHeight - 120
                        && r.width >= 120
                        && r.width <= 260
                        && /^Active$/i.test(text);
                });
        }"""
    )
    if already_active:
        return

    status_box = await page.evaluate(
        """() => {
            const isVisible = (el) => {
                const s = window.getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none'
                    && r.width > 0 && r.height > 0;
            };
            const labels = Array.from(
                document.querySelectorAll('input,button,[role="button"],div,span')
            ).filter((el) => {
                if (!isVisible(el) || el.closest('tr')) return false;
                const text = (
                    el.innerText
                    || el.value
                    || el.placeholder
                    || el.getAttribute('aria-label')
                    || ''
                ).replace(/\\s+/g, ' ').trim();
                return /^(Status|状态|All|全部)$/i.test(text);
            });
            const boxes = labels.map((label) => {
                let el = label;
                while (el.parentElement && !el.parentElement.closest('tr')) {
                    const r = el.getBoundingClientRect();
                    if (r.width >= 120 && r.height >= 32) break;
                    el = el.parentElement;
                }
                const r = el.getBoundingClientRect();
                return {
                    x: r.left + r.width / 2,
                    y: r.top + r.height / 2,
                    top: r.top,
                    width: r.width,
                    height: r.height,
                };
            }).filter((box) => box.width >= 120 && box.height >= 32);
            boxes.sort((a, b) => b.top - a.top);
            return boxes[0] || null;
        }"""
    )
    if not status_box:
        raise RuntimeError("未找到表格上方的 Status 筛选框。")

    await page.mouse.click(status_box["x"], status_box["y"])
    await page.wait_for_timeout(600)
    await _click_active_status_option(page)
    await page.wait_for_timeout(1500)


async def _click_active_status_option(page: Page) -> None:
    """点击 Status 下拉菜单中的 Active 选项，避免误点表格行里的 Active。"""
    active_box = await page.evaluate(
        """(labels) => {
            const isVisible = (el) => {
                const s = window.getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none'
                    && r.width > 0 && r.height > 0;
            };
            const wanted = new Set(labels.map((label) => label.toLowerCase()));
            const candidates = Array.from(
                document.querySelectorAll('[role="option"],[role="menuitem"],li,div,span,button')
            ).filter(isVisible);
            const matches = candidates.filter((el) => {
                const text = (el.innerText || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                return wanted.has(text) && !el.closest('tr');
            });
            const boxes = matches.map((match) => {
                let el = match;
                while (el.parentElement && !el.parentElement.closest('tr')) {
                    const r = el.getBoundingClientRect();
                    if (r.width >= 80 && r.height >= 28) break;
                    el = el.parentElement;
                }
                const r = el.getBoundingClientRect();
                return {
                    x: r.left + r.width / 2,
                    y: r.top + r.height / 2,
                    top: r.top,
                    width: r.width,
                    height: r.height,
                };
            }).filter((box) => box.width >= 80 && box.height >= 24);
            boxes.sort((a, b) => a.top - b.top);
            return boxes[0] || null;
        }""",
        [TEXT["active"], TEXT["activeEn"]],
    )
    if not active_box:
        raise RuntimeError("未能在 Status 筛选中选择 Active。")
    await page.mouse.click(active_box["x"], active_box["y"])


# ── 广告系列收集 ───────────────────────────────────────────────────────────


async def _collect_active_campaigns(page: Page) -> list[dict[str, Any]]:
    """收集所有活跃的 GMV Max 广告系列行。"""
    await _scroll_campaign_list_into_view(page)
    await _dismiss_blocking_popups(page)
    await page.wait_for_timeout(1500)

    rows = await page.evaluate(
        """(text) => {
            const isVisible = (el) => {
                const s = window.getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none'
                    && r.width > 0 && r.height > 0;
            };

            return Array.from(document.querySelectorAll('tr'))
                .filter(isVisible)
                .map((row, rowIndex) => {
                    const rowText = row.innerText.replace(/\\s+/g, ' ').trim();
                    const isActive = rowText.includes(text.active)
                        || rowText.includes(text.activeEn);
                    const cells = Array.from(row.querySelectorAll('td'))
                        .map(c => c.innerText.replace(/\\s+/g, ' ').trim());
                    const preferredName = cells[1] && !cells[1].includes(text.active)
                        && !cells[1].includes(text.activeEn)
                        ? cells[1]
                        : '';
                    const name = preferredName || cells.find(c =>
                        c && !c.includes(text.active)
                        && !c.includes(text.activeEn)
                        && !c.includes(text.dataAnalysis)
                        && !c.includes(text.dataAnalysisEn)
                    ) || rowText.split(text.active)[0] || `campaign-${rowIndex + 1}`;
                    return { rowIndex, name, rowText, isActive };
                })
                .filter(row => row.isActive);
        }""",
        {
            "dataAnalysis": TEXT["dataAnalysis"],
            "dataAnalysisEn": TEXT["dataAnalysisEn"],
            "active": TEXT["active"],
            "activeEn": TEXT["activeEn"],
        },
    )
    return rows


async def _print_campaign_detection_debug(page: Page) -> None:
    """打印当前页面表格识别线索，用于排查真实活跃系列未被识别的问题。"""
    debug_rows = await page.evaluate(
        """() => {
            const isVisible = (el) => {
                const s = window.getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none'
                    && r.width > 0 && r.height > 0;
            };
            return Array.from(document.querySelectorAll('tr'))
                .filter(isVisible)
                .slice(0, 12)
                .map((row, index) => {
                    const buttons = Array.from(
                        row.querySelectorAll('button,[role="button"],a')
                    )
                        .filter(isVisible)
                        .map((b) => b.innerText.replace(/\\s+/g, ' ').trim())
                        .filter(Boolean);
                    return {
                        index,
                        text: row.innerText.replace(/\\s+/g, ' ').trim().slice(0, 300),
                        buttons,
                    };
                });
        }"""
    )
    print("  [DEBUG] 当前可见表格行:")
    for row in debug_rows:
        print(f"    row {row['index']}: {row['text']}")
        if row["buttons"]:
            print(f"      buttons: {', '.join(row['buttons'])}")


async def _scroll_campaign_list_into_view(page: Page) -> None:
    """滚动页面使广告系列列表可见。"""
    await _dismiss_blocking_popups(page)

    labels = [
        TEXT["campaignList"],
        "Ad campaign list",
        "Campaign list",
        "Product GMV Max",
        "商品 GMV Max",
    ]
    for label in labels:
        try:
            target = page.get_by_text(label).first
            if await target.is_visible():
                await target.scroll_into_view_if_needed()
                await page.wait_for_timeout(1000)
                return
        except Exception:
            pass

    await page.evaluate(
        "window.scrollTo({ top: document.body.scrollHeight, behavior: 'instant' })"
    )
    await page.wait_for_timeout(2000)


# ── 单个广告系列导出 ─────────────────────────────────────────────────────


async def _export_one_campaign(
    page: Page,
    config: dict,
    campaign: dict[str, Any],
    campaign_index: int,
    target_date: str,
    download_dir: Path,
) -> list[Path]:
    """导出一个广告系列下所有产品的素材数据。

    Returns:
        导出的文件路径列表。
    """
    # 回到 GMV Max 仪表盘
    await _goto_dashboard(page, config)
    await page.wait_for_timeout(1500)
    await _dismiss_blocking_popups(page)
    await _select_active_campaigns(page)
    await _scroll_campaign_list_into_view(page)
    await _scroll_campaign_table_to_action_column(page)

    # 点击该广告系列的"数据分析/Analytics"入口。不同账号里它可能在右侧操作列，
    # 也可能作为广告系列名称下方的小链接出现。
    await _click_campaign_analytics(page, campaign["name"], campaign_index)
    await page.wait_for_timeout(2500)
    await _scroll_campaign_analytics_drawer_down(page)

    # 本土店铺的素材报表日期在 Analytics 抽屉内设置，然后再进商品素材详情。
    await _set_creative_date_to_yesterday(page, target_date)
    await _scroll_campaign_analytics_drawer_down(page)

    # 进入每个产品的"查看创意素材/View creatives"。有些账号里这是产品名下方的
    # Web Component 小链接，不是普通 button。
    await _scroll_to_creative_export_area(page)
    total_creative_buttons = await _get_creative_entry_count(page)

    if total_creative_buttons == 0:
        await _print_creative_entry_debug(page)
        raise RuntimeError(
            f"广告系列 '{campaign['name']}' 下未找到 '查看创意素材' 按钮。"
        )

    exported: list[Path] = []
    for product_index in range(total_creative_buttons):
        product_label = await _get_creative_product_label(page, product_index)

        # 点击对应产品的"查看创意素材"
        await _click_creative_entry(page, product_index)
        await page.wait_for_timeout(2000)

        # 滚动到导出区域
        await _scroll_to_creative_export_area(page)

        # 导出并保存（xlsx → JSON）
        file_path = await _click_export_and_save(
            page,
            config=config,
            download_dir=download_dir,
            campaign_index=campaign_index,
            product_index=product_index,
            campaign_name=campaign["name"],
            product_label=product_label,
            target_date=target_date,
        )
        exported.append(file_path)

        # 返回产品列表（如果不是最后一个）
        if product_index + 1 < total_creative_buttons:
            await _close_creative_tab_or_return(page)

    return exported


async def _click_campaign_analytics(
    page: Page, campaign_name: str, campaign_index: int
) -> None:
    """点击指定广告系列行里的 Analytics/数据分析入口。"""
    # 本土店铺里 Analytics 通常在推广系列名称左下方，而不是右侧 Action 列。
    clicked = await page.evaluate(
        """(args) => {
            const visible = (el) => {
                const s = window.getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none'
                    && r.width > 0 && r.height > 0;
            };
            const rows = Array.from(document.querySelectorAll('tr')).filter(visible);
            const row = rows.find((tr) =>
                (tr.innerText || '').replace(/\\s+/g, ' ').includes(args.campaignName)
            ) || rows[args.campaignIndex];
            if (!row) return false;

            const nameEl = Array.from(row.querySelectorAll('td,div,span,a'))
                .filter(visible)
                .map((el) => {
                    const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                    const r = el.getBoundingClientRect();
                    return { el, text, r, area: r.width * r.height };
                })
                .filter((item) => item.text.includes(args.campaignName))
                .sort((a, b) => a.area - b.area)[0]?.el;
            const nameCell = nameEl?.closest('td') || nameEl || row;

            const action = Array.from(nameCell.querySelectorAll('button,[role="button"],a,span,div'))
                .filter(visible)
                .find((el) => /^(Analytics|数据分析)$/i.test((el.innerText || el.textContent || '').trim()));
            if (!action) return false;
            action.scrollIntoView({ block: 'center', inline: 'center' });
            action.click();
            return true;
        }""",
        {"campaignName": campaign_name, "campaignIndex": campaign_index},
    )
    if clicked:
        await page.wait_for_timeout(1500)
        if await _wait_campaign_analytics_drawer(page):
            return

    try:
        name_text = page.get_by_text(campaign_name, exact=True).first
        await name_text.scroll_into_view_if_needed(timeout=5000)
        box = await name_text.bounding_box(timeout=5000)
        if box:
            for dx in [16, 36, 60, 88]:
                await page.mouse.move(box["x"] + min(box["width"] / 2, 80), box["y"] + box["height"] / 2)
                await page.wait_for_timeout(500)
                await page.mouse.click(box["x"] + dx, box["y"] + box["height"] + 16)
                await page.wait_for_timeout(1200)
                if await _wait_campaign_analytics_drawer(page):
                    return
    except Exception:
        pass

    row_box = await page.evaluate(
        """(args) => {
            const isVisible = (el) => {
                const s = window.getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none'
                    && r.width > 0 && r.height > 0;
            };
            const nameCandidates = Array.from(
                document.querySelectorAll('td,span,div,a')
            )
                .filter(isVisible)
                .map((el) => {
                    const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                    const r = el.getBoundingClientRect();
                    return { el, text, r, area: r.width * r.height };
                })
                .filter((item) =>
                    item.r.width > 0 && item.r.height > 0
                    && item.text === args.campaignName
                )
                .sort((a, b) => a.area - b.area);
            const nameCell = nameCandidates[0] ? nameCandidates[0].el : null;
            if (!nameCell) return null;
            const r = nameCell.getBoundingClientRect();
            return {
                x: r.left + Math.min(80, Math.max(20, r.width / 3)),
                y: r.top + Math.min(24, r.height / 2),
                nameLeft: r.left,
                nameTop: r.top,
                nameBottom: r.bottom,
                analyticsX: r.left + Math.min(80, Math.max(20, r.width / 3)),
                analyticsY: r.bottom + 16,
            };
        }""",
        {"campaignName": campaign_name, "campaignIndex": campaign_index},
    )
    if row_box:
        for dx, dy in [(30, 18), (55, 18), (85, 18), (30, 30), (70, 30), (110, 30)]:
            if await _wait_campaign_analytics_drawer(page, timeout=1000):
                return
            await page.mouse.move(row_box["x"], row_box["y"])
            await page.wait_for_timeout(500)
            await page.mouse.click(row_box["nameLeft"] + dx, row_box["nameBottom"] + dy)
            await page.wait_for_timeout(1200)
            opened = await _wait_campaign_analytics_drawer(page, timeout=1500)
            if opened:
                return

    clicked_action = await page.evaluate(
        """(args) => {
            const visible = (el) => {
                const s = window.getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none'
                    && r.width > 0 && r.height > 0;
            };
            const scrollers = Array.from(document.querySelectorAll('div'))
                .filter((el) => el.scrollWidth > el.clientWidth + 80);
            for (const el of scrollers) {
                if (/Campaign name|广告系列|广告计划|Status|Active/.test(el.innerText || '')) {
                    el.scrollLeft = el.scrollWidth;
                }
            }
            const rows = Array.from(document.querySelectorAll('tr')).filter(visible);
            const row = rows.find((tr) =>
                (tr.innerText || '').replace(/\\s+/g, ' ').includes(args.campaignName)
            ) || rows[args.campaignIndex];
            if (!row) return false;
            const action = Array.from(row.querySelectorAll('button,[role="button"],a'))
                .filter(visible)
                .find((el) => /^(Analytics|数据分析)$/i.test((el.innerText || '').trim()));
            if (!action) return false;
            action.scrollIntoView({ block: 'center', inline: 'center' });
            action.click();
            return true;
        }""",
        {"campaignName": campaign_name, "campaignIndex": campaign_index},
    )
    if clicked_action:
        await page.wait_for_timeout(1500)
        if await _wait_campaign_analytics_drawer(page):
            return

    try:
        campaign_link = page.get_by_text(campaign_name, exact=True).first
        await campaign_link.scroll_into_view_if_needed(timeout=5000)
        await campaign_link.click(timeout=5000)
        await page.wait_for_timeout(1800)
        if await _wait_campaign_analytics_drawer(page, timeout=3000):
            return
    except Exception:
        pass

    if await _wait_campaign_analytics_drawer(page, timeout=1000):
        return

    raise RuntimeError(f"未找到广告系列 '{campaign_name}' 的 Analytics 入口。")


async def _wait_campaign_analytics_drawer(page: Page, timeout: int = 5000) -> bool:
    """等待 Campaign analytics 抽屉或新版广告系列详情页可见。"""
    title_re = re.compile(
        r"^(Campaign analytics|广告计划数据分析|Product and creatives reporting|商品和创意素材报告)$",
        re.IGNORECASE,
    )
    try:
        await page.get_by_text(title_re).first.wait_for(state="visible", timeout=timeout)
        return True
    except Exception:
        return await _is_campaign_analytics_drawer_open(page)


async def _is_campaign_analytics_drawer_open(page: Page) -> bool:
    """判断右侧 Campaign analytics 抽屉或新版详情页是否已经打开。"""
    return bool(await page.evaluate("""() => {
        const visible = (el) => {
            const s = window.getComputedStyle(el);
            const r = el.getBoundingClientRect();
            return s.visibility !== 'hidden' && s.display !== 'none'
                && r.width > 0 && r.height > 0;
        };
        const pageDetailVisible = Array.from(document.querySelectorAll('div,span,h1,h2,h3,p,th'))
            .filter(visible)
            .some((el) => {
                const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                return /^(Product and creatives reporting|商品和创意素材报告|Product name|商品名称)$/.test(text)
                    || /Product and creatives reporting|商品和创意素材报告/.test(text);
            });
        if (pageDetailVisible) return true;

        const titleVisible = Array.from(document.querySelectorAll('div,span,h1,h2,h3'))
            .filter(visible)
            .some((el) => {
                const r = el.getBoundingClientRect();
                const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                return r.left > 250
                    && r.top < 180
                    && /^(Campaign analytics|广告计划数据分析)$/.test(text);
            });
        if (titleVisible) return true;

        return Array.from(document.querySelectorAll('div'))
            .filter(visible)
            .some((el) => {
                const r = el.getBoundingClientRect();
                const text = el.innerText || '';
                return r.left > 250 && r.left < 500 && r.width > window.innerWidth * 0.35
                    && r.height > window.innerHeight * 0.55
                    && /Campaign analytics|广告计划数据分析|Product and creatives reporting|商品和创意素材报告/.test(text);
            });
    }"""))


async def _scroll_campaign_table_to_action_column(page: Page) -> None:
    """横向滚动广告系列表格，优先保留系列名列以点击名称左下方的 Analytics。"""
    await page.evaluate(
        """() => {
            const scrollables = Array.from(document.querySelectorAll('div'))
                .filter((el) => el.scrollWidth > el.clientWidth + 80);
            for (const el of scrollables) {
                const text = el.innerText || '';
                if (/Campaign name|广告系列|广告计划|Status|Active/.test(text)) {
                    el.scrollLeft = 0;
                }
            }
            window.scrollTo({ left: 0, top: window.scrollY, behavior: 'instant' });
        }"""
    )
    await page.wait_for_timeout(800)


async def _scroll_campaign_analytics_drawer_down(page: Page) -> None:
    """进入 Analytics 抽屉后，向下滚到商品和创意素材报告区域。"""
    await page.evaluate("""() => {
        const isVisible = (el) => {
            const s = window.getComputedStyle(el);
            const r = el.getBoundingClientRect();
            return s.visibility !== 'hidden' && s.display !== 'none'
                && r.width > 0 && r.height > 0;
        };
        const scrollables = [document.scrollingElement, document.body, document.documentElement]
            .concat(Array.from(document.querySelectorAll('div')))
            .filter((el) => isVisible(el) && el.scrollHeight > el.clientHeight + 80);
        const drawer = scrollables
            .filter((el) => {
                const r = el.getBoundingClientRect();
                return r.left > 250
                    && r.width > window.innerWidth * 0.45
                    && /Campaign analytics|广告计划数据分析/.test(el.innerText || '');
            })
            .sort((a, b) => b.clientHeight - a.clientHeight)[0];
        if (drawer) {
            const directEntry = Array.from(drawer.querySelectorAll('button,[role="button"],a,span,div'))
                .filter(isVisible)
                .find((node) => /^(View creatives|查看创意素材)$/i.test((node.innerText || '').trim()));
            if (directEntry) {
                directEntry.scrollIntoView({ block: 'center', inline: 'nearest' });
                return;
            }
            const target = Array.from(drawer.querySelectorAll('div,span,h1,h2,h3,p,td'))
                .filter(isVisible)
                .find((node) =>
                    /Product and creatives reporting|商品和创意素材报告|Product name|商品名称/.test(node.innerText || '')
                );
            if (target) {
                target.scrollIntoView({ block: 'start', inline: 'nearest' });
                drawer.scrollTop = Math.min(drawer.scrollHeight, drawer.scrollTop + drawer.clientHeight * 0.75);
                return;
            }
            drawer.scrollTop = Math.min(drawer.scrollHeight, drawer.scrollTop + drawer.clientHeight * 0.9);
            return;
        }
        for (const el of scrollables) {
            const text = el.innerText || '';
            if (/Campaign analytics|广告计划数据分析|Product and creatives reporting|商品和创意素材报告/.test(text)) {
                const target = Array.from(el.querySelectorAll('div,span,h1,h2,h3,p,td'))
                    .filter(isVisible)
                    .find((node) =>
                        /Product and creatives reporting|商品和创意素材报告|Product name|商品名称/.test(node.innerText || '')
                    );
                if (target) {
                    target.scrollIntoView({ block: 'start', inline: 'nearest' });
                    el.scrollTop = Math.min(el.scrollHeight, el.scrollTop + 360);
                } else {
                    el.scrollTop = Math.max(el.scrollTop, el.scrollHeight * 0.75);
                }
            }
        }
    }""")
    await page.mouse.move(1200, 820)
    for _ in range(2):
        await page.mouse.wheel(0, 850)
        await page.wait_for_timeout(200)
    await page.wait_for_timeout(1000)


# ── 辅助: 可见元素计数 ──────────────────────────────────────────────────


async def _get_visible_text_count(page: Page, text: str) -> int:
    """统计页面上可见的指定文本元素数量。"""
    return await page.evaluate(
        """(targetText) => {
            const isVisible = (el) => {
                const s = window.getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none'
                    && r.width > 0 && r.height > 0;
            };
            return Array.from(
                document.querySelectorAll('button,[role="button"],a')
            ).filter(el =>
                isVisible(el)
                && el.innerText.trim().toLowerCase() === targetText.toLowerCase()
            ).length;
        }""",
        text,
    )


async def _get_creative_entry_count(page: Page) -> int:
    """统计当前产品列表里可点击的 View creatives/查看创意素材入口数。"""
    entry_re = re.compile(r"^(View creatives|查看创意素材)$", re.IGNORECASE)
    try:
        count = await page.get_by_text(entry_re).count()
        if count:
            return count
    except Exception:
        pass
    entries = await _get_creative_entry_boxes(page)
    return len(entries)


async def _click_creative_entry(page: Page, product_index: int) -> None:
    """按坐标点击第 N 个 View creatives/查看创意素材入口。"""
    entry_re = re.compile(r"^(View creatives|查看创意素材)$", re.IGNORECASE)
    try:
        entry = page.get_by_text(entry_re).nth(product_index)
        await entry.scroll_into_view_if_needed(timeout=8000)
        await entry.click(timeout=8000)
        return
    except Exception:
        pass

    entries = await _get_creative_entry_boxes(page)
    if product_index >= len(entries):
        raise RuntimeError(f"未找到第 {product_index + 1} 个 View creatives 入口。")
    box = entries[product_index]
    await page.mouse.click(box["x"], box["y"])


async def _get_creative_entry_boxes(page: Page) -> list[dict[str, float]]:
    """获取所有可见 View creatives/查看创意素材入口的坐标，去重后按纵向排序。"""
    return await page.evaluate(
        """(labels) => {
            const wanted = new Set(labels.map((label) => label.toLowerCase()));
            const isVisible = (el) => {
                const s = window.getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none'
                    && r.width > 0 && r.height > 0;
            };
            const drawerRoot = Array.from(document.querySelectorAll('div'))
                .filter(isVisible)
                .filter((el) => {
                    const r = el.getBoundingClientRect();
                    return r.left > 250
                        && r.width > window.innerWidth * 0.45
                        && r.height > window.innerHeight * 0.55
                        && /Campaign analytics|广告计划数据分析|Product and creatives reporting|商品和创意素材报告/.test(el.innerText || '');
                })
                .sort((a, b) => (b.getBoundingClientRect().width * b.getBoundingClientRect().height)
                    - (a.getBoundingClientRect().width * a.getBoundingClientRect().height))[0];
            const root = drawerRoot || document;
            const allElements = [];
            const walk = (root) => {
                for (const el of Array.from(root.querySelectorAll('*'))) {
                    allElements.push(el);
                    if (el.shadowRoot) walk(el.shadowRoot);
                }
            };
            walk(root);
            const candidates = allElements.filter((el) => {
                const text = (el.innerText || el.textContent || '')
                    .replace(/\\s+/g, ' ')
                    .trim()
                    .toLowerCase();
                return text.length <= 80
                    && Array.from(wanted).some((label) => text.includes(label));
            });
            const boxes = [];
            for (const el of candidates) {
                let target = el.closest && el.closest('button,[role="button"],a');
                if (!target && el.getRootNode && el.getRootNode().host) {
                    target = el.getRootNode().host;
                }
                target = target || el;
                const r = target.getBoundingClientRect();
                const er = el.getBoundingClientRect();
                const box = r.width > 0 && r.height > 0 ? r : er;
                if (box.width <= 0 || box.height <= 0) continue;
                boxes.push({
                    x: box.left + box.width / 2,
                    y: box.top + box.height / 2,
                    top: box.top,
                    left: box.left,
                });
            }
            boxes.sort((a, b) => a.top - b.top || a.left - b.left);
            const deduped = [];
            for (const box of boxes) {
                if (!deduped.some((prev) =>
                    Math.abs(prev.x - box.x) < 8 && Math.abs(prev.y - box.y) < 8
                )) {
                    deduped.push(box);
                }
            }
            if (deduped.length) return deduped;

            const productBlocks = allElements
                .filter((el) => {
                    if (!isVisible(el)) return false;
                    const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                    return /View creatives|查看创意素材|Creatives|创意素材/i.test(text)
                        && /Product|商品|SKU|Available|可用|USD|ROI/i.test(text);
                })
                .map((el) => el.getBoundingClientRect())
                .filter((r) => r.width > 240 && r.height > 45);
            productBlocks.sort((a, b) => a.top - b.top || a.left - b.left);
            const productBoxes = [];
            for (const r of productBlocks) {
                const box = {
                    x: r.left + Math.min(140, r.width * 0.18),
                    y: r.top + Math.min(58, r.height - 10),
                    top: r.top,
                    left: r.left,
                };
                if (!productBoxes.some((prev) =>
                    Math.abs(prev.x - box.x) < 12 && Math.abs(prev.y - box.y) < 12
                )) {
                    productBoxes.push(box);
                }
            }
            if (productBoxes.length) return productBoxes;

            const rows = allElements
                .filter((el) => {
                    if (!isVisible(el)) return false;
                    const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                    return /Available|可用/.test(text) && /Product|商品|USD|ROI/.test(text);
                })
                .map((row) => row.getBoundingClientRect())
                .filter((r) => r.width > 300 && r.height > 40);
            rows.sort((a, b) => a.top - b.top || a.left - b.left);
            return rows.map((r) => ({
                x: r.left + Math.min(150, r.width * 0.18),
                y: r.top + Math.min(52, r.height - 12),
                top: r.top,
                left: r.left,
            }));
        }""",
        [TEXT["viewCreative"], TEXT["viewCreativeEn"]],
    )


async def _print_creative_entry_debug(page: Page) -> None:
    """打印产品创意入口附近的可见文本，用于适配不同账号 UI。"""
    texts = await page.evaluate(
        """() => {
            const isVisible = (el) => {
                const s = window.getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none'
                    && r.width > 0 && r.height > 0;
            };
            return Array.from(
                document.querySelectorAll('button,[role="button"],a,span,div,slot,td')
            )
                .filter(isVisible)
                .map((el) => ({
                    tag: el.tagName.toLowerCase(),
                    role: el.getAttribute('role') || '',
                    text: (el.innerText || el.textContent || '')
                        .replace(/\\s+/g, ' ')
                        .trim()
                        .slice(0, 160),
                }))
                .filter((item) => item.text && /creative|素材|view|analytics/i.test(item.text))
                .slice(0, 40);
        }"""
    )
    print("  [DEBUG] 创意入口候选文本:")
    for item in texts:
        print(f"    <{item['tag']} role='{item['role']}'> {item['text']}")


async def _get_creative_product_label(page: Page, product_index: int) -> str:
    """获取第 N 个产品素材按钮对应的产品标签。"""
    entry_re = re.compile(r"^(View creatives|查看创意素材)$", re.IGNORECASE)
    try:
        entry = page.get_by_text(entry_re).nth(product_index)
        await entry.scroll_into_view_if_needed(timeout=5000)
        label = await entry.evaluate("""(el) => {
            const row = el.closest('tr');
            if (row) return row.innerText.replace(/\\s+/g, ' ').trim().slice(0, 80);
            let parent = el.parentElement;
            for (let i = 0; parent && i < 5; i += 1, parent = parent.parentElement) {
                const text = (parent.innerText || '').replace(/\\s+/g, ' ').trim();
                if (/Product|商品|Available|可用|Target ROI|SKU/.test(text)) {
                    return text.slice(0, 80);
                }
            }
            return '';
        }""")
        if label:
            return label
    except Exception:
        pass

    return await page.evaluate(
        """(args) => {
            const isVisible = (el) => {
                const s = window.getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none'
                    && r.width > 0 && r.height > 0;
            };
            const buttons = Array.from(
                document.querySelectorAll('button,[role="button"],a,span,div,slot')
            ).filter(el => {
                const label = (el.innerText || el.textContent || '').trim().toLowerCase();
                return (isVisible(el) || (el.getRootNode && el.getRootNode().host))
                    && label.length <= 80
                    && (label.includes(args.viewCreative.toLowerCase())
                        || label.includes(args.viewCreativeEn.toLowerCase()));
            });
            const button = buttons[args.productIndex];
            const row = button ? button.closest('tr') : null;
            return row
                ? row.innerText.replace(/\\s+/g, ' ').trim().slice(0, 80)
                : `product-${args.productIndex + 1}`;
        }""",
        {
            "viewCreative": TEXT["viewCreative"],
            "viewCreativeEn": TEXT["viewCreativeEn"],
            "productIndex": product_index,
        },
    )


# ── 日期操作 ──────────────────────────────────────────────────────────────


async def _set_creative_date_to_yesterday(
    page: Page, target_date: str
) -> None:
    """将素材页面的日期筛选设为昨天。"""
    date_box = await _find_date_input_box(page)
    if date_box:
        await page.mouse.click(date_box["x"], date_box["y"])
    else:
        date_input = await _find_date_input(page)
        await date_input.scroll_into_view_if_needed()
        await date_input.click(force=True)
    await page.wait_for_timeout(500)

    # 尝试点击"昨天"快捷按钮
    yesterday_btn = page.get_by_text(
        re.compile(
            f"^({TEXT['yesterday']}|{TEXT['yesterdayEn']})$", re.IGNORECASE
        )
    ).first
    try:
        if await yesterday_btn.is_visible():
            await yesterday_btn.click()
        else:
            raise Exception("not visible")
    except Exception:
        # 键盘输入
        if date_box:
            await page.mouse.click(date_box["x"], date_box["y"], click_count=3)
        else:
            await date_input.click(click_count=3, force=True)
        await page.keyboard.press("Control+A")
        await page.keyboard.type(f"{target_date} - {target_date}")
        await page.keyboard.press("Enter")

    # DOM 注入兜底
    await _force_set_date_input(page, target_date)
    await page.wait_for_timeout(1800)


async def _find_date_input_box(page: Page) -> dict[str, float] | None:
    """优先返回 Analytics 抽屉内日期输入框的点击坐标。"""
    return await page.evaluate("""() => {
        const isVisible = (el) => {
            const s = window.getComputedStyle(el);
            const r = el.getBoundingClientRect();
            return s.visibility !== 'hidden' && s.display !== 'none'
                && r.width > 0 && r.height > 0;
        };
        const drawer = Array.from(document.querySelectorAll('div'))
            .filter(isVisible)
            .filter((el) => {
                const r = el.getBoundingClientRect();
                return r.left > 250
                    && r.width > window.innerWidth * 0.45
                    && r.height > window.innerHeight * 0.55
                    && /Campaign analytics|广告计划数据分析/.test(el.innerText || '');
            })
            .sort((a, b) => (b.getBoundingClientRect().width * b.getBoundingClientRect().height)
                - (a.getBoundingClientRect().width * a.getBoundingClientRect().height))[0];
        const root = drawer || document;
        const inputs = Array.from(root.querySelectorAll('input'))
            .filter(isVisible)
            .map((input) => {
                const r = input.getBoundingClientRect();
                return {
                    x: r.left + r.width / 2,
                    y: r.top + r.height / 2,
                    top: r.top,
                    value: input.value || input.placeholder || '',
                };
            })
            .filter((item) => /202\\d|YYYY|yyyy/i.test(item.value));
        if (!inputs.length && !drawer) {
            const fallback = Array.from(document.querySelectorAll('input'))
                .filter(isVisible)
                .map((input) => {
                    const r = input.getBoundingClientRect();
                    return {
                        x: r.left + r.width / 2,
                        y: r.top + r.height / 2,
                        top: r.top,
                        value: input.value || input.placeholder || '',
                    };
                })
                .filter((item) => item.x > window.innerWidth * 0.45 && /202\\d|YYYY|yyyy/i.test(item.value));
            fallback.sort((a, b) => b.top - a.top);
            return fallback[0] || null;
        }
        inputs.sort((a, b) => b.top - a.top);
        return inputs[0] || null;
    }""")


async def _find_date_input(page: Page):
    """找到日期输入框（多策略降级）。"""
    candidates = [
        page.locator("input").filter(has_text=re.compile(r"202\d")).first,
        page.locator('input[value*="202"]').first,
        page.locator('input[placeholder*="YYYY"],input[placeholder*="yyyy"]').first,
        page.locator("input").first,
    ]

    for i, candidate in enumerate(candidates):
        try:
            await candidate.wait_for(state="visible", timeout=5000)
            value = await candidate.input_value()
            if re.search(r"202\d|YYYY|yyyy", value) or i == len(candidates) - 1:
                return candidate
        except Exception:
            pass

    raise RuntimeError("找不到日期输入框。")


async def _force_set_date_input(page: Page, target_date: str) -> None:
    """通过 DOM 注入强制设置日期。"""
    await page.evaluate(
        """(dateText) => {
            const isVisible = (el) => {
                const s = window.getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none'
                    && r.width > 0 && r.height > 0;
            };
            const drawer = Array.from(document.querySelectorAll('div'))
                .filter(isVisible)
                .filter((el) => {
                    const r = el.getBoundingClientRect();
                    return r.left > 250
                        && r.width > window.innerWidth * 0.45
                        && r.height > window.innerHeight * 0.55
                        && /Campaign analytics|广告计划数据分析|Product and creatives reporting|商品和创意素材报告/.test(el.innerText || '');
                })
                .sort((a, b) => (b.getBoundingClientRect().width * b.getBoundingClientRect().height)
                    - (a.getBoundingClientRect().width * a.getBoundingClientRect().height))[0];
            const root = drawer || document;
            const inputs = Array.from(root.querySelectorAll('input'))
                .filter(input => isVisible(input) && /202\d/.test(input.value))
                .sort((a, b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top);
            const input = inputs[0];
            if (!input) return;
            const descriptor = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value'
            );
            descriptor.set.call(input, `${dateText} - ${dateText}`);
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
            input.dispatchEvent(new Event('blur', { bubbles: true }));
        }""",
        target_date,
    )


# ── 滚动到导出区域 ────────────────────────────────────────────────────────


async def _scroll_to_creative_export_area(page: Page) -> None:
    """滚动页面使导出按钮可见。"""
    for _attempt in range(6):
        visible_entry = await page.evaluate("""() => {
            const isVisible = (el) => {
                const s = window.getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none'
                    && r.width > 0 && r.height > 0
                    && r.bottom > 0 && r.top < window.innerHeight;
            };
            return Array.from(document.querySelectorAll('button,[role="button"],a,span,div'))
                .filter(isVisible)
                .some((el) => /^(View creatives|查看创意素材)$/i.test((el.innerText || el.textContent || '').trim()));
        }""")
        if visible_entry:
            return

        scrolled_drawer = await page.evaluate("""() => {
        const isVisible = (el) => {
            const s = window.getComputedStyle(el);
            const r = el.getBoundingClientRect();
            return s.visibility !== 'hidden' && s.display !== 'none'
                && r.width > 0 && r.height > 0;
        };
        const scrollables = Array.from(document.querySelectorAll('div'))
            .filter((el) => isVisible(el) && el.scrollHeight > el.clientHeight + 80);
        const drawer = scrollables
            .filter((el) => {
                const r = el.getBoundingClientRect();
                return r.left > 250
                    && r.width > window.innerWidth * 0.45
                    && /Campaign analytics|广告计划数据分析|Product and creatives reporting|商品和创意素材报告/.test(el.innerText || '');
            })
            .sort((a, b) => b.clientHeight - a.clientHeight)[0];
        if (!drawer) return false;
        const entry = Array.from(drawer.querySelectorAll('button,[role="button"],a,span,div'))
            .filter(isVisible)
            .find((el) => /^(View creatives|查看创意素材)$/i.test((el.innerText || '').trim()));
        if (entry) {
            entry.scrollIntoView({ block: 'center', inline: 'nearest' });
            return true;
        }
        const target = Array.from(drawer.querySelectorAll('div,span,h1,h2,h3,p,td'))
            .filter(isVisible)
            .find((el) =>
                /Product and creatives reporting|商品和创意素材报告|Product name|商品名称/.test(el.innerText || '')
            );
        if (target) {
            target.scrollIntoView({ block: 'center', inline: 'nearest' });
            drawer.scrollTop = Math.min(drawer.scrollHeight, drawer.scrollTop + drawer.clientHeight * 0.75);
        } else {
            drawer.scrollTop = drawer.scrollHeight * 0.7;
        }
        return true;
    }""")
        if scrolled_drawer:
            await page.mouse.move(1200, 820)
            await page.mouse.wheel(0, 650)
            await page.wait_for_timeout(600)
            continue
        break

    labels = [
        TEXT["creativeMaterial"],
        TEXT["viewCreativeEn"],
        "Creatives",
        "Product and creatives reporting",
        "商品和创意素材报告",
    ]
    for label in labels:
        try:
            target = page.get_by_text(label).first
            if await target.is_visible():
                await target.scroll_into_view_if_needed()
                await page.wait_for_timeout(800)
                break
        except Exception:
            pass
    else:
        # DOM 回退：找导出按钮并滚动到它
        found = await page.evaluate("""() => {
            const isVisible = (el) => {
                const s = window.getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none'
                    && r.width > 0 && r.height > 0;
            };
            const exportLike = Array.from(
                document.querySelectorAll('button,[role="button"],a')
            ).find(el => {
                const visible = isVisible(el);
                const text = el.innerText + ' ' + el.title + ' ' + (el.getAttribute('aria-label') || '');
                return visible && /导出|Export/i.test(text);
            });
            if (exportLike) {
                exportLike.scrollIntoView({ block: 'center', inline: 'center' });
                return true;
            }
            window.scrollTo({ top: document.body.scrollHeight, behavior: 'instant' });
            return false;
        }""")

    await page.wait_for_timeout(1000)


# ── 导出 & 保存 ──────────────────────────────────────────────────────────


async def _click_export_and_save(
    page: Page,
    config: dict,
    download_dir: Path,
    campaign_index: int,
    product_index: int,
    campaign_name: str,
    product_label: str,
    target_date: str,
) -> Path:
    """点击导出按钮并保存 xlsx。

    Returns:
        xlsx 文件路径。JSON 转换会在全部下载完成后统一执行。
    """
    # 触发下载
    download = await _click_export_button(page, min(config.get("exportTimeoutMs", 120000), 45000))

    # 构建文件名
    suggested = download.suggested_filename
    ext = Path(suggested).suffix if Path(suggested).suffix else ".xlsx"
    name_parts = [
        "GMVMax_creatives",
        target_date,
        f"{campaign_index + 1:02d}",
        safe_filename_part(campaign_name),
        safe_filename_part(product_label),
    ]
    filename = "_".join(p for p in name_parts if p) + ext
    xlsx_path = download_dir / filename

    # 文件名冲突处理
    if xlsx_path.exists():
        xlsx_path = download_dir / f"{xlsx_path.stem}_{timestamp_for_filename()}{ext}"

    await download.save_as(str(xlsx_path))
    print(f"  └─ 已下载: {xlsx_path.name}")
    return xlsx_path

async def _click_export_button(page: Page, timeout: int = 120000) -> Download:
    """点击导出按钮（多策略降级）。"""
    export_re = re.compile(
        f"^({TEXT['exportData']}|{TEXT['exportDataEn']})$", re.IGNORECASE
    )

    await _scroll_export_toolbar_into_view(page)

    # 策略 1: get_by_text
    candidate = page.get_by_text(export_re).first
    try:
        await candidate.wait_for(state="visible", timeout=5000)
        async with page.expect_download(timeout=timeout) as info:
            await candidate.click()
        return await info.value
    except Exception:
        pass

    # 策略 2: title / aria-label
    candidate2 = page.locator('[title*="导出"],[aria-label*="导出"]').first
    try:
        await candidate2.wait_for(state="visible", timeout=5000)
        async with page.expect_download(timeout=timeout) as info:
            await candidate2.click()
        return await info.value
    except Exception:
        pass

    # 策略 3: button with text
    candidate3 = page.locator("button").filter(has_text=re.compile(r"导出|Export", re.IGNORECASE)).first
    try:
        await candidate3.wait_for(state="visible", timeout=5000)
        async with page.expect_download(timeout=timeout) as info:
            await candidate3.click()
        return await info.value
    except Exception:
        pass

    # 策略 4: DOM 定位坐标后用真实鼠标点击
    async with page.expect_download(timeout=timeout) as info:
        point = await page.evaluate("""() => {
            const isVisible = (el) => {
                const s = window.getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none'
                    && r.width > 0 && r.height > 0;
            };
            const drawer = Array.from(document.querySelectorAll('div'))
                .filter(isVisible)
                .filter((el) => {
                    const r = el.getBoundingClientRect();
                    return r.left > 250
                        && r.width > window.innerWidth * 0.45
                        && r.height > window.innerHeight * 0.55
                        && /Campaign analytics|广告计划数据分析/.test(el.innerText || '');
                })
                .sort((a, b) => (b.getBoundingClientRect().width * b.getBoundingClientRect().height)
                    - (a.getBoundingClientRect().width * a.getBoundingClientRect().height))[0];
            const root = drawer || document;
            const drawerBox = drawer ? drawer.getBoundingClientRect() : {
                left: 0, right: window.innerWidth, top: 0, bottom: window.innerHeight,
            };
            const exportToolbarPoint = (() => {
                const markers = Array.from(root.querySelectorAll('div,span,a'))
                    .filter(isVisible)
                    .map((el) => {
                        const r = el.getBoundingClientRect();
                        return {
                            el,
                            r,
                            area: r.width * r.height,
                            text: (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim(),
                        };
                    })
                    .filter((item) => /Understanding your data|了解你的数据/i.test(item.text))
                    .sort((a, b) => a.area - b.area);
                const marker = markers[0];
                if (!marker) return null;
                let rootEl = marker.el;
                for (let i = 0; rootEl.parentElement && i < 8; i += 1) {
                    const r = rootEl.getBoundingClientRect();
                    if (r.width > 500 && r.height > 90) break;
                    rootEl = rootEl.parentElement;
                }
                const rr = rootEl.getBoundingClientRect();
                return {
                    x: Math.min(rr.right - 18, window.innerWidth - 24),
                    y: marker.r.top + marker.r.height / 2,
                };
            })();
            if (exportToolbarPoint) return exportToolbarPoint;

            const understanding = Array.from(root.querySelectorAll('div,span,a'))
                .filter(isVisible)
                .map((el) => {
                    const r = el.getBoundingClientRect();
                    return {
                        el,
                        r,
                        area: r.width * r.height,
                        text: (el.innerText || '').replace(/\\s+/g, ' ').trim(),
                    };
                })
                .filter((item) => /Understanding your data|了解你的数据/i.test(item.text))
                .sort((a, b) => a.area - b.area)[0];
            const buttons = Array.from(root.querySelectorAll('button,[role="button"],a,div,span'))
                .filter(isVisible)
                .filter((b) => {
                    const r = b.getBoundingClientRect();
                    return r.left >= drawerBox.left && r.right <= drawerBox.right + 4
                        && r.top >= drawerBox.top && r.bottom <= drawerBox.bottom + 4;
                });
            const exportLike = buttons.find(b =>
                /导出|Export/i.test(
                    b.innerText + ' ' + b.title + ' ' + (b.getAttribute('aria-label') || '')
                )
            );
            const sameRowButtons = understanding ? buttons.filter((b) => {
                const r = b.getBoundingClientRect();
                const text = (b.innerText || '').replace(/\\s+/g, ' ').trim();
                return r.left > understanding.r.left
                    && Math.abs((r.top + r.height / 2) - (understanding.r.top + understanding.r.height / 2)) < 35
                    && r.width >= 24
                    && r.width <= 52
                    && r.height >= 24
                    && r.height <= 52
                    && text.length <= 4;
            }) : [];
            sameRowButtons.sort((a, b) => b.getBoundingClientRect().right - a.getBoundingClientRect().right);
            const searchInputs = Array.from(root.querySelectorAll('input'))
                .filter(isVisible)
                .map((el) => el.getBoundingClientRect())
                .filter((r) => r.width > 200 && r.top > drawerBox.top + drawerBox.height * 0.35)
                .sort((a, b) => a.top - b.top);
            const firstSearch = searchInputs[0] || null;
            const toolbarButtons = firstSearch ? buttons.filter((b) => {
                const r = b.getBoundingClientRect();
                const text = (b.innerText || '').replace(/\\s+/g, ' ').trim();
                return r.left > firstSearch.right
                    && Math.abs((r.top + r.height / 2) - (firstSearch.top + firstSearch.height / 2)) < 45
                    && r.width >= 24
                    && r.width <= 60
                    && r.height >= 24
                    && r.height <= 60
                    && text.length <= 8;
            }) : [];
            toolbarButtons.sort((a, b) => b.getBoundingClientRect().right - a.getBoundingClientRect().right);
            const smallButtons = buttons.filter(b => {
                const r = b.getBoundingClientRect();
                const text = (b.innerText || '').replace(/\\s+/g, ' ').trim();
                return !text
                    && r.width <= 52
                    && r.height <= 52
                    && r.top > drawerBox.top + drawerBox.height * 0.62
                    && r.left > drawerBox.left + drawerBox.width * 0.78;
            });
            smallButtons.sort((a, b) => {
                const ar = a.getBoundingClientRect();
                const br = b.getBoundingClientRect();
                return br.right - ar.right || ar.top - br.top;
            });
            const target = sameRowButtons[0] || toolbarButtons[0] || exportLike || smallButtons[0];
            if (!target) return null;
            const r = target.getBoundingClientRect();
            return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
        }""")
        if point:
            await page.mouse.click(point["x"], point["y"])
        else:
            await page.mouse.click(1374, 830)
    return await info.value


async def _scroll_export_toolbar_into_view(page: Page) -> None:
    """把创意表格工具栏滚到视口内，确保右侧导出图标可点击。"""
    await page.evaluate("""() => {
        const isVisible = (el) => {
            const s = window.getComputedStyle(el);
            const r = el.getBoundingClientRect();
            return s.visibility !== 'hidden' && s.display !== 'none'
                && r.width > 0 && r.height > 0;
        };
        const marker = Array.from(document.querySelectorAll('div,span,a'))
            .filter(isVisible)
            .map((el) => ({
                el,
                text: (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim(),
                area: el.getBoundingClientRect().width * el.getBoundingClientRect().height,
            }))
            .filter((item) => /Understanding your data|了解你的数据/i.test(item.text))
            .sort((a, b) => a.area - b.area)[0]?.el;
        if (marker) {
            marker.scrollIntoView({ block: 'center', inline: 'nearest' });
            return;
        }
        const creativeTable = Array.from(document.querySelectorAll('div'))
            .filter(isVisible)
            .find((el) => /In queue|Learning|Delivering|Authorization recommended|Boosting|Boosted/.test(el.innerText || ''));
        if (creativeTable) {
            creativeTable.scrollIntoView({ block: 'start', inline: 'nearest' });
        }
    }""")
    await page.wait_for_timeout(700)


async def _close_creative_tab_or_return(page: Page) -> None:
    """关闭素材详情面板或返回产品列表。"""
    # 优先回到产品列表。英文界面是 Product，中文界面是 商品。
    for label in ["Product", "商品"]:
        try:
            tab = page.get_by_text(label, exact=True).first
            if await tab.is_visible():
                await tab.click()
                await page.wait_for_timeout(1200)
                return
        except Exception:
            pass

    # 尝试关闭按钮
    close_btn = (
        page.locator("button,[role=\"button\"]")
        .filter(has_text=re.compile(r"^(x|×)$", re.IGNORECASE))
        .first
    )
    try:
        if await close_btn.is_visible():
            await close_btn.click()
            await page.wait_for_timeout(1000)
            return
    except Exception:
        pass

    await page.wait_for_timeout(1000)


def _convert_exported_files(paths: list[Path]) -> list[Path]:
    """在所有浏览器下载完成后，统一将 xlsx 转为 JSON。"""
    if not paths:
        return []

    print(f"\n开始转换 JSON: {len(paths)} 个 xlsx")
    converted: list[Path] = []
    for xlsx_path in paths:
        json_path = xlsx_path.with_suffix(".json")
        try:
            data = xlsx_to_json(xlsx_path)
            save_json_output(data, json_path)
            print(f"  └─ 已转换: {json_path.name}")
            converted.append(json_path)
        except Exception as exc:
            print(f"  └─ xlsx → JSON 转换失败（xlsx 已保留）: {xlsx_path.name}: {exc}")
            converted.append(xlsx_path)
    return converted


# ── 入口 ──────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n用户中断。")
        sys.exit(1)
    except Exception as e:
        print(f"致命错误: {e}", file=sys.stderr)
        sys.exit(1)
