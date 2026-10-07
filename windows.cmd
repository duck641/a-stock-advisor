@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "UV_PROJECT_ENVIRONMENT=%~dp0.venv-windows"
set "UV_CACHE_DIR=%~dp0.uv-cache"
if /i "%~1"=="install" goto install
if not exist "%~dp0.venv-windows\Scripts\python.exe" (
    echo Run windows.cmd install first.
    exit /b 1
)
if "%~1"=="" (
    "%~dp0.venv-windows\Scripts\python.exe" -X utf8 cli.py web
) else (
    "%~dp0.venv-windows\Scripts\python.exe" -X utf8 cli.py %*
)
exit /b %errorlevel%

:install
if exist "%~dp0.venv-windows\Scripts\python.exe" goto dependencies
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
    echo Python 3.11 or newer is required on PATH.
    exit /b 1
)
python -m venv "%~dp0.venv-windows"
if errorlevel 1 exit /b 1
:dependencies
"%~dp0.venv-windows\Scripts\python.exe" -m pip install uv
if errorlevel 1 exit /b 1
"%~dp0.venv-windows\Scripts\uv.exe" sync --locked --inexact --python "%~dp0.venv-windows\Scripts\python.exe"
if errorlevel 1 exit /b 1
if not exist ".env" copy /y ".env.example" ".env" >nul
echo Installation complete. Run windows.cmd setup to configure, then windows.cmd web.
exit /b 0
