[app]
title = Hearbot
package.name = hearbot
package.domain = org.hearbot
source.dir = .
source.include_exts = py,png,jpg,kv,atlas
version = 0.1.0
requirements = python3,kivy==2.3.0,websocket-client,pyjnius,android
orientation = portrait
fullscreen = 0

android.permissions = RECORD_AUDIO,INTERNET
android.api = 33
android.minapi = 23
android.ndk = 25b
android.archs = arm64-v8a,armeabi-v7a
android.allow_backup = True

[buildozer]
log_level = 2
warn_on_root = 0
