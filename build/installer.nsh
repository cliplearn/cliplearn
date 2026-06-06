; ────────────────────────────────────────────────────────────
; ClipLearn NSIS 安装脚本 (electron-builder 注入)
; 特点：无需管理员权限 | 开机自启 | 2键安装
; ────────────────────────────────────────────────────────────

; 安装时清理旧残留（旧版 HKCU Run / Electron 自启）
!macro customInit
  ; 删除旧版 Electron setLoginItemSettings 写入的注册表项
  DeleteRegValue HKCU "SOFTWARE\Microsoft\Windows\CurrentVersion\Run" "ClipLearn"
  DeleteRegValue HKCU "SOFTWARE\Microsoft\Windows\CurrentVersion\Run" "cliplearn"
  ; 删除旧版可能写入的 startup 快捷方式
  Delete "$SMSTARTUP\ClipLearn.lnk"
  Delete "$SMSTARTUP\cliplearn.lnk"
!macroend

; 安装完成后操作
!macro customInstall
  ; Windows 开机自动启动 — 写入 HKCU（无需管理员权限）
  WriteRegStr HKCU "SOFTWARE\Microsoft\Windows\CurrentVersion\Run" "ClipLearn" '"$INSTDIR\ClipLearn.exe" --autostart'
  ; 保存安装信息
  WriteRegStr HKCU "SOFTWARE\ClipLearn" "InstallPath" "$INSTDIR"
  WriteRegStr HKCU "SOFTWARE\ClipLearn" "Version" "${VERSION}"
  ; 创建音频目录
  CreateDirectory "$INSTDIR\clip_audios"
!macroend

; 卸载时清理
!macro customUnInstall
  DeleteRegValue HKCU "SOFTWARE\Microsoft\Windows\CurrentVersion\Run" "ClipLearn"
  DeleteRegValue HKCU "SOFTWARE\Microsoft\Windows\CurrentVersion\Run" "cliplearn"
  DeleteRegKey HKCU "SOFTWARE\ClipLearn"
  RMDir /r "$INSTDIR\clip_audios"
!macroend
