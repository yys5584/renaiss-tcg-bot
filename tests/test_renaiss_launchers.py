import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHERS = (
    ("start_renaiss_bot.bat", "renaiss_bot.main", "renaiss_bot_service.log"),
    (
        "start_renaiss_discord.bat",
        "renaiss_bot.adapters.discord.main",
        "renaiss_discord_service.log",
    ),
    (
        "start_renaiss_referral.bat",
        "renaiss_bot.referral_server",
        "renaiss_referral_service.log",
    ),
)
WEB_LAUNCHER = (
    "start_renaiss_web.bat",
    "renaiss_bot.web.app",
    "renaiss_web_service.log",
)
ALL_LAUNCHERS = LAUNCHERS + (WEB_LAUNCHER,)


def _copy_stream_runner(fake_root: Path) -> None:
    package_dir = fake_root / "renaiss_bot"
    tools_dir = package_dir / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "renaiss_bot" / "__init__.py", package_dir / "__init__.py")
    shutil.copy2(
        ROOT / "renaiss_bot" / "tools" / "__init__.py",
        tools_dir / "__init__.py",
    )
    shutil.copy2(
        ROOT / "renaiss_bot" / "tools" / "stream_runner.py",
        tools_dir / "stream_runner.py",
    )


@pytest.mark.parametrize(("filename", "module_name", "log_name"), ALL_LAUNCHERS)
def test_windows_launcher_uses_its_own_repository_root(filename, module_name, log_name):
    launcher = ROOT / "renaiss_bot" / filename
    source = launcher.read_text(encoding="utf-8")

    assert 'for %%I in ("%~dp0..") do set "RENAISS_ROOT=%%~fI"' in source
    assert 'pushd "%RENAISS_ROOT%" || exit /b 1' in source
    assert r"C:\Users\Administrator\Desktop\pokemon-bot" not in source
    assert r"C:\Users\Administrator\AppData" not in source
    assert r".venv\Scripts\python.exe" in source
    assert "RENAISS_PYTHON_EXE" in source
    assert 'set "ERRORLEVEL="' in source
    assert "%CD%" not in source
    assert "exit /b %ERRORLEVEL%" not in source
    assert 'set "RC=%ERRORLEVEL%"' in source
    assert 'set "RENAISS_LOG_DIR=%ProgramData%\\Renaiss\\logs"' in source
    assert "RENAISS_LOG_DIR must be an absolute local directory" in source
    assert "RENAISS_LOG_DIR must stay outside the source tree" in source
    assert "pre-create RENAISS_LOG_DIR" in source
    assert f'set "RENAISS_LOG_PATH=%RENAISS_LOG_DIR%\\{log_name}"' in source
    assert 'set "RENAISS_LOG_MAX_BYTES=10485760"' in source
    assert 'set "RENAISS_LOG_BACKUPS=5"' in source
    assert "-m renaiss_bot.tools.stream_runner" in source
    assert '--log-path "%RENAISS_LOG_PATH%"' in source
    assert '--max-bytes "%RENAISS_LOG_MAX_BYTES%"' in source
    assert '--backups "%RENAISS_LOG_BACKUPS%"' in source
    assert '>> "%RENAISS_LOG_PATH%"' not in source
    assert f'%RENAISS_ROOT%\\{log_name}' not in source
    assert "-B -E -s -u" in source
    assert f"--module {module_name}" in source
    assert (launcher.parent / "..").resolve() == ROOT


