"""Generic ownership and reuse of local browser MCP runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading

from careereng.platform.web_control import PlaywrightMCPProcess, launch_playwright_mcp, reclaim_profile_processes
from careereng.utils import now_iso

from .browser_profile_owner import BrowserProfileOwnerError, BrowserProfileOwnerRegistry


@dataclass
class BrowserRuntimeLease:
    """A retained browser runtime and the profile lock that protects it."""

    site_key: str
    runtime: PlaywrightMCPProcess
    entry_url: str
    owner_lock_path: Path


class BrowserRuntimeRegistry:
    """Own browser runtime lifecycle without interpreting site workflow state.

    Callers provide the site key and persistent profile directory. The registry
    only starts, reuses, releases, and protects the underlying MCP runtime.
    It does not persist business state or decide which browser phase should run.
    """

    def __init__(
        self,
        *,
        runtime_root: Path | str,
        browser_name: str,
        headless: bool,
        executable_path: str = "",
        default_timeout_ms: int = 45000,
        profile_owners: BrowserProfileOwnerRegistry | None = None,
    ):
        self.runtime_root = Path(runtime_root).resolve()
        self.browser_name = str(browser_name or "chrome")
        self.headless = bool(headless)
        self.executable_path = str(executable_path or "").strip()
        self.default_timeout_ms = int(default_timeout_ms or 45000)
        self.profile_owners = profile_owners or BrowserProfileOwnerRegistry()
        self._lock = threading.RLock()
        self._active: dict[str, BrowserRuntimeLease] = {}

    def profile_lock_path(self, profile_dir: Path | str) -> Path:
        return self.profile_owners.lock_path(Path(profile_dir))

    def reserve(
        self,
        *,
        site_key: str,
        entry_url: str,
        profile_dir: Path | str,
        timeout_ms: int | None = None,
    ) -> tuple[BrowserRuntimeLease, bool]:
        """Return a retained runtime for one profile, creating it if needed."""
        normalized_site = str(site_key or "").strip()
        if not normalized_site:
            raise ValueError("site_key is required")
        resolved_profile = Path(profile_dir).resolve()
        effective_timeout_ms = int(timeout_ms or self.default_timeout_ms or 45000)
        with self._lock:
            current = self._active.get(normalized_site)
            if current is not None and current.runtime.is_running():
                current.entry_url = str(entry_url or current.entry_url)
                current.runtime.command_timeout_seconds = max(
                    float(getattr(current.runtime, "command_timeout_seconds", 0.0) or 0.0),
                    max(45.0, float(effective_timeout_ms) / 1000.0 + 30.0),
                )
                self.profile_owners.refresh(
                    site_key=normalized_site,
                    run_id=current.runtime.run_id,
                    profile_dir=current.runtime.profile_dir,
                    output_dir=current.runtime.output_dir,
                )
                return current, True
            if current is not None:
                self._active.pop(normalized_site, None)
                self._stop_lease(current)

            run_id = now_iso().replace(":", "").replace("-", "")
            output_dir = self.runtime_root / normalized_site / run_id
            owner_lock_path = self.profile_owners.acquire(
                site_key=normalized_site,
                run_id=run_id,
                profile_dir=resolved_profile,
                output_dir=output_dir,
            )
            try:
                runtime = launch_playwright_mcp(
                    site_key=normalized_site,
                    run_id=run_id,
                    browser_name=self.browser_name,
                    headless=self.headless,
                    profile_dir=resolved_profile,
                    output_dir=output_dir,
                    timeout_ms=effective_timeout_ms,
                    executable_path=self.executable_path,
                )
            except Exception as exc:
                self.profile_owners.release(profile_dir=resolved_profile, run_id=run_id)
                if "browser is already in use" in str(exc).lower():
                    raise BrowserProfileOwnerError(
                        f"browser_profile_in_use: site={normalized_site} profile={resolved_profile}"
                    ) from exc
                raise
            self.profile_owners.refresh(
                site_key=normalized_site,
                run_id=run_id,
                profile_dir=resolved_profile,
                output_dir=output_dir,
            )
            lease = BrowserRuntimeLease(
                site_key=normalized_site,
                runtime=runtime,
                entry_url=str(entry_url or ""),
                owner_lock_path=owner_lock_path,
            )
            self._active[normalized_site] = lease
            return lease, False

    def active(self, site_key: str) -> BrowserRuntimeLease:
        """Return one currently running lease or raise a lifecycle error."""
        normalized_site = str(site_key or "").strip()
        with self._lock:
            lease = self._active.get(normalized_site)
        if lease is None or not lease.runtime.is_running():
            raise RuntimeError(f"no active browser runtime for site={normalized_site}")
        return lease

    def release(self, site_key: str) -> BrowserRuntimeLease | None:
        normalized_site = str(site_key or "").strip()
        with self._lock:
            lease = self._active.pop(normalized_site, None)
        if lease is not None:
            self._stop_lease(lease)
        return lease

    def release_or_reclaim(self, *, site_key: str, profile_dir: Path | str) -> bool:
        """Release a live lease or recover children left by an earlier release.

        Profile ownership keeps this generic and site-isolated. A running foreign
        owner is never reclaimed by this process.
        """

        lease = self.release(site_key)
        if lease is not None:
            return True
        resolved_profile = Path(profile_dir).resolve()
        if not self.profile_owners.can_reclaim(profile_dir=resolved_profile):
            return False
        cleanup = reclaim_profile_processes(resolved_profile)
        lock_released = self.profile_owners.release_if_reclaimable(profile_dir=resolved_profile)
        return bool(cleanup.terminated_pids or lock_released)

    def release_all(self) -> list[BrowserRuntimeLease]:
        with self._lock:
            leases = list(self._active.values())
            self._active.clear()
        for lease in leases:
            self._stop_lease(lease)
        return leases

    def _stop_lease(self, lease: BrowserRuntimeLease) -> None:
        try:
            lease.runtime.stop()
        finally:
            # The MCP server normally closes Chrome. Reclaim any child that did
            # not exit with the stdio owner before releasing the profile lock.
            reclaim_profile_processes(lease.runtime.profile_dir)
            self.profile_owners.release(
                profile_dir=lease.runtime.profile_dir,
                run_id=lease.runtime.run_id,
            )
