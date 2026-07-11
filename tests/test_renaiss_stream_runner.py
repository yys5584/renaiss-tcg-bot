import os
from pathlib import Path

import pytest

from renaiss_bot.tools import stream_runner


def test_rotating_log_splits_oversized_chunk_without_exceeding_bound(tmp_path):
    log_path = tmp_path / "service.log"
    writer = stream_runner.BoundedRotatingLog(
        log_path,
        max_bytes=10,
        backups=2,
    )

    writer.write(b"0123456789ABCDEFGHIJklm")
    writer.close()

    assert log_path.read_bytes() == b"klm"
    assert Path(f"{log_path}.1").read_bytes() == b"ABCDEFGHIJ"
    assert Path(f"{log_path}.2").read_bytes() == b"0123456789"
    assert all(
        path.stat().st_size <= 10
        for path in (log_path, Path(f"{log_path}.1"), Path(f"{log_path}.2"))
    )


def test_rotating_log_caps_legacy_files_and_removes_excess_backups(tmp_path):
    log_path = tmp_path / "service.log"
    log_path.write_bytes(b"0123456789ABCDE")
    Path(f"{log_path}.1").write_bytes(b"abcdefghijklmno")
    Path(f"{log_path}.3").write_bytes(b"stale")

    writer = stream_runner.BoundedRotatingLog(
        log_path,
        max_bytes=10,
        backups=2,
    )
    writer.close()

    assert log_path.read_bytes() == b""
    assert Path(f"{log_path}.1").read_bytes() == b"56789ABCDE"
    assert Path(f"{log_path}.2").read_bytes() == b"fghijklmno"
    assert not Path(f"{log_path}.3").exists()


def test_windows_rename_denial_degrades_to_bounded_active_log(tmp_path, monkeypatch):
    log_path = tmp_path / "service.log"
    log_path.write_bytes(b"x" * 128)
    monkeypatch.setattr(stream_runner, "_FILE_RETRY_ATTEMPTS", 1)

    def deny_replace(source, destination):
        raise PermissionError("simulated Windows sharing violation")

    monkeypatch.setattr(stream_runner.os, "replace", deny_replace)

    writer = stream_runner.BoundedRotatingLog(
        log_path,
        max_bytes=128,
        backups=2,
    )
    writer.write(b"service-output")
    writer.close()

    assert writer.degraded_rotations >= 1
    assert log_path.stat().st_size <= 128
    assert len(list(tmp_path.glob("service.log.[0-9]*"))) <= 2
    assert b"[launcher]" in log_path.read_bytes()


def test_module_runs_in_current_process_and_preserves_system_exit(
    tmp_path,
    monkeypatch,
):
    log_path = tmp_path / "service.log"
    observed = {}

    def fake_run_module(module, *, run_name, alter_sys):
        observed.update(
            module=module,
            run_name=run_name,
            alter_sys=alter_sys,
            pid=os.getpid(),
        )
        os.write(1, b"native-stdout\n")
        os.write(2, b"native-stderr\n")
        raise SystemExit(7)

    monkeypatch.setattr(stream_runner.runpy, "run_module", fake_run_module)
    writer = stream_runner.BoundedRotatingLog(
        log_path,
        max_bytes=1024,
        backups=1,
    )

    result = stream_runner.run_module_with_log("fixture.service", writer)

    assert result == 7
    assert observed == {
        "module": "fixture.service",
        "run_name": "__main__",
        "alter_sys": True,
        "pid": os.getpid(),
    }
    output = log_path.read_bytes()
    assert b"native-stdout" in output
    assert b"native-stderr" in output


def test_partial_fd_setup_failure_restores_streams_and_closes_resources(
    tmp_path,
    monkeypatch,
):
    log_path = tmp_path / "service.log"
    writer = stream_runner.BoundedRotatingLog(
        log_path,
        max_bytes=1024,
        backups=1,
    )
    capture = stream_runner._StandardStreamCapture(writer)
    real_dup2 = os.dup2
    calls = 0

    def fail_second_dup2(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated stderr redirect failure")
        return real_dup2(source, destination)

    monkeypatch.setattr(stream_runner.os, "dup2", fail_second_dup2)

    with pytest.raises(OSError, match="stderr redirect failure"):
        capture.start()

    assert calls == 3  # stdout redirect, failed stderr redirect, stdout restore
    assert writer._stream is None
    assert capture.saved_stdout_fd == -1
    assert capture.saved_stderr_fd == -1
    assert capture.read_fd == -1
    assert capture.write_fd == -1


def test_external_log_lock_rejects_second_runner_and_lives_beside_log(tmp_path):
    log_path = tmp_path / "service.log"
    Path(f"{log_path}.lock").write_bytes(b"legacy-lock-data")
    first = stream_runner.ExternalLogLock(log_path)

    with first:
        assert first.path == tmp_path / "service.log.lock"
        assert first.path.stat().st_size == 1
        with pytest.raises(stream_runner.LogAlreadyInUseError):
            with stream_runner.ExternalLogLock(log_path):
                pytest.fail("a second runner must not acquire the same log")

    with stream_runner.ExternalLogLock(log_path):
        pass


def test_runner_rejects_source_tree_log_and_unsafe_retention(tmp_path, capsys):
    source_root = tmp_path / "source"
    source_root.mkdir()
    internal_log = source_root / "service.log"

    result = stream_runner.main(
        [
            "--source-root",
            str(source_root),
            "--log-path",
            str(internal_log),
            "--max-bytes",
            str(stream_runner.MIN_MAX_BYTES - 1),
            "--backups",
            "5",
            "--module",
            "fixture.service",
        ]
    )

    assert result == 2
    assert "RENAISS_LOG_MAX_BYTES" in capsys.readouterr().err
    assert not internal_log.exists()
