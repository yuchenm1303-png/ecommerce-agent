param(
    [string]$Target = ""
)

$ErrorActionPreference = "Stop"

$repo = (git rev-parse --show-toplevel).Trim()
if (-not $repo) {
    throw "Current directory is not inside ecommerce-agent."
}

if (-not $Target) {
    $parent = Split-Path $repo -Parent
    $Target = Join-Path $parent "ecommerce-agent-gui"
}

if (Test-Path $Target) {
    throw "Target worktree already exists: $Target"
}

Write-Host "Fetching GUI branch without touching the current worktree..."
git fetch origin feat/local-test-gui
if ($LASTEXITCODE -ne 0) {
    throw "git fetch failed"
}

$null = git show-ref --verify --quiet refs/heads/feat/local-test-gui
$hasLocalBranch = ($LASTEXITCODE -eq 0)

if ($hasLocalBranch) {
    git worktree add "$Target" feat/local-test-gui
} else {
    git worktree add --track -b feat/local-test-gui "$Target" origin/feat/local-test-gui
}

if ($LASTEXITCODE -ne 0) {
    throw "git worktree add failed"
}

Write-Host ""
Write-Host "GUI worktree ready: $Target" -ForegroundColor Green
Write-Host "The original dirty worktree was not reset, stashed, cleaned, or checked out."
Write-Host "Next:"
Write-Host "  cd `"$Target`""
Write-Host "  python -m pip install -r requirements.txt"
Write-Host "  python -m pip install -r requirements-gui.txt"
Write-Host "  python run_local_gui.py"
