@echo off
chcp 65001 >nul
title Pedro
cd /d "%~dp0"

rem ==== Ajustes (se guardan solos desde el menu) ====
set "JARVIS_PORT=8787"
set "JARVIS_API=http://127.0.0.1:%JARVIS_PORT%"
set "JARVIS_MIC=auto"
set "JARVIS_VOZ=es-CO-GonzaloNeural"
set "JARVIS_AGENTE=orchestrator"
set "VOICESTUDIO_VOZ="
set "PEDRO_CEREBRO=nuevo"
if exist "ajustes.cmd" call "ajustes.cmd"
rem El microfono se elige por NOMBRE (opcion 5). Los numeros viejos cambian al conectar otro: se ignoran.
echo %JARVIS_MIC%| findstr /r "^[0-9][0-9]*$" >nul && set "JARVIS_MIC=auto"
if /i "%~1"=="auto" goto auto

:menu
cls
if /i "%PEDRO_CEREBRO%"=="nuevo" (set "CEREBRO=NUEVO - motor de Claude Code") else (set "CEREBRO=ANTERIOR - OpenJarvis en Ubuntu")
echo.
echo     =========================================
echo                   P E D R O
echo     =========================================
call :estado
echo.
echo       [1]  Iniciar Pedro con voz  (di "Pedro, ...")
echo       [2]  Iniciar Pedro con voz  (Enter para hablar)
echo       [3]  Chat por texto
echo       [4]  Elegir voz
echo       [5]  Microfono y parlante (elegir y probar)
echo       [6]  Apagar Pedro
echo       [7]  Probar a Pedro (pruebas automaticas, no ejecuta nada real)
echo       [8]  Instalar Node.js (para que Pedro maneje Comet)
echo       [9]  Conectar Google (Gmail y Calendar)
echo       [W]  Pedro por WhatsApp (te responde en tu chat contigo mismo)
echo       [A]  Arrancar Pedro solo al prender el PC (activar/quitar)
echo       [C]  Cambiar cerebro: %CEREBRO%
echo       [0]  Salir
echo.
set "MICN=automatico (el predeterminado de Windows)"
if exist "voz\microfono.txt" set /p MICN=<"voz\microfono.txt"
echo       Microfono: %MICN%
if defined VOICESTUDIO_VOZ (echo       Voz: VoiceStudio - %VOICESTUDIO_VOZ%) else (echo       Voz: %JARVIS_VOZ%)
echo.
set "OP="
set /p "OP=      Elige una opcion: "
if "%OP%"=="1" goto voz
if "%OP%"=="2" goto teclado
if "%OP%"=="3" goto texto
if "%OP%"=="4" goto voces
if "%OP%"=="5" goto prueba
if "%OP%"=="6" goto apagar
if "%OP%"=="7" goto probar
if "%OP%"=="8" goto node
if "%OP%"=="9" goto google
if /i "%OP%"=="C" goto cambiar
if /i "%OP%"=="W" goto whatsapp
if /i "%OP%"=="A" goto autoarranque
if "%OP%"=="0" exit /b
goto menu

rem ---------------------------------------------------------------
:estado
curl.exe -s -m 2 %JARVIS_API%/health >nul 2>&1
if errorlevel 1 (
  echo       Estado: APAGADO
) else (
  echo       Estado: ENCENDIDO
)
exit /b

:matar
rem Apaga cualquier cerebro (el viejo de Ubuntu y el nuevo de Windows)
wsl.exe -d Ubuntu -- bash -c "pkill -f 'serve --port %JARVIS_PORT%'; pkill -f 'jarvis serve'; true" >nul 2>&1
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort %JARVIS_PORT% -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue } | Where-Object { $_.ProcessName -match '^(python|pythonw|uv)$' } | Stop-Process -Force -ErrorAction SilentlyContinue" >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name like 'python%%' or Name='uv.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'pedro_cerebro' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
timeout /t 2 /nobreak >nul
exit /b

