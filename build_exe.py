"""Script de compilação do Cortaí para Executável (.exe) standalone usando PyInstaller.

Uso:
    python build_exe.py
"""
import os
import sys
import subprocess

def build():
    print("=== Compilando Cortaí para Executável Windows (.exe) ===")
    
    # Garantir que o PyInstaller está instalado
    try:
        import PyInstaller
    except ImportError:
        print("Instalando PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
        
    spec_content = """# -*- mode: python ; coding: utf-8 -*-
import sys
from PyInstaller.utils.hooks import collect_all, collect_data_files

datas = [('src/cortai/templates', 'cortai/templates'), ('src/cortai/models', 'cortai/models')]
# Modelo de detecção de voz (silero VAD) usado pela transcrição
datas += collect_data_files('faster_whisper')
binaries = []
hiddenimports = ['cortai', 'flask', 'faster_whisper', 'cv2', 'numpy', 'httpx', 'srt', 'tqdm']

a = Analysis(
    ['src/cortai/__main__.py'],
    pathex=['src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Cortai',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
"""
    
    with open("cortai.spec", "w", encoding="utf-8") as f:
        f.write(spec_content)
        
    print("Executando PyInstaller...")
    subprocess.check_call([sys.executable, "-m", "PyInstaller", "--noconfirm", "cortai.spec"])
    print("\n✅ Compilação concluída com sucesso! O executável Cortai.exe está salvo na pasta 'dist/Cortai.exe'.")

if __name__ == "__main__":
    build()
