param(
    [string]$Version = "",
    [switch]$RunUpdateE2E
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = (Get-Content (Join-Path $Root "packaging\VERSION") -Raw).Trim()
}
if ($Version -notmatch '^\d+\.\d+\.\d+([.-][0-9A-Za-z.-]+)?$') {
    throw "Invalid package version: $Version"
}

# The full frozen old-app -> updater -> Inno -> relaunch test is deliberately a
# Stable-release gate, not a tax on every development package build.  Local
# callers can opt in explicitly with -RunUpdateE2E.
$ShouldRunUpdateE2E = [bool]$RunUpdateE2E -or ($env:GITHUB_WORKFLOW -eq "Publish Update")

$DistRoot = Join-Path $Root "dist"
$AppDir = Join-Path $DistRoot "EcommerceAgent"
$WorkDir = Join-Path $Root "build\pyinstaller"
$ArtifactDir = Join-Path $Root "artifacts"
$PortableZip = Join-Path $ArtifactDir "EcommerceAgent-$Version-portable.zip"
$SetupExe = Join-Path $ArtifactDir "EcommerceAgent-Setup-$Version.exe"
$IconFile = Join-Path $Root "packaging\app_icon.ico"

foreach ($Path in @($AppDir, $WorkDir, $PortableZip, $SetupExe, $IconFile)) {
    if (Test-Path $Path) {
        Remove-Item $Path -Recurse -Force
    }
}
New-Item -ItemType Directory -Force -Path $DistRoot, $WorkDir, $ArtifactDir | Out-Null

Write-Host "[1/6] Generating application icon"
& python (Join-Path $Root "scripts\generate_app_icon.py") --output $IconFile
if ($LASTEXITCODE -ne 0) {
    throw "Application icon generation failed with exit code $LASTEXITCODE"
}
if (-not (Test-Path $IconFile)) {
    throw "Generated application icon missing: $IconFile"
}

