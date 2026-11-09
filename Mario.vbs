' Mario'yu cift tikla baslatir: sunucu kapaliysa pencere acmadan baslatir, sonra Mario'yu
' Chrome'da uygulama penceresi olarak acar. Asil is backend\scripts\start-mario.ps1'de.
' (VBScript ANSI okur: bu dosyada Turkce karakter kullanma.)
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
script = root & "\backend\scripts\start-mario.ps1"

If Not fso.FileExists(script) Then
  MsgBox "Baslatici bulunamadi: " & script, vbCritical, "Mario"
  WScript.Quit 1
End If

' 0 = gizli pencere, False = bekleme
CreateObject("WScript.Shell").Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & script & """", 0, False
