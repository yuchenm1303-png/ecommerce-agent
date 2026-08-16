param(
    [Parameter(Mandatory = $true)][string]$MsiPath,
    [Parameter(Mandatory = $true)][string]$ExpectedVersion,
    [Parameter(Mandatory = $true)][string]$InstallDir,
    [string]$PackId = "Smirel.ListingStudio"
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
        Get-Content $Path -Tail 100 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
        Write-Host "---- end MSI log tail ----"
    }
}

$msi = (Resolve-Path $MsiPath).Path
$installDir = Normalize-ComparablePath $InstallDir
$msiArpKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\MSI:$PackId"
$velopackArpKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$PackId"
$logPath = "$installDir.msi.log"

# Keep this test isolated from the Setup.exe smoke. Velopack uses the same PackId
# for both installation surfaces, so a previous installed copy would make the MSI
# result ambiguous instead of proving a fresh MSI install.
foreach ($registration in @($msiArpKey, $velopackArpKey)) {
    if (Test-Path $registration) {
        throw "MSI smoke requires a clean PackId registration; found $registration"
    }
}
if (Test-Path $installDir) {
    Remove-Item $installDir -Recurse -Force
}
Remove-Item $logPath -Force -ErrorAction SilentlyContinue

$installed = $false
$failure = $null
try {
    # Velopack 1.2.0's own MSI tests use INSTALLFOLDER for silent installs.
    # Use one raw argument string so PowerShell cannot re-tokenize the quoted MSI
    # property value before it reaches msiexec.
    $installArgs = "/i `"$msi`" /qn /norestart INSTALLFOLDER=`"$installDir`" /L*v `"$logPath`""
    $install = Start-Process -FilePath "msiexec.exe" -ArgumentList $installArgs -Wait -PassThru
    if ($install.ExitCode -ne 0) {
        throw "Velopack MSI install failed: $($install.ExitCode)"
    }
    $installed = $true

    $stub = Join-Path $installDir "EcommerceAgent.exe"
    $update = Join-Path $installDir "Update.exe"
    $gui = Join-Path $installDir "current\EcommerceAgent.exe"
    $worker = Join-Path $installDir "current\EcommerceAgentWorker.exe"
    $versionFile = Join-Path $installDir "current\_internal\packaging\VERSION"
    foreach ($required in @($stub, $update, $gui, $worker, $versionFile)) {
        if (-not (Test-Path $required -PathType Leaf)) {
            throw "MSI installed component missing: $required"
        }
    }

    $actualVersion = (Get-Content $versionFile -Raw).Trim()
    if ($actualVersion -ne $ExpectedVersion) {
        throw "MSI installed VERSION mismatch: expected=$ExpectedVersion actual=$actualVersion"
    }

    if (-not (Test-Path $msiArpKey)) {
        throw "MSI uninstall registration missing: $msiArpKey"
    }
    $registration = Get-ItemProperty $msiArpKey
    $registeredLocation = Normalize-ComparablePath ([string]$registration.InstallLocation)
    if (-not [string]::Equals($registeredLocation, $installDir, [StringComparison]::OrdinalIgnoreCase)) {
        throw "MSI InstallLocation mismatch: expected=$installDir actual=$registeredLocation"
    }
    if ([string]$registration.DisplayVersion -ne $ExpectedVersion) {
        throw "MSI registered version mismatch: expected=$ExpectedVersion actual=$($registration.DisplayVersion)"
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
        $uninstallArgs = "/x `"$msi`" /qn /norestart /L*v `"$logPath`""
        $uninstall = Start-Process -FilePath "msiexec.exe" -ArgumentList $uninstallArgs -Wait -PassThru
        if ($uninstall.ExitCode -ne 0) {
            $cleanupMessage = "Velopack MSI uninstall failed: $($uninstall.ExitCode)"
            Write-MsiLogTail $logPath
            if ($null -eq $failure) {
                $failure = [System.Exception]::new($cleanupMessage)
            }
            else {
                Write-Warning $cleanupMessage
            }
        }
    }
}

if ($null -ne $failure) {
    throw $failure
}

if (Test-Path $msiArpKey) {
    throw "MSI uninstall registration remained after uninstall: $msiArpKey"
}
foreach ($residual in @(
    (Join-Path $installDir "EcommerceAgent.exe"),
    (Join-Path $installDir "Update.exe"),
    (Join-Path $installDir "current\EcommerceAgent.exe"),
    (Join-Path $installDir "current\EcommerceAgentWorker.exe")
)) {
    if (Test-Path $residual) {
        throw "MSI uninstall left installed component: $residual"
    }
}

if (Test-Path $installDir) {
    $remaining = @(Get-ChildItem $installDir -Force -ErrorAction SilentlyContinue)
    if ($remaining.Count -eq 0) {
        Remove-Item $installDir -Force
    }
}
Remove-Item $logPath -Force -ErrorAction SilentlyContinue
Write-Host "Velopack MSI smoke passed: install -> verify -> uninstall ($ExpectedVersion)"
