#!/bin/bash
# Removes the app, background service and browser registration. Your rules and vault stay unless you run:
#   ~/.walled/venv/bin/walled uninstall --purge
cd "$(dirname "$0")" && bash ./install.sh --uninstall
echo; read -r -p "Press Enter to close this window. " _
