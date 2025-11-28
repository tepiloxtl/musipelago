# -*- mode: python ; coding: utf-8 -*-
from kivy_deps import sdl2, glew
from PyInstaller.utils.hooks import collect_data_files
import os
spec_dir = SPECPATH
src_dir = os.path.join(spec_dir, 'src', 'musipelago')

block_cipher = None
plugin_dependencies = [
    'mutagen',
    'requests',
    'plyer',
    'plyer.platforms.win.filechooser', # Explicitly include Windows filechooser
    'python-vlc',
    'win32timezone'
]

shared_modules = [
    'musipelago.utils_client',
    'musipelago.client_ui_components',
    'musipelago.utils'
]

shared_datas = [
    (os.path.join(src_dir, 'musipelagoapwgen.kv'), '.'), 
    (os.path.join(src_dir, 'musipelagoclient.kv'), '.'),
    (os.path.join(src_dir, 'resources'), 'resources'), # Icons
    (os.path.join(src_dir, 'plugins'), 'plugins'),     # The plugin python files
    (os.path.join(src_dir, 'vlc_engine'), 'vlc_engine')
]


gen_a = Analysis(
    [src_dir + '/musipelago_apworld_gen.py'],
    pathex=[src_dir],
    binaries=[],
    datas=shared_datas + [
        (os.path.join(src_dir, 'apworld_template'), 'apworld_template'),
        *collect_data_files('jinja2')
    ],
    hiddenimports=['jinja2.ext'] + plugin_dependencies+ shared_modules,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
gen_pyz = PYZ(gen_a.pure, gen_a.zipped_data, cipher=block_cipher)

gen_exe = EXE(
    gen_pyz,
    gen_a.scripts,
    [],
    exclude_binaries=True,
    name='musipelago_apworld_gen',
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

client_a = Analysis(
    [src_dir + '/musipelago_client.py'],
    pathex=[src_dir],
    binaries=[],
    datas=shared_datas,
    hiddenimports=plugin_dependencies+ shared_modules,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
client_pyz = PYZ(client_a.pure, client_a.zipped_data, cipher=block_cipher)

client_exe = EXE(
    client_pyz,
    client_a.scripts,
    [],
    exclude_binaries=True,
    name='musipelago_client',
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


coll = COLLECT(
    gen_exe,
    gen_a.binaries,
    gen_a.zipfiles,
    gen_a.datas,
    client_exe,
    client_a.binaries,
    client_a.zipfiles,
    client_a.datas,
    *[Tree(p) for p in (sdl2.dep_bins + glew.dep_bins)],
    strip=False,
    upx=True,
    upx_exclude=[],
    name='musipelago',
)