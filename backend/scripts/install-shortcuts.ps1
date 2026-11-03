# Masaüstüne "Mario" kısayolu (sunucuyu gerekirse başlatıp Mario'yu açar) ve Başlangıç klasörüne
# Windows açılınca sunucuyu sessizce başlatan bir kısayol koyar. -Remove: ikisini de kaldırır.
param([switch]$Remove)

$start = Join-Path $PSScriptRoot 'start-mario.ps1'
$links = @{
  (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Mario.lnk') = ''
  (Join-Path ([Environment]::GetFolderPath('Startup')) 'Mario.lnk') = ' -NoBrowser'
}

foreach ($path in $links.Keys) {
  if ($Remove) {
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path; "Kaldırıldı: $path" }
    continue
  }
  $lnk = (New-Object -ComObject WScript.Shell).CreateShortcut($path)
  $lnk.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
  $lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$start`"$($links[$path])"
  $lnk.WorkingDirectory = Split-Path -Parent $PSScriptRoot
  $lnk.WindowStyle = 7   # simge durumunda: PowerShell penceresi görünmesin
  $lnk.IconLocation = "$env:SystemRoot\System32\SndVol.exe,0"
  $lnk.Description = 'Mario sesli asistan'
  $lnk.Save()
  "Kuruldu: $path"
}