def test_web_launcher_requires_external_web_only_environment():
    filename, _, _ = WEB_LAUNCHER
    source = (ROOT / "renaiss_bot" / filename).read_text(encoding="utf-8")

    assert "RENAISS_ENV_FILE" in source
    assert "%ProgramData%\\Renaiss\\secrets\\web.env" in source
    assert "RENAISS_ENV_FILE must be an absolute local file" in source
    assert "RENAISS_ENV_FILE must be a regular file outside the source tree" in source
    assert "pre-create the web-only RENAISS_ENV_FILE" in source


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd launcher semantics")
def test_web_launcher_refuses_missing_external_environment(tmp_path):
    filename, _, log_name = WEB_LAUNCHER
    fake_root = tmp_path / "standalone"
    launcher_dir = fake_root / "renaiss_bot"
    launcher_dir.mkdir(parents=True)
    launcher = launcher_dir / filename
    shutil.copy2(ROOT / "renaiss_bot" / filename, launcher)
    log_dir = tmp_path / "external-logs"
    log_dir.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "RENAISS_PYTHON_EXE": sys.executable,
            "RENAISS_ENV_FILE": str(tmp_path / "missing-web.env"),
            "RENAISS_LOG_DIR": str(log_dir),
        }
    )

    result = subprocess.run(
        [env.get("COMSPEC", "cmd.exe"), "/d", "/c", str(launcher)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 2
    assert "pre-create the web-only" in result.stderr.lower()
    assert not (log_dir / log_name).exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd launcher semantics")
def test_web_launcher_runs_only_with_external_environment(tmp_path):
    filename, module_name, log_name = WEB_LAUNCHER
    fake_root = tmp_path / "standalone with spaces"
    launcher_dir = fake_root / "renaiss_bot"
    launcher_dir.mkdir(parents=True)
    source = (ROOT / "renaiss_bot" / filename).read_text(encoding="utf-8")
    source = source.replace(module_name, "renaiss_missing_web_module_for_test")
    launcher = launcher_dir / filename
    launcher.write_text(source, encoding="utf-8")
    _copy_stream_runner(fake_root)
    log_dir = tmp_path / "external logs"
    log_dir.mkdir()
    env_file = tmp_path / "secrets" / "web.env"
    env_file.parent.mkdir()
    env_file.write_text("RENAISS_WEB_PREVIEW=0\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "RENAISS_PYTHON_EXE": sys.executable,
            "RENAISS_ENV_FILE": str(env_file),
            "RENAISS_LOG_DIR": str(log_dir),
        }
    )

    result = subprocess.run(
        [env.get("COMSPEC", "cmd.exe"), "/d", "/c", str(launcher)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 1
    assert "renaiss_missing_web_module_for_test" in (
        log_dir / log_name
    ).read_text(encoding="utf-8")


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd launcher semantics")
@pytest.mark.parametrize(("filename", "module_name", "log_name"), LAUNCHERS)
def test_windows_launcher_ignores_shadowed_cmd_pseudo_variables(
    tmp_path,
    filename,
    module_name,
    log_name,
):
    fake_root = tmp_path / "standalone with spaces !"
    launcher_dir = fake_root / "renaiss_bot"
    launcher_dir.mkdir(parents=True)
    source = (ROOT / "renaiss_bot" / filename).read_text(encoding="utf-8")
    source = source.replace(module_name, "renaiss_missing_module_for_launcher_test")
    launcher = launcher_dir / filename
    launcher.write_text(source, encoding="utf-8")
    _copy_stream_runner(fake_root)
    log_dir = tmp_path / "external logs with spaces !"
    log_dir.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "RENAISS_PYTHON_EXE": sys.executable,
            "RENAISS_LOG_DIR": str(log_dir),
            "CD": str(tmp_path / "wrong-cd"),
            "ERRORLEVEL": "0",
        }
    )

    result = subprocess.run(
        [env.get("COMSPEC", "cmd.exe"), "/d", "/c", str(launcher)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 1
    log_path = log_dir / log_name
    assert log_path.is_file()
    assert "renaiss_missing_module_for_launcher_test" in log_path.read_text(
        encoding="utf-8"
    )
    assert not (fake_root / log_name).exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd launcher semantics")
@pytest.mark.parametrize(("filename", "module_name", "log_name"), LAUNCHERS)
def test_windows_launcher_defaults_to_external_programdata_log_directory(
    tmp_path,
    filename,
    module_name,
    log_name,
):
    fake_root = tmp_path / "standalone"
    launcher_dir = fake_root / "renaiss_bot"
    launcher_dir.mkdir(parents=True)
    source = (ROOT / "renaiss_bot" / filename).read_text(encoding="utf-8")
    source = source.replace(module_name, "renaiss_missing_module_for_launcher_test")
    launcher = launcher_dir / filename
    launcher.write_text(source, encoding="utf-8")
    _copy_stream_runner(fake_root)
    program_data = tmp_path / "program data"
    log_dir = program_data / "Renaiss" / "logs"
    log_dir.mkdir(parents=True)
    env = os.environ.copy()
    env.pop("RENAISS_LOG_DIR", None)
    env.update(
        {
            "ProgramData": str(program_data),
            "RENAISS_PYTHON_EXE": sys.executable,
        }
    )

    result = subprocess.run(
        [env.get("COMSPEC", "cmd.exe"), "/d", "/c", str(launcher)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 1
    assert "renaiss_missing_module_for_launcher_test" in (
        log_dir / log_name
    ).read_text(encoding="utf-8")
    assert not (fake_root / log_name).exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd launcher semantics")
@pytest.mark.parametrize(("filename", "module_name", "log_name"), LAUNCHERS)
def test_windows_launcher_refuses_missing_virtualenv(
    tmp_path,
    filename,
    module_name,
    log_name,
):
    fake_root = tmp_path / "standalone"
    launcher_dir = fake_root / "renaiss_bot"
    launcher_dir.mkdir(parents=True)
    launcher = launcher_dir / filename
    shutil.copy2(ROOT / "renaiss_bot" / filename, launcher)
    env = os.environ.copy()
    env.pop("RENAISS_PYTHON_EXE", None)

    result = subprocess.run(
        [env.get("COMSPEC", "cmd.exe"), "/d", "/c", str(launcher)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 2
    assert "launcher refused" in result.stderr.lower()
    assert not (fake_root / log_name).exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd launcher semantics")
@pytest.mark.parametrize(("filename", "module_name", "log_name"), LAUNCHERS)
@pytest.mark.parametrize("unsafe_kind", ("relative", "source_tree", "missing"))
def test_windows_launcher_refuses_unsafe_log_directory(
    tmp_path,
    filename,
    module_name,
    log_name,
    unsafe_kind,
):
    fake_root = tmp_path / "standalone"
    launcher_dir = fake_root / "renaiss_bot"
    launcher_dir.mkdir(parents=True)
    launcher = launcher_dir / filename
    shutil.copy2(ROOT / "renaiss_bot" / filename, launcher)

    if unsafe_kind == "relative":
        log_dir = Path("relative-logs")
        expected_error = "absolute local directory"
    elif unsafe_kind == "source_tree":
        log_dir = fake_root / "logs"
        log_dir.mkdir()
        expected_error = "outside the source tree"
    else:
        log_dir = tmp_path / "missing-logs"
        expected_error = "pre-create renaiss_log_dir"

    env = os.environ.copy()
    env.update(
        {
            "RENAISS_PYTHON_EXE": sys.executable,
            "RENAISS_LOG_DIR": str(log_dir),
        }
    )

    result = subprocess.run(
        [env.get("COMSPEC", "cmd.exe"), "/d", "/c", str(launcher)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 2
    assert expected_error in result.stderr.lower()
    assert not (fake_root / log_name).exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd launcher semantics")
@pytest.mark.parametrize(("filename", "module_name", "log_name"), LAUNCHERS)
def test_windows_launcher_refuses_unwritable_log_file(
    tmp_path,
    filename,
    module_name,
    log_name,
):
    fake_root = tmp_path / "standalone"
    launcher_dir = fake_root / "renaiss_bot"
    launcher_dir.mkdir(parents=True)
    launcher = launcher_dir / filename
    shutil.copy2(ROOT / "renaiss_bot" / filename, launcher)
    log_dir = tmp_path / "external-logs"
    log_dir.mkdir()
    (log_dir / log_name).mkdir()
    env = os.environ.copy()
    env.update(
        {
            "RENAISS_PYTHON_EXE": sys.executable,
            "RENAISS_LOG_DIR": str(log_dir),
        }
    )

    result = subprocess.run(
        [env.get("COMSPEC", "cmd.exe"), "/d", "/c", str(launcher)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 2
    assert "cannot write its log file" in result.stderr.lower()


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd launcher semantics")
@pytest.mark.parametrize(("filename", "module_name", "log_name"), LAUNCHERS)
def test_windows_launcher_rotates_merged_output_and_preserves_exit_code(
    tmp_path,
    filename,
    module_name,
    log_name,
):
    fake_root = tmp_path / "standalone"
    launcher_dir = fake_root / "renaiss_bot"
    launcher_dir.mkdir(parents=True)
    source = (ROOT / "renaiss_bot" / filename).read_text(encoding="utf-8")
    source = source.replace(module_name, "renaiss_launcher_fixture")
    launcher = launcher_dir / filename
    launcher.write_text(source, encoding="utf-8")
    _copy_stream_runner(fake_root)
    (fake_root / "renaiss_launcher_fixture.py").write_text(
        "import os\n"
        "out = b'O' * 70000\n"
        "err = b'E' * 70000\n"
        "while out:\n"
        "    written = os.write(1, out)\n"
        "    out = out[written:]\n"
        "while err:\n"
        "    written = os.write(2, err)\n"
        "    err = err[written:]\n"
        "raise SystemExit(7)\n",
        encoding="utf-8",
    )
    log_dir = tmp_path / "external-logs"
    log_dir.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "RENAISS_PYTHON_EXE": sys.executable,
            "RENAISS_LOG_DIR": str(log_dir),
            "RENAISS_LOG_MAX_BYTES": "65536",
            "RENAISS_LOG_BACKUPS": "1",
        }
    )

    result = subprocess.run(
        [env.get("COMSPEC", "cmd.exe"), "/d", "/c", str(launcher)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    log_path = log_dir / log_name
    backup_path = log_dir / f"{log_name}.1"
    assert result.returncode == 7
    assert log_path.is_file()
    assert backup_path.is_file()
    assert log_path.stat().st_size <= 65536
    assert backup_path.stat().st_size <= 65536
    assert not (log_dir / f"{log_name}.2").exists()
    retained = backup_path.read_bytes() + log_path.read_bytes()
    assert b"O" in retained
    assert b"E" in retained
    assert (log_dir / f"{log_name}.lock").is_file()
    assert not (fake_root / log_name).exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd launcher semantics")
@pytest.mark.parametrize(("filename", "module_name", "log_name"), LAUNCHERS)
def test_windows_launcher_refuses_unbounded_retention_before_service_module(
    tmp_path,
    filename,
    module_name,
    log_name,
):
    fake_root = tmp_path / "standalone"
    launcher_dir = fake_root / "renaiss_bot"
    launcher_dir.mkdir(parents=True)
    source = (ROOT / "renaiss_bot" / filename).read_text(encoding="utf-8")
    source = source.replace(module_name, "module_that_must_not_run")
    launcher = launcher_dir / filename
    launcher.write_text(source, encoding="utf-8")
    _copy_stream_runner(fake_root)
    (fake_root / "module_that_must_not_run.py").write_text(
        "from pathlib import Path\nPath('module-ran').write_text('bad')\n",
        encoding="utf-8",
    )
    log_dir = tmp_path / "external-logs"
    log_dir.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "RENAISS_PYTHON_EXE": sys.executable,
            "RENAISS_LOG_DIR": str(log_dir),
            "RENAISS_LOG_MAX_BYTES": "0",
            "RENAISS_LOG_BACKUPS": "5",
        }
    )

    result = subprocess.run(
        [env.get("COMSPEC", "cmd.exe"), "/d", "/c", str(launcher)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 2
    assert "renaiss_log_max_bytes" in result.stderr.lower()
    assert not (fake_root / "module-ran").exists()


def test_readme_install_command_points_to_standalone_repository():
    source = (ROOT / "renaiss_bot" / "README.md").read_text(encoding="utf-8")

    assert r"cd C:\Users\Administrator\Desktop\renaiss-tcg-bot" in source
    assert r"cd C:\Users\Administrator\Desktop\pokemon-bot" not in source
