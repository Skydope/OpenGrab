#!/usr/bin/env python3
"""
OpenGrab Installer — Interactive TUI setup wizard.

Three installation modes:
  1. Docker      — docker compose build + up
  2. Bare Metal  — pip install + optional systemd service
  3. Desktop     — PyInstaller build + optional AppImage/DMG

Uses Rich for the terminal UI. Auto-installs Rich if missing.

Usage:
    python install.py          # guided TUI wizard
    python install.py --help   # see options
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Bootstrap: ensure Rich is available
# --------------------------------------------------------------------------- #
_REPO_URL = "https://github.com/Skydope/OpenGrab.git"


def _maybe_install_rich() -> None:
    """If Rich isn't importable, pip install it. Exit on failure."""
    try:
        import rich  # noqa: F401
        return
    except ImportError:
        pass
    print("[opengrab] Installing Rich (one-time, ~2MB)...")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--user", "rich>=13"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        sys.exit(
            "[opengrab] Failed to install Rich.\n"
            "Run: pip install rich>=13\n"
            "Then retry: python install.py"
        )


_maybe_install_rich()

# Now import — guaranteed available
from rich import box  # noqa: E402
from rich.align import Align  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.live import Live  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.progress import Progress, SpinnerColumn, TextColumn  # noqa: E402
from rich.prompt import Confirm, IntPrompt, Prompt  # noqa: E402
from rich.table import Table  # noqa: E402

console = Console()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _check_cmd(name: str) -> bool:
    """True if `name` is on PATH."""
    return shutil.which(name) is not None


def _run(
    cmd: list[str],
    desc: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 300,
) -> tuple[int, str]:
    """Run a command with a spinner. Returns (returncode, captured_output)."""
    output: list[str] = []
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(cwd) if cwd else None,
            env=env or os.environ.copy(),
        )
    except FileNotFoundError:
        return 127, f"command not found: {cmd[0]}"

    spinner = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    )
    with spinner:
        task_id = spinner.add_task(desc, total=None)
        try:
            stdout, _ = proc.communicate(timeout=timeout)
            output.append(stdout)
            if proc.returncode == 0:
                spinner.update(task_id, description=f"[green]✓[/] {desc}")
            else:
                spinner.update(task_id, description=f"[red]✗[/] {desc}")
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            spinner.update(task_id, description=f"[red]✗[/] {desc} (timeout)")
            return 124, "timeout"

    return proc.returncode, "".join(output)


def _run_live(
    cmd: list[str],
    desc: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 600,
) -> tuple[int, str]:
    """Run a command showing live-scrolling output in a Panel (for long builds)."""
    output_lines: list[str] = []
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(cwd) if cwd else None,
            env=env or os.environ.copy(),
        )
    except FileNotFoundError:
        return 127, f"command not found: {cmd[0]}"

    panel = Panel("", title=f"[bold]{desc}[/]", border_style="cyan")
    with Live(panel, console=console, refresh_per_second=8, transient=True) as live:
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                output_lines.append(line.rstrip())
                if len(output_lines) > 12:
                    output_lines = output_lines[-12:]
                live.update(Panel(
                    "\n".join(output_lines),
                    title=f"[bold]{desc}[/]",
                    border_style="cyan",
                ))
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return 124, "timeout"

    if proc.returncode == 0:
        console.print(f"  [green]✓[/] {desc}")
    else:
        console.print(f"  [red]✗[/] {desc}")

    return proc.returncode, "\n".join(output_lines)


def _panel(title: str, content: str, style: str = "cyan") -> Panel:
    return Panel(
        Align.center(content),
        title=f"[bold {style}]{title}[/]",
        border_style=style,
        padding=(1, 2),
    )


