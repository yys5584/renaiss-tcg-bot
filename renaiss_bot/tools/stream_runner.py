"""Run one Renaiss entry point with bounded, Windows-safe stdout logging.

The service module runs in this process. File descriptors 1 and 2 are drained
through a pipe by one writer thread, so rotation never races an inherited open
log handle and terminating the launcher cannot orphan a wrapper-owned child.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import runpy
import sys
import threading
import time
import traceback
from types import TracebackType
from typing import BinaryIO, Callable


DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_BACKUPS = 5
MIN_MAX_BYTES = 64 * 1024
MAX_MAX_BYTES = 1024 * 1024 * 1024
MAX_BACKUPS = 20
LOG_IO_FAILURE_EXIT = 74
_READ_CHUNK_BYTES = 64 * 1024
_FILE_RETRY_ATTEMPTS = 5
_FILE_RETRY_DELAY_SECONDS = 0.05
_DRAIN_JOIN_SECONDS = 3.0
_MODULE_PATTERN = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
_DEGRADED_ROTATION_MARKER = (
    b"[launcher] Log backup rotation was blocked; the active log was truncated "
    b"to preserve the configured size bound.\r\n"
)


class RunnerConfigurationError(ValueError):
    """A launcher setting is unsafe or outside the supported bounds."""


class LogAlreadyInUseError(RuntimeError):
    """Another runner already owns this exact service log."""


def _retry_file_operation(operation: Callable[[], None]) -> None:
    last_error: OSError | None = None
    for attempt in range(_FILE_RETRY_ATTEMPTS):
        try:
            operation()
            return
        except OSError as exc:
            last_error = exc
            if attempt + 1 < _FILE_RETRY_ATTEMPTS:
                time.sleep(_FILE_RETRY_DELAY_SECONDS)
    assert last_error is not None
    raise last_error


def _write_all(stream: BinaryIO, data: memoryview) -> None:
    offset = 0
    while offset < len(data):
        written = stream.write(data[offset:])
        if written is None or written <= 0:
            raise OSError("log write made no progress")
        offset += written


def _truncate_file(path: Path) -> None:
    def truncate() -> None:
        with path.open("wb", buffering=0):
            pass

    _retry_file_operation(truncate)


def _cap_file_to_tail(path: Path, max_bytes: int) -> None:
    size = path.stat().st_size
    if size <= max_bytes:
        return

    # Shift the newest max_bytes to the start in bounded chunks. The source
    # window is always ahead of the destination, so the in-place copy is safe.
    with path.open("r+b", buffering=0) as stream:
        source_start = size - max_bytes
        copied = 0
        while copied < max_bytes:
            amount = min(_READ_CHUNK_BYTES, max_bytes - copied)
            stream.seek(source_start + copied)
            chunk = stream.read(amount)
            if not chunk:
                raise OSError("unexpected end of log while enforcing size bound")
            stream.seek(copied)
            _write_all(stream, memoryview(chunk))
            copied += len(chunk)
        stream.truncate(max_bytes)


class ExternalLogLock:
    """Cross-process, one-byte lock stored beside the external log."""

    def __init__(self, log_path: Path) -> None:
        self.path = Path(f"{log_path}.lock")
        self._stream: BinaryIO | None = None

    def __enter__(self) -> "ExternalLogLock":
        if self.path.is_symlink():
            raise RunnerConfigurationError("the log lock path must not be a symlink")
        try:
            stream = self.path.open("a+b", buffering=0)
            if self.path.stat().st_size == 0:
                stream.write(b"\0")
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            stream.truncate(1)
        except OSError as exc:
            try:
                stream.close()
            except (NameError, OSError):
                pass
            raise LogAlreadyInUseError from exc
        self._stream = stream
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


class BoundedRotatingLog:
    """Unbuffered binary log with an exact active/backup size ceiling."""

    def __init__(self, path: Path, *, max_bytes: int, backups: int) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if backups < 0:
            raise ValueError("backups must be non-negative")
        self.path = path
        self.max_bytes = max_bytes
        self.backups = backups
        self.degraded_rotations = 0
        self._stream: BinaryIO | None = None
        self._size = 0

        self._sanitize_existing_backups()
        degraded = False
        if self.path.exists():
            if self.path.is_symlink() or not self.path.is_file():
                raise RunnerConfigurationError("the active log must be a regular file")
            _cap_file_to_tail(self.path, self.max_bytes)
            if self.path.stat().st_size >= self.max_bytes:
                degraded = self._rotate_closed_file()
        self._open_active()
        if degraded:
            self._write_rotation_marker()

    def _backup_path(self, index: int) -> Path:
        return Path(f"{self.path}.{index}")

    def _sanitize_existing_backups(self) -> None:
        prefix = f"{self.path.name}."
        for candidate in self.path.parent.glob(f"{self.path.name}.*"):
            suffix = candidate.name[len(prefix) :]
            if not suffix.isdecimal():
                continue
            index = int(suffix)
            is_canonical = suffix == str(index) and index >= 1
            if not is_canonical or index > self.backups:
                _retry_file_operation(candidate.unlink)
                continue
            if candidate.is_symlink() or not candidate.is_file():
                raise RunnerConfigurationError("log backups must be regular files")
            _cap_file_to_tail(candidate, self.max_bytes)

    def _open_active(self) -> None:
        self._stream = self.path.open("ab", buffering=0)
        self._size = self.path.stat().st_size

    def _close_active(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None

    def _rotate_closed_file(self) -> bool:
        if self.backups == 0:
            _truncate_file(self.path)
            return False
        try:
            oldest = self._backup_path(self.backups)
            if oldest.exists():
                _retry_file_operation(oldest.unlink)
            for index in range(self.backups - 1, 0, -1):
                source = self._backup_path(index)
                if source.exists():
                    destination = self._backup_path(index + 1)
                    _retry_file_operation(
                        lambda source=source, destination=destination: os.replace(
                            source,
                            destination,
                        )
                    )
            if self.path.exists():
                first = self._backup_path(1)
                _retry_file_operation(lambda: os.replace(self.path, first))
            return False
        except OSError:
            # Windows viewers or scanners can temporarily deny rename/delete.
            # The runner is the sole active writer, so truncating the closed
            # active file keeps service availability and the hard size bound.
            _truncate_file(self.path)
            self.degraded_rotations += 1
            return True

    def _write_rotation_marker(self) -> None:
        # Always leave room for service output, even when a unit test or a
        # future supported configuration uses a very small ceiling.
        marker_limit = max(0, self.max_bytes - 1)
        marker = memoryview(_DEGRADED_ROTATION_MARKER[:marker_limit])
        assert self._stream is not None
        _write_all(self._stream, marker)
        self._size += len(marker)

    def _rotate_and_reopen(self) -> None:
        self._close_active()
        degraded = self._rotate_closed_file()
        self._open_active()
        if degraded:
            self._write_rotation_marker()

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        offset = 0
        while offset < len(view):
            if self._size >= self.max_bytes:
                self._rotate_and_reopen()
            room = self.max_bytes - self._size
            amount = min(room, len(view) - offset)
            assert self._stream is not None
            _write_all(self._stream, view[offset : offset + amount])
            self._size += amount
            offset += amount

    def close(self) -> None:
        self._close_active()


def _flush_standard_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (AttributeError, OSError, ValueError):
            pass


def _diagnostic(fd: int, message: str) -> None:
    try:
        os.write(fd, f"Renaiss log runner: {message}\r\n".encode("utf-8"))
    except OSError:
        pass


def _drain_pipe(
    read_fd: int,
    writer: BoundedRotatingLog,
    diagnostic_fd: int,
) -> None:
    try:
        while True:
            chunk = os.read(read_fd, _READ_CHUNK_BYTES)
            if not chunk:
                break
            writer.write(chunk)
        writer.close()
    except BaseException as exc:
        _diagnostic(
            diagnostic_fd,
            f"fatal log write failure ({type(exc).__name__}); stopping action",
        )
        os._exit(LOG_IO_FAILURE_EXIT)
    finally:
        try:
            os.close(read_fd)
        except OSError:
            pass


def _close_fd(fd: int) -> None:
    if fd < 0:
        return
    try:
        os.close(fd)
    except OSError:
        pass


class _StandardStreamCapture:
    """Own fd redirection and restore it on every non-fatal setup path."""

    def __init__(self, writer: BoundedRotatingLog) -> None:
        self.writer = writer
        self.saved_stdout_fd = -1
        self.saved_stderr_fd = -1
        self.read_fd = -1
        self.write_fd = -1
        self.stdout_redirected = False
        self.stderr_redirected = False
        self.reader: threading.Thread | None = None
        self.reader_started = False

    def _restore(self) -> None:
        if self.stdout_redirected and self.saved_stdout_fd >= 0:
            os.dup2(self.saved_stdout_fd, 1)
            self.stdout_redirected = False
        if self.stderr_redirected and self.saved_stderr_fd >= 0:
            os.dup2(self.saved_stderr_fd, 2)
            self.stderr_redirected = False
        _close_fd(self.write_fd)
        self.write_fd = -1

    def _close_saved(self) -> None:
        _close_fd(self.saved_stdout_fd)
        _close_fd(self.saved_stderr_fd)
        self.saved_stdout_fd = -1
        self.saved_stderr_fd = -1

    def start(self) -> None:
        _flush_standard_streams()
        try:
            self.saved_stdout_fd = os.dup(1)
            self.saved_stderr_fd = os.dup(2)
            self.read_fd, self.write_fd = os.pipe()
            os.dup2(self.write_fd, 1)
            self.stdout_redirected = True
            os.dup2(self.write_fd, 2)
            self.stderr_redirected = True
            _close_fd(self.write_fd)
            self.write_fd = -1
            self.reader = threading.Thread(
                target=_drain_pipe,
                args=(self.read_fd, self.writer, self.saved_stderr_fd),
                name="renaiss-log-writer",
                daemon=True,
            )
            self.reader.start()
            self.reader_started = True
        except BaseException:
            try:
                self._restore()
            finally:
                if not self.reader_started:
                    _close_fd(self.read_fd)
                    self.read_fd = -1
                    self.writer.close()
                self._close_saved()
            raise

    def finish(self, exit_code: int) -> None:
        _flush_standard_streams()
        try:
            self._restore()
        except OSError as exc:
            diagnostic_fd = self.saved_stderr_fd
            if diagnostic_fd >= 0:
                _diagnostic(
                    diagnostic_fd,
                    f"fatal stdout restore failure ({type(exc).__name__})",
                )
            os._exit(LOG_IO_FAILURE_EXIT)

        if not self.reader_started or self.reader is None:
            self.writer.close()
            self._close_saved()
            raise RuntimeError("log reader did not start")
        try:
            self.reader.join(_DRAIN_JOIN_SECONDS)
        except KeyboardInterrupt:
            _diagnostic(
                self.saved_stderr_fd,
                "interrupted while draining final service output; ending action",
            )
            os._exit(130)
        if self.reader.is_alive():
            _diagnostic(
                self.saved_stderr_fd,
                "stdout pipe remained open after the service stopped; ending action",
            )
            os._exit(LOG_IO_FAILURE_EXIT)
        self.read_fd = -1
        self._close_saved()


def _system_exit_code(exc: SystemExit) -> int:
    if exc.code is None:
        return 0
    if isinstance(exc.code, int):
        return exc.code
    print(exc.code, file=sys.stderr)
    return 1


def run_module_with_log(module: str, writer: BoundedRotatingLog) -> int:
    """Run ``module`` as ``__main__`` while capturing fd 1 and fd 2."""

    capture = _StandardStreamCapture(writer)
    capture.start()
    exit_code = 1
    try:
        try:
            runpy.run_module(module, run_name="__main__", alter_sys=True)
            exit_code = 0
        except SystemExit as exc:
            exit_code = _system_exit_code(exc)
        except KeyboardInterrupt:
            traceback.print_exc()
            exit_code = 130
        except BaseException:
            traceback.print_exc()
            exit_code = 1
    finally:
        capture.finish(exit_code)
    return exit_code


def _validate_paths(source_root_arg: str, log_path_arg: str) -> tuple[Path, Path]:
    source_root_raw = Path(source_root_arg)
    log_path_raw = Path(log_path_arg)
    if not source_root_raw.is_absolute() or not log_path_raw.is_absolute():
        raise RunnerConfigurationError("source and log paths must be absolute")
    if os.name == "nt" and log_path_raw.drive.startswith("\\\\"):
        raise RunnerConfigurationError("the log path must be on a local drive")

    try:
        source_root = source_root_raw.resolve(strict=True)
        log_parent = log_path_raw.parent.resolve(strict=True)
    except OSError as exc:
        raise RunnerConfigurationError("source and log directories must exist") from exc
    if not source_root.is_dir() or not log_parent.is_dir():
        raise RunnerConfigurationError("source and log paths must use directories")
    if log_parent == source_root or source_root in log_parent.parents:
        raise RunnerConfigurationError("the log directory must stay outside the source tree")
    if log_path_raw.is_symlink():
        raise RunnerConfigurationError("the active log path must not be a symlink")
    return source_root, log_parent / log_path_raw.name


def _validate_limits(max_bytes: int, backups: int) -> None:
    if not MIN_MAX_BYTES <= max_bytes <= MAX_MAX_BYTES:
        raise RunnerConfigurationError(
            f"RENAISS_LOG_MAX_BYTES must be between {MIN_MAX_BYTES} and {MAX_MAX_BYTES}"
        )
    if not 0 <= backups <= MAX_BACKUPS:
        raise RunnerConfigurationError(
            f"RENAISS_LOG_BACKUPS must be between 0 and {MAX_BACKUPS}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a Renaiss service with bounded external logging.",
        allow_abbrev=False,
    )
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--log-path", required=True)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--backups", type=int, default=DEFAULT_BACKUPS)
    parser.add_argument("--module", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if _MODULE_PATTERN.fullmatch(args.module) is None:
            raise RunnerConfigurationError("the service module name is invalid")
        _validate_limits(args.max_bytes, args.backups)
        source_root, log_path = _validate_paths(args.source_root, args.log_path)
        previous_cwd = Path.cwd()
        with ExternalLogLock(log_path):
            writer = BoundedRotatingLog(
                log_path,
                max_bytes=args.max_bytes,
                backups=args.backups,
            )
            try:
                os.chdir(source_root)
                return run_module_with_log(args.module, writer)
            finally:
                try:
                    writer.close()
                finally:
                    os.chdir(previous_cwd)
    except (RunnerConfigurationError, LogAlreadyInUseError) as exc:
        print(f"Renaiss launcher refused: {exc}.", file=sys.stderr)
        return 2
    except OSError as exc:
        print(
            f"Renaiss launcher failed before service start ({type(exc).__name__}).",
            file=sys.stderr,
        )
        return LOG_IO_FAILURE_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
