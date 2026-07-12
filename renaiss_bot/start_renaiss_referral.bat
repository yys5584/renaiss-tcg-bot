@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "ERRORLEVEL="
for %%I in ("%~dp0..") do set "RENAISS_ROOT=%%~fI"
if not defined RENAISS_PYTHON_EXE set "RENAISS_PYTHON_EXE=%RENAISS_ROOT%\.venv\Scripts\python.exe"
if not exist "%RENAISS_PYTHON_EXE%" (
  >&2 echo Renaiss launcher refused: create the standalone repository .venv or set RENAISS_PYTHON_EXE to its python.exe.
  exit /b 2
)
if not defined RENAISS_LOG_DIR (
  if not defined ProgramData (
    >&2 echo Renaiss launcher refused: set RENAISS_LOG_DIR to a pre-created absolute local log directory.
    exit /b 2
  )
  set "RENAISS_LOG_DIR=%ProgramData%\Renaiss\logs"
)
if not "%RENAISS_LOG_DIR:~1,2%"==":\" (
  >&2 echo Renaiss launcher refused: RENAISS_LOG_DIR must be an absolute local directory.
  exit /b 2
)
"%RENAISS_PYTHON_EXE%" -B -E -s -c "from pathlib import Path; import sys; root = Path(sys.argv[1]).resolve(); log_dir = Path(sys.argv[2]).resolve(); raise SystemExit(log_dir == root or root in log_dir.parents)" "%RENAISS_ROOT%" "%RENAISS_LOG_DIR%" >nul 2>&1
if errorlevel 1 (
  >&2 echo Renaiss launcher refused: RENAISS_LOG_DIR must stay outside the source tree.
  exit /b 2
)
if not exist "%RENAISS_LOG_DIR%\." (
  >&2 echo Renaiss launcher refused: pre-create RENAISS_LOG_DIR and grant the service account write access.
  exit /b 2
)
set "RENAISS_LOG_PATH=%RENAISS_LOG_DIR%\renaiss_referral_service.log"
"%RENAISS_PYTHON_EXE%" -B -E -s -c "from pathlib import Path; import sys; stream = Path(sys.argv[1]).open(mode='a', encoding='utf-8'); stream.close()" "%RENAISS_LOG_PATH%" >nul 2>&1
if errorlevel 1 (
  >&2 echo Renaiss launcher refused: the service account cannot write its log file.
  exit /b 2
)
if not defined RENAISS_LOG_MAX_BYTES set "RENAISS_LOG_MAX_BYTES=10485760"
if not defined RENAISS_LOG_BACKUPS set "RENAISS_LOG_BACKUPS=5"
pushd "%RENAISS_ROOT%" || exit /b 1
"%RENAISS_PYTHON_EXE%" -B -E -s -u -m renaiss_bot.tools.stream_runner --source-root "%RENAISS_ROOT%" --log-path "%RENAISS_LOG_PATH%" --max-bytes "%RENAISS_LOG_MAX_BYTES%" --backups "%RENAISS_LOG_BACKUPS%" --module renaiss_bot.referral_server
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%
