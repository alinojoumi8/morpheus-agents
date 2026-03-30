@echo off
REM ============================================================================
REM Morpheus Agent — One-command launcher (Windows)
REM ============================================================================
REM Clone the repo and run this script. It handles everything:
REM   1. Checks for Python / uv
REM   2. Creates venv + installs dependencies (first run only)
REM   3. Runs the setup wizard (first run only)
REM   4. Starts Morpheus Agent
REM
REM Usage:
REM   run.bat                  # Interactive CLI
REM   run.bat setup            # Re-run setup wizard
REM   run.bat gateway          # Start messaging gateway
REM   run.bat --help           # Show all commands
REM ============================================================================

setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "VENV_DIR=%SCRIPT_DIR%venv"

REM ============================================================================
REM Step 1: Find uv or pip
REM ============================================================================

set "UV_CMD="
where uv >nul 2>&1 && set "UV_CMD=uv"

if not defined UV_CMD (
    REM Check common install locations
    if exist "%LOCALAPPDATA%\uv\uv.exe" set "UV_CMD=%LOCALAPPDATA%\uv\uv.exe"
    if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV_CMD=%USERPROFILE%\.local\bin\uv.exe"
)

if not defined UV_CMD (
    echo.
    echo [INFO] uv not found. Installing uv...
    powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" 2>nul
    where uv >nul 2>&1 && set "UV_CMD=uv"
)

if not defined UV_CMD (
    echo.
    echo [WARNING] uv not available. Falling back to pip...
    echo For best experience, install uv: https://docs.astral.sh/uv/
    echo.
    goto :use_pip
)

REM ============================================================================
REM Step 2: Create venv + install (first run only)
REM ============================================================================

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo.
    echo ========================================
    echo   MORPHEUS AGENT - First-time setup
    echo ========================================
    echo.

    echo [*] Creating virtual environment...
    %UV_CMD% venv "%VENV_DIR%" 2>nul
    if errorlevel 1 (
        echo [*] Trying with system Python...
        python -m venv "%VENV_DIR%"
    )

    echo [*] Installing dependencies...
    %UV_CMD% pip install -e ".[intelligence]" --python "%VENV_DIR%\Scripts\python.exe" 2>nul
    if errorlevel 1 (
        %UV_CMD% pip install -e "." --python "%VENV_DIR%\Scripts\python.exe" 2>nul
    )

    echo [OK] Dependencies installed
    echo.

    REM Create .env if missing
    if not exist ".env" (
        if exist ".env.example" (
            copy ".env.example" ".env" >nul
            echo [OK] Created .env from template
        )
    )

    REM Check if setup needed
    set "MORPHEUS_HOME_DIR=%USERPROFILE%\.morpheus"
    if defined MORPHEUS_HOME set "MORPHEUS_HOME_DIR=%MORPHEUS_HOME%"

    if not exist "!MORPHEUS_HOME_DIR!\config.yaml" (
        echo.
        echo [INFO] No API keys configured. Running setup wizard...
        echo.
        "%VENV_DIR%\Scripts\python.exe" -m morpheus_cli.main setup
    )

    echo.
)

REM ============================================================================
REM Step 3: Run Morpheus
REM ============================================================================

"%VENV_DIR%\Scripts\python.exe" -m morpheus_cli.main %*
goto :eof

REM ============================================================================
REM Fallback: use pip directly (no uv)
REM ============================================================================

:use_pip
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [*] Creating virtual environment with pip...
    python -m venv "%VENV_DIR%"
    "%VENV_DIR%\Scripts\pip.exe" install -e ".[intelligence]" 2>nul || "%VENV_DIR%\Scripts\pip.exe" install -e "."
    echo [OK] Dependencies installed

    if not exist ".env" if exist ".env.example" copy ".env.example" ".env" >nul

    set "MORPHEUS_HOME_DIR=%USERPROFILE%\.morpheus"
    if defined MORPHEUS_HOME set "MORPHEUS_HOME_DIR=%MORPHEUS_HOME%"
    if not exist "!MORPHEUS_HOME_DIR!\config.yaml" (
        "%VENV_DIR%\Scripts\python.exe" -m morpheus_cli.main setup
    )
)

"%VENV_DIR%\Scripts\python.exe" -m morpheus_cli.main %*
