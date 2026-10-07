@echo off
title Django AI Face Attendance System
color 0A
echo ======================================================================
echo           Django AI Face Recognition Attendance System
echo ======================================================================
echo.

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment (.venv) not found!
    echo Creating virtual environment...
    python -m venv --system-site-packages .venv
    .venv\Scripts\pip install --force-reinstall --no-deps opencv-contrib-python==4.13.0.92
)

echo [1/2] Checking database migrations...
.venv\Scripts\python.exe manage.py migrate --noinput
echo.

echo [2/2] Starting Django Development Server on http://127.0.0.1:8000/ ...
echo.
echo ======================================================================
echo  Open your web browser and navigate to:
echo  - Main Dashboard: http://127.0.0.1:8000/
echo  - Admin Panel:    http://127.0.0.1:8000/admin/
echo ======================================================================
echo.
start http://127.0.0.1:8000/
.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
pause
