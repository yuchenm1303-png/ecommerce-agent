function Invoke-RepositoryVelopack {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    # Velopack 1.2.0 imports every VPK_* variable through .NET Configuration
    # before command execution. GitHub Actions can materialize an unset secret as
    # an empty environment entry, and typed options (for example FileInfo) then
    # fail during configuration binding. Own the process boundary explicitly:
    # every Velopack invocation gets a sanitized child environment. Explicit
    # VPK_TOKEN/signing values remain authoritative; when VPK_TOKEN is absent,
    # the standard GitHub Actions token is adapted only inside the child process.
    $StartInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = "dotnet"
    $StartInfo.UseShellExecute = $false
    $StartInfo.WorkingDirectory = (Get-Location).Path

    foreach ($Argument in @("tool", "run", "vpk", "--") + $Arguments) {
        if ($null -eq $Argument) {
            throw "Velopack argument list contains null."
        }
        $StartInfo.ArgumentList.Add([string]$Argument)
    }

    # GitHub Actions exposes repository credentials as GH_TOKEN, while Velopack's
    # GitHub source reads VPK_TOKEN. Bridge that naming mismatch at the child
    # process boundary so private release discovery/download is authenticated.
    # Never put the credential on the command line, mutate the parent environment,
    # or print the token value.
    $ExplicitVelopackToken = [string]$StartInfo.Environment["VPK_TOKEN"]
    if ([string]::IsNullOrWhiteSpace($ExplicitVelopackToken)) {
        foreach ($GitHubTokenName in @("GH_TOKEN", "GITHUB_TOKEN")) {
            $GitHubToken = [string]$StartInfo.Environment[$GitHubTokenName]
            if (-not [string]::IsNullOrWhiteSpace($GitHubToken)) {
                $StartInfo.Environment["VPK_TOKEN"] = $GitHubToken
                Write-Host "Velopack child environment mapped $GitHubTokenName to VPK_TOKEN for authenticated GitHub access."
                break
            }
        }
    }

    $BlankVelopackNames = @(
        $StartInfo.Environment.Keys | Where-Object {
            $_ -like "VPK_*" -and
            [string]::IsNullOrWhiteSpace([string]$StartInfo.Environment[$_])
        }
    )
    foreach ($Name in $BlankVelopackNames) {
        $null = $StartInfo.Environment.Remove($Name)
    }

    $RemainingBlank = @(
        $StartInfo.Environment.Keys | Where-Object {
            $_ -like "VPK_*" -and
            [string]::IsNullOrWhiteSpace([string]$StartInfo.Environment[$_])
        }
    )
    if ($RemainingBlank.Count -ne 0) {
        throw "Velopack child environment still contains blank VPK_* entries: $($RemainingBlank -join ', ')"
    }
    if ($BlankVelopackNames.Count -gt 0) {
        Write-Host "Velopack child environment ignored blank entries: $($BlankVelopackNames -join ', ')"
    }

    $Process = [System.Diagnostics.Process]::new()
    $Process.StartInfo = $StartInfo
    try {
        if (-not $Process.Start()) {
            throw "Failed to start pinned Velopack CLI through dotnet."
        }
        $Process.WaitForExit()
        return [int]$Process.ExitCode
    }
    finally {
        $Process.Dispose()
    }
}
