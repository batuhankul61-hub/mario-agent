"""Windows toast notifications, through PowerShell's WinRT bridge (no extra
packages). The title and text travel as environment variables and are
XML-escaped in PowerShell, so they can never become script or markup."""

import logging
import os
import subprocess

log = logging.getLogger(__name__)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# PowerShell's own AppUserModelID: always registered, so the toast shows
# without installing a shortcut for Mario.
APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$t = [Security.SecurityElement]::Escape($env:MARIO_TOAST_TITLE)
$b = [Security.SecurityElement]::Escape($env:MARIO_TOAST_BODY)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$t</text><text>$b</text></binding></visual><audio src='ms-winsoundevent:Notification.Reminder'/></toast>")
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:MARIO_TOAST_APP).Show($toast)
"""


def toast(title: str, body: str) -> bool:
    env = {
        **os.environ,
        "MARIO_TOAST_TITLE": title[:120],
        "MARIO_TOAST_BODY": body[:400],
        "MARIO_TOAST_APP": APP_ID,
    }
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", SCRIPT],
            env=env,
            capture_output=True,
            timeout=20,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        log.warning("Toast could not be shown", exc_info=True)
        return False
    if p.returncode:
        log.warning("Toast failed: %s", p.stderr.decode(errors="replace")[-300:])
    return p.returncode == 0
