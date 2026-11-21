"""Self-updater: checks a public GitHub repo for a newer VERSION and installs it.

Never touches: config.json, data/, .venv/, start.bat (start.bat is running while
we update, and Windows reads .bat files line by line).

Run standalone (StreamHub closed):  .venv\\Scripts\\python.exe server\\updater.py
"""
import asyncio
import io
import json
import logging
import subprocess
import sys
import zipfile
from pathlib import Path

import aiohttp

log = logging.getLogger("update")

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"
SOURCE_FILE = ROOT / "update.json"
PROTECTED_DIRS = {"data", ".venv", ".git"}
PROTECTED_FILES = {"config.json", "start.bat"}
RESTART_CODE = 42
CHECK_EVERY = 6 * 3600


def read_version():
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"


def parse(v):
    out = []
    for part in str(v).strip().lstrip("vV").split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out + [0] * (3 - len(out)))


def read_source():
    try:
        src = json.loads(SOURCE_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        src = {}
    repo = str(src.get("repo", "")).strip().strip("/")
    if repo.startswith("https://github.com/"):
        repo = repo[len("https://github.com/"):].strip("/")
    return repo, str(src.get("branch", "main")).strip() or "main"


class Updater:
    def __init__(self, hub=None):
        self.hub = hub
        self.current = read_version()
        self.latest = None
        self.busy = False
        self.error = None
        self.repo, self.branch = read_source()

    def snapshot(self):
        return {
            "current": self.current,
            "latest": self.latest,
            "available": bool(self.latest and parse(self.latest) > parse(self.current)),
            "configured": bool(self.repo),
            "busy": self.busy,
            "error": self.error,
        }

    def _publish(self):
        if self.hub:
            self.hub.publish("update", self.snapshot())

    async def _get(self, session, url, binary=False):
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as r:
            if r.status != 200:
                raise RuntimeError(f"GitHub answered {r.status} for {url.split('/')[-1]}. Is the repo public?")
            return await (r.read() if binary else r.text())

    async def check(self):
        if not self.repo:
            return self._publish()
        url = f"https://raw.githubusercontent.com/{self.repo}/{self.branch}/VERSION"
        try:
            async with aiohttp.ClientSession() as s:
                self.latest = (await self._get(s, url)).strip()
            self.error = None
            if parse(self.latest) > parse(self.current):
                log.info("update available: %s -> %s", self.current, self.latest)
        except Exception as e:  # noqa: BLE001
            self.error = f"Couldn't check for updates: {e}"
            log.info(self.error)
        self._publish()

    async def run(self):
        await asyncio.sleep(5)
        while True:
            await self.check()
            await asyncio.sleep(CHECK_EVERY)

    async def apply(self):
        """Download + install. Returns True if files changed (caller restarts)."""
        if not self.repo:
            raise RuntimeError("Updates aren't set up (update.json has no repo)")
        if self.busy:
            raise RuntimeError("Already updating")
        self.busy, self.error = True, None
        self._publish()
        try:
            url = f"https://github.com/{self.repo}/archive/refs/heads/{self.branch}.zip"
            async with aiohttp.ClientSession() as s:
                blob = await self._get(s, url, binary=True)
            changed, reqs_changed = await asyncio.get_running_loop().run_in_executor(
                None, install_zip, blob)
            if reqs_changed:
                await asyncio.get_running_loop().run_in_executor(None, pip_install)
            self.current = read_version()
            log.info("updated %d files, now version %s", changed, self.current)
            return changed > 0
        except Exception as e:  # noqa: BLE001
            self.error = f"Update failed: {e}"
            log.warning(self.error)
            raise
        finally:
            self.busy = False
            self._publish()


def install_zip(blob):
    """Copy files from a GitHub branch zip over ROOT. Returns (changed, requirements_changed)."""
    root = ROOT.resolve()
    changed, reqs_changed = 0, False
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        if not any(n.split("/", 1)[-1] == "server/main.py" for n in names):
            raise RuntimeError("That download doesn't look like StreamHub")
        for name in names:
            rel = name.split("/", 1)[1] if "/" in name else name
            parts = Path(rel).parts
            if not parts or parts[0] in PROTECTED_DIRS or rel in PROTECTED_FILES:
                continue
            dest = (root / rel).resolve()
            if root not in dest.parents:          # never write outside the folder
                continue
            data = z.read(name)
            if dest.exists() and dest.read_bytes() == data:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".part")
            tmp.write_bytes(data)
            tmp.replace(dest)
            changed += 1
            if rel == "requirements.txt":
                reqs_changed = True
    return changed, reqs_changed


def pip_install():
    subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q",
                    "-r", str(ROOT / "requirements.txt")], check=True)
    stamp = ROOT / ".venv" / "req.stamp"
    if stamp.parent.exists():
        stamp.write_bytes((ROOT / "requirements.txt").read_bytes())


if __name__ == "__main__":   # update.bat
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    u = Updater()
    if not u.repo:
        print("Updates aren't set up: update.json has no \"repo\".")
        sys.exit(1)
    asyncio.run(u.check())
    if u.error:
        print(u.error)
        sys.exit(1)
    if not u.snapshot()["available"]:
        print(f"You're up to date (version {u.current}).")
        sys.exit(0)
    print(f"Updating {u.current} -> {u.latest} ...")
    asyncio.run(u.apply())
    print(f"Done! Now on version {read_version()}.")
