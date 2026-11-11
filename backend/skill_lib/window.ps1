# Shared helpers for Mario's learned skills. Dot-source it in run.ps1:
#
#     . (Join-Path $env:MARIO_LIB 'window.ps1')
#     if (-not (Set-MarioForeground -ProcessName 'WhatsApp')) { Write-Output 'Hata: WhatsApp penceresi öne getirilemedi.'; exit 1 }
#
# Windows refuses SetForegroundWindow to a process that isn't in the foreground
# itself (Mario's server never is), which is why "pencere öne getirilemedi"
# kept happening. The usual workarounds, in order: restore the window, send a
# harmless ALT key (it unlocks the foreground lock), attach to the foreground
# thread's input, then bring the window up. The result is checked, not assumed.

if (-not ('MarioWin' -as [type])) {
    Add-Type -Namespace '' -Name MarioWin -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
[DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
[DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
[DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
[DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, IntPtr pid);
[DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool attach);
[DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
[DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
'@
}

function Find-MarioWindow {
    param([string]$ProcessName, [string]$Title)
    $procs = Get-Process | Where-Object { $_.MainWindowHandle -ne 0 }
    if ($ProcessName) { $procs = $procs | Where-Object { $_.ProcessName -like "*$ProcessName*" } }
    if ($Title) { $procs = $procs | Where-Object { $_.MainWindowTitle -like "*$Title*" } }
    $procs | Select-Object -First 1
}

function Set-MarioForeground {
    param([string]$ProcessName, [string]$Title, [IntPtr]$Handle = [IntPtr]::Zero)
    if ($Handle -eq [IntPtr]::Zero) {
        $p = Find-MarioWindow -ProcessName $ProcessName -Title $Title
        if (-not $p) { return $false }
        $Handle = $p.MainWindowHandle
    }
    for ($try = 0; $try -lt 3; $try++) {
        if ([MarioWin]::IsIconic($Handle)) { [void][MarioWin]::ShowWindow($Handle, 9) }  # SW_RESTORE
        # ALT down/up: lets this process change the foreground window.
        [MarioWin]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero)
        [MarioWin]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
        $fg = [MarioWin]::GetForegroundWindow()
        $fgThread = [MarioWin]::GetWindowThreadProcessId($fg, [IntPtr]::Zero)
        $me = [MarioWin]::GetCurrentThreadId()
        $attached = $fgThread -ne $me -and [MarioWin]::AttachThreadInput($me, $fgThread, $true)
        [void][MarioWin]::BringWindowToTop($Handle)
        [void][MarioWin]::SetForegroundWindow($Handle)
        if ($attached) { [void][MarioWin]::AttachThreadInput($me, $fgThread, $false) }
        Start-Sleep -Milliseconds 150
        if ([MarioWin]::GetForegroundWindow() -eq $Handle) { return $true }
    }
    return $false
}