:encender
curl.exe -s -m 2 %JARVIS_API%/health >nul 2>&1
if errorlevel 1 goto arrancar
rem Si cambie archivos de Pedro despues de que arranco el cerebro, lo reinicio solo
powershell -NoProfile -Command "$m = Get-Item cerebro.iniciado -ErrorAction SilentlyContinue; if (-not $m) { exit 1 }; $n = Get-ChildItem -Path .\*, .\herramientas\*, .\cerebro\* -Include *.py,*.md,*.json,*.sh,*.ps1 -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -notin @('recordatorios.json','historial.json','MEMORY.md','avisos.json','rutinas.json','vigilancia.json','estado.json','posicion.json') -and $_.LastWriteTime -gt $m.LastWriteTime }; if ($n) { exit 1 } else { exit 0 }" >nul 2>&1
if not errorlevel 1 exit /b 0
echo.
echo     Hay cambios nuevos en Pedro: reiniciando el cerebro...
call :matar
:arrancar
echo.
if /i "%PEDRO_CEREBRO%"=="nuevo" (
  echo     Encendiendo el cerebro de Pedro. La primera vez descarga cosas: 1-3 minutos...
  start "Pedro - cerebro (no cerrar)" /min powershell -NoProfile -ExecutionPolicy Bypass -File "cerebro\iniciar-cerebro.ps1"
) else (
  echo     Encendiendo el cerebro anterior en Ubuntu...
  start "Jarvis - cerebro (no cerrar)" /min wsl.exe -d Ubuntu -- bash /mnt/c/Users/PC/Documents/jarvis/cerebro.sh %JARVIS_PORT% %JARVIS_AGENTE%
)
set /a INTENTOS=0
:esperar
timeout /t 2 /nobreak >nul
curl.exe -s -m 2 %JARVIS_API%/health >nul 2>&1
if not errorlevel 1 (
  echo     Cerebro en linea.
  exit /b 0
)
set /a INTENTOS+=1
if %INTENTOS% lss 100 goto esperar
echo.
echo     No pude encender el cerebro. Ultimas lineas del registro:
echo.
if /i "%PEDRO_CEREBRO%"=="nuevo" (
  powershell -NoProfile -Command "Get-Content -Tail 25 -Encoding UTF8 cerebro\cerebro.log -ErrorAction SilentlyContinue"
) else (
  wsl.exe -d Ubuntu -- bash -c "tail -n 20 ~/.openjarvis/cerebro.log 2>/dev/null"
)
pause
exit /b 1

rem ---------------------------------------------------------------
:voz
call :encender || goto menu
powershell -NoProfile -ExecutionPolicy Bypass -File "voz\iniciar-voz.ps1"
pause
goto menu

:teclado
call :encender || goto menu
powershell -NoProfile -ExecutionPolicy Bypass -File "voz\iniciar-voz.ps1" --teclado
pause
goto menu

:texto
call :encender || goto menu
if /i "%PEDRO_CEREBRO%"=="nuevo" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "cerebro\correr.ps1" chat.py
) else (
  wsl.exe -d Ubuntu -- bash -ic "jarvis"
)
goto menu

:probar
call :encender || goto menu
echo.
echo     Corriendo las pruebas de Pedro (unos minutos). Las acciones se simulan; solo lee archivos y terminales.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "cerebro\correr.ps1" probar.py
echo.
echo     Listo. El reporte quedo en cerebro\pruebas\ultimo-reporte.md (Claude lo puede leer directo).
pause
goto menu

:prueba
call :encender || goto menu
rem Se guarda por NOMBRE en voz\microfono.txt: no importa si Windows cambia los numeros
powershell -NoProfile -ExecutionPolicy Bypass -File "voz\iniciar-voz.ps1" --elegir-mic
powershell -NoProfile -ExecutionPolicy Bypass -File "voz\iniciar-voz.ps1" --elegir-salida
echo.
echo     Ahora habla para ver el nivel del microfono. Ctrl+C para terminar (luego responde N).
powershell -NoProfile -ExecutionPolicy Bypass -File "voz\iniciar-voz.ps1" --prueba
goto menu

