# Forward the phone's localhost:8000 to this machine's server over USB (adb reverse).
# Usage: scripts\usb_connect.ps1 [-Serial <adb-serial>]
param([string]$Serial = $env:ANDROID_SERIAL)

$Port = 8000

function Fail([string]$Message) {
    [Console]::Error.WriteLine("error: $Message")
    exit 1
}

if (-not (Get-Command adb -ErrorAction SilentlyContinue)) {
    Fail "adb not found on PATH. Install Android platform-tools and add the folder to PATH (see README: Phone over USB)."
}

$lines = @(& adb devices | Select-Object -Skip 1 | Where-Object { $_.Trim() })
$ready = @($lines | Where-Object { $_ -match "\sdevice\s*$" } | ForEach-Object { ($_ -split "\s+")[0] })

if ($ready.Count -eq 0) {
    if ($lines | Where-Object { $_ -match "\sunauthorized\s*$" }) {
        Fail "Phone is connected but not authorized. Unlock it and accept the 'Allow USB debugging?' prompt, then re-run."
    } elseif ($lines | Where-Object { $_ -match "\soffline\s*$" }) {
        Fail "Phone shows as offline. Unplug/replug the cable, or run 'adb kill-server', then re-run."
    }
    Fail "No device listed by 'adb devices'. Connect the phone by USB, enable USB debugging, and use a data-capable cable."
}

if (-not $Serial) {
    if ($ready.Count -gt 1) {
        Fail "Multiple devices authorized ($($ready -join ', ')). Re-run with -Serial <id>."
    }
    $Serial = $ready[0]
} elseif ($ready -notcontains $Serial) {
    Fail "Device '$Serial' is not in the authorized list ($($ready -join ', '))."
}

& adb -s $Serial reverse "tcp:$Port" "tcp:$Port"
if ($LASTEXITCODE -ne 0) {
    Fail "adb reverse failed (exit $LASTEXITCODE)."
}

Write-Host "Forwarding phone localhost:$Port -> this machine's localhost:$Port (device $Serial)"
Write-Host "Start the server:  `$env:PLAIN_HTTP='1'; .\.venv\Scripts\python.exe backend\run.py"
Write-Host "Open on the phone: http://localhost:$Port"
