@echo off
setlocal
if "%DONNA_HOME%"=="" set "DONNA_HOME=%APPDATA%\Friday"
echo This removes Friday's private Python folder.
if exist "%DONNA_HOME%\venv" rmdir /s /q "%DONNA_HOME%\venv"
if exist "%DONNA_HOME%\friday.log" del "%DONNA_HOME%\friday.log"
echo.
echo Your data (CV, jobs, drafts, settings, Gmail login) is in: %DONNA_HOME%
set /p ANSWER=Delete your data and your Gmail login too? This cannot be undone. Type yes to delete, or press Enter to keep them: 
if /i "%ANSWER%"=="yes" ( rmdir /s /q "%DONNA_HOME%" & echo Your data and login are deleted. ) else ( echo Your data and login are kept. )
echo.
echo Last step: delete this folder: %~dp0
pause
