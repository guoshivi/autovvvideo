@echo off
chcp 65001 >nul
cd /d "%~dp0.."
if "%~1"=="" (
  echo.
  echo  Drag the folder that failed to index ONTO this file, or run:
  echo    diagnose.bat "D:\your\video\folder"
  echo.
  pause
  exit /b 2
)
if not exist ".runtime\embedding\Scripts\python.exe" (
  echo.
  echo  Project Python not found. Run install.ps1 first (see docs\DEPLOY.md).
  echo.
  pause
  exit /b 3
)
".runtime\embedding\Scripts\python.exe" "deploy\diagnose_index.py" "%~1"
echo.
echo  Done. Send back the folder:  deploy\diagnose_out\
echo.
pause
