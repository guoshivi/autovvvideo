Option Explicit
Dim sh, fs, root, py
Set sh=CreateObject("WScript.Shell")
Set fs=CreateObject("Scripting.FileSystemObject")
root=fs.GetParentFolderName(WScript.ScriptFullName)
py=root & "\.runtime\embedding\Scripts\pythonw.exe"
If Not fs.FileExists(py) Then
  sh.Popup "Project Python is missing. Ask your Agent to finish docs/DEPLOY.md.",0,"autovvvideo",16
  WScript.Quit 2
End If
sh.Run Chr(34) & py & Chr(34) & " " & Chr(34) & root & "\launcher.pyw" & Chr(34),0,False
