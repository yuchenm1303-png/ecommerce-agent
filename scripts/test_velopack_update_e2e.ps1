param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$AppDir,
    [string]$Channel = "win-x64-stable"
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
        throw "Expected exactly one Velopack E2E $Label matching '$Filter', found $($Items.Count): $Names"
    }
    return $Items[0]
}

function Resolve-E2EFullPackage {
    param(
        [Parameter(Mandatory = $true)][string]$Directory,
        [Parameter(Mandatory = $true)][string]$ReleaseIndex,
        [Parameter(Mandatory = $true)][string]$PackageId,
        [Parameter(Mandatory = $true)][string]$PackageVersion
    )

    $Feed = Get-Content $ReleaseIndex -Raw -Encoding UTF8 | ConvertFrom-Json
    $Target = @($Feed.Assets | Where-Object {
        [string]$_.PackageId -eq $PackageId -and
        [string]$_.Version -eq $PackageVersion -and
        [string]$_.Type -eq "Full"
    })
    if ($Target.Count -ne 1) {
        throw "Velopack E2E feed must contain exactly one Full target v$PackageVersion; found $($Target.Count)"
    }
    $FileName = [string]$Target[0].FileName
    if ([string]::IsNullOrWhiteSpace($FileName) -or [IO.Path]::GetFileName($FileName) -ne $FileName) {
        throw "Unsafe Velopack E2E package filename: '$FileName'"
    }
    $PackagePath = Join-Path $Directory $FileName
    if (-not (Test-Path $PackagePath -PathType Leaf)) {
        throw "Velopack E2E feed points to missing package: $PackagePath"
    }
    $Package = Get-Item $PackagePath
    if ([int64]$Target[0].Size -ne [int64]$Package.Length) {
        throw "Velopack E2E feed/package size mismatch for $FileName"
    }
    return $Package
}

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$AppDir = (Resolve-Path $AppDir).Path
$ProbeRoot = Join-Path $Root "build\velopack-e2e"
$FeedDir = Join-Path $ProbeRoot "feed"
$InstallDir = Join-Path $ProbeRoot "install"
$OldAppDir = Join-Path $ProbeRoot "old-app"
$Marker = Join-Path $ProbeRoot "real-gui-relaunch.json"
$IconFile = Join-Path $Root "packaging\app_icon.ico"
$PackId = "Smirel.ListingStudio.E2E"
$OldVersion = "0.0.1"

if (Test-Path $ProbeRoot) { Remove-Item $ProbeRoot -Recurse -Force }
New-Item -ItemType Directory -Force -Path $ProbeRoot, $FeedDir | Out-Null
Copy-Item -Path $AppDir -Destination $OldAppDir -Recurse -Force
$OldEmbeddedVersion = Join-Path $OldAppDir "_internal\packaging\VERSION"
if (-not (Test-Path $OldEmbeddedVersion)) {
    throw "Velopack E2E old app VERSION file missing: $OldEmbeddedVersion"
}
Set-Content -Path $OldEmbeddedVersion -Value $OldVersion -Encoding ascii -NoNewline

function Invoke-E2EPack([string]$PackVersion, [string]$PackDirectory) {
    & dotnet tool run vpk -- pack `
        --outputDir $FeedDir `
        --channel $Channel `
        --runtime win-x64 `
        --packId $PackId `
        --packVersion $PackVersion `
        --packDir $PackDirectory `
        --packAuthors Smirel `
        --packTitle "Listing Studio E2E" `
        --icon $IconFile `
        --mainExe EcommerceAgent.exe
    if ($LASTEXITCODE -ne 0) { throw "Velopack E2E pack failed for $PackVersion" }
}

Write-Host "  [E2E 1/5] Pack and install old Velopack version $OldVersion"
Invoke-E2EPack $OldVersion $OldAppDir
$Setup = Get-SingleVelopackArtifact -Directory $FeedDir -Filter "$PackId*-Setup.exe" -Label "old setup"
$Install = Start-Process -FilePath $Setup.FullName -ArgumentList @("--silent", "--installto", $InstallDir) -Wait -PassThru
if ($Install.ExitCode -ne 0) { throw "Velopack E2E old install failed: $($Install.ExitCode)" }

$RootGui = Join-Path $InstallDir "EcommerceAgent.exe"
$UpdateExe = Join-Path $InstallDir "Update.exe"
$CurrentGui = Join-Path $InstallDir "current\EcommerceAgent.exe"
$InstalledVersionFile = Join-Path $InstallDir "current\_internal\packaging\VERSION"
foreach ($Required in @($RootGui, $UpdateExe, $CurrentGui, $InstalledVersionFile)) {
    if (-not (Test-Path $Required)) { throw "Velopack E2E installed component missing: $Required" }
}
$InstalledOldVersion = (Get-Content $InstalledVersionFile -Raw).Trim()
if ($InstalledOldVersion -ne $OldVersion) {
    throw "Velopack E2E old install VERSION mismatch: expected=$OldVersion actual=$InstalledOldVersion"
}

Write-Host "  [E2E 2/5] Add target v$Version to the same local Velopack feed"
Invoke-E2EPack $Version $AppDir
$Index = Join-Path $FeedDir "releases.$Channel.json"
if (-not (Test-Path $Index)) { throw "Velopack E2E release index missing: $Index" }
$TargetPackage = Resolve-E2EFullPackage `
    -Directory $FeedDir `
    -ReleaseIndex $Index `
    -PackageId $PackId `
    -PackageVersion $Version

Write-Host "  [E2E 3/5] Launch installed old app and let Velopack update + restart it"
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

Write-Host "  [E2E 4/5] Verify real installed GUI restarted on v$Version"
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

Write-Host "  [E2E 5/5] Uninstall the updated Velopack application"
$Uninstall = Start-Process -FilePath $UpdateExe -ArgumentList @("--silent", "uninstall") -Wait -PassThru
if ($Uninstall.ExitCode -ne 0) { throw "Velopack E2E uninstall failed: $($Uninstall.ExitCode)" }
$UninstallDeadline = (Get-Date).AddSeconds(15)
while ((Test-Path $InstallDir) -and (Get-Date) -lt $UninstallDeadline) {
    Start-Sleep -Milliseconds 100
}
if (Test-Path $InstallDir) {
    throw "Velopack E2E uninstall left installation root behind: $InstallDir"
}

Write-Host "  Velopack E2E passed: install v$OldVersion -> update $($TargetPackage.Name) -> restart v$Version -> uninstall"
exit 0
