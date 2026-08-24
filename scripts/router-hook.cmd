@echo off
setlocal
if not defined PYTHONUTF8 if not defined PYTHONIOENCODING set "PYTHONUTF8=1"
python "%~dp0router_hook.py" %*
exit /b %errorlevel%
