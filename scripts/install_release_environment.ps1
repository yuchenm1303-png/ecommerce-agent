param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LockFile = Join-Path $Root "requirements-release.lock"
$VenvRoot = Join-Path $Root ".release-venv"
$VenvScripts = Join-Path $VenvRoot "Scripts"
$VenvPython = Join-Path $VenvScripts "python.exe"
$Artifacts = Join-Path $Root "artifacts"
$ManifestPath = Join-Path $Artifacts "release-environment.json"
$ExpectedPython = "3.11.9"
$ExpectedDotNet = "8.0.424"
$ExpectedPip = "26.2.1"

if (-not (Test-Path $LockFile -PathType Leaf)) {
    throw "Release dependency lock missing: $LockFile"
}

$HostPython = (& python -c "import platform; print(platform.python_version())").Trim()
if ($LASTEXITCODE -ne 0 -or $HostPython -ne $ExpectedPython) {
    throw "Release build requires CPython $ExpectedPython; actual=$HostPython"
}

$DotNetVersion = (& dotnet --version).Trim()
if ($LASTEXITCODE -ne 0 -or $DotNetVersion -ne $ExpectedDotNet) {
    throw "Release build requires .NET SDK $ExpectedDotNet; actual=$DotNetVersion"
}

if (Test-Path $VenvRoot) {
    Remove-Item $VenvRoot -Recurse -Force
}
& python -m venv $VenvRoot
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VenvPython -PathType Leaf)) {
    throw "Failed to create isolated release virtual environment"
}

& $VenvPython -m pip install --disable-pip-version-check "pip==$ExpectedPip"
if ($LASTEXITCODE -ne 0) { throw "Failed to pin release pip to $ExpectedPip" }
& $VenvPython -m pip install --disable-pip-version-check -r $LockFile
if ($LASTEXITCODE -ne 0) { throw "Failed to install release dependency lock" }
& $VenvPython -m pip check
if ($LASTEXITCODE -ne 0) { throw "Release dependency graph failed pip check" }

Push-Location $Root
try {
    & dotnet tool restore
    if ($LASTEXITCODE -ne 0) { throw "dotnet tool restore failed: $LASTEXITCODE" }

    $ToolVersion = (& dotnet tool run vpk -- --version 2>$null | Select-Object -First 1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        $global:LASTEXITCODE = 0
        & dotnet tool run vpk -- --help *> $null
        if ($LASTEXITCODE -ne 0) { throw "Pinned Velopack CLI failed to start" }
        $ToolVersion = "1.2.0"
    }

    $SourceSha = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($SourceSha)) {
        throw "Unable to resolve release source commit"
    }

    $VersionFile = Join-Path $Root "packaging\VERSION"
    if (-not (Test-Path $VersionFile -PathType Leaf)) { throw "packaging/VERSION missing" }
    $PackageVersion = (Get-Content $VersionFile -Raw -Encoding UTF8).Trim()
    if ([string]::IsNullOrWhiteSpace($PackageVersion)) { throw "packaging/VERSION is empty" }

    $LockSha = (Get-FileHash $LockFile -Algorithm SHA256).Hash.ToLowerInvariant()
    $SpecSha = (Get-FileHash (Join-Path $Root "packaging\EcommerceAgent.spec") -Algorithm SHA256).Hash.ToLowerInvariant()
    $BuildScriptSha = (Get-FileHash (Join-Path $Root "scripts\build_windows.ps1") -Algorithm SHA256).Hash.ToLowerInvariant()
    $DotNetToolsSha = (Get-FileHash (Join-Path $Root ".config\dotnet-tools.json") -Algorithm SHA256).Hash.ToLowerInvariant()
    $InstalledPython = (& $VenvPython -c "import platform; print(platform.python_version())").Trim()
    $InstalledPip = (& $VenvPython -m pip --version).Trim()
    $Freeze = @(& $VenvPython -m pip freeze --all | Sort-Object)

    New-Item -ItemType Directory -Force -Path $Artifacts | Out-Null
    $Manifest = [ordered]@{
        schema = 1
        source_sha = $SourceSha
        package_version = $PackageVersion
        python = $InstalledPython
        pip = $InstalledPip
        dotnet = $DotNetVersion
        velopack_cli = $ToolVersion
        release_lock_sha256 = $LockSha
        pyinstaller_spec_sha256 = $SpecSha
        build_windows_sha256 = $BuildScriptSha
        dotnet_tools_sha256 = $DotNetToolsSha
        runner_image_os = [string]$env:ImageOS
        runner_image_version = [string]$env:ImageVersion
        packages = $Freeze
    }
    $Manifest | ConvertTo-Json -Depth 6 | Set-Content $ManifestPath -Encoding UTF8
}
finally {
    Pop-Location
}

# GitHub applies GITHUB_PATH to subsequent steps. Every test/build command after
# this point therefore runs inside the same isolated environment for Test and Stable.
$VenvScripts | Out-File -FilePath $env:GITHUB_PATH -Encoding utf8 -Append

Write-Host "Release environment ready:"
Write-Host "  Python      : $ExpectedPython"
Write-Host "  .NET SDK    : $ExpectedDotNet"
Write-Host "  Lock        : $LockFile"
Write-Host "  Manifest    : $ManifestPath"
