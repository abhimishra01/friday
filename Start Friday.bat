@echo off
setlocal enabledelayedexpansion
rem Start Friday on Windows. Safe to run again.
cd /d "%~dp0"
set "APP=%~dp0"
if "%DONNA_HOME%"=="" set "DONNA_HOME=%APPDATA%\Friday"
if "%DONNA_DATA_DIR%"=="" set "DONNA_DATA_DIR=%DONNA_HOME%\data"
if not exist "%DONNA_HOME%" mkdir "%DONNA_HOME%"
if not exist "%DONNA_DATA_DIR%" mkdir "%DONNA_DATA_DIR%"
echo Starting Friday...

rem 1. Python 3.11 to 3.13
set "PY=%DONNA_PYTHON%"
if "%PY%"=="" (
  for %%V in (3.13 3.12 3.11) do (
    if "!PY!"=="" (
      py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V"
    )
  )
)
if "%PY%"=="" (
  echo.
  echo Friday needs Python 3.11, 3.12 or 3.13, and cannot find one.
  echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH", then start Friday again.
  pause
  exit /b 1
)

rem 2. Claude Code
where claude >nul 2>nul
if errorlevel 1 (
  echo.
  echo Friday uses your own Claude Code, and cannot find the claude program.
  echo Install it from https://docs.claude.com/en/docs/claude-code , open a terminal, type: claude , sign in, then start Friday again.
  pause
  exit /b 1
)
if not "%DONNA_SKIP_CLAUDE_CHECK%"=="1" (
  echo Checking that Claude answers. This uses a few words of your Claude usage.
  claude -p "Reply with the single word: ready" >"%DONNA_HOME%\claude-check.txt" 2>nul <nul
  for %%F in ("%DONNA_HOME%\claude-check.txt") do if %%~zF==0 (
    echo.
    echo Claude did not answer. Open a terminal, type: claude , sign in, then start Friday again.
    pause
    exit /b 1
  )
)

rem 3. Private Python folder with Friday's packages
if "%DONNA_NO_VENV%"=="" (
  set "VENV=%DONNA_HOME%\venv"
  if not exist "!VENV!\Scripts\python.exe" (
    echo Installing Friday's packages. First start only, about a minute.
    %PY% -m venv "!VENV!"
    if errorlevel 1 ( echo Could not make Friday's private Python folder. & pause & exit /b 1 )
    "!VENV!\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check %DONNA_PIP_ARGS% -r "%APP%requirements.txt"
    if errorlevel 1 ( echo Could not install the packages. Check your internet connection and start Friday again. & rmdir /s /q "!VENV!" & pause & exit /b 1 )
  )
  set "PY=!VENV!\Scripts\python.exe"
)

rem 4. Start on this computer only (port 8795, or the next free one)
if "%DONNA_PORT%"=="" set "DONNA_PORT=8795"
set "DONNA_FRONTEND_DIR=%APP%frontend"
echo Friday is starting. Keep this window open while she works. Close it to stop her.
if "%DONNA_NO_BROWSER%"=="" start "" /b cmd /c "timeout /t 4 >nul & start http://127.0.0.1:%DONNA_PORT%/"
%PY% -m backend.donna_app >>"%DONNA_HOME%\friday.log" 2>&1
echo.
echo Friday stopped. The log is in: %DONNA_HOME%\friday.log
pause
