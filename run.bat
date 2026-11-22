@echo off
cd /d "%~dp0"
if not exist .venv (
  echo First run: creating a private Python environment...
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip >nul
  .venv\Scripts\python -m pip install -r requirements.txt
)
.venv\Scripts\python -m app
pause
