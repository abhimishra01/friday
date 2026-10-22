@echo off
cd /d "%~dp0"
if exist ".git" (
  echo Getting the newest Friday...
  git pull --ff-only
  if errorlevel 1 ( echo The update failed. Your data is safe. Try again later. & pause & exit /b 1 )
  echo Updated. Start Friday now.
  pause
  exit /b 0
)
echo To update Friday from a zip:
echo   1. Download the newest Friday zip and unzip it.
echo   2. Start Friday from the new folder (Start Friday.bat).
echo Your data stays: it is kept outside the Friday folder. You can delete the old folder afterwards.
pause
