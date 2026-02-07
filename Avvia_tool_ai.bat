@echo off
cd /d C:\tool_ai_edilizia

start "" /B "C:\Program Files\Python312\python.exe" -m streamlit run app.py --server.port 8501 --server.address localhost

:wait
powershell -Command "try { (Invoke-WebRequest -UseBasicParsing http://localhost:8501 | Out-Null); exit 0 } catch { exit 1 }"
if errorlevel 1 (
  timeout /t 1 /nobreak > nul
  goto wait
)

start "" http://localhost:8501
