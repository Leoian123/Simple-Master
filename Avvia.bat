@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Simple Master - avvio

rem ============================================================
rem  Avvio a un clic: controlla Python e dipendenze, prepara il
rem  .env e apre la UI sul menu principale (da li' si sceglie la
rem  campagna). Chiudere questa finestra ferma tutto.
rem  Uso:  Avvia.bat                       -> menu principale
rem        Avvia.bat --cli                 -> in terminale (campagna esempio)
rem        Avvia.bat --cli --campaign campaigns\NOME
rem  Per preparazione, glossario e registri da terminale: SimpleMaster.bat
rem ============================================================

rem --- 1. Python --------------------------------------------------------------
python --version >nul 2>&1
if errorlevel 1 (
    echo Python non trovato. Installalo da https://www.python.org/downloads/
    echo e spunta "Add python to PATH", poi rilancia questo file.
    pause
    exit /b 1
)

rem --- 2. Ambiente virtuale .venv e dipendenze (creati solo se mancano) ----------------
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo Creo l'ambiente virtuale .venv...
    python -m venv ".venv"
    if errorlevel 1 (
        echo Creazione di .venv fallita.
        pause
        exit /b 1
    )
)
"%PY%" -c "import anthropic, pypdf" >nul 2>&1
if errorlevel 1 (
    echo Installo le dipendenze in .venv...
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Installazione fallita: controlla la connessione e rilancia.
        pause
        exit /b 1
    )
)

rem --- 3. Chiave API in .env -----------------------------------------------------
if not exist ".env" copy ".env.example" ".env" >nul
findstr /b /c:"ANTHROPIC_API_KEY=sk-ant-..." ".env" >nul
if not errorlevel 1 (
    echo.
    echo Manca la chiave API. Si apre il file .env: sostituisci sk-ant-... con la
    echo tua chiave, salva e chiudi il Blocco note. L'avvio prosegue da solo.
    echo.
    start /wait notepad ".env"
    findstr /b /c:"ANTHROPIC_API_KEY=sk-ant-..." ".env" >nul
    if not errorlevel 1 (
        echo Chiave ancora mancante: rilancia quando l'hai inserita.
        pause
        exit /b 1
    )
)

rem --- 4. Via: menu principale nel browser -----------------------------------------------
echo.
echo Simple Master: si apre il menu principale nel browser  (chiudi questa finestra per fermare)
echo.
"%PY%" main.py %*
set "RC=%errorlevel%"
if not "%RC%"=="0" (
    echo.
    echo Il programma si e' chiuso con errore %RC%. Dettagli nell'ultimo file in log\
    pause
)
endlocal
