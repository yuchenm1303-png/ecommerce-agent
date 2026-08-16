param(
    [Parameter(Mandatory = $true)][string]$SourceSha,
    [Parameter(Mandatory = $true)][string]$Tag,
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$DefaultRef = "origin/main"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-Git {
    param([Parameter(Mandatory = $true)][string[]]$Args)
    & git @Args
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Args -join ' ') failed with exit code $LASTEXITCODE"
    }
}

$remoteTag = (& git ls-remote --tags origin "refs/tags/$Tag" 2>$null | Out-String).Trim()
$global:LASTEXITCODE = 0
if (-not [string]::IsNullOrWhiteSpace($remoteTag)) {
    throw "Release tag already exists: $Tag"
}

$baseSha = (& git rev-parse $DefaultRef).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($baseSha)) {
    throw "Unable to resolve default release base: $DefaultRef"
}
$sourceResolved = (& git rev-parse $SourceSha).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($sourceResolved)) {
    throw "Unable to resolve source commit: $SourceSha"
}

$tempRoot = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [IO.Path]::GetTempPath() }
$worktree = Join-Path $tempRoot ("listing-release-snapshot-" + [Guid]::NewGuid().ToString("N"))

try {
    Invoke-Git @("worktree", "add", "--detach", $worktree, $DefaultRef)
    Push-Location $worktree
    try {
        # Build a release-source commit whose application tree is the requested source,
        # while workflow files remain byte-for-byte identical to the default branch.
        # GitHub's built-in GITHUB_TOKEN cannot create a release tag that points at a
        # commit which changes .github/workflows, even with contents: write.
        Invoke-Git @("read-tree", "--reset", "-u", $sourceResolved)
        & git rm -r -q --ignore-unmatch -- ".github/workflows"
        if ($LASTEXITCODE -ne 0) { throw "Unable to clear source workflow files from release snapshot" }
        Invoke-Git @("checkout", $DefaultRef, "--", ".github/workflows")

        if (-not (Test-Path "packaging\VERSION" -PathType Leaf)) {
            throw "Release snapshot missing packaging/VERSION"
        }
        Set-Content "packaging\VERSION" $Version -Encoding ascii -NoNewline

        Invoke-Git @("add", "-A")
        Invoke-Git @("config", "user.name", "github-actions[bot]")
        Invoke-Git @("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
        Invoke-Git @("commit", "-m", "release snapshot $Tag")

        $snapshotSha = (& git rev-parse HEAD).Trim()
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($snapshotSha)) {
            throw "Unable to resolve release snapshot commit"
        }

        $workflowDiff = @(& git diff --name-only $DefaultRef $snapshotSha -- ".github/workflows")
        if ($LASTEXITCODE -ne 0) { throw "Unable to validate release workflow tree" }
        if ($workflowDiff.Count -gt 0) {
            throw "Release snapshot unexpectedly changes workflow files: $($workflowDiff -join ', ')"
        }

        $allDiff = @(& git diff --name-only $sourceResolved $snapshotSha -- .)
        if ($LASTEXITCODE -ne 0) { throw "Unable to validate release source snapshot" }
        $unexpected = @($allDiff | Where-Object {
            $_ -ne "packaging/VERSION" -and
            -not ([string]$_).StartsWith(".github/workflows/", [StringComparison]::OrdinalIgnoreCase)
        })
        if ($unexpected.Count -gt 0) {
            throw "Release snapshot differs from source outside workflow/version metadata: $($unexpected -join ', ')"
        }

        Invoke-Git @("tag", "-a", $Tag, $snapshotSha, "-m", "Listing Studio $Tag")
        Invoke-Git @("push", "origin", "refs/tags/$Tag")

        if ($env:GITHUB_OUTPUT) {
            "snapshot_sha=$snapshotSha" >> $env:GITHUB_OUTPUT
            "tag=$Tag" >> $env:GITHUB_OUTPUT
        }
        if ($env:GITHUB_ENV) {
            "RELEASE_SNAPSHOT_SHA=$snapshotSha" >> $env:GITHUB_ENV
            "RELEASE_TAG_CREATED=1" >> $env:GITHUB_ENV
        }

        Write-Host "Release tag prepared before build: $Tag -> $snapshotSha"
        Write-Host "Application source: $sourceResolved"
        Write-Host "Workflow baseline: $baseSha"
    }
    finally {
        Pop-Location
    }
}
finally {
    if (Test-Path $worktree) {
        & git worktree remove --force $worktree 2>$null
        $global:LASTEXITCODE = 0
    }
}
