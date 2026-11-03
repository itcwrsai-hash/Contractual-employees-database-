@echo off
REM Double-click to start the Contractual Employees Management Software.
REM Developed by IT Center / Wagon Repair Shop / Raipur / SECR.
cd /d "%~dp0"
python -c "import flask, waitress, segno" 2>nul || python -m pip install -r requirements.txt
start "" http://localhost:5000
python app.py
pause
