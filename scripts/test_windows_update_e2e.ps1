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
$RelaunchDist = Join-Path $ProbeRoot "relaunch-dist"
$RelaunchWork = Join-Path $ProbeRoot "relaunch-work"
$OldInstall = Join-Path $ProbeRoot "old-install"
$StateDir = Join-Path $ProbeRoot "state"
$SetupLog = Join-Path $ProbeRoot "inno-update.log"

if (Test-Path $ProbeRoot) { Remove-Item $ProbeRoot -Recurse -Force }
New-Item -ItemType Directory -Force -Path $ProbeRoot | Out-Null

Write-Host "  [E2E 1/5] Building frozen old-app handoff probe"
& python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --name EcommerceAgent `
    --distpath $ParentDist `
    --workpath $ParentWork `
    (Join-Path $Root "tests\windows_updater_e2e_parent.py")
if ($LASTEXITCODE -ne 0) { throw "Updater E2E parent probe build failed: $LASTEXITCODE" }

Write-Host "  [E2E 2/5] Building independent frozen relaunch probe"
& python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --name RelaunchProbe `
    --distpath $RelaunchDist `
    --workpath $RelaunchWork `
    (Join-Path $Root "tests\windows_updater_e2e_relaunch.py")
if ($LASTEXITCODE -ne 0) { throw "Updater E2E relaunch probe build failed: $LASTEXITCODE" }

$ParentBundle = Join-Path $ParentDist "EcommerceAgent"
$ParentExe = Join-Path $OldInstall "EcommerceAgent.exe"
$RelaunchExe = Join-Path $RelaunchDist "RelaunchProbe.exe"
if (-not (Test-Path (Join-Path $ParentBundle "EcommerceAgent.exe"))) {
    throw "Updater E2E parent executable missing"
}
if (-not (Test-Path $RelaunchExe)) { throw "Updater E2E relaunch executable missing" }

New-Item -ItemType Directory -Force -Path $OldInstall | Out-Null
Copy-Item (Join-Path $ParentBundle "*") $OldInstall -Recurse -Force
$OldVersionFile = Join-Path $OldInstall "_internal\packaging\VERSION"
New-Item -ItemType Directory -Force -Path (Split-Path $OldVersionFile -Parent) | Out-Null
"0.0.1" | Set-Content $OldVersionFile -Encoding ascii
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

Write-Host "  [E2E 3/5] Frozen EcommerceAgent.exe hands off to packaged updater.exe"
$ParentArgs = @(
    "--updater", $UpdaterExe,
    "--installer", $SetupExe,
    "--target-version", $Version,
    "--install-dir", $OldInstall,
    "--relaunch-probe", $RelaunchExe,
    "--state-dir", $StateDir,
    "--setup-log", $SetupLog
)
& $ParentExe @ParentArgs
$ParentExit = $LASTEXITCODE
if ($ParentExit -ne 0) {
    throw "Frozen updater E2E parent failed before handoff completed: $ParentExit"
}

Write-Host "  [E2E 4/5] Waiting for real Inno upgrade + installed VERSION verification"
$ResultPath = Join-Path $StateDir "last-result.json"
$UpdaterLog = Join-Path $StateDir "updater.jsonl"
$Deadline = (Get-Date).AddMinutes(5)
while ((Get-Date) -lt $Deadline -and -not (Test-Path $ResultPath)) {
    Start-Sleep -Milliseconds 250
}
if (-not (Test-Path $ResultPath)) {
    if (Test-Path $UpdaterLog) { Write-Host "---- updater log ----"; Get-Content $UpdaterLog -Tail 160 }
    if (Test-Path $SetupLog) { Write-Host "---- Inno log ----"; Get-Content $SetupLog -Tail 160 }
    throw "Updater E2E timed out waiting for result"
}

$Result = Get-Content $ResultPath -Raw | ConvertFrom-Json
if ([string]$Result.status -ne "installed") {
    if (Test-Path $UpdaterLog) { Write-Host "---- updater log ----"; Get-Content $UpdaterLog -Tail 160 }
    if (Test-Path $SetupLog) { Write-Host "---- Inno log ----"; Get-Content $SetupLog -Tail 160 }
    throw "Updater E2E failed: status=$($Result.status) detail=$($Result.detail)"
}

$InstalledVersionFile = Join-Path $OldInstall "_internal\packaging\VERSION"
if (-not (Test-Path $InstalledVersionFile)) { throw "Updater E2E installed VERSION is missing" }
$InstalledVersion = (Get-Content $InstalledVersionFile -Raw).Trim()
if ($InstalledVersion -ne $Version) {
    throw "Updater E2E version mismatch: expected=$Version actual=$InstalledVersion"
}

Write-Host "  [E2E 5/5] Verifying updater really restarted an independent frozen child"
$RelaunchMarker = Join-Path $StateDir "relaunch-probe.json"
$RelaunchDeadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $RelaunchDeadline -and -not (Test-Path $RelaunchMarker)) {
    Start-Sleep -Milliseconds 100
}
if (-not (Test-Path $RelaunchMarker)) {
    if (Test-Path $UpdaterLog) { Write-Host "---- updater log ----"; Get-Content $UpdaterLog -Tail 160 }
    throw "Updater E2E installed successfully but independent relaunch never reached Python"
}
$Relaunch = Get-Content $RelaunchMarker -Raw | ConvertFrom-Json
if (-not [bool]$Relaunch.started -or -not [bool]$Relaunch.frozen) {
    throw "Updater E2E relaunch probe did not run as a frozen application"
}

Write-Host "  Updater E2E passed: frozen old app -> updater -> Inno -> v$InstalledVersion -> frozen relaunch"
