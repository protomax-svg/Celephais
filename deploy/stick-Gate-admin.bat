@echo off
rem Celephais Gate, elevated. Goes on the stick beside Gate.exe.
rem
rem tailscaled cannot create its own control pipe under a plain Windows
rem account: the pipe it asks for is owned by the Administrators group, and
rem Windows refuses it with "Access is denied". So a Windows machine that is
rem NOT already on the tailnet can only reach Core through a Gate started
rem with administrator rights - this file.
rem
rem On a machine that IS on the tailnet, Gate.exe on its own is enough and
rem does not ask for anything: it notices Core already answers and never
rem starts a network of its own.
powershell -NoProfile -Command "Start-Process -FilePath '%~dp0Gate.exe' -Verb RunAs"
