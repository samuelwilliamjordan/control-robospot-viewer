import os
import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

script_name = "main.py"  

import cv2
cv2_path = os.path.dirname(cv2.__file__)
cv2_dlls = [(os.path.join(cv2_path, f), "cv2") for f in os.listdir(cv2_path) if f.endswith(".dll")]


wsd_hidden = collect_submodules("wsdiscovery")
wsd_data = collect_data_files("wsdiscovery")


block_cipher = None

a = Analysis(
    [viewer.py],
    pathex=[os.getcwd()],
    binaries=cv2_dlls,
    datas=wsd_data,
    hiddenimports=wsd_hidden + [
        "xml.etree.ElementTree",
        "xml.dom.minidom",
        "xml.sax",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="CameraViewer",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=None,
)

