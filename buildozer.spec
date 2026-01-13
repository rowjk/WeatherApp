[app]

# (str) Title of your application
title = WeatherApp

# (str) Package name
package.name = weatherapp

# (str) Package domain (needed for android/ios packaging)
package.domain = org.wkj

# (str) Source code where the main.py live
source.dir = .

# (list) Source files to include (let empty to include all the files)
# [關鍵] 包含 json 設定檔與 ttf 字體檔
source.include_exts = py,png,jpg,kv,atlas,json,ttf,txt

# (list) List of exclusions using pattern matching
# 排除無用的暫存檔與虛擬環境
source.exclude_patterns = license,images/*/*.jpg,*.pyc,*.pyo,*.md,PKG-INFO,setup.py,*.txt,env/*,venv/*,__pycache__/*

# (str) Application versioning (method 1)
version = 0.052

# (list) Application requirements
# [關鍵修正] 移除了 openssl 以避免 Colab 編譯失敗
# 保留 pillow 用於圖片處理，requests 用於網路請求
requirements = python3,kivy==2.3.0,kivymd==1.1.1,requests,pillow

# (str) Presplash of the application
# 啟動畫面圖示
presplash.filename = %(source.dir)s/icon.png

# (str) Icon of the application
# APP 圖示
icon.filename = %(source.dir)s/icon.png

# (str) Supported orientation (one of landscape, sensorLandscape, portrait or all)
orientation = portrait

# (list) List of service to declare
#services = NAME:NAME.py

#
# Android Specific
#

# (bool) Indicate if the application should be fullscreen or not
fullscreen = 0

# (string) Presplash background color (for android)
android.presplash_color = #FFFFFF

# (list) Permissions
# [關鍵] 開啟網路權限
android.permissions = INTERNET, ACCESS_NETWORK_STATE

# (int) Target Android API, should be as high as possible.
android.api = 33

# (int) Minimum API your APK will support.
android.minapi = 21

# (bool) If True, then automatically accept SDK license
android.accept_sdk_license = True

# (str) The format used to package the app for debug mode (apk or aar).
android.debug_artifact = apk

# (list) The architectures to build for
# [相容性優化] 同時打包 64 位元與 32 位元，確保所有手機都能安裝
android.archs = arm64-v8a, armeabi-v7a

# (bool) enables Android auto backup feature (Android API >=23)
android.allow_backup = True

[buildozer]

# (int) Log level (0 = error only, 1 = info, 2 = debug (with command output))
log_level = 2

# (int) Display warning if buildozer is run as root (0 = False, 1 = True)
warn_on_root = 1