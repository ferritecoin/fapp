@echo off
rem Build Ferritext's Windows executable from a Command Prompt, as CI builds
rem the release:
rem
rem     desktop\build_exe.bat
rem
rem It needs Python 3.13 (python.org, with its "py" launcher) and Visual
rem Studio's C++ build tools. The first build compiles for an hour or so;
rem later ones reuse what did not change. The executable is left in
rem desktop\dist\. Then check it: desktop\smoke_exe.py starts it as a person
rem would and holds it against plain Python (video, screen sharing, remote
rem control: the executable must not be able to do less).
rem
rem Anything after the name goes to build_desktop.py (--out, --name). Set
rem PY to a Python that already has desktop\requirements-exe.txt to skip
rem the environment (CI does).
setlocal
cd /d "%~dp0.."
if defined PY goto :build
set "PY=desktop\build\venv\Scripts\python.exe"
if not exist "%PY%" py -3.13 -m venv desktop\build\venv || goto :failed
"%PY%" -m pip install --quiet --upgrade pip || goto :failed
"%PY%" -m pip install --quiet -r desktop\requirements-exe.txt || goto :failed

:build
"%PY%" desktop\build_desktop.py --onefile %* || goto :failed
echo.
echo Built. Check it: python desktop\smoke_exe.py ^<the executable^>
exit /b 0

:failed
echo.
echo [ERROR] The build failed: see the output above.
exit /b 1
