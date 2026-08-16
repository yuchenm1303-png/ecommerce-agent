param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$AppDir,
    [Parameter(Mandatory = $true)][string]$SetupExe
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$AppDir = (Resolve-Path $AppDir).Path
$SetupExe = (Resolve-Path $SetupExe).Path
$UpdaterExe = Join-Path $AppDir "updater\updater.exe"
if (-not (Test-Path $UpdaterExe)) { throw "Packaged updater missing: $UpdaterExe" }

$ProbeRoot = Join-Path $Root "build\updater-e2e"
$ParentDist = Join-Path $ProbeRoot "parent-dist"
$ParentWork = Join-Path $ProbeRoot "parent-work"
$EdgeDist = Join-Path $ProbeRoot "edge-dist"
$EdgeWork = Join-Path $ProbeRoot "edge-work"
$OldInstall = Join-Path $ProbeRoot "old-install"
$StateDir = Join-Path $ProbeRoot "state"
$PayloadDir = Join-Path $ProbeRoot "payload"
$SetupLog = Join-Path $ProbeRoot "inno-update.log"
$BrowserPort = 19222

if (Test-Path $ProbeRoot) { Remove-Item $ProbeRoot -Recurse -Force }
New-Item -ItemType Directory -Force -Path $ProbeRoot, $PayloadDir | Out-Null

$ProbeSetupExe = Join-Path $PayloadDir (Split-Path $SetupExe -Leaf)
$SourceSetupHash = (Get-FileHash $SetupExe -Algorithm SHA256).Hash
Copy-Item $SetupExe $ProbeSetupExe -Force
if (-not (Test-Path $ProbeSetupExe)) { throw "Updater E2E payload copy missing: $ProbeSetupExe" }
$ProbeSetupHash = (Get-FileHash $ProbeSetupExe -Algorithm SHA256).Hash
if ($ProbeSetupHash -ne $SourceSetupHash) { throw "Updater E2E payload copy hash mismatch" }

Write-Host "  [E2E 1/5] Building stubborn frozen old app + managed Edge ownership probe"
& python -m PyInstaller --noconfirm --clean --onedir --name EcommerceAgent --distpath $ParentDist --workpath $ParentWork (Join-Path $Root "tests\windows_updater_e2e_parent.py")
if ($LASTEXITCODE -ne 0) { throw "Updater E2E parent probe build failed: $LASTEXITCODE" }
& python -m PyInstaller --noconfirm --clean --onefile --name msedge --distpath $EdgeDist --workpath $EdgeWork (Join-Path $Root "tests\windows_updater_fake_edge.py")
if ($LASTEXITCODE -ne 0) { throw "Updater E2E managed Edge probe build failed: $LASTEXITCODE" }

$ParentBundle = Join-Path $ParentDist "EcommerceAgent"
$ParentExe = Join-Path $OldInstall "EcommerceAgent.exe"
$FakeEdgeExe = Join-Path $EdgeDist "msedge.exe"
if (-not (Test-Path (Join-Path $ParentBundle "EcommerceAgent.exe"))) { throw "Updater E2E parent executable missing" }
if (-not (Test-Path $FakeEdgeExe)) { throw "Updater E2E managed Edge executable missing" }

New-Item -ItemType Directory -Force -Path $OldInstall | Out-Null
Copy-Item (Join-Path $ParentBundle "*") $OldInstall -Recurse -Force
$OldVersionFile = Join-Path $OldInstall "_internal\packaging\VERSION"
New-Item -ItemType Directory -Force -Path (Split-Path $OldVersionFile -Parent) | Out-Null
"0.0.1" | Set-Content $OldVersionFile -Encoding ascii
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

$ExistingListener = netstat -ano -p tcp | Select-String -Pattern ":$BrowserPort\s+.*LISTENING"
if ($ExistingListener) { throw "Updater E2E reserved browser port $BrowserPort is already in use" }
$FakeEdge = Start-Process -FilePath $FakeEdgeExe -ArgumentList @("--port", "$BrowserPort") -PassThru -WindowStyle Hidden
$BrowserDeadline = (Get-Date).AddSeconds(20)
$Listener = $null
while ((Get-Date) -lt $BrowserDeadline) {
    $Listener = netstat -ano -p tcp | Select-String -Pattern ":$BrowserPort\s+.*LISTENING"
    if ($Listener) { break }
    Start-Sleep -Milliseconds 100
}
if (-not $Listener) {
    if (-not $FakeEdge.HasExited) { Stop-Process -Id $FakeEdge.Id -Force -ErrorAction SilentlyContinue }
    throw "Updater E2E managed Edge probe did not open CDP port $BrowserPort"
}

Write-Host "  [E2E 2/5] Frozen EcommerceAgent.exe closes managed Edge, hands off, stays alive, and is force-closed without killing updater.exe"
$ParentArgs = @(
    "--updater", $UpdaterExe,
    "--installer", $ProbeSetupExe,
    "--target-version", $Version,
    "--install-dir", $OldInstall,
    "--state-dir", $StateDir,
    "--setup-log", $SetupLog,
    "--app-deadline-s", "2",
    "--linger-after-ack-s", "5",
    "--browser-cdp-port", "$BrowserPort"
)
& $ParentExe @ParentArgs
$ParentExit = $LASTEXITCODE
$AckPath = Join-Path $StateDir "handoff.json"
$UpdaterLog = Join-Path $StateDir "updater.jsonl"
if (-not (Test-Path $AckPath)) { throw "Frozen updater E2E parent never received updater ACK; exit=$ParentExit" }
$Ack = Get-Content $AckPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]$Ack.status -ne "accepted" -or [string]$Ack.target_version -ne $Version) { throw "Frozen updater E2E handoff ACK invalid; exit=$ParentExit" }
if ($ParentExit -eq 0) { throw "Frozen updater E2E parent exited normally; force-close survival path was not exercised" }

