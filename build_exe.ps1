$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root "..\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

$hasPyInstaller = $false
try {
    & $python -c "import PyInstaller" *> $null
    $hasPyInstaller = ($LASTEXITCODE -eq 0)
} catch {
    $hasPyInstaller = $false
}
if (-not $hasPyInstaller) {
    & $python -m pip install pyinstaller
}

$addData = @()
foreach ($file in @("scanned_models.json", "form_fields.json")) {
    $path = Join-Path $root $file
    if (Test-Path $path) {
        $addData += @("--add-data", "$path;.")
    }
}
Get-ChildItem -Path $root -Filter "form_fields_*.json" | ForEach-Object {
    $addData += @("--add-data", "$($_.FullName);.")
}

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "BMW-AutoBuyer" `
    --collect-all playwright `
    --collect-all greenlet `
    --collect-all pyee `
    @addData `
    (Join-Path $root "gui_app.py")

Write-Host ""
Write-Host "Build complete: $(Join-Path $root 'dist\BMW-AutoBuyer.exe')"
Write-Host "Note: target PC must have Google Chrome installed."
