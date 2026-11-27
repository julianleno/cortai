# Ponto de entrada de `python -m cortai` e do Cortai.exe (PyInstaller). Usa import
# absoluto: o app.py tem imports relativos e não roda como script solto.
from cortai.app import main

main()
