from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

from datasource.errors import ProviderError


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCAL_PACKAGE_DIRS = [
    PROJECT_ROOT.parent / "local_packages",
    PROJECT_ROOT.parent / "packages",
    PROJECT_ROOT.parent / "vendor",
    PROJECT_ROOT.parent,
]

# 自动安装开关：默认关闭（生产环境禁止运行时安装依赖）。
# 设置 DATASOURCE_AUTO_INSTALL=true 才允许 pip 自动安装缺失的 provider 依赖。
_AUTO_INSTALL_ENV = "DATASOURCE_AUTO_INSTALL"


def _auto_install_enabled() -> bool:
    return os.getenv(_AUTO_INSTALL_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _default_search_dirs() -> list[Path]:
    seen: set[Path] = set()
    dirs: list[Path] = []

    env_value = os.environ.get("LOCAL_PACKAGE_SEARCH_DIRS", "")
    for raw in env_value.split(os.pathsep):
        raw = raw.strip()
        if not raw:
            continue
        candidate = Path(raw).expanduser()
        if candidate not in seen:
            seen.add(candidate)
            dirs.append(candidate)

    for candidate in LOCAL_PACKAGE_DIRS:
        if candidate not in seen:
            seen.add(candidate)
            dirs.append(candidate)

    for sibling in PROJECT_ROOT.parent.iterdir():
        if not sibling.is_dir():
            continue
        for candidate in (
            sibling,
            sibling / "dist",
            sibling / "packages",
            sibling / "vendor",
            sibling / "local_packages",
        ):
            if candidate not in seen:
                seen.add(candidate)
                dirs.append(candidate)
    return dirs


def find_local_package_file(package_name: str, search_dirs: list[Path] | None = None) -> Path | None:
    dirs = search_dirs or _default_search_dirs()
    normalized = package_name.replace("-", "_").lower()
    patterns = [
        f"{package_name}-*.whl",
        f"{normalized}-*.whl",
        f"{package_name}-*.tar.gz",
        f"{normalized}-*.tar.gz",
        f"{package_name}-*.zip",
        f"{normalized}-*.zip",
    ]
    matches: list[Path] = []
    for base in dirs:
        if not base.exists():
            continue
        for pattern in patterns:
            matches.extend(base.rglob(pattern))
    if not matches:
        return None
    matches = sorted(set(matches), key=lambda path: (path.suffix != ".whl", path.stat().st_mtime, path.name))
    return matches[-1]


def _pip_install(target: str) -> None:
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", target],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300,  # 避免 pip 网络挂起时永久阻塞数据请求路径
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"pip install 超时(300s): {target}") from exc
    except subprocess.CalledProcessError as exc:
        output = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(output or f"pip install 失败: {target}") from exc


def ensure_module_or_install(module_name: str, *, provider: str, package_name: str | None = None):
    """确保依赖可用：已装直接返回；未装时默认报清晰错误，显式开启自动安装才走 pip。"""
    package_name = package_name or module_name
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        missing_name = getattr(exc, "name", None)
        if missing_name and missing_name != module_name:
            raise
        if not missing_name and module_name not in str(exc):
            raise
        if not _auto_install_enabled():
            raise ProviderError(
                f"{package_name} 未安装。请先执行 pip install {package_name}"
                f"（或设置 {_AUTO_INSTALL_ENV}=true 允许自动安装）",
                provider=provider,
            ) from exc
        local_pkg = find_local_package_file(package_name)
        if local_pkg is not None:
            try:
                _pip_install(str(local_pkg))
                return importlib.import_module(module_name)
            except Exception as exc:  # pragma: no cover - install path only in integration env
                raise ProviderError(
                    f"{package_name} 本地安装失败: {exc}",
                    provider=provider,
                ) from exc
        try:
            _pip_install(package_name)
            return importlib.import_module(module_name)
        except Exception as exc:
            raise ProviderError(
                f"{package_name} 未安装且未找到本地安装包文件，自动安装失败: {exc}",
                provider=provider,
            ) from exc
