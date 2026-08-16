param(
    [Parameter(Mandatory = $true)][string]$MsiPath,
    [Parameter(Mandatory = $true)][string]$ExpectedVersion,
    [Parameter(Mandatory = $true)][string]$InstallDir,
    [string]$PackId = "Smirel.ListingStudio",
    [string]$PackTitle = "Listing Studio"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Normalize-ComparablePath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [IO.Path]::GetFullPath($Path).TrimEnd([char[]]"\/")
}

function Write-MsiLogTail {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (Test-Path $Path -PathType Leaf) {
        Write-Host "---- MSI log tail: $Path ----"
        Get-Content $Path -Tail 120 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
        Write-Host "---- end MSI log tail ----"
    }
}

function Invoke-MsiExec {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Operation
    )

    # ProcessStartInfo.ArgumentList preserves every MSI token exactly. Avoid
    # Start-Process -ArgumentList string re-tokenisation/quote loss for paths.
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = "msiexec.exe"
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    foreach ($arg in $Arguments) {
        [void]$psi.ArgumentList.Add([string]$arg)
    }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $psi
    if (-not $process.Start()) { throw "Failed to start msiexec for $Operation" }
    if (-not $process.WaitForExit(180000)) {
        try { $process.Kill($true) } catch { }
        throw "Velopack MSI $Operation timed out after 180 seconds"
    }
    if ($process.ExitCode -ne 0) {
        throw "Velopack MSI $Operation failed: $($process.ExitCode)"
    }
}

$msi = (Resolve-Path $MsiPath).Path
$installDir = Normalize-ComparablePath $InstallDir
$msiArpKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\MSI:$PackId"
$velopackArpKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$PackId"
$logPath = "$installDir.msi.log"
$msiRootStubName = "$PackTitle.exe"

# Keep this test isolated from Setup.exe. MSI and Setup share PackId state.
foreach ($registration in @($msiArpKey, $velopackArpKey)) {
    if (Test-Path $registration) {
        throw "MSI smoke requires a clean PackId registration; found $registration"
    }
}
if (Test-Path $installDir) { Remove-Item $installDir -Recurse -Force }
Remove-Item $logPath -Force -ErrorAction SilentlyContinue

$installed = $false
$failure = $null
$actualInstallDir = $installDir
try {
    # Velopack 1.2.0 public custom-directory contract.
    Invoke-MsiExec -Operation "install" -Arguments @(
        "/i", $msi,
        "/qn",
        "/norestart",
        "VELOPACK_INSTALLDIR=$installDir",
        "/L*v", $logPath
    )
    $installed = $true

    # Validate MSI registration first and use it as the authoritative install
    # location. This distinguishes an override failure from a file-layout failure.
    if (-not (Test-Path $msiArpKey)) {
        throw "MSI uninstall registration missing: $msiArpKey"
    }
    $registration = Get-ItemProperty $msiArpKey
    if ([string]::IsNullOrWhiteSpace([string]$registration.InstallLocation)) {
        throw "MSI InstallLocation registration is empty"
    }
    $actualInstallDir = Normalize-ComparablePath ([string]$registration.InstallLocation)
    if (-not [string]::Equals($actualInstallDir, $installDir, [StringComparison]::OrdinalIgnoreCase)) {
        throw "MSI InstallLocation mismatch: expected=$installDir actual=$actualInstallDir"
    }
    if ([string]$registration.DisplayVersion -ne $ExpectedVersion) {
        throw "MSI registered version mismatch: expected=$ExpectedVersion actual=$($registration.DisplayVersion)"
    }

    # Important Velopack MSI semantic: the root execution stub is named from
    # PackTitle ("Listing Studio.exe"), while current/ keeps the real main EXE
    # name ("EcommerceAgent.exe"). Setup.exe uses a different root layout.
    $stub = Join-Path $actualInstallDir $msiRootStubName
    $update = Join-Path $actualInstallDir "Update.exe"
    $gui = Join-Path $actualInstallDir "current\EcommerceAgent.exe"
    $worker = Join-Path $actualInstallDir "current\EcommerceAgentWorker.exe"
    $versionFile = Join-Path $actualInstallDir "current\_internal\packaging\VERSION"
    foreach ($required in @($stub, $update, $gui, $worker, $versionFile)) {
        if (-not (Test-Path $required -PathType Leaf)) {
            throw "MSI installed component missing: $required"
        }
    }

    $actualVersion = (Get-Content $versionFile -Raw).Trim()
    if ($actualVersion -ne $ExpectedVersion) {
        throw "MSI installed VERSION mismatch: expected=$ExpectedVersion actual=$actualVersion"
    }

    & $worker --self-test
    if ($LASTEXITCODE -ne 0) {
        throw "MSI installed worker self-test failed: $LASTEXITCODE"
    }
}
catch {
    $failure = $_
    Write-MsiLogTail $logPath
}
finally {
    if ($installed -or (Test-Path $msiArpKey)) {
        try {
            Invoke-MsiExec -Operation "uninstall" -Arguments @(
                "/x", $msi,
                "/qn",
                "/norestart",
                "/L*v", $logPath
            )
        }
        catch {
            $cleanupMessage = $_.Exception.Message
            Write-MsiLogTail $logPath
            if ($null -eq $failure) {
                $failure = $_
            }
            else {
                Write-Warning $cleanupMessage
            }
        }
    }
}

if ($null -ne $failure) { throw $failure }
if (Test-Path $msiArpKey) {
    throw "MSI uninstall registration remained after uninstall: $msiArpKey"
}

foreach ($residual in @(
    (Join-Path $actualInstallDir $msiRootStubName),
    (Join-Path $actualInstallDir "Update.exe"),
    (Join-Path $actualInstallDir "current\EcommerceAgent.exe"),
    (Join-Path $actualInstallDir "current\EcommerceAgentWorker.exe")
)) {
    if (Test-Path $residual) {
        throw "MSI uninstall left installed component: $residual"
    }
}

if (Test-Path $actualInstallDir) {
    $remaining = @(Get-ChildItem $actualInstallDir -Force -ErrorAction SilentlyContinue)
    if ($remaining.Count -eq 0) { Remove-Item $actualInstallDir -Force }
}
Remove-Item $logPath -Force -ErrorAction SilentlyContinue
Write-Host "Velopack MSI smoke passed: $msiRootStubName -> current\\EcommerceAgent.exe -> uninstall ($ExpectedVersion)"
