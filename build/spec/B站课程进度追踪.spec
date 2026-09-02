# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:/Users/18509/Desktop/bilibili_work/src/app.py'],
    pathex=['C:/Users/18509/Desktop/bilibili_work/src'],
    binaries=[],
    datas=[('C:/Users/18509/Desktop/bilibili_work/src/index.html', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='B站课程进度追踪',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['C:/Users/18509/Desktop/bilibili_work/assets/app_icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='B站课程进度追踪',
)
