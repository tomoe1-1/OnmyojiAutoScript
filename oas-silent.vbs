Option Explicit
Dim fs, shell, root, python, env
Set fs = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fs.GetParentFolderName(WScript.ScriptFullName)
python = fs.BuildPath(root, "toolkit\pythonw.exe")
If Not fs.FileExists(python) Then
    MsgBox "Missing toolkit\pythonw.exe", 16, "OAS"
    WScript.Quit 1
End If
shell.CurrentDirectory = root
Set env = shell.Environment("PROCESS")
env("PATH") = root & "\toolkit;" & root & "\toolkit\Scripts;" & root & "\toolkit\Git\mingw64\bin;" & root & "\toolkit\Lib\site-packages\adbutils\binaries;" & env("PATH")
shell.Run Chr(34) & python & Chr(34) & " " & Chr(34) & fs.BuildPath(root, "server.py") & Chr(34), 0, False
