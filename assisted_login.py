"""
TikTok Ads GMV Max — 半自动登录
==================================
通过环境变量自动填写邮箱和密码，用户只需手动处理验证码/2FA。
登录成功后自动保存登录状态到 storage-state.json。

使用方式:
    # macOS / Linux
    TIKTOK_ADS_EMAIL='your@email.com' TIKTOK_ADS_PASSWORD='your-password' python assisted_login.py

    # Windows PowerShell
    $env:TIKTOK_ADS_EMAIL='your@email.com'
    $env:TIKTOK_ADS_PASSWORD='your-password'
    python assisted_login.py
"""

from __future__ import annotations

import asyncio
import os
import re
import sys

from playwright.async_api import async_playwright

from utils import load_config, resolve_from_project


TEXT = {
    "emailLogin": "邮箱",
}


async def main() -> None:
    config = load_config()
    profile_dir = resolve_from_project(config.get("browserProfileDir", "./browser-profile"))
    state_path = resolve_from_project(config.get("storageStatePath", "./storage-state.json"))

    email = os.environ.get("TIKTOK_ADS_EMAIL")
    password = os.environ.get("TIKTOK_ADS_PASSWORD")

    if not email or not password:
        print(
            "错误: 需要设置环境变量 TIKTOK_ADS_EMAIL 和 TIKTOK_ADS_PASSWORD。\n"
            "  macOS/Linux:  TIKTOK_ADS_EMAIL='xxx' TIKTOK_ADS_PASSWORD='xxx' python assisted_login.py\n"
            "  Windows:  $env:TIKTOK_ADS_EMAIL='xxx'; ...",
            file=sys.stderr,
        )
        sys.exit(1)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            accept_downloads=True,
            viewport={"width": 1440, "height": 900},
        )

        page = context.pages[0] if context.pages else await context.new_page()
        page.set_default_timeout(30000)

        await page.goto(
            config["gmvMaxUrl"],
            wait_until="domcontentloaded",
            timeout=config.get("navigationTimeoutMs", 60000),
        )
        await page.wait_for_load_state("networkidle")

        # 选择邮箱登录
        await _choose_email_login(page)

        # 填写邮箱和密码
        await _fill_login_form(page, email, password)

        # 点击登录按钮
        await _click_login_button(page)

        print("=" * 60)
        print("邮箱和密码已自动填写，登录按钮已点击。")
        print("请手动完成验证码 / 2FA。")
        print("看到 GMV Max 仪表盘后，回到终端按 Enter 保存登录状态。")
        print("=" * 60)
        input(">>> 按 Enter 继续...")

        await context.storage_state(path=str(state_path))
        print(f"登录状态已保存到: {state_path}")
        await context.close()


async def _choose_email_login(page) -> None:
    """如果页面提供多种登录方式，选择邮箱/账号登录。"""
    choices = [
        page.get_by_text(TEXT["emailLogin"]).first,
        page.get_by_text(re.compile(r"Email|email|邮箱|账号")).first,
    ]
    for choice in choices:
        try:
            await choice.click(timeout=5000)
            await page.wait_for_timeout(1000)
            return
        except Exception:
            pass


async def _fill_login_form(page, email: str, password: str) -> None:
    """填写邮箱和密码输入框。"""
    # 邮箱输入框
    email_selectors = [
        'input[type="email"]',
        'input[name*="email" i]',
        'input[placeholder*="email" i]',
        'input[placeholder*="邮箱"]',
        'input[placeholder*="账号"]',
        'input:not([type="password"])',
    ]
    await _fill_first_matching(page, email_selectors, email)

    # 密码输入框
    password_selectors = [
        'input[type="password"]',
        'input[name*="password" i]',
        'input[placeholder*="password" i]',
        'input[placeholder*="密码"]',
    ]
    await _fill_first_matching(page, password_selectors, password)


async def _fill_first_matching(page, selectors: list[str], value: str) -> None:
    """按顺序尝试选择器，找到第一个可见的输入框并填写。"""
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=5000)
            await locator.click(click_count=3)
            await locator.fill(value)
            return
        except Exception:
            pass


async def _click_login_button(page) -> None:
    """点击登录按钮（多策略降级）。"""
    candidates = [
        page.get_by_role("button", name=re.compile(r"^Log in$", re.IGNORECASE)).first,
        page.get_by_role("button", name=re.compile(r"登录|登入|Log in", re.IGNORECASE)).first,
        page.locator("button").filter(has_text=re.compile(r"登录|登入|Log in", re.IGNORECASE)).first,
    ]
    for candidate in candidates:
        try:
            await candidate.wait_for(state="visible", timeout=5000)
            await candidate.click()
            return
        except Exception:
            pass

    raise RuntimeError("找不到登录按钮。")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n用户中断。")
        sys.exit(1)
    except Exception as e:
        print(f"出错: {e}", file=sys.stderr)
        sys.exit(1)
