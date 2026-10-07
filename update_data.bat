@echo off
title Update Data & Recompute AI Face Models
color 0B
echo ======================================================================
echo        Update Data, Database Migrations, & FaceNet Encodings
echo ======================================================================
echo.

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment (.venv) not found!
    echo Creating virtual environment...
    python -m venv --system-site-packages .venv
    .venv\Scripts\pip install --force-reinstall --no-deps opencv-contrib-python==4.13.0.92
)

echo [1/3] Applying database migrations...
.venv\Scripts\python.exe manage.py makemigrations
.venv\Scripts\python.exe manage.py migrate
echo.

echo [2/3] Recomputing 512-D FaceNet embeddings & LBPH models...
.venv\Scripts\python.exe recompute_encodings.py
echo.

echo [3/3] Checking system status...
.venv\Scripts\python.exe manage.py check
echo.

echo ======================================================================
echo [SUCCESS] Database and Face Recognition models updated successfully!
echo ======================================================================
echo.
pause