def _status_table(rows: list[tuple[bool, str, str]]) -> Table:
    """Rows: (ok, label, detail)."""
    tbl = Table(box=box.SIMPLE_HEAVY, show_header=False, padding=(0, 2))
    tbl.add_column("", width=3)
    tbl.add_column("", ratio=1)
    for ok, label, detail in rows:
        icon = "[green]✓[/]" if ok else "[red]✗[/]"
        detail_str = f" [dim]({detail})[/]" if detail else ""
        tbl.add_row(icon, f"{label}{detail_str}")
    return tbl


def _is_in_repo() -> Path | None:
    """Return Path to repo root if CWD is inside OpenGrab repo, else None."""
    candidate = Path.cwd()
    for _ in range(5):
        if (candidate / "pyproject.toml").exists():
            try:
                import tomllib
            except ImportError:
                import tomli as tomllib  # type: ignore[import-not-found,no-redef]
            with open(candidate / "pyproject.toml", "rb") as f:
                data = tomllib.load(f)
            if data.get("project", {}).get("name") == "opengrab":
                return candidate
        candidate = candidate.parent
    return None


def _clone_repo(target: Path) -> bool:
    console.print("[dim]Cloning OpenGrab repository...[/]")
    rc, out = _run(
        ["git", "clone", "--depth", "1", _REPO_URL, str(target)],
        "Cloning repository",
    )
    if rc != 0:
        console.print(_panel("Error", f"Git clone failed:\n{out}", "red"))
        return False
    return True


def _check_python() -> bool:
    v = sys.version_info
    if v >= (3, 12):
        return True
    console.print(_panel(
        "Python version",
        f"Python 3.12+ required. Detected {v.major}.{v.minor}.\n"
        "Install Python 3.12+ and try again.",
        "red",
    ))
    return False


