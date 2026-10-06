@echo off
rem Installs, updates and starts pulserver's virtual scanner in Docker for
rem MaRGE's page, with the scanner settings the page wrote into this file.
rem Double-click it; run it again to apply new settings or to take a newer image.
rem Run so, it also copies itself to %USERPROFILE%\.pulserver and registers that
rem copy as the handler of pulserver: links, which the page's Start button opens
rem with its settings: pulserver:start/x<limits>.x<sequences>.x<recon>, each part
rem base64url behind an x. Run with such a link, it takes the settings from it.
setlocal
set "IMAGE=ghcr.io/pulserver/pulserver"
set "NAME=pulserver"
set "PAGE=@PAGE@"
set "SEQUENCES=@SEQUENCES@"
set "RECON=@RECON@"
if "%PULSERVER_HOME%"=="" (set "HOME_DIR=%USERPROFILE%\.pulserver") else (set "HOME_DIR=%PULSERVER_HOME%")

where docker >nul 2>&1
if errorlevel 1 (
    echo Docker is not installed: install it from https://docs.docker.com/get-started/get-docker/ and run this again.
    goto :failed
)
call docker info >nul 2>&1
if errorlevel 1 (
    echo Docker is installed but not running: start Docker Desktop and run this again.
    goto :failed
)

if not exist "%HOME_DIR%" mkdir "%HOME_DIR%"
set "LIMITS=%HOME_DIR%\limits.txt"
set "LINK=%~1"
if not "%LINK%"=="" goto :from_link
>"%LIMITS%" echo [Limits]
@LIMITS@
>>"%LIMITS%" echo [Limits End]
if /i not "%~f0"=="%HOME_DIR%\pulserver.bat" copy /y "%~f0" "%HOME_DIR%\pulserver.bat" >nul
reg add "HKCU\Software\Classes\pulserver" /ve /d "URL:pulserver" /f >nul
reg add "HKCU\Software\Classes\pulserver" /v "URL Protocol" /d "" /f >nul
reg add "HKCU\Software\Classes\pulserver\shell\open\command" /ve /d "\"%HOME_DIR%\pulserver.bat\" \"%%1\"" /f >nul
echo Start pulserver on the page now starts it with the page's settings.
goto :docker

:from_link
set "PAGE="
for /f "tokens=2-4 delims=/." %%a in ("%LINK%") do (
    set "PART_L=%%a"
    set "PART_S=%%b"
    set "PART_R=%%c"
)
call :decode "%PART_L%" "%LIMITS%"
set "SEQUENCES="
set "RECON="
call :decode "%PART_S%" "%HOME_DIR%\sequences.txt"
if exist "%HOME_DIR%\sequences.txt" set /p SEQUENCES=<"%HOME_DIR%\sequences.txt"
call :decode "%PART_R%" "%HOME_DIR%\recon.txt"
if exist "%HOME_DIR%\recon.txt" set /p RECON=<"%HOME_DIR%\recon.txt"

:docker

set "BEFORE="
for /f "delims=" %%i in ('docker image inspect --format "{{.Id}}" %IMAGE% 2^>nul') do set "BEFORE=%%i"
if "%BEFORE%"=="" (echo pulserver is not installed: downloading %IMAGE%.) else (echo pulserver is installed: checking for a newer version.)
call docker pull %IMAGE%
if errorlevel 1 (
    if "%BEFORE%"=="" (
        echo pulserver could not be downloaded.
        goto :failed
    )
    echo The newest version could not be checked: starting the installed one.
)
set "AFTER="
for /f "delims=" %%i in ('docker image inspect --format "{{.Id}}" %IMAGE% 2^>nul') do set "AFTER=%%i"
if "%BEFORE%"=="" (
    echo pulserver is installed.
) else if "%BEFORE%"=="%AFTER%" (
    echo pulserver is up to date.
) else (
    echo pulserver was outdated and is updated.
)

set "DIGEST="
for /f "delims=" %%i in ('docker image inspect --format "{{index .RepoDigests 0}}" %IMAGE% 2^>nul') do set "DIGEST=%%i"
set MOUNTS=-e "PULSERVER_IMAGE=%DIGEST%" -v "%LIMITS%:/console/limits.txt:ro"
if not "%SEQUENCES%"=="" set MOUNTS=%MOUNTS% -v "%SEQUENCES%:/console/user/plugins:ro"
if not "%RECON%"=="" set MOUNTS=%MOUNTS% -v "%RECON%:/console/user/recon:ro"
call docker rm -f %NAME% >nul 2>&1
call docker run -d --restart unless-stopped --name %NAME% -p 127.0.0.1:8765:8765 %MOUNTS% %IMAGE% >nul
if errorlevel 1 goto :failed
echo pulserver is running with these settings.
if not "%PAGE%"=="" start "" "%PAGE%"
if "%LINK%"=="" pause
exit /b 0

:failed
pause
exit /b 1

rem Writes the base64url part %1, behind its x, decoded into the file %2.
:decode
set "PART=%~1"
set "PART=%PART:~1%"
if exist "%~2" del "%~2"
if "%PART%"=="" exit /b 0
set "PART=%PART:-=+%"
set "PART=%PART:_=/%"
>"%TEMP%\pulserver.b64" echo %PART%
certutil -f -decode "%TEMP%\pulserver.b64" "%~2" >nul
del "%TEMP%\pulserver.b64"
exit /b 0
