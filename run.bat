@echo off
REM Windows runner for company-name extractor
REM Ensure Python 3 is on PATH as "py -3"

py -3 -m pip install -r requirements.txt
py -3 main.py --urls-file input.txt --out company_names.csv
pause
