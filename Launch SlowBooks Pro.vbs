' ============================================================================
' SlowBooks Pro 2026 -- hidden daily launcher (no console window).
'
' This is what the "SlowBooks Pro" Desktop shortcut points to. It starts
' the app with pythonw.exe (which never allocates a console) and the
' --hidden flag, so nothing appears on screen but the app window itself.
'
' If something goes wrong before the app window can open, a small popup
' explains it, and full details are written to:
'   %LOCALAPPDATA%\SlowBooksPro\data\launcher.log
'
' Troubleshooting: for live console output instead, double-click
' "Launch SlowBooks Pro.bat" in this same folder.
' ============================================================================
On Error Resume Next

Dim fso, scriptDir, shell
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = scriptDir
shell.Run "pythonw.exe """ & scriptDir & "\desktop_launcher.py"" --hidden", 0, False

If Err.Number <> 0 Then
    MsgBox "Could not start SlowBooks Pro (" & Err.Description & ")." & vbCrLf & _
        "Try double-clicking 'Launch SlowBooks Pro.bat' in the app folder instead.", _
        vbExclamation, "SlowBooks Pro 2026"
End If
