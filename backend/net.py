"""HTTPS helpers. TLS is always verified.

PyInstaller-built Python (which Decky ships) sometimes cannot find the system
CA store, so we point it at the bundle explicitly and fall back to the system
``curl``, which uses the OS store. There is no unverified fallback.
"""
from __future__ import annotations

import hashlib
import json
import os
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import fsutil
from .config import PLUGIN_PACKAGE

USER_AGENT = f"Mozilla/5.0 {PLUGIN_PACKAGE}"
CA_BUNDLES = [
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/ca-certificates/extracted/tls-ca-bundle.pem",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/cert.pem",
]
MAX_TEXT_BYTES = 16 * 1024 * 1024


class NetError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    for env in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        value = os.environ.get(env)
        if value and os.path.isfile(value):
            return ssl.create_default_context(cafile=value)
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    for bundle in CA_BUNDLES:
        if os.path.isfile(bundle):
            return ssl.create_default_context(cafile=bundle)
    return ssl.create_default_context()


def clean_env() -> dict[str, str]:
    """Environment for host binaries; Decky's bundled libraries break them."""
    env = dict(os.environ)
    for key in ("LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONHOME", "PYTHONPATH"):
        env.pop(key, None)
    return env


def _request(url: str, headers: dict[str, str] | None) -> urllib.request.Request:
    if not url.startswith("https://"):
        raise NetError(f"Refusing non-HTTPS URL: {url}")
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})


def _curl(url: str, output: Path | None, timeout: int, headers: dict[str, str] | None) -> bytes:
    command = ["curl", "-fsSL", "--proto", "=https", "--proto-redir", "=https", "--max-time", str(timeout), "-A", USER_AGENT]
    for key, value in (headers or {}).items():
        command += ["-H", f"{key}: {value}"]
    if output is not None:
        command += ["-o", str(output)]
    command.append(url)
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout + 5, env=clean_env())
    except (OSError, subprocess.TimeoutExpired) as error:
        raise NetError(f"curl failed for {url}: {error}") from error
    if result.returncode != 0:
        raise NetError(f"curl failed for {url}: {result.stderr.decode('utf-8', 'ignore').strip()[-200:]}")
    return result.stdout


def fetch_bytes(url: str, *, timeout: int = 20, headers: dict[str, str] | None = None, max_bytes: int = MAX_TEXT_BYTES) -> bytes:
    request = _request(url, headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl_context()) as response:
            data = response.read(max_bytes + 1)
    except (urllib.error.URLError, OSError, ssl.SSLError) as error:
        if not _looks_like_tls_store_problem(error):
            raise NetError(f"Request failed for {url}: {error}") from error
        data = _curl(url, None, timeout, headers)
    if len(data) > max_bytes:
        raise NetError(f"Response from {url} is larger than {max_bytes} bytes")
    return data


def fetch_text(url: str, **kwargs: Any) -> str:
    return fetch_bytes(url, **kwargs).decode("utf-8", "replace")


def fetch_json(url: str, **kwargs: Any) -> Any:
    headers = {"Accept": "application/json", **(kwargs.pop("headers", None) or {})}
    try:
        return json.loads(fetch_bytes(url, headers=headers, **kwargs))
    except ValueError as error:
        raise NetError(f"Invalid JSON from {url}: {error}") from error


def download(url: str, target: Path, *, timeout: int = 180, min_size: int = 1, sha256: str = "") -> Path:
    """Download to ``target`` atomically, optionally checking a SHA-256 digest."""
    target = Path(target)
    fsutil.makedirs(target.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".part", dir=str(target.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        try:
            with urllib.request.urlopen(_request(url, None), timeout=timeout, context=ssl_context()) as response, open(tmp, "wb") as handle:
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    handle.write(chunk)
        except (urllib.error.URLError, OSError, ssl.SSLError) as error:
            if not _looks_like_tls_store_problem(error):
                raise NetError(f"Download failed for {url}: {error}") from error
            _curl(url, tmp, timeout, None)
        size = tmp.stat().st_size
        if size < min_size:
            raise NetError(f"Download from {url} is too small ({size} bytes)")
        if sha256:
            actual = file_sha256(tmp)
            if actual.lower() != sha256.lower():
                raise NetError(f"Checksum mismatch for {url}: expected {sha256}, got {actual}")
        os.chmod(tmp, 0o644)
        fsutil.chown(tmp)
        os.replace(tmp, target)
        return target
    finally:
        if tmp.exists():
            tmp.unlink()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _looks_like_tls_store_problem(error: BaseException) -> bool:
    """Only fall back to curl when Python itself could not verify (missing CA store).

    curl still verifies against the OS store, so this never weakens TLS.
    """
    reason = getattr(error, "reason", error)
    return isinstance(reason, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(error)
