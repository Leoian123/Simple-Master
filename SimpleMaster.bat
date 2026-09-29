@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Simple Master

rem Trascinando un manuale (PDF o testo) sopra questo file si va dritti alla preparazione.
set "FONTE=%~1"

call :verifica_python || goto fine
call :verifica_env || goto fine

if not "%FONTE%"=="" goto prepara

:menu
cls
echo ==========================================
echo   SIMPLE MASTER - master AI per il gioco di ruolo
echo ==========================================
echo.
echo   1. Gioca                 (apre il menu principale nel browser)
echo   2. Prepara da un manuale (PDF, testo o URL -^> file di campagna)
echo   3. Riordina il glossario (allinea ai file e pulisce)
echo   4. Nuova campagna
echo   5. Installa / aggiorna le dipendenze
echo   6. Apri la cartella delle campagne
echo   7. Apri la cartella dei registri (log: dove e perche' si e' fermato qualcosa)
echo   8. Registri: elenco con errori, poi pulizia (archivia i vecchi)
echo   0. Esci
echo.
set "scelta="
set /p "scelta=Scelta: "
if "%scelta%"=="1" goto gioca
if "%scelta%"=="2" goto prepara
if "%scelta%"=="3" goto riordina
if "%scelta%"=="4" goto nuova
if "%scelta%"=="5" goto installa
if "%scelta%"=="6" start "" "campaigns" & goto menu
if "%scelta%"=="7" (if not exist "log" mkdir "log") & start "" "log" & goto menu
if "%scelta%"=="8" goto registri
if "%scelta%"=="0" goto fine
goto menu

rem ------------------------------------------------------------------
:gioca
echo.
echo Avvio la UI sul menu principale: da li' scegli la campagna. (chiudi questa finestra per fermare)
echo.
"%PY%" main.py
call :attendi
goto menu

rem ------------------------------------------------------------------
:prepara
cls
echo --- PREPARA UNA CAMPAGNA DA UN MANUALE ---
echo.
if "%FONTE%"=="" (
    echo Trascina qui il file del manuale ^(PDF o testo^) oppure incolla un URL, poi Invio.
    set /p "FONTE=Fonte: "
)
set "FONTE=!FONTE:"=!"
if "!FONTE!"=="" goto menu
echo.
call :scegli_campagna || goto menu
echo.
echo Modalita' di lettura:
echo   1. testo  - estrae il testo dal PDF, economica (consigliata)
echo   2. pdf    - invia le pagine originali: tabelle complesse o PDF scansionati
set "m="
set /p "m=Modalita' [1]: "
set "MODE=testo"
if "!m!"=="2" set "MODE=pdf"
set "PAGINE="
set /p "PAGINE=Solo alcune pagine? (es. 10-120, Invio per tutte): "
set "EXTRA="
if not "!PAGINE!"=="" set "EXTRA=--pages !PAGINE!"
echo.
echo Fonte:     !FONTE!
echo Campagna:  !CAMPAGNA!
echo Modalita': !MODE! !PAGINE!
echo.
echo Un manuale grande richiede molte chiamate API: se si interrompe, rilancia e riparte da dove era.
set "ok="
set /p "ok=Procedo? [S/n]: "
if /i "!ok!"=="n" (set "FONTE=" & goto menu)
echo.
"%PY%" main.py --campaign "campaigns\!CAMPAGNA!" --prepare "!FONTE!" --mode !MODE! !EXTRA!
echo.
set "FONTE="
call :attendi
goto menu

rem ------------------------------------------------------------------
:riordina
call :scegli_campagna || goto menu
echo.
"%PY%" main.py --campaign "campaigns\!CAMPAGNA!" --tidy-glossary
echo.
call :attendi
goto menu

rem ------------------------------------------------------------------
:nuova
cls
echo --- NUOVA CAMPAGNA ---
echo.
set "NOME="
set /p "NOME=Nome della nuova campagna (senza spazi): "
if "!NOME!"=="" goto menu
echo.
echo   1. Copia della campagna di esempio (Velmora)
echo   2. File vuoti, da riempire con la preparazione da manuale
set "t="
set /p "t=Tipo [1]: "
set "VUOTA="
if "!t!"=="2" set "VUOTA=--empty"
"%PY%" main.py --new "campaigns\!NOME!" !VUOTA!
echo.
call :attendi
goto menu

rem ------------------------------------------------------------------
:registri
cls
echo --- REGISTRI ---
echo.
"%PY%" main.py --logs list
echo.
set "ok="
set /p "ok=Archivio i registri piu' vecchi di 7 giorni e cancello l'archivio oltre 30? [s/N]: "
if /i "!ok!"=="s" "%PY%" main.py --logs clean
echo.
call :attendi
goto menu

rem ------------------------------------------------------------------
:installa
echo.
"%PY%" -m pip install -r requirements.txt
echo.
call :attendi
goto menu

rem ------------------------------------------------------------------
:scegli_campagna
set "CAMPAGNA="
set n=0
for /d %%d in ("campaigns\*") do (
    set /a n+=1
    set "camp[!n!]=%%~nxd"
)
if !n!==0 (
    echo Nessuna campagna in campaigns\ : creane una con l'opzione 4.
    call :attendi
    exit /b 1
)
if !n!==1 (
    set "CAMPAGNA=!camp[1]!"
    exit /b 0
)
echo Campagne disponibili:
for /l %%i in (1,1,!n!) do echo   %%i. !camp[%%i]!
set "c="
set /p "c=Numero campagna [1]: "
if "!c!"=="" set c=1
if not defined camp[!c!] (
    echo Scelta non valida.
    call :attendi
    exit /b 1
)
set "CAMPAGNA=!camp[%c%]!"
exit /b 0

rem ------------------------------------------------------------------
:verifica_python
set "PY=%~dp0.venv\Scripts\python.exe"
if exist "%PY%" goto verifica_dipendenze
python --version >nul 2>&1
if errorlevel 1 (
    echo Python non trovato. Installalo da https://www.python.org/downloads/ (spunta "Add python to PATH").
    call :attendi
    exit /b 1
)
echo Creo l'ambiente virtuale .venv...
python -m venv ".venv"
if errorlevel 1 (
    echo Creazione di .venv fallita.
    call :attendi
    exit /b 1
)
:verifica_dipendenze
"%PY%" -c "import anthropic, pypdf" >nul 2>&1 && exit /b 0
echo Installo le dipendenze in .venv...
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Installazione fallita: controlla la connessione e rilancia.
    call :attendi
    exit /b 1
)
exit /b 0

:verifica_env
if exist ".env" (
    findstr /b /c:"ANTHROPIC_API_KEY=sk-ant-..." ".env" >nul && goto env_da_compilare
    exit /b 0
)
copy ".env.example" ".env" >nul
:env_da_compilare
echo.
echo Manca la chiave API. Si apre il file .env: sostituisci sk-ant-... con la tua chiave,
echo salva, chiudi il Blocco note e rilancia questo file.
echo.
call :attendi
start /wait notepad ".env"
findstr /b /c:"ANTHROPIC_API_KEY=sk-ant-..." ".env" >nul && exit /b 1
exit /b 0

:attendi
set "_k="
set /p "_k=Premi Invio per continuare... "
exit /b 0

:fine
endlocal
