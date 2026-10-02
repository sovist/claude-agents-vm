@echo off
rem Windows notifications for the agents VM; see agents-notify.ps1. "agents-notify -test" shows one test toast.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0agents-notify.ps1" %*