:voces
call :encender || goto menu
powershell -NoProfile -ExecutionPolicy Bypass -File "voz\iniciar-voz.ps1" --voces
echo.
set "N="
echo     [10] VoiceStudio: tu voz local clonada o disenada (gratis, en tu GPU)
echo     [0]  Quitar VoiceStudio y volver a las voces de Microsoft
echo.
set /p "N=    Numero de la voz que prefieres (Enter = no cambiar): "
if "%N%"=="1" set "JARVIS_VOZ=en-US-AndrewMultilingualNeural"
if "%N%"=="2" set "JARVIS_VOZ=en-US-BrianMultilingualNeural"
if "%N%"=="3" set "JARVIS_VOZ=en-US-AvaMultilingualNeural"
if "%N%"=="4" set "JARVIS_VOZ=en-US-EmmaMultilingualNeural"
if "%N%"=="5" set "JARVIS_VOZ=fr-FR-RemyMultilingualNeural"
if "%N%"=="6" set "JARVIS_VOZ=es-MX-JorgeNeural"
if "%N%"=="7" set "JARVIS_VOZ=es-ES-AlvaroNeural"
if "%N%"=="8" set "JARVIS_VOZ=es-CO-SalomeNeural"
if "%N%"=="9" set "JARVIS_VOZ=es-CO-GonzaloNeural"
if "%N%"=="10" (
  echo.
  echo     Escribe el NOMBRE exacto del perfil de voz que creaste en VoiceStudio.
  set /p "VOICESTUDIO_VOZ=    Perfil: "
)
if "%N%"=="0" set "VOICESTUDIO_VOZ="
call :guardar
echo     Voz guardada: %JARVIS_VOZ%
timeout /t 2 >nul
goto menu

:cambiar
if /i "%PEDRO_CEREBRO%"=="nuevo" (set "PEDRO_CEREBRO=anterior") else (set "PEDRO_CEREBRO=nuevo")
call :guardar
echo.
echo     Cambiando de cerebro...
call :matar
call :encender
timeout /t 2 >nul
goto menu

:node
where npx >nul 2>&1
if not errorlevel 1 (
  echo.
  echo     Node.js ya esta instalado. Pedro puede manejar Comet.
  timeout /t 3 >nul
  goto menu
)
echo.
echo     Instalando Node.js LTS con winget...
winget install --id OpenJS.NodeJS.LTS -e --accept-package-agreements --accept-source-agreements
echo.
echo     Listo. Cierra y vuelve a abrir este menu para que Pedro lo detecte.
pause
goto menu

:google
echo.
echo     Conectando Gmail y Calendar. Ten a mano el JSON de credenciales de Google Cloud.
echo.
wsl.exe -d Ubuntu -- bash -ic "~/.openjarvis/.venv/bin/python /mnt/c/Users/PC/Documents/jarvis/herramientas/google_auth_pedro.py"
echo.
echo     Reiniciando el cerebro para que Pedro use la conexion...
call :matar
call :encender
pause
goto menu

:apagar
call :matar
rem tambien el panel (HUD) y WhatsApp
powershell -NoProfile -Command "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'hud\\hud\.py|pedro-whatsapp' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
echo.
echo     Pedro apagado.
timeout /t 2 >nul
goto menu

:auto
rem Arranque automatico al prender el PC (acceso directo en Inicio con "Jarvis.bat auto")
timeout /t 15 /nobreak >nul
call :encender || exit /b 1
powershell -NoProfile -ExecutionPolicy Bypass -File "voz\iniciar-voz.ps1"
exit /b

:whatsapp
call :encender || goto menu
start "Pedro - WhatsApp (no cerrar)" powershell -NoProfile -ExecutionPolicy Bypass -File "whatsapp\iniciar-whatsapp.ps1"
echo.
echo     Pedro por WhatsApp se abrio en otra ventana. La primera vez escanea el QR desde tu celular:
echo     WhatsApp - Dispositivos vinculados - Vincular un dispositivo.
timeout /t 4 >nul
goto menu

:autoarranque
set "AA="
if exist "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Pedro.lnk" set "AA=1"
if defined AA (
  powershell -NoProfile -ExecutionPolicy Bypass -File "tools\instalar-autoarranque.ps1" -Quitar
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "tools\instalar-autoarranque.ps1"
)
pause
goto menu

:guardar
> "ajustes.cmd" (
  echo set "JARVIS_MIC=%JARVIS_MIC%"
  echo set "JARVIS_VOZ=%JARVIS_VOZ%"
  echo set "JARVIS_AGENTE=%JARVIS_AGENTE%"
  echo set "VOICESTUDIO_VOZ=%VOICESTUDIO_VOZ%"
  echo set "PEDRO_CEREBRO=%PEDRO_CEREBRO%"
)
exit /b
