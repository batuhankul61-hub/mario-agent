# Mario'yu başlatır: sunucu çalışmıyorsa arka planda, pencere açmadan başlatır; sonra Mario'yu Chrome'da
# uygulama penceresi olarak açar. -NoBrowser: yalnızca sunucu (Windows açılışında böyle çalışır).
# Kısayolları install-shortcuts.ps1 kurar. Sunucu günlüğü: backend\data\server.log (serve.py yazar)
param([switch]$NoBrowser)

$backend = Split-Path -Parent $PSScriptRoot
$data = Join-Path $backend 'data'
$url = 'http://localhost:8100'

# Port open = server up (same check as JARVIS-ekler\nobetci.py). Invoke-WebRequest stalled here on
# proxy auto-detection when run from a hidden startup window.
function Ayakta {
  $c = New-Object Net.Sockets.TcpClient
  try { $c.ConnectAsync('127.0.0.1', 8100).Wait(1000) -and $c.Connected } catch { $false } finally { $c.Dispose() }
}

if (-not (Ayakta)) {
  # pythonw: no console window. No output redirection here: PowerShell 5.1 would then keep this
  # launcher alive (and take the server down with it). serve.py writes its own log.
  Start-Process -FilePath (Join-Path $backend '.venv\Scripts\pythonw.exe') `
    -ArgumentList "`"$(Join-Path $PSScriptRoot 'serve.py')`"" -WorkingDirectory $backend
  for ($i = 0; $i -lt 40 -and -not (Ayakta); $i++) { Start-Sleep -Milliseconds 500 }
  if (-not (Ayakta)) {
    Add-Type -AssemblyName PresentationFramework
    [void][Windows.MessageBox]::Show("Mario sunucusu başlamadı. Ayrıntı: $data\server.log", 'Mario')
    exit 1
  }
}

if ($NoBrowser) { exit 0 }
$chrome = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe' -ErrorAction SilentlyContinue).'(default)'
if ($chrome) { Start-Process $chrome "--app=$url" } else { Start-Process $url }
