# Mario'nun take_screenshot aracı bunu çağırır (JARVIS-ekler\ekran-goruntusu.ps1'den uyarlandı): ekran görüntüsü
# alır ve Resimler\Screenshots klasörüne kaydeder. Son satır her zaman "...: <dosya yolu>" ya da "Hata: ..." olur.
# Argüman: boş ya da 'ekran' (tüm ekranlar) veya bir pencere/program adı ('powershell', 'chrome', 'Flo Desk').
# Kullanım: powershell -File screenshot.ps1 'chrome'
[Console]::OutputEncoding = [Text.Encoding]::UTF8   # Python tarafı çıktıyı UTF-8 okur
$istek = ($args -join ' ').Trim()

Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class Pencere {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr h, int a, out RECT r, int s);
}
'@
[void][Pencere]::SetProcessDPIAware()   # yüksek DPI'da kırpık görüntü olmasın

try {
  if (-not $istek -or $istek -match '^(ekran|tum ekran|tüm ekran|masaüstü|masaustu)$') {
    $alan = [Windows.Forms.SystemInformation]::VirtualScreen
    $ne = 'Ekranın'
  } else {
    # Başlığında ya da program adında istenen sözcük geçen, görünür penceresi olan ilk süreç
    $s = Get-Process | Where-Object { $_.MainWindowHandle -ne 0 -and ($_.ProcessName -like "*$istek*" -or $_.MainWindowTitle -like "*$istek*") } |
         Sort-Object StartTime -Descending -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $s) { "Hata: '$istek' adında açık bir pencere bulunamadı."; exit 1 }
    $h = $s.MainWindowHandle
    if ([Pencere]::IsIconic($h)) { [void][Pencere]::ShowWindow($h, 9) }
    [void][Pencere]::SetForegroundWindow($h)
    Start-Sleep -Milliseconds 400        # pencere öne gelip çizilsin
    $r = New-Object Pencere+RECT
    [void][Pencere]::DwmGetWindowAttribute($h, 9, [ref]$r, [Runtime.InteropServices.Marshal]::SizeOf($r))  # gölgesiz sınırlar
    $alan = [Drawing.Rectangle]::FromLTRB($r.Left, $r.Top, $r.Right, $r.Bottom)
    if ($alan.Width -le 0 -or $alan.Height -le 0) { "Hata: '$istek' penceresinin boyutu okunamadı."; exit 1 }
    $ne = "$($s.ProcessName) penceresinin"
  }

  $klasor = [Environment]::GetFolderPath('MyPictures') + '\Screenshots'
  New-Item -ItemType Directory -Force -Path $klasor | Out-Null
  $ad = 'Ekran görüntüsü ' + (Get-Date -Format 'yyyy-MM-dd HHmmss')
  $yol = Join-Path $klasor "$ad.png"
  for ($i = 2; Test-Path -LiteralPath $yol; $i++) { $yol = Join-Path $klasor "$ad ($i).png" }

  $bmp = New-Object Drawing.Bitmap $alan.Width, $alan.Height
  $g = [Drawing.Graphics]::FromImage($bmp)
  $g.CopyFromScreen($alan.Location, [Drawing.Point]::Empty, $alan.Size)
  $bmp.Save($yol, [Drawing.Imaging.ImageFormat]::Png)
  $g.Dispose(); $bmp.Dispose()

  "$ne görüntüsü alındı ve kaydedildi: $yol"
} catch {
  "Hata: Ekran görüntüsü alınamadı ($($_.Exception.Message))."
  exit 1
}