Write-Host "[2/6] Building PyInstaller onedir package $Version"
$PreviousBuildVersion = $env:ECOMMERCE_AGENT_BUILD_VERSION
$env:ECOMMERCE_AGENT_BUILD_VERSION = $Version
try {
    & python -m PyInstaller `
        --noconfirm `
        --clean `
        --distpath $DistRoot `
        --workpath $WorkDir `
        (Join-Path $Root "packaging\EcommerceAgent.spec")
    $PyInstallerExitCode = $LASTEXITCODE
}
finally {
    if ($null -eq $PreviousBuildVersion) {
        Remove-Item Env:ECOMMERCE_AGENT_BUILD_VERSION -ErrorAction SilentlyContinue
    }
    else {
        $env:ECOMMERCE_AGENT_BUILD_VERSION = $PreviousBuildVersion
    }
}
if ($PyInstallerExitCode -ne 0) {
    throw "PyInstaller failed with exit code $PyInstallerExitCode"
}

$GuiExe = Join-Path $AppDir "EcommerceAgent.exe"
$WorkerExe = Join-Path $AppDir "EcommerceAgentWorker.exe"
foreach ($Required in @($GuiExe, $WorkerExe)) {
    if (-not (Test-Path $Required)) {
        throw "Packaging output missing: $Required"
    }
}

Write-Host "[2.5/6] Building standalone updater"
$UpdaterWork = Join-Path $WorkDir "updater"
& python -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath $DistRoot `
    --workpath $UpdaterWork `
    (Join-Path $Root "packaging\Updater.spec")
if ($LASTEXITCODE -ne 0) {
    throw "Updater build failed with exit code $LASTEXITCODE"
}
$UpdaterExe = Join-Path $DistRoot "updater.exe"
if (-not (Test-Path $UpdaterExe)) {
    throw "Updater build output missing: $UpdaterExe"
}
Write-Host "  Verifying standalone updater runtime"
& $UpdaterExe --self-check
if ($LASTEXITCODE -ne 0) {
    throw "Standalone updater self-check failed with exit code $LASTEXITCODE"
}
New-Item -ItemType Directory -Force -Path (Join-Path $AppDir "updater") | Out-Null
Copy-Item $UpdaterExe (Join-Path $AppDir "updater\updater.exe") -Force
Write-Host "  Standalone updater: $(Join-Path $AppDir "updater\updater.exe")"

Write-Host "[3/6] Verifying packaged GUI import path"
$PreviousImportProbe = $env:ECOMMERCE_AGENT_PACKAGE_IMPORT_PROBE
$env:ECOMMERCE_AGENT_PACKAGE_IMPORT_PROBE = "1"
try {
    $GuiProbe = Start-Process -FilePath $GuiExe -Wait -PassThru
    $GuiProbeExitCode = $GuiProbe.ExitCode
}
finally {
    if ($null -eq $PreviousImportProbe) {
        Remove-Item Env:ECOMMERCE_AGENT_PACKAGE_IMPORT_PROBE -ErrorAction SilentlyContinue
    }
    else {
        $env:ECOMMERCE_AGENT_PACKAGE_IMPORT_PROBE = $PreviousImportProbe
    }
}
if ($GuiProbeExitCode -ne 0) {
    throw "Packaged GUI import probe failed with exit code $GuiProbeExitCode"
}

Write-Host "[4/6] Verifying packaged Playwright worker"
& $WorkerExe --self-test
if ($LASTEXITCODE -ne 0) {
    throw "Packaged worker self-test failed with exit code $LASTEXITCODE"
}

Write-Host "[5/6] Creating portable archive"
Compress-Archive -Path (Join-Path $AppDir "*") -DestinationPath $PortableZip -CompressionLevel Optimal

$IsccFromPath = Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1
$IsccCandidates = @(
    $IsccFromPath,
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 7\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 7\ISCC.exe")
) | Where-Object { $_ -and (Test-Path $_) }
$Iscc = $IsccCandidates | Select-Object -First 1
if (-not $Iscc) {
    throw "Inno Setup compiler (ISCC.exe) was not found. Install Inno Setup 6+ and rerun."
}

Write-Host "[6/6] Building installer"
& $Iscc `
    "/DAppVersion=$Version" `
    "/DSourceDir=$AppDir" `
    "/DOutputDir=$ArtifactDir" `
    "/DIconFile=$IconFile" `
    (Join-Path $Root "packaging\installer.iss")
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed with exit code $LASTEXITCODE"
}
if (-not (Test-Path $SetupExe)) {
    throw "Installer output missing: $SetupExe"
}

$ReleaseSetupHash = (Get-FileHash $SetupExe -Algorithm SHA256).Hash
$ReleaseSetupSize = (Get-Item $SetupExe).Length

if ($ShouldRunUpdateE2E) {
    Write-Host "[release-gate] Running real frozen updater end-to-end handoff"
    & (Join-Path $Root "scripts\test_windows_update_e2e.ps1") `
        -Version $Version `
        -AppDir $AppDir `
        -SetupExe $SetupExe
    if ($LASTEXITCODE -ne 0) {
        throw "Frozen updater end-to-end smoke test failed with exit code $LASTEXITCODE"
    }

    # The release artifact is immutable. The E2E owns and may delete only its
    # probe copy, never the installer that later smoke/manifest/release steps use.
    if (-not (Test-Path $SetupExe)) {
        throw "Release installer disappeared during updater E2E: $SetupExe"
    }
    $ReleaseSetupHashAfterE2E = (Get-FileHash $SetupExe -Algorithm SHA256).Hash
    $ReleaseSetupSizeAfterE2E = (Get-Item $SetupExe).Length
    if ($ReleaseSetupHashAfterE2E -ne $ReleaseSetupHash -or $ReleaseSetupSizeAfterE2E -ne $ReleaseSetupSize) {
        throw "Release installer changed during updater E2E: $SetupExe"
    }
}
else {
    Write-Host "[release-gate] Skipping heavy updater E2E for normal development build"
}

Write-Host ""
Write-Host "Windows package ready:"
Write-Host "  Installer: $SetupExe"
Write-Host "  Portable : $PortableZip"
Write-Host "  App dir  : $AppDir"
Write-Host "  Icon     : $IconFile"
