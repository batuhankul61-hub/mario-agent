# Builds dist\Mario\Mario.exe and dist\Mario-<version>-windows.zip (the file to share).
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
Push-Location $backend
try {
  uv sync --group build
  if ($LASTEXITCODE) { throw 'uv sync failed' }
  # The exe icon, drawn from the same shape as frontend/icons/icon.svg.
  uv run python -c "from app.desktop import icon_image; icon_image(256).save(r'$root\packaging\mario.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
  if ($LASTEXITCODE) { throw 'icon failed' }
  uv run pyinstaller --noconfirm --clean --distpath "$root\dist" --workpath "$root\build" "$root\packaging\mario.spec"
  if ($LASTEXITCODE) { throw 'pyinstaller failed' }
  $version = (Select-String -Path pyproject.toml -Pattern '^version = "(.+)"').Matches[0].Groups[1].Value
} finally { Pop-Location }

Copy-Item (Join-Path $root 'packaging\BENIOKU.txt') (Join-Path $root 'dist\Mario\BENIOKU.txt')
$zip = Join-Path $root "dist\Mario-$version-windows.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path (Join-Path $root 'dist\Mario') -DestinationPath $zip
Write-Host "Hazir: $zip"
