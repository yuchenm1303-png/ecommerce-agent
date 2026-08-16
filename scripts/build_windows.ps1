param(
    [string]$Version = "",
    [string]$Channel = "win-x64-stable",
    [string]$ReleaseNotesPath = "",
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
if ([string]::IsNullOrWhiteSpace($Channel)) {
    throw "Velopack channel must not be empty"
}

$PackId = "Smirel.ListingStudio"
$PackTitle = "Listing Studio"
$PackAuthors = "Smirel"
$ShouldRunUpdateE2E = [bool]$RunUpdateE2E -or ($env:GITHUB_WORKFLOW -eq "Publish Update")

$DistRoot = Join-Path $Root "dist"
$AppDir = Join-Path $DistRoot "EcommerceAgent"
$WorkDir = Join-Path $Root "build\pyinstaller"
$ArtifactDir = Join-Path $Root "artifacts"
$VelopackDir = Join-Path $ArtifactDir "velopack"
$SetupAlias = Join-Path $ArtifactDir "EcommerceAgent-Setup-$Version.exe"
$PortableAlias = Join-Path $ArtifactDir "EcommerceAgent-$Version-portable.zip"
$IconFile = Join-Path $Root "packaging\app_icon.ico"

foreach ($Path in @($AppDir, $WorkDir, $VelopackDir, $SetupAlias, $PortableAlias, $IconFile)) {
    if (Test-Path $Path) { Remove-Item $Path -Recurse -Force }
}
New-Item -ItemType Directory -Force -Path $DistRoot, $WorkDir, $ArtifactDir, $VelopackDir | Out-Null

Write-Host "[1/5] Restoring pinned Velopack CLI"
& dotnet tool restore
if ($LASTEXITCODE -ne 0) { throw "dotnet tool restore failed: $LASTEXITCODE" }
& dotnet tool run vpk -- --help *> $null
if ($LASTEXITCODE -ne 0) { throw "Pinned Velopack CLI failed to start: $LASTEXITCODE" }

Write-Host "[2/5] Generating application icon"
& python (Join-Path $Root "scripts\generate_app_icon.py") --output $IconFile
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $IconFile)) {
    throw "Application icon generation failed"
}

Write-Host "[3/5] Building PyInstaller onedir application v$Version"
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
if ($PyInstallerExitCode -ne 0) { throw "PyInstaller failed: $PyInstallerExitCode" }

$GuiExe = Join-Path $AppDir "EcommerceAgent.exe"
$WorkerExe = Join-Path $AppDir "EcommerceAgentWorker.exe"
foreach ($Required in @($GuiExe, $WorkerExe)) {
    if (-not (Test-Path $Required)) { throw "Packaging output missing: $Required" }
}

Write-Host "[4/5] Verifying frozen GUI and worker"
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
if ($GuiProbeExitCode -ne 0) { throw "Packaged GUI import probe failed: $GuiProbeExitCode" }
& $WorkerExe --self-test
if ($LASTEXITCODE -ne 0) { throw "Packaged worker self-test failed: $LASTEXITCODE" }

Write-Host "[5/5] Packing standard Velopack release"
$PackArgs = @(
    "pack",
    "--outputDir", $VelopackDir,
    "--channel", $Channel,
    "--runtime", "win-x64",
    "--packId", $PackId,
    "--packVersion", $Version,
    "--packDir", $AppDir,
    "--packAuthors", $PackAuthors,
    "--packTitle", $PackTitle,
    "--icon", $IconFile,
    "--mainExe", "EcommerceAgent.exe"
)
if (-not [string]::IsNullOrWhiteSpace($ReleaseNotesPath)) {
    $resolvedNotes = (Resolve-Path $ReleaseNotesPath).Path
    $PackArgs += @("--releaseNotes", $resolvedNotes)
}
& dotnet tool run vpk -- @PackArgs
if ($LASTEXITCODE -ne 0) { throw "Velopack pack failed: $LASTEXITCODE" }

$NativeSetup = Join-Path $VelopackDir "$PackId-Setup.exe"
$NativePortable = Join-Path $VelopackDir "$PackId-Portable.zip"
$ReleaseIndex = Join-Path $VelopackDir "releases.$Channel.json"
$FullPackage = Get-ChildItem $VelopackDir -Filter "$PackId-$Version-full.nupkg" | Select-Object -First 1
foreach ($Required in @($NativeSetup, $NativePortable, $ReleaseIndex)) {
    if (-not (Test-Path $Required)) { throw "Velopack output missing: $Required" }
}
if (-not $FullPackage) { throw "Velopack full package missing for v$Version" }

Copy-Item $NativeSetup $SetupAlias -Force
Copy-Item $NativePortable $PortableAlias -Force

if ($ShouldRunUpdateE2E) {
    Write-Host "[release-gate] Running real Velopack old -> new update E2E"
    & (Join-Path $Root "scripts\test_velopack_update_e2e.ps1") `
        -Version $Version `
        -AppDir $AppDir
    if ($LASTEXITCODE -ne 0) {
        throw "Velopack end-to-end update test failed: $LASTEXITCODE"
    }
}
else {
    Write-Host "[release-gate] Heavy Velopack E2E skipped for normal development build"
}

Write-Host ""
Write-Host "Windows package ready:"
Write-Host "  Velopack feed : $VelopackDir"
Write-Host "  Installer     : $SetupAlias"
Write-Host "  Portable      : $PortableAlias"
Write-Host "  App dir       : $AppDir"
