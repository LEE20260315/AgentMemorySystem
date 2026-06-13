Set fso = CreateObject("Scripting.FileSystemObject")
Set objShell = CreateObject("WScript.Shell")
objShell.CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName)

' Find python first
Dim pyExe, pywExe
pyExe = ""
pywExe = ""

' Use python for pip (needs console), derive pythonw from same directory
Dim whereRet
whereRet = objShell.Run("cmd /c where python >nul 2>&1", 0, True)
If whereRet = 0 Then
    ' Get full path of python
    Dim execObj
    Set execObj = objShell.Exec("cmd /c where python")
    pyExe = Trim(execObj.StdOut.ReadLine())
    ' Derive pythonw from same directory
    Dim pyDir
    pyDir = fso.GetParentFolderName(pyExe)
    Dim pywPath
    pywPath = pyDir & "\pythonw.exe"
    If fso.FileExists(pywPath) Then
        pywExe = pywPath
    End If
End If

' Fallback: try where pythonw
If pywExe = "" Then
    whereRet = objShell.Run("cmd /c where pythonw >nul 2>&1", 0, True)
    If whereRet = 0 Then
        Set execObj = objShell.Exec("cmd /c where pythonw")
        pywExe = Trim(execObj.StdOut.ReadLine())
    End If
End If

' If still no pythonw, use python
If pywExe = "" Then pywExe = pyExe

' If no python at all, error out
If pyExe = "" Then
    MsgBox "Python not found. Please install Python 3.8+ first.", 16, "Error"
    WScript.Quit
End If

' Check and install dependencies using python (not pythonw, pip needs console)
Dim checkCmd
checkCmd = "cmd /c """ & pyExe & """ -c ""import pystray, PIL"" >nul 2>&1"
Dim ret
ret = objShell.Run(checkCmd, 0, True)

If ret <> 0 Then
    objShell.Run "cmd /c """ & pyExe & """ -m pip install pystray Pillow --quiet --disable-pip-version-check", 0, True
End If

' Launch GUI with pythonw (no console window)
objShell.Run "cmd /c """ & pywExe & """ memory_sync_app.py", 0, False
