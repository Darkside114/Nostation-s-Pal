' Launches the NOSTATION clock-sync watcher with no window at all.
' Used by the Startup shortcut (and by the scheduled task, if registered).
Option Explicit

Dim shell, fso, here, pyw, script, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

here = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = here & "\python\pythonw.exe"
script = here & "\nostation_watcher.py"

If Not fso.FileExists(pyw) Then
    WScript.Quit 3
End If
If Not fso.FileExists(script) Then
    WScript.Quit 4
End If

cmd = Chr(34) & pyw & Chr(34) & " " & Chr(34) & script & Chr(34) & " --interval 3"
' 0 = hidden window, False = do not wait
shell.Run cmd, 0, False
