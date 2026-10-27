@echo off
REM One-time developer setup on Windows.
python -m venv .venv
call .venv\Scripts\activate.bat
pip install -r requirements.txt
echo Setup complete.
