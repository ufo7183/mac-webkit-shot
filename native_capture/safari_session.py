"""Safari WebDriver session 建立與 capabilities 驗證（對應 SDD §6 硬閘 2、3）。

沿用 C1 spike 已驗證過的 `webdriver.Safari()` 建立方式；只補上可重用的 capabilities
檢查與 macOS 平台檢查函式，不改變 spike 已通過的行為。
"""

from __future__ import annotations

import logging
import platform as platform_module
import shutil
import subprocess

from selenium import webdriver
from selenium.webdriver.safari.options import Options as SafariOptions

logger = logging.getLogger("native_capture.safari_session")


class SafariSessionError(RuntimeError):
    """Safari session 建立或 capabilities 驗證失敗時拋出。"""


def create_driver() -> webdriver.Safari:
    """建立原生 Safari WebDriver session（假設外部已執行 safaridriver --enable）。"""
    if platform_module.system() != "Darwin":
        raise SafariSessionError("原生 Safari WebDriver 只能在 macOS 建立")
    options = SafariOptions()
    return webdriver.Safari(options=options)


def assert_safari_capabilities(driver: webdriver.Safari) -> dict:
    """驗證 capabilities.browserName 為 safari（大小寫不敏感），回傳可序列化的 capabilities 子集。

    對應 SDD §6 硬閘 2：browserName 不是 safari 時視為硬閘失敗。
    """
    caps = driver.capabilities
    browser_name = caps.get("browserName")
    logger.info("session capabilities.browserName=%s", browser_name)
    if (browser_name or "").lower() != "safari":
        raise SafariSessionError(f"capabilities.browserName 不是 safari，實際為 {browser_name}")
    return {k: v for k, v in caps.items() if isinstance(v, (str, int, float, bool))}


def assert_macos_platform(driver: webdriver.Safari) -> str:
    """驗證 navigator.platform 為 macOS 環境，回傳 platform 字串。

    對應 SDD §6 硬閘 3：navigator.platform／capability 不是 macOS 環境時視為硬閘失敗。
    """
    platform = driver.execute_script("return navigator.platform")
    if "Mac" not in (platform or ""):
        raise SafariSessionError(f"navigator.platform 不是 macOS 環境，實際為 {platform}")
    return platform


def collect_environment_basics(driver: webdriver.Safari) -> dict:
    """收集 environment 區塊需要的 navigator 與 Safari 版本真值。"""
    caps = driver.capabilities
    return {
        "user_agent": driver.execute_script("return navigator.userAgent"),
        "max_touch_points": driver.execute_script("return navigator.maxTouchPoints"),
        "macos_version": _first_capability(
            caps, ("safari:platformVersion", "platformVersion", "macos_version")
        )
        or platform_module.mac_ver()[0]
        or None,
        "safari_version": _first_capability(caps, ("browserVersion", "safariVersion")),
        "safaridriver_version": _first_capability(
            caps, ("safari:driverVersion", "safaridriverVersion", "driverVersion")
        )
        or _read_safaridriver_version(),
    }


def _first_capability(caps: dict, names: tuple[str, ...]) -> str | None:
    """從 WebDriver capabilities 取第一個非空版本欄位。"""
    for name in names:
        value = caps.get(name)
        if value not in (None, ""):
            return str(value)
    return None


def _read_safaridriver_version() -> str | None:
    """讀取本機 safaridriver 版本；找不到時回傳 None 讓成功閘門拒絕空值。"""
    executable = shutil.which("safaridriver")
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (result.stdout or result.stderr).strip() or None
