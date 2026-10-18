"""Build a self-contained Apple Silicon app without user credentials."""
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / '.macos-build'
APP = BUILD / 'Freqtrade Bolsa.app'
BUILD.mkdir(exist_ok=True)
subprocess.run([
    sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
    '--name', 'equities-backend', '--paths', str(ROOT),
    '--distpath', str(BUILD / 'dist'), '--workpath', str(BUILD / 'work'),
    '--specpath', str(BUILD), '--target-architecture', 'arm64',
    '--collect-data', 'certifi',
    '--add-data', f'{ROOT}/equities/dashboard_ui.js:equities',
    '--add-data', f'{ROOT}/equities/dashboard.html:equities',
    '--add-data', f'{ROOT}/equities/backtest_ui.js:equities',
    str(ROOT / 'packaging/macos/backend_entry.py'),
], check=True)
if APP.exists():
    shutil.rmtree(APP)
macos = APP / 'Contents/MacOS'
resources = APP / 'Contents/Resources'
macos.mkdir(parents=True)
resources.mkdir()
subprocess.run(['xcrun', 'swiftc', str(ROOT / 'packaging/macos/Launcher.swift'),
                '-o', str(macos / 'FreqtradeBolsa'), '-target', 'arm64-apple-macosx12.0',
                '-framework', 'Cocoa', '-framework', 'WebKit'], check=True)
shutil.copytree(BUILD / 'dist/equities-backend', resources / 'backend', symlinks=True)
from PIL import Image
Image.open(ROOT / 'freqtrade/rpc/api_server/ui/favicon.ico').convert('RGBA').resize((1024, 1024)).save(resources / 'AppIcon.icns')
info = {
    'CFBundleIdentifier': 'local.freqtrade.bolsa', 'CFBundleExecutable': 'FreqtradeBolsa',
    'CFBundleName': 'Freqtrade Bolsa', 'CFBundleDisplayName': 'Freqtrade Bolsa',
    'CFBundleShortVersionString': '1.0', 'CFBundleVersion': '1', 'CFBundlePackageType': 'APPL',
    'LSMinimumSystemVersion': '12.0', 'NSHighResolutionCapable': True,
    'NSPrincipalClass': 'NSApplication', 'CFBundleIconFile': 'AppIcon.icns',
    'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True},
}
with (APP / 'Contents/Info.plist').open('wb') as output:
    plistlib.dump(info, output)
subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(APP)], check=True)
subprocess.run(['codesign', '--verify', '--deep', '--strict', str(APP)], check=True)
print(APP)
