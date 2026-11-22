param(
    [Parameter(Mandatory = $true)]
    [string]$RemoteUrl,
    [string]$CommitMessage = "Prepare PERIODLY CP-SAT release"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is not installed or is not available in PATH. Install Git for Windows first."
}

if (-not (Test-Path ".git")) {
    git init
    if ($LASTEXITCODE -ne 0) { throw "git init failed." }
}

git branch -M main
if ($LASTEXITCODE -ne 0) { throw "Could not set the main branch." }

git add --all
if ($LASTEXITCODE -ne 0) { throw "git add failed." }

$staged = @(git diff --cached --name-only)
$blocked = @($staged | Where-Object {
    $_ -match '(^|/)\.env($|\.)' -and $_ -notmatch '(^|/)\.env\.example$' -or
    $_ -match '(^|/)(\.venv|venv|__pycache__)(/|$)' -or
    $_ -match '\.(db|sqlite|sqlite3|db-wal|db-shm)(\..*)?$' -or
    $_ -match '\.(backup|bak)$' -or
    $_ -match '(^|/)(periodly[-_].*backup|.*migration_source.*)\.' -or
    $_ -match '(^|/)exports?/' -or
    $_ -match '\.(xlsx|xls|pdf)$'
})

if ($blocked.Count -gt 0) {
    Write-Host "The following sensitive/generated files are staged and must not be pushed:" -ForegroundColor Red
    $blocked | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    throw "Git push stopped. Remove these files from the Git index, then rerun the script. Your working files were not deleted."
}

Write-Host "Files staged for review:" -ForegroundColor Cyan
git status --short

$existingRemote = $null
try { $existingRemote = (git remote get-url origin 2>$null).Trim() } catch { $existingRemote = $null }
if ($existingRemote) {
    if ($existingRemote -ne $RemoteUrl) {
        throw "An origin remote already exists and differs from the supplied URL. Review git remote -v; no remote was changed."
    }
} else {
    git remote add origin $RemoteUrl
    if ($LASTEXITCODE -ne 0) { throw "Could not add the origin remote." }
}

$stagedCount = @(git diff --cached --name-only).Count
if ($stagedCount -gt 0) {
    git commit -m $CommitMessage
    if ($LASTEXITCODE -ne 0) { throw "Commit failed. Configure git user.name and user.email, then retry." }
} else {
    Write-Host "No new staged changes to commit."
}

git push -u origin main
if ($LASTEXITCODE -ne 0) { throw "Push failed. Check the remote URL, repository permissions and Git authentication." }

Write-Host "PERIODLY main branch pushed successfully." -ForegroundColor Green