Write-Host "  [E2E 3/5] Verifying managed Edge close + native updater panel before Inno"
$BrowserClosedDeadline = (Get-Date).AddSeconds(20)
$Listener = $null
while ((Get-Date) -lt $BrowserClosedDeadline) {
    $Listener = netstat -ano -p tcp | Select-String -Pattern ":$BrowserPort\s+.*LISTENING"
    if (-not $Listener) { break }
    Start-Sleep -Milliseconds 100
}
if ($Listener) { throw "Updater E2E managed Edge CDP port remained open after shutdown gate" }
$PanelDeadline = (Get-Date).AddSeconds(10)
while ((Get-Date) -lt $PanelDeadline) {
    if ((Test-Path $UpdaterLog) -and (Select-String -Path $UpdaterLog -Pattern "native update panel ready" -Quiet)) { break }
    Start-Sleep -Milliseconds 100
}
if (-not ((Test-Path $UpdaterLog) -and (Select-String -Path $UpdaterLog -Pattern "native update panel ready" -Quiet))) {
    throw "Standalone updater did not prove the continuous native progress panel was created"
}

Write-Host "  [E2E 4/5] Waiting for updater survival + lock audit + hidden Inno upgrade + installed VERSION verification"
$ResultPath = Join-Path $StateDir "last-result.json"
$Deadline = (Get-Date).AddMinutes(5)
while ((Get-Date) -lt $Deadline -and -not (Test-Path $ResultPath)) { Start-Sleep -Milliseconds 250 }
if (-not (Test-Path $ResultPath)) {
    if (Test-Path $UpdaterLog) { Write-Host "---- updater log ----"; Get-Content $UpdaterLog -Tail 160 }
    if (Test-Path $SetupLog) { Write-Host "---- Inno log ----"; Get-Content $SetupLog -Tail 160 }
    throw "Updater E2E timed out waiting for result"
}
$Result = Get-Content $ResultPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]$Result.status -ne "installed") {
    if (Test-Path $UpdaterLog) { Write-Host "---- updater log ----"; Get-Content $UpdaterLog -Tail 160 }
    if (Test-Path $SetupLog) { Write-Host "---- Inno log ----"; Get-Content $SetupLog -Tail 160 }
    throw "Updater E2E failed: status=$($Result.status) detail=$($Result.detail)"
}
if (-not (Select-String -Path $UpdaterLog -Pattern "forcing owned application PID" -Quiet)) { throw "Updater E2E did not exercise the stubborn-GUI force-close path" }
if (-not (Select-String -Path $UpdaterLog -Pattern "browser gate closed managed Edge" -Quiet)) { throw "Updater E2E did not verify managed Edge termination" }
if (-not (Select-String -Path $UpdaterLog -Pattern "install tree lock audit clean" -Quiet)) { throw "Updater E2E did not verify a clean Restart Manager install-tree lock audit" }
if (-not (Select-String -Path $UpdaterLog -Pattern "running installer:.*\/VERYSILENT" -Quiet)) { throw "Updater E2E did not launch Inno in hidden /VERYSILENT mode" }

$InstalledVersionFile = Join-Path $OldInstall "_internal\packaging\VERSION"
$InstalledGui = Join-Path $OldInstall "EcommerceAgent.exe"
foreach ($required in @($InstalledVersionFile, $InstalledGui)) { if (-not (Test-Path $required)) { throw "Updater E2E installed component missing: $required" } }
$InstalledVersion = (Get-Content $InstalledVersionFile -Raw).Trim()
if ($InstalledVersion -ne $Version) { throw "Updater E2E version mismatch: expected=$Version actual=$InstalledVersion" }

Write-Host "  [E2E 5/5] Verifying updater restarted the real installed EcommerceAgent.exe"
$RelaunchMarker = Join-Path $StateDir "real-gui-relaunch.json"
$RelaunchDeadline = (Get-Date).AddSeconds(45)
while ((Get-Date) -lt $RelaunchDeadline -and -not (Test-Path $RelaunchMarker)) { Start-Sleep -Milliseconds 100 }
if (-not (Test-Path $RelaunchMarker)) {
    if (Test-Path $UpdaterLog) { Write-Host "---- updater log ----"; Get-Content $UpdaterLog -Tail 160 }
    if (Test-Path $SetupLog) { Write-Host "---- Inno log ----"; Get-Content $SetupLog -Tail 160 }
    throw "Updater E2E installed successfully but real EcommerceAgent.exe never reached Qt/Python startup"
}
$Relaunch = Get-Content $RelaunchMarker -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not [bool]$Relaunch.started -or -not [bool]$Relaunch.frozen) { throw "Updater E2E real GUI did not run as a frozen application" }
if ([string]$Relaunch.version -ne $Version) { throw "Updater E2E relaunched GUI version mismatch: expected=$Version actual=$($Relaunch.version)" }
$ExpectedExe = [IO.Path]::GetFullPath($InstalledGui)
$ActualExe = [IO.Path]::GetFullPath([string]$Relaunch.executable)
if ($ActualExe -ne $ExpectedExe) { throw "Updater E2E relaunched wrong executable: expected=$ExpectedExe actual=$ActualExe" }

Write-Host "  Updater E2E passed: managed Edge closed -> stubborn app -> surviving updater -> clean lock audit -> hidden Inno -> v$InstalledVersion -> real installed GUI"
exit 0
