"""Playwright helpers for site search/apply."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus


class _AsyncRuntime:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="careereng-playwright-runtime", daemon=True)
        self._started = threading.Event()
        self.closed = False
        self._thread.start()
        self._started.wait(timeout=5.0)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self._started.set()
        self.loop.run_forever()
        pending = asyncio.all_tasks(self.loop)
        for task in pending:
            task.cancel()
        if pending:
            self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self.loop.run_until_complete(self.loop.shutdown_asyncgens())
        self.loop.close()

    def run(self, awaitable):
        if self.closed:
            raise RuntimeError("playwright runtime is closed")
        future = asyncio.run_coroutine_threadsafe(awaitable, self.loop)
        return future.result()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5.0)


class _AsyncLocatorProxy:
    def __init__(self, runtime: _AsyncRuntime, locator):
        self.runtime = runtime
        self.locator = locator

    @property
    def first(self):
        return _AsyncLocatorProxy(self.runtime, self.locator.first)

    def count(self) -> int:
        return int(self.runtime.run(self.locator.count()))

    def click(self, *args, **kwargs):
        return self.runtime.run(self.locator.click(*args, **kwargs))


class _AsyncContextProxy:
    def __init__(self, runtime: _AsyncRuntime, context):
        self.runtime = runtime
        self.context = context

    @property
    def pages(self):
        return [_AsyncPageProxy(self.runtime, page) for page in self.runtime.run(self._pages())]

    async def _pages(self):
        return list(self.context.pages)

    def new_page(self):
        page = self.runtime.run(self.context.new_page())
        return _AsyncPageProxy(self.runtime, page)

    def close(self):
        return self.runtime.run(self.context.close())


class _AsyncBrowserProxy:
    def __init__(self, runtime: _AsyncRuntime, browser):
        self.runtime = runtime
        self.browser = browser

    def new_page(self):
        page = self.runtime.run(self.browser.new_page())
        return _AsyncPageProxy(self.runtime, page)

    def close(self):
        return self.runtime.run(self.browser.close())


class _AsyncPageProxy:
    def __init__(self, runtime: _AsyncRuntime, page):
        self.runtime = runtime
        self.page = page

    @property
    def url(self) -> str:
        return str(self.runtime.run(self._url()) or "")

    async def _url(self):
        return self.page.url

    @property
    def context(self):
        return _AsyncContextProxy(self.runtime, self.runtime.run(self._context()))

    async def _context(self):
        return self.page.context

    def is_closed(self) -> bool:
        return bool(self.runtime.run(self._is_closed()))

    async def _is_closed(self):
        return self.page.is_closed()

    def goto(self, *args, **kwargs):
        return self.runtime.run(self.page.goto(*args, **kwargs))

    def wait_for_timeout(self, timeout_ms: int):
        return self.runtime.run(self.page.wait_for_timeout(timeout_ms))

    def reload(self, *args, **kwargs):
        return self.runtime.run(self.page.reload(*args, **kwargs))

    def text_content(self, *args, **kwargs):
        return self.runtime.run(self.page.text_content(*args, **kwargs))

    def title(self) -> str:
        return str(self.runtime.run(self.page.title()) or "")

    def locator(self, *args, **kwargs):
        return _AsyncLocatorProxy(self.runtime, self.page.locator(*args, **kwargs))

    def get_by_role(self, *args, **kwargs):
        return _AsyncLocatorProxy(self.runtime, self.page.get_by_role(*args, **kwargs))

    def get_by_text(self, *args, **kwargs):
        return _AsyncLocatorProxy(self.runtime, self.page.get_by_text(*args, **kwargs))

    def evaluate(self, *args, **kwargs):
        return self.runtime.run(self.page.evaluate(*args, **kwargs))

    def wait_for_load_state(self, *args, **kwargs):
        return self.runtime.run(self.page.wait_for_load_state(*args, **kwargs))

    def close(self):
        return self.runtime.run(self.page.close())


class _AsyncManagerProxy:
    def __init__(self, runtime: _AsyncRuntime, manager):
        self.runtime = runtime
        self.manager = manager
        self.stopped = False

    def stop(self):
        if self.stopped:
            return None
        self.stopped = True
        try:
            return self.runtime.run(self.manager.stop())
        finally:
            self.runtime.close()


class PlaywrightRunSession:
    def __init__(
        self,
        tools: "PlaywrightTools",
        manager,
        browser_or_context,
        page,
    ):
        self.tools = tools
        self.manager = manager
        self.browser_or_context = browser_or_context
        self.page = page

    def is_alive(self) -> bool:
        try:
            checker = getattr(self.page, "is_closed", None)
            if callable(checker) and checker():
                return False
            _ = str(self.page.url or "")
            return True
        except Exception:
            return False

    def discover_jobs(self, url: str, max_items: int = 20) -> dict[str, Any]:
        return self.tools._discover_jobs_on_page(self.page, url, max_items=max_items)

    def discover_jobs_guided(
        self,
        url: str,
        guidance_text: str = "",
        signal_config: dict[str, list[str]] | None = None,
        auto_login_config: dict[str, Any] | None = None,
        max_items: int = 20,
    ) -> dict[str, Any]:
        return self.tools._discover_jobs_guided_on_page(
            self.page,
            url,
            guidance_text=guidance_text,
            signal_config=signal_config,
            auto_login_config=auto_login_config,
            max_items=max_items,
        )

    def quick_apply(self, url: str) -> dict[str, Any]:
        return self.tools._quick_apply_on_page(self.page, url)

    def inspect_authenticated(
        self,
        url: str,
        signal_config: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        return self.tools._inspect_authenticated_on_page(self.page, url, signal_config=signal_config)

    def prepare_session(
        self,
        url: str,
        signal_config: dict[str, list[str]] | None = None,
        auto_login_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.tools._prepare_session_on_page(
            self.page,
            url,
            signal_config=signal_config,
            auto_login_config=auto_login_config,
        )

    def close(self) -> None:
        self.tools._close_browser(self.manager, self.browser_or_context, force=True)


class PlaywrightSessionOpenError(RuntimeError):
    def __init__(self, status: str, message: str = "", *, detail: dict[str, Any] | None = None):
        super().__init__(message or status)
        self.status = str(status or "session_open_failed")
        self.message = str(message or status or "session_open_failed")
        self.detail = detail if isinstance(detail, dict) else {}


class PlaywrightTools:
    def __init__(self, *, headless: bool = True, keep_open: bool = False, timeout_ms: int = 45000, slow_mo_ms: int = 0):
        self.headless = headless
        self.keep_open = bool(keep_open)
        self.timeout_ms = timeout_ms
        self.slow_mo_ms = slow_mo_ms

    def _launch_error(self, exc: Exception | None) -> str:
        if exc is None:
            return "playwright launch failed"
        return f"playwright launch failed: {str(exc)}"

    def _launch_browser(self, user_data_dir: str | None = None, *, headless: bool | None = None):
        from playwright.async_api import async_playwright

        runtime = _AsyncRuntime()
        manager = None
        effective_headless = self.headless if headless is None else bool(headless)
        try:
            pw = runtime.run(async_playwright().start())
            manager = _AsyncManagerProxy(runtime, pw)
            if user_data_dir:
                context = runtime.run(
                    pw.chromium.launch_persistent_context(
                        user_data_dir=user_data_dir,
                        headless=effective_headless,
                        slow_mo=self.slow_mo_ms,
                    )
                )
                return manager, _AsyncContextProxy(runtime, context), None
            browser = runtime.run(
                pw.chromium.launch(headless=effective_headless, slow_mo=self.slow_mo_ms)
            )
            browser_proxy = _AsyncBrowserProxy(runtime, browser)
            page = browser_proxy.new_page()
            return manager, browser_proxy, page
        except Exception:
            try:
                if manager is not None:
                    manager.stop()
                else:
                    runtime.close()
            except Exception:
                runtime.close()
            raise

    def _is_profile_lock_error(self, exc: Exception | None) -> bool:
        lowered = str(exc or "").lower()
        return any(marker in lowered for marker in ("user data directory is already in use", "profile appears to be in use", "lock"))

    def _profile_lock_paths(self, profile_dir: str) -> list[Path]:
        root = Path(profile_dir)
        return [root / "SingletonLock", root / "SingletonCookie", root / "SingletonSocket"]

    def _profile_lock_target(self, profile_dir: str) -> str:
        lock_path = self._profile_lock_paths(profile_dir)[0]
        try:
            if lock_path.is_symlink():
                return os.readlink(lock_path)
        except Exception:
            pass
        try:
            return lock_path.read_text(encoding="utf-8").strip()
        except Exception:
            return ""

    def _extract_profile_lock_pid(self, target: str) -> int:
        match = re.search(r"(\d+)(?:\D*)$", str(target or ""))
        if not match:
            return 0
        try:
            return int(match.group(1))
        except Exception:
            return 0

    def _pid_alive(self, pid: int) -> bool:
        if int(pid or 0) <= 0:
            return False
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def _recover_stale_profile_lock(self, profile_dir: str) -> dict[str, Any]:
        lock_path, cookie_path, socket_path = self._profile_lock_paths(profile_dir)
        if not lock_path.exists() and not lock_path.is_symlink():
            return {
                "attempted": False,
                "recovered": False,
                "reason": "lock_file_missing",
                "profile_dir": str(profile_dir),
            }

        target = self._profile_lock_target(profile_dir)
        pid = self._extract_profile_lock_pid(target)
        if pid <= 0:
            return {
                "attempted": False,
                "recovered": False,
                "reason": "pid_unknown",
                "profile_dir": str(profile_dir),
                "lock_target": target,
            }
        if self._pid_alive(pid):
            return {
                "attempted": True,
                "recovered": False,
                "reason": "pid_alive",
                "profile_dir": str(profile_dir),
                "lock_target": target,
                "lock_owner_pid": pid,
            }

        removed: list[str] = []
        for path in (lock_path, cookie_path, socket_path):
            try:
                if path.exists() or path.is_symlink():
                    path.unlink()
                    removed.append(path.name)
            except FileNotFoundError:
                continue
        return {
            "attempted": True,
            "recovered": True,
            "reason": "stale_lock_removed",
            "profile_dir": str(profile_dir),
            "lock_target": target,
            "lock_owner_pid": pid,
            "removed": removed,
        }

    def _recover_stale_profile_lock_with_grace(self, profile_dir: str) -> dict[str, Any]:
        recovery = self._recover_stale_profile_lock(profile_dir)
        if str(recovery.get("reason") or "") != "pid_alive":
            return recovery

        pid = int(recovery.get("lock_owner_pid") or 0)
        if pid <= 0:
            return recovery

        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            time.sleep(0.1)
            if not self._pid_alive(pid):
                retried = self._recover_stale_profile_lock(profile_dir)
                retried["waited_for_exit"] = True
                retried["initial_lock_owner_pid"] = pid
                return retried

        recovery = dict(recovery)
        recovery["waited_for_exit"] = True
        recovery["initial_lock_owner_pid"] = pid
        return recovery

    def _profile_dir_has_contents(self, profile_dir: str) -> bool:
        root = Path(profile_dir)
        if not root.exists():
            return False
        try:
            next(root.iterdir())
        except StopIteration:
            return False
        except Exception:
            return False
        return True

    def _backup_and_reset_profile_dir(self, profile_dir: str) -> dict[str, Any]:
        root = Path(profile_dir)
        if not self._profile_dir_has_contents(profile_dir):
            try:
                root.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                return {
                    "attempted": False,
                    "recreated": False,
                    "profile_dir": str(profile_dir),
                    "reason": "mkdir_failed",
                    "error": str(exc).strip(),
                }
            return {
                "attempted": False,
                "recreated": True,
                "profile_dir": str(profile_dir),
                "reason": "profile_empty",
            }

        timestamp = time.strftime("%Y%m%dT%H%M%S")
        backup_root = root.parent
        for index in range(1000):
            suffix = f".backup.{timestamp}" if index == 0 else f".backup.{timestamp}.{index}"
            candidate = backup_root / f"{root.name}{suffix}"
            if candidate.exists():
                continue
            try:
                shutil.move(str(root), str(candidate))
                root.mkdir(parents=True, exist_ok=True)
                return {
                    "attempted": True,
                    "recreated": True,
                    "profile_dir": str(profile_dir),
                    "backup_dir": str(candidate),
                    "reason": "profile_backed_up_and_recreated",
                }
            except Exception as exc:
                return {
                    "attempted": True,
                    "recreated": False,
                    "profile_dir": str(profile_dir),
                    "backup_dir": str(candidate),
                    "reason": "backup_failed",
                    "error": str(exc).strip(),
                }
        return {
            "attempted": True,
            "recreated": False,
            "profile_dir": str(profile_dir),
            "reason": "backup_name_exhausted",
        }

    def _retry_launch_after_profile_reset(
        self,
        profile_dir: str,
        *,
        headless: bool | None = None,
        detail: dict[str, Any],
        original_exc: Exception | None = None,
    ):
        reset = self._backup_and_reset_profile_dir(profile_dir)
        detail["profile_reset"] = reset
        if not bool(reset.get("recreated")):
            raise PlaywrightSessionOpenError(
                "launch_failed",
                str(original_exc).strip() if original_exc is not None else "playwright launch failed",
                detail=detail,
            ) from original_exc
        try:
            manager, browser_or_context, page = self._launch_browser(user_data_dir=profile_dir, headless=headless)
            return manager, browser_or_context, page, detail
        except Exception as retry_exc:
            detail["retry_error"] = str(retry_exc).strip() or self._launch_error(retry_exc)
            detail["retry_error_kind"] = "profile_lock_error" if self._is_profile_lock_error(retry_exc) else "launch_error"
            raise PlaywrightSessionOpenError(
                "launch_failed",
                str(retry_exc).strip() or self._launch_error(retry_exc),
                detail=detail,
            ) from retry_exc

    def _open_profile_context(
        self,
        profile_dir: str,
        *,
        headless: bool | None = None,
    ):
        effective_headless = self.headless if headless is None else bool(headless)
        detail: dict[str, Any] = {
            "profile_dir": str(profile_dir),
            "headless": effective_headless,
        }
        try:
            manager, browser_or_context, page = self._launch_browser(user_data_dir=profile_dir, headless=headless)
            return manager, browser_or_context, page, detail
        except Exception as exc:
            if not self._is_profile_lock_error(exc):
                return self._retry_launch_after_profile_reset(
                    profile_dir,
                    headless=headless,
                    detail=detail,
                    original_exc=exc,
                )

            recovery = self._recover_stale_profile_lock_with_grace(profile_dir)
            detail["lock_recovery"] = recovery
            if bool(recovery.get("recovered")):
                try:
                    manager, browser_or_context, page = self._launch_browser(user_data_dir=profile_dir, headless=headless)
                    return manager, browser_or_context, page, detail
                except Exception as retry_exc:
                    return self._retry_launch_after_profile_reset(
                        profile_dir,
                        headless=headless,
                        detail=detail,
                        original_exc=retry_exc,
                    )

            raise PlaywrightSessionOpenError(
                "profile_locked",
                str(exc).strip() or self._launch_error(exc),
                detail=detail,
            ) from exc

    def _open_profile_session(
        self,
        profile_dir: str,
        *,
        target_url: str = "",
        headless: bool | None = None,
    ) -> PlaywrightRunSession:
        try:
            manager, browser_or_context, page, detail = self._open_profile_context(profile_dir, headless=headless)
        except PlaywrightSessionOpenError:
            raise

        if page is None:
            page = browser_or_context.pages[0] if browser_or_context.pages else browser_or_context.new_page()
        if target_url:
            try:
                self._goto_with_retry(page, target_url)
            except Exception as exc:
                self._close_browser(manager, browser_or_context, force=True)
                raise PlaywrightSessionOpenError(
                    "navigate_failed",
                    str(exc).strip() or "failed to navigate browser",
                    detail={
                        **detail,
                        "target_url": str(target_url),
                    },
                ) from exc
        return PlaywrightRunSession(self, manager, browser_or_context, page)

    def _close_browser(self, manager, browser_or_context, *, force: bool = False):
        if self.keep_open and not force:
            return
        try:
            browser_or_context.close()
        except Exception:
            pass
        try:
            manager.stop()
        except Exception:
            pass

    def open_site_session(
        self,
        profile_dir: str | None = None,
        target_url: str = "",
        headless: bool | None = None,
        allow_launch: bool = True,
    ) -> PlaywrightRunSession | None:
        if profile_dir:
            return self._open_profile_session(
                profile_dir,
                target_url=target_url,
                headless=headless,
            )

        try:
            manager, browser_or_context, page = self._launch_browser(user_data_dir=profile_dir, headless=headless)
        except Exception as exc:
            raise PlaywrightSessionOpenError(
                "launch_failed",
                self._launch_error(exc),
                detail={
                    "headless": self.headless if headless is None else bool(headless),
                    "allow_launch": bool(allow_launch),
                },
            ) from exc
        if page is None:
            page = browser_or_context.pages[0] if browser_or_context.pages else browser_or_context.new_page()
        return PlaywrightRunSession(self, manager, browser_or_context, page)

    def _goto_with_retry(self, page, url: str) -> None:
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                page.wait_for_timeout(1200)
                return
            except Exception as exc:
                last_exc = exc
                if attempt == 0:
                    try:
                        page.reload(wait_until="domcontentloaded", timeout=self.timeout_ms)
                        page.wait_for_timeout(1200)
                        return
                    except Exception as reload_exc:
                        last_exc = reload_exc
        if last_exc is not None:
            raise last_exc

    def _body_text(self, page) -> str:
        try:
            return str(page.text_content("body") or "")
        except Exception:
            return ""

    def _normalized_signal_list(self, signal_config: dict[str, list[str]] | None, key: str) -> list[str]:
        values = signal_config.get(key) if isinstance(signal_config, dict) else []
        rows: list[str] = []
        seen: set[str] = set()
        for raw in values if isinstance(values, list) else []:
            value = str(raw or "").strip()
            if not value:
                continue
            lowered = value.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            rows.append(value)
        return rows

    def _page_text_blob(self, page) -> str:
        try:
            title = str(page.title() or "")
        except Exception:
            title = ""
        body = self._body_text(page)[:12000]
        url = str(page.url or "")
        return "\n".join(part for part in (url, title, body) if part).lower()

    def _signal_hit_count(self, text: str, signals: list[str]) -> int:
        lowered = str(text or "").lower()
        matched: set[str] = set()
        for raw in signals:
            value = str(raw or "").strip().lower()
            if value and value in lowered:
                matched.add(value)
        return len(matched)

    def _selector_exists(self, page, selectors: list[str]) -> bool:
        for selector in selectors:
            try:
                if page.locator(selector).count() > 0:
                    return True
            except Exception:
                continue
        return False

    def _is_role_like_title(self, title: str) -> bool:
        lowered = str(title or "").strip().lower()
        if not lowered:
            return False
        return bool(
            re.search(
                r"\b(engineer|developer|architect|scientist|researcher|analyst|administrator|manager|consultant|specialist|lead|principal)\b",
                lowered,
            )
        )

    def _job_candidate_score(self, title: str, text: str, url: str, signal_config: dict[str, list[str]] | None = None) -> int:
        combined = "\n".join(part for part in (title, text, url) if part).lower()
        score = 0
        if self._is_role_like_title(title):
            score += 2
        score += self._signal_hit_count(combined, self._normalized_signal_list(signal_config, "list_signals"))
        if any(marker in str(url or "").lower() for marker in ("/job/", "/jobs/", "jobid", "jobdetail", "requisition")):
            score += 1
        if any(marker in combined for marker in ("full time", "posted", "location", "requisition", "job id", "employment type")):
            score += 1
        return score

    def _auth_state(self, page, signal_config: dict[str, list[str]] | None = None) -> dict[str, Any]:
        text = self._page_text_blob(page)
        positive = self._signal_hit_count(text, self._normalized_signal_list(signal_config, "auth_positive"))
        negative = self._signal_hit_count(text, self._normalized_signal_list(signal_config, "auth_negative"))
        if self._selector_exists(
            page,
            [
                "[aria-label*='account' i]",
                "[aria-label*='profile' i]",
                "[data-testid*='avatar' i]",
                "img[alt*='avatar' i]",
                "img[alt*='profile' i]",
                "[role='button']:has-text('@')",
                "[role='link']:has-text('@')",
                "button:has-text('@')",
                "a:has-text('@')",
            ],
        ):
            positive += 1
        if self._selector_exists(
            page,
            [
                "input[type='password']",
                "button:has-text('Sign in')",
                "button:has-text('Log in')",
                "a:has-text('Sign in')",
                "a:has-text('Log in')",
                "button:has-text('Continue with Google')",
                "a:has-text('Continue with Google')",
                "button:has-text('Use another account')",
            ],
        ):
            negative += 1
        authenticated = positive > 0 and negative == 0
        return {
            "authenticated": authenticated,
            "need_auth": negative > 0 and positive == 0,
            "positive_hits": positive,
            "negative_hits": negative,
        }

    def _page_snapshot(self, page) -> dict[str, Any]:
        try:
            title = str(page.title() or "")
        except Exception:
            title = ""
        try:
            url = str(page.url or "")
        except Exception:
            url = ""
        body = self._body_text(page)[:2000]
        return {"url": url, "title": title, "body": body}

    def _confirm_authenticated_via_actions(
        self,
        page,
        *,
        signal_config: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        actions = self._normalized_signal_list(signal_config, "auth_confirmation_actions")
        confirm_signals = self._normalized_signal_list(signal_config, "auth_confirmation_signals")
        if not actions:
            return {"confirmed": False, "page": page, "hits": 0}
        current_page = page
        for label in actions:
            next_page = self._click_action_label(current_page, label)
            if next_page is None:
                continue
            current_page = next_page
            hits = self._signal_hit_count(self._page_text_blob(current_page), confirm_signals)
            if hits > 0:
                return {"confirmed": True, "page": current_page, "hits": hits}
        return {"confirmed": False, "page": current_page, "hits": 0}

    def _session_ready_state(
        self,
        page,
        *,
        signal_config: dict[str, list[str]] | None = None,
        items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        auth_state = self._auth_state(page, signal_config=signal_config)
        channel_state = self._channel_state(page, signal_config=signal_config, items=items)
        confirmed = {"confirmed": False, "page": page, "hits": 0}
        if not bool(auth_state["authenticated"]) and not bool(auth_state["need_auth"]):
            confirmed = self._confirm_authenticated_via_actions(page, signal_config=signal_config)
            page = confirmed.get("page") or page
            if bool(confirmed.get("confirmed")):
                auth_state = dict(auth_state)
                auth_state["authenticated"] = True
                auth_state["positive_hits"] = int(auth_state.get("positive_hits") or 0) + int(confirmed.get("hits") or 0)
                channel_state = self._channel_state(page, signal_config=signal_config, items=items)
        ready = bool(auth_state["authenticated"])
        return {
            "ready": ready,
            "page": page,
            "auth_state": auth_state,
            "channel_state": channel_state,
            "auth_status": "authenticated" if bool(auth_state["authenticated"]) else ("need_auth" if bool(auth_state["need_auth"]) else "ambiguous"),
            "workflow_status": "ready_for_retrieval" if bool(channel_state["channel_ready"]) else "needs_navigation",
        }

    def _inspect_authenticated_on_page(
        self,
        page,
        url: str,
        *,
        signal_config: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        target = url or "about:blank"
        self._goto_with_retry(page, target)
        ready_state = self._session_ready_state(page, signal_config=signal_config)
        page = ready_state.get("page") or page
        auth_state = ready_state["auth_state"]
        channel_state = ready_state["channel_state"]
        authenticated = bool(auth_state["authenticated"])
        return {
            "ok": authenticated,
            "status": "authenticated" if authenticated else "login_required",
            "url": str(page.url or ""),
            "title": str(page.title() or ""),
            "auth_status": str(ready_state.get("auth_status") or ""),
            "workflow_status": str(ready_state.get("workflow_status") or ""),
            "auth_positive_hits": auth_state["positive_hits"],
            "auth_negative_hits": auth_state["negative_hits"],
            "channel_ready_hits": channel_state["ready_hits"],
        }

    def _prepare_session_on_page(
        self,
        page,
        url: str,
        *,
        signal_config: dict[str, list[str]] | None = None,
        auto_login_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target = url or "about:blank"
        self._goto_with_retry(page, target)
        max_auto_attempts = max(1, min(3, int((auto_login_config or {}).get("max_attempts") or 2)))
        attempts = 0

        while True:
            ready_state = self._session_ready_state(page, signal_config=signal_config)
            page = ready_state.get("page") or page
            auth_state = ready_state["auth_state"]
            channel_state = ready_state["channel_state"]
            if bool(ready_state["ready"]):
                return {
                    "ok": True,
                    "status": "authenticated",
                    "url": str(page.url or ""),
                    "title": str(page.title() or ""),
                    "auth_status": str(ready_state.get("auth_status") or "authenticated"),
                    "workflow_status": str(ready_state.get("workflow_status") or ""),
                    "auth_positive_hits": auth_state["positive_hits"],
                    "auth_negative_hits": auth_state["negative_hits"],
                    "channel_ready_hits": channel_state["ready_hits"],
                }

            auto_result = self._attempt_safe_auto_login(page, auto_login_config=auto_login_config)
            if bool(auto_result.get("attempted")) and attempts < max_auto_attempts:
                attempts += 1
                page = auto_result.get("page") or page
                continue

            status = str(auto_result.get("status") or "")
            if status == "manual_takeover_required":
                status = "need_auth"
            elif not status or status in {"no_safe_action_found", "no_safe_action_configured", "clicked_action", "clicked_single_account_tile"}:
                status = "need_auth"
            return {
                "ok": False,
                "status": status,
                "url": str(page.url or target),
                "title": str(page.title() or ""),
                "auth_status": str(ready_state.get("auth_status") or "need_auth"),
                "workflow_status": str(ready_state.get("workflow_status") or ""),
                "auth_positive_hits": auth_state["positive_hits"],
                "auth_negative_hits": auth_state["negative_hits"],
                "channel_ready_hits": channel_state["ready_hits"],
                "detail": {"auto_login": auto_result, "page_snapshot": self._page_snapshot(page)},
            }

    def _channel_state(
        self,
        page,
        *,
        signal_config: dict[str, list[str]] | None = None,
        items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        text = self._page_text_blob(page)
        ready_hits = self._signal_hit_count(text, self._normalized_signal_list(signal_config, "channel_ready"))
        negative_hits = self._signal_hit_count(text, self._normalized_signal_list(signal_config, "channel_negative"))
        list_hits = self._signal_hit_count(text, self._normalized_signal_list(signal_config, "list_signals"))
        search_ui = self._selector_exists(
            page,
            [
                "input[type='search']",
                "input[placeholder*='search' i]",
                "input[aria-label*='search' i]",
                "input[name*='search' i]",
                "[role='search'] input",
            ],
        )
        if search_ui:
            ready_hits += 1
        if items:
            list_hits += 1
        return {
            "channel_ready": ready_hits > 0 or (search_ui and negative_hits == 0),
            "ready_hits": ready_hits,
            "negative_hits": negative_hits,
            "list_hits": list_hits,
        }

    def _detail_page_matches(self, page, signal_config: dict[str, list[str]] | None = None) -> bool:
        text = self._page_text_blob(page)
        hits = self._signal_hit_count(text, self._normalized_signal_list(signal_config, "detail_signals"))
        if hits >= 2:
            return True
        if hits >= 1 and self._is_role_like_title(str(page.title() or "")):
            return True
        return False

    def _extract_keyword_label(self, text: str, patterns: list[str]) -> str:
        lowered = str(text or "").lower()
        for pattern in patterns:
            match = re.search(pattern, lowered)
            if match:
                return match.group(0)
        return ""

    def _infer_job_signals(self, text: str) -> dict[str, str]:
        lowered = str(text or "").lower()
        match_label = ""
        if "strong match" in lowered:
            match_label = "Strong Match"
        elif "good match" in lowered:
            match_label = "Good Match"

        posted_label = self._extract_keyword_label(
            lowered,
            [
                r"posted\s+\d+\+?\s+days?\s+ago",
                r"posted\s+today",
                r"posted\s+yesterday",
                r"posted\s+\d+\s+hours?\s+ago",
                r"posted\s+\d+\s+day[s]?\s+ago",
                r"30\+\s*days",
            ],
        )
        apply_state = ""
        if "view application" in lowered or "view applicant" in lowered:
            apply_state = "View Application"
        return {
            "match_label": match_label,
            "posted_label": posted_label,
            "apply_state": apply_state,
        }

    def _is_non_job_card(self, title: str, text: str = "", url: str = "") -> bool:
        lowered = " ".join(part for part in (title, text, url) if part).lower()
        blocked = (
            "view previous applications",
            "consultez votre profil",
            "action center",
            "application history",
            "candidate home",
            "my profile",
            "create a new profile",
            "sign in",
            "signin",
            "login",
            "log in",
            "privacy",
            "cookie",
            "terms",
            "search jobs",
            "open positions",
            "careers",
        )
        return any(marker in lowered for marker in blocked)

    def _extract_job_cards(self, page, max_items: int, signal_config: dict[str, list[str]] | None = None):
        items = page.evaluate(
            """
            (maxItems) => {
                const rows = [];
                const anchors = Array.from(document.querySelectorAll('a[href]'));
                const scanLimit = Math.max(maxItems * 30, 200);
                for (const a of anchors) {
                    if (rows.length >= scanLimit) break;
                    const href = (a.href || '').trim();
                    if (!href.startsWith('http')) continue;
                    const title = (a.innerText || '').trim();
                    if (!title) continue;
                    const text = (a.closest('article,li,div')?.innerText || '').trim();
                    rows.push({
                      title,
                      url: href,
                      description: text.slice(0, 500),
                      card_text: text.slice(0, 2000),
                    });
                }
                return rows;
            }
            """,
            max_items,
        )
        out: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            text = str(item.get("card_text") or item.get("description") or "")
            if not title or not url or url in seen_urls or self._is_non_job_card(title, text, url):
                continue
            if self._job_candidate_score(title, text, url, signal_config=signal_config) < 2:
                continue
            signals = self._infer_job_signals(text)
            seen_urls.add(url)
            out.append(
                {
                    **item,
                    "title": title,
                    "url": url,
                    "card_text": text,
                    "match_label": signals["match_label"],
                    "posted_label": signals["posted_label"],
                    "apply_state": signals["apply_state"],
                }
            )
        return out

    def _ordered_navigation_labels(self, guidance_text: str) -> list[str]:
        ordered: list[str] = []

        def _push(label: str) -> None:
            value = str(label or "").strip()
            if not value:
                return
            lowered = value.lower()
            if lowered in {existing.lower() for existing in ordered}:
                return
            ordered.append(value)

        for match in re.finditer(r"`([^`]{1,80})`", str(guidance_text or "")):
            _push(match.group(1))
        for label in (
            "Search Jobs",
            "Search jobs",
            "Open Positions",
            "Open positions",
            "View Jobs",
            "Job Search",
            "Jobs",
            "Careers",
        ):
            _push(label)
        return ordered

    def _click_navigation_label(self, page, label: str):
        pattern = re.compile(re.escape(str(label or "").strip()), re.I)
        candidates = [
            page.get_by_role("link", name=pattern),
            page.get_by_role("button", name=pattern),
            page.get_by_text(pattern),
        ]
        for locator in candidates:
            try:
                if locator.count() <= 0:
                    continue
                before_count = len(page.context.pages)
                locator.first.click(timeout=3000)
                page.wait_for_timeout(1500)
                pages = page.context.pages
                if len(pages) > before_count:
                    page = pages[-1]
                    page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
                    page.wait_for_timeout(1200)
                return page
            except Exception:
                continue
        return None

    def _click_action_label(self, page, label: str):
        raw = str(label or "").strip()
        if not raw:
            return None
        patterns = [re.compile(rf"^\s*{re.escape(raw)}\s*$", re.I)]
        if " " in raw or len(raw) >= 8:
            patterns.append(re.compile(re.escape(raw), re.I))
        for pattern in patterns:
            candidates = [
                page.get_by_role("button", name=pattern),
                page.get_by_role("link", name=pattern),
                page.get_by_role("menuitem", name=pattern),
                page.get_by_text(pattern),
            ]
            for locator in candidates:
                try:
                    if locator.count() <= 0:
                        continue
                    before_count = len(page.context.pages)
                    locator.first.click(timeout=3000)
                    page.wait_for_timeout(1500)
                    pages = page.context.pages
                    if len(pages) > before_count:
                        page = pages[-1]
                        page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
                        page.wait_for_timeout(1200)
                    return page
                except Exception:
                    continue
        return None

    def _single_account_tile_texts(self, page) -> list[str]:
        try:
            rows = page.evaluate(
                """
                () => {
                    const out = [];
                    const nodes = Array.from(document.querySelectorAll('button,a,[role="button"],[role="link"]'));
                    for (const node of nodes) {
                        const text = (node.innerText || '').trim().replace(/\\s+/g, ' ');
                        if (!text || !text.includes('@')) continue;
                        if (text.length > 120) continue;
                        out.push(text);
                    }
                    return Array.from(new Set(out)).slice(0, 5);
                }
                """
            )
        except Exception:
            rows = []
        return [str(row).strip() for row in rows if str(row).strip()]

    def _click_single_account_tile(self, page):
        texts = self._single_account_tile_texts(page)
        if len(texts) != 1:
            return None
        return self._click_action_label(page, texts[0])

    def _manual_takeover_required(self, page, signals: list[str]) -> bool:
        text = self._page_text_blob(page)
        if self._signal_hit_count(text, signals) > 0:
            return True
        return self._selector_exists(
            page,
            [
                "input[type='password']",
                "input[autocomplete='current-password']",
                "input[autocomplete='one-time-code']",
                "input[name*='code' i]",
                "input[name*='otp' i]",
            ],
        )

    def _attempt_safe_auto_login(
        self,
        page,
        *,
        auto_login_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        config = auto_login_config if isinstance(auto_login_config, dict) else {}
        action_labels = [str(row).strip() for row in config.get("action_labels", []) if str(row).strip()]
        allow_single_account_tile = bool(config.get("allow_single_account_tile"))
        manual_takeover_signals = [str(row).strip() for row in config.get("manual_takeover_signals", []) if str(row).strip()]
        if not action_labels and not allow_single_account_tile:
            return {"attempted": False, "status": "no_safe_action_configured", "page": page}
        if self._manual_takeover_required(page, manual_takeover_signals):
            return {"attempted": False, "status": "manual_takeover_required", "page": page}
        for label in action_labels:
            next_page = self._click_action_label(page, label)
            if next_page is not None:
                return {"attempted": True, "status": "clicked_action", "action": label, "page": next_page}
        if allow_single_account_tile:
            next_page = self._click_single_account_tile(page)
            if next_page is not None:
                return {
                    "attempted": True,
                    "status": "clicked_single_account_tile",
                    "action": "single remembered account tile",
                    "page": next_page,
                }
        return {"attempted": False, "status": "no_safe_action_found", "page": page}

    def _discovery_payload(
        self,
        page,
        items: list[dict[str, Any]],
        *,
        ok: bool,
        url: str,
        error: str = "",
        state: str = "",
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "ok": ok,
            "url": str(page.url or url),
            "title": str(page.title() or ""),
            "items": items if isinstance(items, list) else [],
        }
        if error:
            payload["error"] = error
        if state:
            payload["state"] = state
        if isinstance(detail, dict) and detail:
            payload["detail"] = detail
        return payload

    def _verify_job_detail_pages(
        self,
        page,
        items: list[dict[str, Any]],
        *,
        signal_config: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        sample: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url or url in sample:
                continue
            sample.append(url)
            if len(sample) >= 3:
                break
        if not sample:
            return {"ok": True, "checked": 0, "matched": 0}

        matched = 0
        checked = 0
        for detail_url in sample:
            detail_page = page.context.new_page()
            try:
                self._goto_with_retry(detail_page, detail_url)
                checked += 1
                if self._detail_page_matches(detail_page, signal_config=signal_config):
                    matched += 1
            except Exception:
                checked += 1
            finally:
                try:
                    detail_page.close()
                except Exception:
                    pass
        required = min(2, checked)
        return {"ok": matched >= required, "checked": checked, "matched": matched, "required": required}

    def _discover_jobs_on_page(self, page, url: str, max_items: int = 20) -> dict[str, Any]:
        try:
            self._goto_with_retry(page, url)
            items = self._extract_job_cards(page, max_items)
            if not items:
                page.reload(wait_until="domcontentloaded", timeout=self.timeout_ms)
                page.wait_for_timeout(1200)
                items = self._extract_job_cards(page, max_items)
            return {
                "ok": True,
                "url": page.url,
                "title": page.title(),
                "items": items if isinstance(items, list) else [],
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc), "url": url, "items": []}

    def _discover_jobs_guided_on_page(
        self,
        page,
        url: str,
        guidance_text: str = "",
        signal_config: dict[str, list[str]] | None = None,
        auto_login_config: dict[str, Any] | None = None,
        max_items: int = 20,
    ) -> dict[str, Any]:
        try:
            self._goto_with_retry(page, url)
            labels = self._ordered_navigation_labels(guidance_text)

            for step in range(len(labels) + 1):
                items = self._extract_job_cards(page, max_items, signal_config=signal_config)
                auth_state = self._auth_state(page, signal_config=signal_config)
                channel_state = self._channel_state(page, signal_config=signal_config, items=items)
                if items and channel_state["negative_hits"] == 0 and not auth_state["need_auth"]:
                    verification = self._verify_job_detail_pages(page, items, signal_config=signal_config)
                    if verification.get("ok"):
                        return self._discovery_payload(
                            page,
                            items,
                            ok=True,
                            url=url,
                            state="verified",
                            detail={"detail_verification": verification},
                        )
                    return self._discovery_payload(
                        page,
                        items,
                        ok=False,
                        url=url,
                        error="detail_verification_failed",
                        state="detail_verification_failed",
                            detail={"detail_verification": verification},
                        )
                if auth_state["need_auth"] or self._manual_takeover_required(
                    page,
                    [str(row).strip() for row in (auto_login_config or {}).get("manual_takeover_signals", []) if str(row).strip()],
                ):
                    return self._discovery_payload(
                        page,
                        items,
                        ok=False,
                        url=url,
                        error="login_required",
                        state="need_auth",
                    )
                if step >= len(labels):
                    break
                next_page = self._click_navigation_label(page, labels[step])
                if next_page is not None:
                    page = next_page

            items = self._extract_job_cards(page, max_items, signal_config=signal_config)
            auth_state = self._auth_state(page, signal_config=signal_config)
            if auth_state["need_auth"]:
                return self._discovery_payload(page, items, ok=False, url=url, error="login_required", state="need_auth")
            channel_state = self._channel_state(page, signal_config=signal_config, items=items)
            if channel_state["channel_ready"] and not items:
                return self._discovery_payload(page, items, ok=True, url=url, state="verified")
            return self._discovery_payload(page, items, ok=False, url=url, error="job_channel_not_found", state="channel_not_found")
        except Exception as exc:
            return {"ok": False, "error": str(exc), "url": url, "items": []}

    def _has_submit_confirmation(self, text: str) -> bool:
        lowered = str(text or "").lower()
        return any(
            marker in lowered
            for marker in (
                "application submitted",
                "application received",
                "successfully applied",
                "submitted successfully",
                "thank you for applying",
            )
        )

    def _quick_apply_on_page(self, page, url: str) -> dict[str, Any]:
        clicked = False
        selectors = [
            "button:has-text('Apply')",
            "a:has-text('Apply')",
            "button:has-text('申请')",
            "a:has-text('申请')",
            "button:has-text('Submit application')",
            "button:has-text('Submit')",
            "button[type='submit']",
        ]
        try:
            self._goto_with_retry(page, url)
            body_before = self._body_text(page)
            before_signals = self._infer_job_signals(body_before)
            if before_signals.get("apply_state") == "View Application":
                return {
                    "ok": True,
                    "url_before": url,
                    "url_after": page.url,
                    "clicked": False,
                    "submitted": False,
                    "status": "already_applied",
                    "apply_state": "View Application",
                }
            for sel in selectors:
                loc = page.locator(sel)
                if loc.count() > 0:
                    loc.first.click(timeout=2000)
                    clicked = True
                    break
            if not clicked:
                return {
                    "ok": False,
                    "url_before": url,
                    "url_after": page.url,
                    "clicked": False,
                    "submitted": False,
                    "status": "apply_control_missing",
                }
            page.wait_for_timeout(1500)
            body_after = self._body_text(page)
            after_signals = self._infer_job_signals(body_after)
            submit_confirmed = self._has_submit_confirmation(body_after) or after_signals.get("apply_state") == "View Application"
            return {
                "ok": True,
                "url_before": url,
                "url_after": page.url,
                "clicked": clicked,
                "submitted": bool(submit_confirmed),
                "submit_confirmed": bool(submit_confirmed),
                "status": "submitted" if submit_confirmed else "submit_confirmation_missing",
                "apply_state": after_signals.get("apply_state") or before_signals.get("apply_state") or "",
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc), "url": url, "clicked": clicked, "status": "apply_failed"}

    def search_google(self, query: str, max_items: int = 10) -> dict[str, Any]:
        try:
            manager, browser, page = self._launch_browser()
        except Exception as exc:
            return {
                "ok": False,
                "error": self._launch_error(exc),
                "query": query,
                "items": [],
            }

        target = "https://www.google.com/search?q=" + quote_plus(query)
        try:
            page.goto(target, wait_until="domcontentloaded", timeout=self.timeout_ms)
            items = page.evaluate(
                """
                (maxItems) => {
                  const out = [];
                  const cards = Array.from(document.querySelectorAll('div.g'));
                  for (const card of cards) {
                    if (out.length >= maxItems) break;
                    const a = card.querySelector('a[href]');
                    const h3 = card.querySelector('h3');
                    if (!a || !h3) continue;
                    const url = (a.href || '').trim();
                    const title = (h3.innerText || '').trim();
                    if (!url || !title) continue;
                    const sn = card.querySelector('div.VwiC3b, span.aCOpRe, div[data-sncf]');
                    const snippet = sn ? (sn.innerText || '').trim() : '';
                    out.push({ title, url, snippet });
                  }
                  return out;
                }
                """,
                max_items,
            )
            return {"ok": True, "query": query, "url": page.url, "items": items if isinstance(items, list) else []}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "query": query, "items": []}
        finally:
            self._close_browser(manager, browser, force=True)

    def discover_jobs(self, url: str, max_items: int = 20) -> dict[str, Any]:
        try:
            manager, browser, page = self._launch_browser()
        except Exception as exc:
            return {
                "ok": False,
                "error": self._launch_error(exc),
                "url": url,
                "items": [],
            }

        try:
            return self._discover_jobs_on_page(page, url, max_items=max_items)
        finally:
            self._close_browser(manager, browser, force=True)

    def discover_jobs_guided(
        self,
        url: str,
        guidance_text: str = "",
        signal_config: dict[str, list[str]] | None = None,
        auto_login_config: dict[str, Any] | None = None,
        max_items: int = 20,
    ) -> dict[str, Any]:
        try:
            manager, browser, page = self._launch_browser()
        except Exception as exc:
            return {
                "ok": False,
                "error": self._launch_error(exc),
                "url": url,
                "items": [],
            }

        try:
            return self._discover_jobs_guided_on_page(
                page,
                url,
                guidance_text=guidance_text,
                signal_config=signal_config,
                auto_login_config=auto_login_config,
                max_items=max_items,
            )
        finally:
            self._close_browser(manager, browser, force=True)

    def discover_jobs_with_profile(self, profile_dir: str, url: str, max_items: int = 20) -> dict[str, Any]:
        try:
            manager, context, _, _ = self._open_profile_context(profile_dir)
            page = context.pages[0] if context.pages else context.new_page()
        except PlaywrightSessionOpenError as exc:
            return {
                "ok": False,
                "status": exc.status,
                "error": exc.message,
                "url": url,
                "items": [],
                "detail": exc.detail,
            }
        try:
            return self._discover_jobs_on_page(page, url, max_items=max_items)
        finally:
            self._close_browser(manager, context, force=True)

    def discover_jobs_with_profile_guided(
        self,
        profile_dir: str,
        url: str,
        guidance_text: str = "",
        signal_config: dict[str, list[str]] | None = None,
        auto_login_config: dict[str, Any] | None = None,
        max_items: int = 20,
    ) -> dict[str, Any]:
        try:
            manager, context, _, _ = self._open_profile_context(profile_dir)
            page = context.pages[0] if context.pages else context.new_page()
        except PlaywrightSessionOpenError as exc:
            return {
                "ok": False,
                "status": exc.status,
                "error": exc.message,
                "url": url,
                "items": [],
                "detail": exc.detail,
            }
        try:
            return self._discover_jobs_guided_on_page(
                page,
                url,
                guidance_text=guidance_text,
                signal_config=signal_config,
                auto_login_config=auto_login_config,
                max_items=max_items,
            )
        finally:
            self._close_browser(manager, context, force=True)

    def quick_apply(self, url: str) -> dict[str, Any]:
        try:
            manager, browser, page = self._launch_browser()
        except Exception as exc:
            return {"ok": False, "error": self._launch_error(exc), "url": url}

        try:
            return self._quick_apply_on_page(page, url)
        finally:
            self._close_browser(manager, browser, force=True)

    def quick_apply_with_profile(self, profile_dir: str, url: str) -> dict[str, Any]:
        try:
            manager, context, _, _ = self._open_profile_context(profile_dir)
            page = context.pages[0] if context.pages else context.new_page()
        except PlaywrightSessionOpenError as exc:
            return {
                "ok": False,
                "status": exc.status,
                "error": exc.message,
                "url": url,
                "detail": exc.detail,
            }
        try:
            return self._quick_apply_on_page(page, url)
        finally:
            self._close_browser(manager, context, force=True)

    def inspect_authenticated_with_profile(
        self,
        profile_dir: str,
        url: str,
        *,
        signal_config: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        try:
            manager, context, _, _ = self._open_profile_context(profile_dir)
            page = context.pages[0] if context.pages else context.new_page()
        except PlaywrightSessionOpenError as exc:
            return {
                "ok": False,
                "status": exc.status if exc.status == "profile_locked" else "validate_failed",
                "error": exc.message,
                "detail": exc.detail,
            }

        try:
            return self._inspect_authenticated_on_page(page, url, signal_config=signal_config)
        finally:
            self._close_browser(manager, context, force=True)

    def prepare_session_with_profile(
        self,
        profile_dir: str,
        url: str,
        *,
        signal_config: dict[str, list[str]] | None = None,
        auto_login_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            manager, context, _, _ = self._open_profile_context(profile_dir)
            page = context.pages[0] if context.pages else context.new_page()
        except PlaywrightSessionOpenError as exc:
            return {
                "ok": False,
                "status": exc.status if exc.status == "profile_locked" else "prepare_failed",
                "error": exc.message,
                "detail": exc.detail,
            }

        try:
            return self._prepare_session_on_page(
                page,
                url,
                signal_config=signal_config,
                auto_login_config=auto_login_config,
            )
        finally:
            self._close_browser(manager, context, force=True)

    def validate_session(self, profile_dir: str, url: str) -> dict[str, Any]:
        try:
            result = self.inspect_authenticated_with_profile(profile_dir, url)
        except Exception:
            return {"ok": False, "status": "unavailable", "error": "playwright is not installed"}
        status = "ready" if result.get("ok") else str(result.get("status") or "login_required")
        if status == "authenticated":
            status = "ready"
        return {
            "ok": bool(result.get("ok")),
            "status": status,
            "url": result.get("url") or "",
            "title": result.get("title") or "",
            "error": result.get("error") or "",
        }
