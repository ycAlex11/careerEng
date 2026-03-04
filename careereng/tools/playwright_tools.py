"""Playwright helpers for site search/apply."""

from __future__ import annotations

from typing import Any


class PlaywrightTools:
    def __init__(self, *, headless: bool = True, timeout_ms: int = 45000):
        self.headless = headless
        self.timeout_ms = timeout_ms

    def discover_jobs(self, url: str, max_items: int = 20) -> dict[str, Any]:
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            return {
                "ok": False,
                "error": "playwright is not installed",
                "url": url,
                "items": [],
            }

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            page = browser.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                items = page.evaluate(
                    """
                    (maxItems) => {
                        const rows = [];
                        const anchors = Array.from(document.querySelectorAll('a[href]'));
                        for (const a of anchors) {
                            if (rows.length >= maxItems) break;
                            const href = (a.href || '').trim();
                            if (!href.startsWith('http')) continue;
                            const title = (a.innerText || '').trim();
                            if (!title) continue;
                            rows.push({ title, url: href });
                        }
                        return rows;
                    }
                    """,
                    max_items,
                )
                return {
                    "ok": True,
                    "url": page.url,
                    "title": page.title(),
                    "items": items if isinstance(items, list) else [],
                }
            except Exception as exc:
                return {"ok": False, "error": str(exc), "url": url, "items": []}
            finally:
                browser.close()

    def quick_apply(self, url: str) -> dict[str, Any]:
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            return {"ok": False, "error": "playwright is not installed", "url": url}

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            page = browser.new_page()
            clicked = False
            selectors = [
                "button:has-text('Apply')",
                "a:has-text('Apply')",
                "button:has-text('申请')",
                "a:has-text('申请')",
            ]
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                for sel in selectors:
                    loc = page.locator(sel)
                    if loc.count() > 0:
                        loc.first.click(timeout=2000)
                        clicked = True
                        break
                return {
                    "ok": True,
                    "url_before": url,
                    "url_after": page.url,
                    "clicked": clicked,
                }
            except Exception as exc:
                return {"ok": False, "error": str(exc), "url": url, "clicked": clicked}
            finally:
                browser.close()
