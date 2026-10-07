@echo off
title SmartAttend Google App Password Setup
color 0B
echo ==============================================================
echo       SmartAttend SMTP Google App Password Setup Tool
echo ==============================================================
echo.
echo 1. Make sure you have generated a 16-character App Password from:
echo    https://myaccount.google.com/apppasswords
echo.
echo 2. Paste it below (spaces will be automatically cleaned up).
echo.
set /p app_pass="Paste your 16-character Google App Password: "
echo.
echo Updating .env file and verifying connection...
echo.
python "C:\Users\Dhanin\.gemini\antigravity\scratch\update_password.py" %app_pass%
echo.
echo ==============================================================
pause
