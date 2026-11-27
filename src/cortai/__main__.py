# Ponto de entrada de `python -m cortai` e do Cortai.exe (PyInstaller). Usa import
# absoluto: o app.py tem imports relativos e não roda como script solto.
import os
import sys

# O .exe sem console não tem stdout/stderr: qualquer print ou barra de progresso
# (tqdm, faster-whisper) quebraria. Manda tudo para um log ao lado do executável.
if sys.stdout is None or sys.stderr is None:
    _log = open(os.path.join(os.path.dirname(sys.executable), "cortai.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _log
    sys.stderr = sys.stderr or _log

from cortai.app import main

main()
