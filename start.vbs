' SysMon — 桌面系统监控条启动脚本（后台静默运行）
' 将此文件快捷方式放入 shell:startup 可实现开机自启
Set ws = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
ws.Run "cmd /c cd /d " & dir & " && .venv\Scripts\pythonw.exe sysmon.pyw", 0, False
