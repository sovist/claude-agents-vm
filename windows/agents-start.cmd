@echo off
rem Starts all agents on the VM ("agents.sh start-all": each resumes its last conversation), then opens
rem one Windows Terminal window: first a tab with "agents.sh watch", then a tab with agents-notify (Windows
rem notifications for events that need you), then a tab per agent, attached to that agent's Claude Code
rem session. The window opens on the watch tab.
rem In an agent tab, detach with Ctrl+B, then D; the agent keeps running. Closing a tab only detaches too.
rem Closing the notify tab stops the notifications only; the watcher on the VM keeps recording events.
rem
rem It waits 15 s before doing anything, then until the VM answers on ssh (up to 5 minutes), so it can run
rem at Windows logon (a shortcut in the Startup folder) while the VM is still booting.
rem
rem   agents-start.cmd            start the agents and open the tabs
rem   agents-start.cmd /now       without the 15 s wait
rem   agents-start.cmd /nowatch   without the watch tab
rem   agents-start.cmd /nonotify  without the notify tab
rem   agents-start.cmd /dry       only print the Windows Terminal command (no wait)
setlocal EnableDelayedExpansion

rem The VM's ssh host alias and the repo folder on it (docs/ssh-setup.md); set AGENTS_VM_HOST or AGENTS_VM_DIR to change them.
if not defined AGENTS_VM_HOST set "AGENTS_VM_HOST=agents"
if not defined AGENTS_VM_DIR set "AGENTS_VM_DIR=~/claude-agents-vm"

set "NOWATCH="
set "NONOTIFY="
set "DRY="
set "NOW="
for %%f in (%*) do (
    if /i "%%f"=="/nowatch" set "NOWATCH=1"
    if /i "%%f"=="/nonotify" set "NONOTIFY=1"
    if /i "%%f"=="/dry" set "DRY=1"
    if /i "%%f"=="/now" set "NOW=1"
)

if not defined DRY if not defined NOW (
    echo Starting the agents in 15 s... ^(Ctrl+C stops; agents-start /now skips the wait^)
    timeout /t 15 /nobreak >nul 2>&1 || ping -n 16 127.0.0.1 >nul
)

rem At logon the VM may still be booting: try about every 15 s (a 5 s connect timeout plus 10 s) until ssh
rem answers, for about 5 minutes.
set "TRIES=0"
:waitvm
ssh -o BatchMode=yes -o ConnectTimeout=5 %AGENTS_VM_HOST% true >nul 2>&1 && goto vmready
set /a TRIES+=1
if !TRIES! geq 20 (
    echo The VM doesn't answer on ssh after 5 minutes; is it running?
    exit /b 1
)
if !TRIES!==1 echo Waiting for the VM to answer on ssh...
ping -n 11 127.0.0.1 >nul
goto waitvm
:vmready

set "WT_ARGS="
if not defined NOWATCH set "WT_ARGS=-w new new-tab --title watch --suppressApplicationTitle ssh -t %AGENTS_VM_HOST% %AGENTS_VM_DIR%/agents.sh watch"
if not defined NONOTIFY (
    if defined WT_ARGS (set "WT_ARGS=!WT_ARGS! ; new-tab") else (set "WT_ARGS=-w new new-tab")
    set "WT_ARGS=!WT_ARGS! --title notify --suppressApplicationTitle %~dp0agents-notify.cmd"
)

set "COUNT=0"
echo Starting agents on the VM...
rem start-all prints one line per agent, starting with its name: "agent-1: already running".
for /f "tokens=1,* delims=:" %%a in ('ssh %AGENTS_VM_HOST% "%AGENTS_VM_DIR%/agents.sh start-all"') do (
    echo   %%a:%%b
    echo %%a| findstr /r /x "[a-z0-9][a-z0-9-]*" >nul && (
        if defined WT_ARGS (set "WT_ARGS=!WT_ARGS! ; new-tab") else (set "WT_ARGS=-w new new-tab")
        set "WT_ARGS=!WT_ARGS! --title %%a --suppressApplicationTitle ssh -t %AGENTS_VM_HOST% %AGENTS_VM_DIR%/agents.sh attach %%a"
        set /a COUNT+=1
    )
)

if "!COUNT!"=="0" (
    echo No agents found on the VM.
    exit /b 1
)
rem Windows Terminal shows the last tab it created; switch back to the first one, the watch tab.
if not defined NOWATCH set "WT_ARGS=!WT_ARGS! ; focus-tab -t 0"

if defined DRY (
    echo wt !WT_ARGS!
    exit /b 0
)
set "TABS=!COUNT! agent tabs"
if not defined NONOTIFY set "TABS=the notify tab and !TABS!"
if not defined NOWATCH set "TABS=the watch tab, !TABS!"
echo Opening !TABS!...
wt !WT_ARGS!
