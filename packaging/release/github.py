"""Small GitHub API adapter; authentication remains owned by gh."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from urllib.parse import quote

from .model import REPOSITORY, ReleaseError, file_hash, sha256


class APIError(ReleaseError):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class GitHub:
    def __init__(self, repository: str = REPOSITORY):
        self.repository = repository

    def api(self, path: str, *, method: str = "GET", data=None, binary: bool = False):
        endpoint = path if path.startswith("https://") else f"repos/{self.repository}/{path}"
        command = ["gh", "api", endpoint, "--method", method]
        if binary:
            command += ["-H", "Accept: application/octet-stream"]
        body = None
        if data is not None:
            command += ["--input", "-"]
            body = json.dumps(data).encode()
        result = subprocess.run(command, input=body, capture_output=True, check=False)
        if result.returncode:
            error = result.stderr.decode(errors="replace")
            match = re.search(r"HTTP (\d+)", error)
            raise APIError(f"GitHub {method} {path}: {error.strip()}", int(match[1]) if match else 0)
        if binary:
            return result.stdout
        return json.loads(result.stdout) if result.stdout.strip() else None

    def optional(self, path: str):
        try:
            return self.api(path)
        except APIError as exc:
            if exc.status == 404:
                return None
            raise

    def pages(self, path: str, key: str | None = None) -> list:
        items = []
        separator = "&" if "?" in path else "?"
        for page in range(1, 101):
            response = self.api(f"{path}{separator}per_page=100&page={page}")
            batch = response[key] if key else response
            items.extend(batch)
            if len(batch) < 100:
                return items
        raise ReleaseError("GitHub pagination exceeded supported limit")

    def release(self, tag: str):
        return self.optional("releases/tags/" + quote(tag, safe=""))

    def tag_sha(self, tag: str) -> str:
        obj = self.api("git/ref/tags/" + quote(tag, safe=""))["object"]
        for _ in range(10):
            if obj["type"] == "commit":
                return obj["sha"]
            if obj["type"] != "tag":
                break
            obj = self.api(f"git/tags/{obj['sha']}")["object"]
        raise ReleaseError("Tag does not resolve to a commit")

    def asset_bytes(self, asset: dict) -> bytes:
        data = self.api(f"releases/assets/{asset['id']}", binary=True)
        digest = asset.get("digest")
        if len(data) != asset["size"] or (digest and digest != "sha256:" + sha256(data)):
            raise ReleaseError(f"Release asset digest mismatch: {asset['name']}")
        return data

    def verify_asset(self, asset: dict, path: Path) -> None:
        if asset.get("state") != "uploaded" or asset["size"] != path.stat().st_size:
            raise ReleaseError(f"Remote asset incomplete: {path.name}")
        digest = asset.get("digest")
        actual = digest.removeprefix("sha256:") if digest else sha256(self.asset_bytes(asset))
        if actual != file_hash(path):
            raise ReleaseError(f"Remote asset conflict: {path.name}")

    def upload(self, tag: str, path: Path) -> None:
        # Deliberately no --clobber: retry decisions are made from remote hashes.
        subprocess.run(["gh", "release", "upload", tag, str(path), "--repo", self.repository], check=True)


def named_asset(release: dict | None, name: str) -> dict | None:
    matches = [a for a in (release or {}).get("assets", []) if a["name"] == name]
    if len(matches) > 1:
        raise ReleaseError(f"Duplicate release asset: {name}")
    return matches[0] if matches else None
