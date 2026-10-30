' HoverBar launcher - starts the bar silently (no console window).
' Put a shortcut to this file into shell:startup to run it at logon.
' NOTE: keep this file ASCII-only. WScript reads .vbs as ANSI, so UTF-8
'       comments would eat the line breaks and break the script.
Option Explicit

Dim fso, ws, dir, exe, q, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set ws  = CreateObject("WScript.Shell")
q = Chr(34)

dir = fso.GetParentFolderName(WScript.ScriptFullName)

If fso.FileExists(dir & "\.venv\Scripts\pythonw.exe") Then
    exe = dir & "\.venv\Scripts\pythonw.exe"
Else
    exe = "pythonw.exe"
End If

cmd = q & exe & q & " " & q & dir & "\hoverbar.pyw" & q
ws.Run cmd, 0, False
