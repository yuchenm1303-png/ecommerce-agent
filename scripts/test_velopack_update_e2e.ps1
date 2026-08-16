param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$AppDir
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$AppDir = (Resolve-Path $AppDir).Path
$ProbeRoot = Join-Path $Root "build\velopack-e2e"
$FeedDir = Join-Path $ProbeRoot "feed"
$InstallDir = Join-Path $ProbeRoot "install"
$Marker = Join-Path $ProbeRoot "real-gui-relaunch.json"
$IconFile = Join-Path $Root "packaging\app_icon.ico"
$PackId = "Smirel.ListingStudio.E2E"
$Channel = "win-e2e"
$OldVersion = "0.0.1"

if (Test-Path $ProbeRoot) { Remove-Item $ProbeRoot -Recurse -Force }
New-Item -ItemType Directory -Force -Path $ProbeRoot, $FeedDir | Out-Null

function Invoke-E2EPack([string]$PackVersion) {
    & dotnet tool run vpk -- pack `
        --outputDir $FeedDir `
        --channel $Channel `
        --runtime win-x64 `
        --packId $PackId `
        --packVersion $PackVersion `
        --packDir $AppDir `
        --packAuthors Smirel `
        --packTitle "Listing Studio E2E" `
        --icon $IconFile `
        --mainExe EcommerceAgent.exe
    if ($LASTEXITCODE -ne 0) { throw "Velopack E2E pack failed for $PackVersion" }
}

Write-Host "  [E2E 1/4] Pack and install old Velopack version $OldVersion"
Invoke-E2EPack $OldVersion
$Setup = Join-Path $FeedDir "$PackId-Setup.exe"
if (-not (Test-Path $Setup)) { throw "Velopack E2E old Setup missing: $Setup" }
$Install = Start-Process -FilePath $Setup -ArgumentList @("--silent", "--installto", $InstallDir) -Wait -PassThru
if ($Install.ExitCode -ne 0) { throw "Velopack E2E old install failed: $($Install.ExitCode)" }

$RootGui = Join-Path $InstallDir "EcommerceAgent.exe"
$UpdateExe = Join-Path $InstallDir "Update.exe"
$CurrentGui = Join-Path $InstallDir "current\EcommerceAgent.exe"
foreach ($Required in @($RootGui, $UpdateExe, $CurrentGui)) {
    if (-not (Test-Path $Required)) { throw "Velopack E2E installed component missing: $Required" }
}

Write-Host "  [E2E 2/4] Add target v$Version to the same local Velopack feed"
Invoke-E2EPack $Version
$Index = Join-Path $FeedDir "releases.$Channel.json"
$TargetPackage = Join-Path $FeedDir "$PackId-$Version-full.nupkg"
foreach ($Required in @($Index, $TargetPackage)) {
    if (-not (Test-Path $Required)) { throw "Velopack E2E target feed missing: $Required" }
}

Write-Host "  [E2E 3/4] Launch installed old app and let Velopack update + restart it"
$Process = Start-Process -FilePath $RootGui -ArgumentList @(
    "--velopack-e2e-source", $FeedDir,
    "--velopack-e2e-target", $Version,
    "--velopack-e2e-marker", $Marker
) -PassThru

$Deadline = (Get-Date).AddMinutes(5)
while ((Get-Date) -lt $Deadline -and -not (Test-Path $Marker)) {
    Start-Sleep -Milliseconds 250
}
if (-not (Test-Path $Marker)) {
    throw "Velopack E2E timed out waiting for the real updated GUI startup marker"
}

Write-Host "  [E2E 4/4] Verify real installed GUI restarted on v$Version"
$Result = Get-Content $Marker -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not [bool]$Result.started -or -not [bool]$Result.frozen) {
    throw "Velopack E2E updated GUI did not reach frozen Qt/Python startup"
}
if ([string]$Result.version -ne $Version) {
    throw "Velopack E2E version mismatch: expected=$Version actual=$($Result.version)"
}
$ExpectedExe = [IO.Path]::GetFullPath((Join-Path $InstallDir "current\EcommerceAgent.exe"))
$ActualExe = [IO.Path]::GetFullPath([string]$Result.executable)
if ($ActualExe -ne $ExpectedExe) {
    throw "Velopack E2E relaunched wrong executable: expected=$ExpectedExe actual=$ActualExe"
}
$VersionFile = Join-Path $InstallDir "current\_internal\packaging\VERSION"
if (-not (Test-Path $VersionFile)) { throw "Updated VERSION file missing" }
$Embedded = (Get-Content $VersionFile -Raw).Trim()
if ($Embedded -ne $Version) {
    throw "Updated embedded VERSION mismatch: expected=$Version actual=$Embedded"
}

Write-Host "  Velopack E2E passed: v$OldVersion -> Update.exe -> v$Version -> real installed GUI"
exit 0
