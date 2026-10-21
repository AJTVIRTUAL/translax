@echo off
rem TRANSLAX -- serveur de traduction local compatible DeepL (voir SPEC.md, section 5 tricies octies).
rem Double-clic : demarre le serveur sur http://127.0.0.1:8765 avec MADLAD-400 (CTranslate2 int8).
rem Arguments en plus transmis tels quels, par exemple :
rem     serveur_traduction.bat --engine opus-mt
rem     serveur_traduction.bat --port 8800 --auth-key ma-cle
rem Arret : Ctrl+C dans cette fenetre, ou la fermer.
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
title TRANSLAX - serveur de traduction
"%PY%" cli.py serve --port 8765 --engine madlad-ct2 %*
echo.
echo Serveur arrete.
pause