# --------------------------------------------------------------------------- #
# Config wizard (shared by Docker + Bare Metal)
# --------------------------------------------------------------------------- #
def _configure_env(defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    """Interactive config prompts. Returns dict of env keys → values."""
    d = defaults or {}
    config: dict[str, Any] = {}

    console.print()
    console.print(Panel(
        "Configure your OpenGrab installation.\n"
        "Press Enter to accept defaults shown in brackets.",
        title="[bold]Configuration[/]",
        border_style="magenta",
    ))

    # Token
    if Confirm.ask(
        "Set a custom access token?", default=False,
    ):
        config["OPENGRAB_TOKEN"] = Prompt.ask(
            "Token", default=secrets.token_urlsafe(16)
        )
    else:
        config["OPENGRAB_TOKEN"] = secrets.token_urlsafe(16)
        console.print(
            f"  [dim]Auto-generated token:[/] [yellow]{config['OPENGRAB_TOKEN']}[/]"
        )

    # Port
    config["OPENGRAB_PORT"] = str(IntPrompt.ask(
        "HTTP port", default=d.get("port", 8800),
    ))

    # Download directory
    default_dir = d.get("download_dir", str(Path.home() / "Downloads" / "OpenGrab"))
    config["OPENGRAB_DIR"] = Prompt.ask(
        "Download directory", default=default_dir,
    )

    # Max concurrent jobs
    config["OPENGRAB_MAX_JOBS"] = str(IntPrompt.ask(
        "Max concurrent downloads", default=d.get("max_jobs", 2),
    ))

    # Disk budget
    if Confirm.ask("Set a total disk budget?", default=False):
        config["OPENGRAB_MAX_TOTAL_MB"] = str(IntPrompt.ask(
            "Max total storage (MB)", default=0,
        ))
    else:
        config["OPENGRAB_MAX_TOTAL_MB"] = "0"

    # Auto-update yt-dlp
    if Confirm.ask(
        "Auto-update yt-dlp on start? (supply-chain risk)",
        default=False,
    ):
        config["OPENGRAB_AUTOUPDATE"] = "1"
    else:
        config["OPENGRAB_AUTOUPDATE"] = "0"

    return config


def _write_env_file(repo: Path, config: dict[str, Any]) -> None:
    env_path = repo / ".env"
    backup = None
    if env_path.exists():
        backup = env_path.parent / ".env.bak"
        shutil.copy2(env_path, backup)

    lines: list[str] = [
        "# OpenGrab — environment configuration",
        f"# Generated by install.py at {__import__('datetime').datetime.now()}",
        "",
    ]
    for key, val in config.items():
        lines.append(f"{key}={val}")
    lines.append("")

    env_path.write_text("\n".join(lines), encoding="utf-8")
    console.print(f"  [green]✓[/] Config written to [bold]{env_path}[/]")
    if backup:
        console.print(f"  [dim]Previous .env backed up to {backup}[/]")


# --------------------------------------------------------------------------- #
# Mode 1: Docker
# --------------------------------------------------------------------------- #
def _mode_docker() -> None:
    console.print()
    console.print(_panel("Docker Mode", "Install OpenGrab via Docker Compose"))

    # ── Prerequisites ──────────────────────────────────────────────────
    prereqs: list[tuple[bool, str, str]] = []
    docker_ok = _check_cmd("docker")
    prereqs.append((docker_ok, "Docker", "found" if docker_ok else "not found"))
    compose_ok = _check_cmd("docker-compose") or (
        _check_cmd("docker") and "compose" in " ".join(
            subprocess.run(
                ["docker", "help"], capture_output=True, text=True,
            ).stdout.split()
        )
    )
    prereqs.append((compose_ok, "Docker Compose", "found" if compose_ok else "not found"))
    git_ok = _check_cmd("git")
    prereqs.append((git_ok, "Git", "found" if git_ok else "not found"))

    console.print(_status_table(prereqs))

    if not docker_ok:
        console.print(_panel("Error", "Docker is not installed.\nVisit https://docs.docker.com/engine/install/", "red"))
        return
    if not compose_ok:
        console.print(_panel("Error", "Docker Compose is not available.", "red"))
        return
    if not git_ok:
        console.print(_panel("Error", "Git is required to clone the repository.", "red"))
        return

    # ── Repo ───────────────────────────────────────────────────────────
    existing = _is_in_repo()
    if existing:
        console.print(f"  [green]✓[/] Found OpenGrab repo at [bold]{existing}[/]")
        repo = existing
    else:
        dest = Path.cwd() / "opengrab"
        if not _clone_repo(dest):
            return
        repo = dest

    # ── Config ─────────────────────────────────────────────────────────
    config = _configure_env()
    _write_env_file(repo, config)

    # ── Build & start ──────────────────────────────────────────────────
    console.print()
    console.print("[bold]Building and starting OpenGrab...[/]")

    rc, _out = _run_live(
        ["docker", "compose", "up", "-d", "--build"],
        "docker compose up",
        cwd=repo,
        timeout=600,
    )
    if rc != 0:
        console.print(_panel(
            "Build failed",
            f"Docker Compose exited with code {rc}.\n"
            f"Check the output above or run 'docker compose up' manually.",
            "red",
        ))
        return

    # ── Success ────────────────────────────────────────────────────────
    port = config.get("OPENGRAB_PORT", "8800")
    token = config.get("OPENGRAB_TOKEN", "")
    url = f"http://localhost:{port}"

    console.print()
    console.print(_panel(
        "Installation Complete",
        f"OpenGrab is running!\n\n"
        f"URL:   [bold cyan]{url}[/]\n"
        f"Token: [yellow]{token}[/]\n\n"
        f"Save the token — you'll need it to log in.",
        "green",
    ))


# --------------------------------------------------------------------------- #
# Mode 2: Bare Metal
# --------------------------------------------------------------------------- #
def _mode_baremetal() -> None:
    console.print()
    console.print(_panel("Bare Metal Mode", "Install OpenGrab with pip + systemd (optional)"))

    # ── Prerequisites ──────────────────────────────────────────────────
    prereqs: list[tuple[bool, str, str]] = []
    py_ok = _check_python()
    prereqs.append((py_ok, "Python 3.12+", f"{sys.version_info.major}.{sys.version_info.minor}"))
    pip_ok = _check_cmd("pip3") or _check_cmd("pip")
    prereqs.append((pip_ok, "pip", "found" if pip_ok else "not found"))
    ffmpeg_ok = _check_cmd("ffmpeg")
    prereqs.append((ffmpeg_ok, "ffmpeg", "found" if ffmpeg_ok else "not found"))
    git_ok = _check_cmd("git")
    prereqs.append((git_ok, "Git", "found" if git_ok else "not found"))

    console.print(_status_table(prereqs))

    if not py_ok or not pip_ok:
        console.print(_panel("Error", "Python 3.12+ and pip are required.", "red"))
        return
    if not git_ok:
        console.print(_panel("Error", "Git is required to clone the repository.", "red"))
        return
    if not ffmpeg_ok:
        console.print()
        console.print("[yellow]ffmpeg not found.[/] It is required for video processing.")
        if Confirm.ask("Attempt to install ffmpeg via system package manager?", default=True):
            _install_system_package("ffmpeg")
            ffmpeg_ok = _check_cmd("ffmpeg")
            if not ffmpeg_ok:
                console.print("[red]Could not install ffmpeg.[/] Install it manually and retry.")
                return
        else:
            console.print("[dim]Skipping ffmpeg install. Downloads may fail during muxing.[/]")

    # ── Repo ───────────────────────────────────────────────────────────
    existing = _is_in_repo()
    if existing:
        console.print(f"  [green]✓[/] Found OpenGrab repo at [bold]{existing}[/]")
        repo = existing
    else:
        dest = Path.cwd() / "opengrab"
        if not _clone_repo(dest):
            return
        repo = dest

    # ── Venv (optional) ────────────────────────────────────────────────
    use_venv = Confirm.ask(
        "Create a Python virtual environment?",
        default=True,
    )
    venv_dir: Path | None = None
    if use_venv:
        venv_dir = repo / ".venv"
        if venv_dir.exists():
            overwrite = Confirm.ask(f"Virtual env already exists at {venv_dir}. Recreate?", default=False)
            if overwrite:
                shutil.rmtree(venv_dir, ignore_errors=True)
        if not venv_dir.exists():
            rc, out = _run(
                [sys.executable, "-m", "venv", str(venv_dir)],
                "Creating virtual environment",
                cwd=repo,
            )
            if rc != 0:
                console.print(_panel("Error", f"venv creation failed:\n{out}", "red"))
                return
        console.print(f"  [green]✓[/] Virtual env at [bold]{venv_dir}[/]")

    # ── Pip install ────────────────────────────────────────────────────
    pip_cmd = [str(venv_dir / "bin" / "pip")] if venv_dir else [sys.executable, "-m", "pip"]
    pip_cmd += ["install", "-e", "."]
    rc, out = _run(pip_cmd, "Installing OpenGrab (pip install -e .)", cwd=repo)
    if rc != 0:
        console.print(_panel("Error", f"pip install failed:\n{out[:800]}", "red"))
        return

    # ── Config ─────────────────────────────────────────────────────────
    config = _configure_env()
    _write_env_file(repo, config)

    # ── Systemd service (optional, Linux only) ─────────────────────────
    if sys.platform == "linux" and Confirm.ask(
        "Install as a systemd service (auto-start on boot)?",
        default=False,
    ):
        _install_systemd(repo, venv_dir, config)

    # ── Success ────────────────────────────────────────────────────────
    port = config.get("OPENGRAB_PORT", "8800")
    token = config.get("OPENGRAB_TOKEN", "")
    python_exe = str(venv_dir / "bin" / "python") if venv_dir else sys.executable
    console.print()
    console.print(_panel(
        "Installation Complete",
        f"OpenGrab is ready!\n\n"
        f"Start it with:\n"
        f"  [bold]cd {repo} && {python_exe} app.py[/]\n\n"
        f"URL:   [bold cyan]http://localhost:{port}[/]\n"
        f"Token: [yellow]{token}[/]",
        "green",
    ))


def _install_systemd(repo: Path, venv_dir: Path | None, config: dict[str, Any]) -> None:
    python_exe = str(venv_dir / "bin" / "python") if venv_dir else sys.executable
    env_lines = "\n".join(
        f"Environment={k}={v}" for k, v in config.items()
    )
    unit = textwrap.dedent(f"""\
    [Unit]
    Description=OpenGrab — self-hosted video downloader
    After=network.target

    [Service]
    Type=simple
    WorkingDirectory={repo}
    ExecStart={python_exe} app.py
    Restart=on-failure
    RestartSec=10
    {env_lines}

    [Install]
    WantedBy=multi-user.target
    """)
    unit_path = Path.home() / ".config" / "systemd" / "user" / "opengrab.service"
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(unit)
    console.print(f"  [green]✓[/] Unit written to [bold]{unit_path}[/]")
    console.print()
    console.print(
        "  [dim]To enable and start:[/]\n"
        "  [bold]systemctl --user enable --now opengrab.service[/]\n"
        "  [dim]To enable lingering (survive logout):[/]\n"
        "  [bold]loginctl enable-linger[/]"
    )


def _install_system_package(name: str) -> None:
    if sys.platform != "linux":
        return
    for installer in ("apt", "dnf", "pacman", "zypper"):
        if _check_cmd(installer):
            cmd = [installer, "install", "-y", name]
            if installer == "pacman":
                cmd = ["sudo", "pacman", "-S", "--noconfirm", name]
            else:
                cmd = ["sudo", installer, "install", "-y", name]
            _run_live(cmd, f"Installing {name} via {installer}")
            return
    console.print(f"  [yellow]Could not install {name} automatically.[/]")


# --------------------------------------------------------------------------- #
# Mode 3: Desktop Binary
# --------------------------------------------------------------------------- #
def _mode_desktop() -> None:
    console.print()
    console.print(_panel("Desktop Binary Mode", "Build a native desktop application with PyInstaller"))

    # ── Prerequisites ──────────────────────────────────────────────────
    prereqs: list[tuple[bool, str, str]] = []
    py_ok = _check_python()
    prereqs.append((py_ok, "Python 3.12+", f"{sys.version_info.major}.{sys.version_info.minor}"))
    pip_ok = _check_cmd("pip3") or _check_cmd("pip")
    prereqs.append((pip_ok, "pip", "found" if pip_ok else "not found"))
    git_ok = _check_cmd("git")
    prereqs.append((git_ok, "Git", "found" if git_ok else "not found"))
    console.print(_status_table(prereqs))

    if not py_ok or not pip_ok:
        console.print(_panel("Error", "Python 3.12+ and pip are required.", "red"))
        return

    # ── Repo ───────────────────────────────────────────────────────────
    existing = _is_in_repo()
    if existing:
        console.print(f"  [green]✓[/] Found OpenGrab repo at [bold]{existing}[/]")
        repo = existing
    elif git_ok:
        dest = Path.cwd() / "opengrab"
        if not _clone_repo(dest):
            return
        repo = dest
    else:
        console.print(_panel(
            "Error",
            "Desktop build requires the full repository.\n"
            "Run this script from inside the repo, or install Git.",
            "red",
        ))
        return

    # ── FFmpeg bundle ──────────────────────────────────────────────────
    vendor = repo / "vendor"
    ffmpeg_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    ffmpeg_path = vendor / ffmpeg_name
    if not ffmpeg_path.exists():
        console.print()
        console.print(
            f"[yellow]ffmpeg not found at {ffmpeg_path}.[/]\n"
            "The desktop build bundles ffmpeg. Without it, video processing will fail."
        )
        if _check_cmd("ffmpeg"):
            if Confirm.ask("Copy system ffmpeg to vendor/?", default=True):
                vendor.mkdir(parents=True, exist_ok=True)
                shutil.copy2(shutil.which("ffmpeg") or "ffmpeg", ffmpeg_path)
                console.print(f"  [green]✓[/] ffmpeg copied to {ffmpeg_path}")
        else:
            console.print("[yellow]Install ffmpeg first, then place it in vendor/[/]")

    # ── Install build deps ─────────────────────────────────────────────
    console.print()
    rc, out = _run(
        [sys.executable, "-m", "pip", "install", "-e", ".[desktop,build]"],
        "Installing build dependencies (PyInstaller + pywebview)",
        cwd=repo,
        timeout=300,
    )
    if rc != 0:
        console.print(_panel("Error", f"pip install failed:\n{out[:800]}", "red"))
        return

    # ── PyInstaller build ──────────────────────────────────────────────
    console.print()
    console.print("[bold]Building with PyInstaller...[/]")
    console.print("[dim]This may take several minutes.[/]")
    console.print()
    rc, out = _run_live(
        [sys.executable, "-m", "PyInstaller", "OpenGrab.spec", "--noconfirm"],
        "pyinstaller OpenGrab.spec",
        cwd=repo,
        timeout=900,
    )
    if rc != 0:
        last_lines = "\n".join(out.splitlines()[-20:]) if out else "(no output)"
        console.print(_panel(
            "Build failed",
            f"PyInstaller exited with code {rc}.\n\nLast 20 lines:\n{last_lines}",
            "red",
        ))
        return

    # ── Output ─────────────────────────────────────────────────────────
    dist = repo / "dist" / "OpenGrab"
    output_files = sorted(dist.glob("*")) if dist.exists() else []
    console.print()
    console.print(_panel(
        "Build Complete",
        f"Output directory: [bold]{dist}[/]\n"
        + (f"Files: {len(output_files)}" if output_files else "(check dist/OpenGrab/)\n")
        + "\nPre-built AppImage and macOS releases:\n"
        + "https://github.com/Skydope/OpenGrab/releases",
        "green",
    ))


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def _show_welcome() -> None:
    console.clear()
    console.print()
    console.print(_panel(
        "OpenGrab Installer",
        "Self-hosted video & audio downloader\n"
        "Wraps yt-dlp (1800+ sites) + ffmpeg behind a clean web UI",
    ))
    console.print()


def _select_mode() -> str | None:
    console.print(
        Panel(
            Align.center(
                "[1] [bold]Docker[/]          docker compose up (recommended)\n"
                "[2] [bold]Bare Metal[/]      pip install + systemd\n"
                "[3] [bold]Desktop Binary[/]  PyInstaller build\n"
                "[q] [bold]Quit[/]\n"
            ),
            title="[bold]Installation Mode[/]",
            border_style="cyan",
        )
    )
    choice = Prompt.ask("Select mode", choices=["1", "2", "3", "q"], default="1")
    if choice == "q":
        return None
    return {"1": "docker", "2": "baremetal", "3": "desktop"}[choice]


def main() -> None:
    try:
        _show_welcome()
        mode = _select_mode()
        if mode is None:
            console.print("[dim]Exited.[/]")
            return
        if mode == "docker":
            _mode_docker()
        elif mode == "baremetal":
            _mode_baremetal()
        elif mode == "desktop":
            _mode_desktop()
    except KeyboardInterrupt:
        console.print()
        console.print("[dim]Cancelled.[/]")
    except Exception:
        console.print_exception()
        console.print()
        console.print(_panel(
            "Unexpected error",
            "Please report this issue:\nhttps://github.com/Skydope/OpenGrab/issues",
            "red",
        ))


if __name__ == "__main__":
    main()
