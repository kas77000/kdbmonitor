@echo off
REM Start the KdbMonitor report scheduler in its own window.
REM
REM Not required: the app starts one automatically when a scheduler should be
REM running and none is. This is for running it by hand, or for dropping in
REM Startup on a machine that does not keep the app open. Either way the lock
REM in the database decides who actually runs, so starting a second one is
REM safe -- it will find the lock held and exit.
cd /d "%~dp0"
if "%KDBMONITOR_DB%"=="" set KDBMONITOR_DB=kdbmonitor.db
python -m kdbmonitor.scheduler
pause
