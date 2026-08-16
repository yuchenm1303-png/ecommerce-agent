param(
    [string]$Version = "",
    [string]$Channel = "win-x64-stable",
    [string]$ReleaseNotesPath = "",
    [switch]$RunUpdateE2E
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-SingleVelopackArtifact {
    param(
        [Parameter(Mandatory = $true)][string]$Directory,
        [Parameter(Mandatory = $true)][string]$Filter,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $Items = @(Get-ChildItem -Path $Directory -File -Filter $Filter -ErrorAction Stop)
    if ($Items.Count -ne 1) {
        $Names = if ($Items.Count -eq 0) { "<none>" } else { ($Items.Name -join ", ") }
        throw "Expected exactly one Velopack $Label matching '$Filter' in '$Directory', found $($Items.Count): $Names"
    }
    return $Items[0]
}

function Resolve-VelopackFullPackage {
    param(
        [Parameter(Mandatory = $true)][string]$Directory,
        [Parameter(Mandatory = $true)][string]$ReleaseIndex,
        [Parameter(Mandatory = $true)][string]$PackageId,
        [Parameter(Mandatory = $true)][string]$PackageVersion
    )

    if (-not (Test-Path $ReleaseIndex)) { throw "Velopack release index missing: $ReleaseIndex" }
    $Feed = Get-Content $ReleaseIndex -Raw -Encoding UTF8 | ConvertFrom-Json
    $Target = @($Feed.Assets | Where-Object {
        [string]$_.PackageId -eq $PackageId -and
        [string]$_.Version -eq $PackageVersion -and
        [string]$_.Type -eq "Full"
    })
    if ($Target.Count -ne 1) {
        throw "Velopack release index must contain exactly one Full asset for $PackageId v$PackageVersion; found $($Target.Count)"
    }

    $FileName = [string]$Target[0].FileName
    if ([string]::IsNullOrWhiteSpace($FileName) -or [IO.Path]::GetFileName($FileName) -ne $FileName) {
        throw "Unsafe Velopack package filename in release index: '$FileName'"
    }
    $PackagePath = Join-Path $Directory $FileName
    if (-not (Test-Path $PackagePath -PathType Leaf)) {
        throw "Velopack release index points to missing package: $PackagePath"
    }
    $Package = Get-Item $PackagePath
    if ([int64]$Target[0].Size -ne [int64]$Package.Length) {
        throw "Velopack release index/package size mismatch for $FileName"
    }
    return $Package
}

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
$MsiAlias = Join-Path $ArtifactDir "EcommerceAgent-Setup-$Version.msi"
$PortableAlias = Join-Path $ArtifactDir "EcommerceAgent-$Version-portable.zip"
$IconFile = Join-Path $Root "packaging\app_icon.ico"
$SplashFile = Join-Path $Root "packaging\installer_splash.png"
$MsiBannerFile = Join-Path $Root "packaging\msi_banner.bmp"
$MsiLogoFile = Join-Path $Root "packaging\msi_logo.bmp"

foreach ($Path in @(
    $AppDir,
    $WorkDir,
    $VelopackDir,
    $SetupAlias,
    $MsiAlias,
    $PortableAlias,
    $IconFile,
    $SplashFile,
    $MsiBannerFile,
    $MsiLogoFile
)) {
    if (Test-Path $Path) { Remove-Item $Path -Recurse -Force }
}
New-Item -ItemType Directory -Force -Path $DistRoot, $WorkDir, $ArtifactDir, $VelopackDir | Out-Null

Write-Host "[1/5] Restoring pinned Velopack CLI"
& dotnet tool restore
if ($LASTEXITCODE -ne 0) { throw "dotnet tool restore failed: $LASTEXITCODE" }
& dotnet tool run vpk -- --help *> $null
if ($LASTEXITCODE -ne 0) { throw "Pinned Velopack CLI failed to start: $LASTEXITCODE" }

Write-Host "[2/5] Generating canonical Windows branding"
& python (Join-Path $Root "scripts\generate_app_icon.py") `
    --output $IconFile `
    --splash $SplashFile `
    --msi-banner $MsiBannerFile `
    --msi-logo $MsiLogoFile `
    --version $Version
if ($LASTEXITCODE -ne 0) { throw "Windows branding generation failed" }
foreach ($Required in @($IconFile, $SplashFile, $MsiBannerFile, $MsiLogoFile)) {
    if (-not (Test-Path $Required -PathType Leaf)) { throw "Windows branding output missing: $Required" }
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

Write-Host "[5/5] Packing branded standard Velopack release"
$AzureTrustedSignFile = [string]$env:VPK_AZURE_TRUSTED_SIGN_FILE
$SignParams = [string]$env:VPK_SIGN_PARAMS
if (-not [string]::IsNullOrWhiteSpace($AzureTrustedSignFile) -and -not [string]::IsNullOrWhiteSpace($SignParams)) {
    throw "Configure only one Velopack signing mode: VPK_AZURE_TRUSTED_SIGN_FILE or VPK_SIGN_PARAMS"
}
if (-not [string]::IsNullOrWhiteSpace($AzureTrustedSignFile)) {
    if (-not (Test-Path $AzureTrustedSignFile)) {
        throw "Velopack Azure signing metadata file not found: $AzureTrustedSignFile"
    }
    Write-Host "Velopack code signing enabled via Azure Artifact Signing metadata"
}
elseif (-not [string]::IsNullOrWhiteSpace($SignParams)) {
    Write-Host "Velopack code signing enabled via signtool parameters"
}
else {
    Write-Warning "Velopack package is unsigned. This is acceptable for development/E2E only; configure VPK_AZURE_TRUSTED_SIGN_FILE or VPK_SIGN_PARAMS before production distribution."
}

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
    "--splashImage", $SplashFile,
    "--splashProgressColor", "#5DA7FF",
    "--aumid", $PackId,
    "--shortcuts", "Desktop,StartMenuRoot",
    "--msi", "true",
    "--instLocation", "PerUser",
    "--msiBanner", $MsiBannerFile,
    "--msiLogo", $MsiLogoFile,
    "--mainExe", "EcommerceAgent.exe"
)
if (-not [string]::IsNullOrWhiteSpace($ReleaseNotesPath)) {
    $resolvedNotes = (Resolve-Path $ReleaseNotesPath).Path
    $PackArgs += @("--releaseNotes", $resolvedNotes)
}
if (-not [string]::IsNullOrWhiteSpace($AzureTrustedSignFile)) {
    $PackArgs += @("--azureTrustedSignFile", (Resolve-Path $AzureTrustedSignFile).Path)
}
elseif (-not [string]::IsNullOrWhiteSpace($SignParams)) {
    $PackArgs += @("--signParams", $SignParams)
}
& dotnet tool run vpk -- @PackArgs
if ($LASTEXITCODE -ne 0) { throw "Velopack pack failed: $LASTEXITCODE" }

# Native artifact names are a Velopack implementation detail and may include
# channel/runtime qualifiers. Setup/portable/MSI are discovered from the clean
# output directory; the update package itself is resolved only through the feed.
$NativeSetup = Get-SingleVelopackArtifact -Directory $VelopackDir -Filter "$PackId*-Setup.exe" -Label "setup bundle"
$NativeMsi = Get-SingleVelopackArtifact -Directory $VelopackDir -Filter "$PackId*.msi" -Label "MSI bundle"
$NativePortable = Get-SingleVelopackArtifact -Directory $VelopackDir -Filter "$PackId*-Portable.zip" -Label "portable bundle"
$ReleaseIndex = Join-Path $VelopackDir "releases.$Channel.json"
$FullPackage = Resolve-VelopackFullPackage `
    -Directory $VelopackDir `
    -ReleaseIndex $ReleaseIndex `
    -PackageId $PackId `
    -PackageVersion $Version

Copy-Item $NativeSetup.FullName $SetupAlias -Force
Copy-Item $NativeMsi.FullName $MsiAlias -Force
Copy-Item $NativePortable.FullName $PortableAlias -Force

if ($ShouldRunUpdateE2E) {
    Write-Host "[release-gate] Running real Velopack old -> new update E2E"
    & (Join-Path $Root "scripts\test_velopack_update_e2e.ps1") `
        -Version $Version `
        -AppDir $AppDir `
        -Channel $Channel
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
Write-Host "  Native setup  : $($NativeSetup.Name)"
Write-Host "  Native MSI    : $($NativeMsi.Name)"
Write-Host "  Native portable: $($NativePortable.Name)"
Write-Host "  Full package  : $($FullPackage.Name)"
Write-Host "  Installer EXE : $SetupAlias"
Write-Host "  Installer MSI : $MsiAlias"
Write-Host "  Portable      : $PortableAlias"
Write-Host "  App dir       : $AppDir"
