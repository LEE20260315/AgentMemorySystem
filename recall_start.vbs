' recall_start.vbs - silently start the sync daemon in the background (no window).
' Prefers dist\recall.exe (no Python needed); falls back to pythonw.
' Stop: recall_stop.bat, or Task Manager -> end recall.exe
Option Explicit
Dim fso, sh, here, exe
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = here
exe = here & "\dist\recall.exe"
If fso.FileExists(exe) Then
  sh.Run """" & exe & """ watch", 0, False
Else
  sh.Run "cmd /c ""pythonw -m recall watch""", 0, False
End If