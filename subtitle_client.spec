# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# customtkinter 需要帶入其 data files（主題 JSON 等）
ctk_datas = collect_data_files("customtkinter")

a = Analysis(
    ["subtitle_client.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("silero_vad_v6.onnx", "."),
        ("NotoSansTC-SemiBold.ttf", "."),
        *ctk_datas,
    ],
    hiddenimports=[
        "pyaudiowpatch",
        "scipy.signal",
        "scipy._lib.messagestream",
        "opencc",
    ],
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
    [],
    exclude_binaries=True,
    name="RealtimeSubtitle",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # 不顯示黑色 console 視窗
    disable_windowed_traceback=False,
    argv_emulation=False,
    contents_directory=".",  # 所有 DLL/資源與 exe 同層，避免 _internal 路徑問題
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="RealtimeSubtitle",
)
