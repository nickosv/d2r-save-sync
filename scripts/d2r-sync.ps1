# Launch d2rsync from the repo root.
# With no arguments this syncs. Set D2R_SYNC_NO_PAUSE=1 to skip the trailing prompt.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$prefix = @()
$exe = "python"
if (Get-Command py -ErrorAction SilentlyContinue) {
    $exe = "py"
    $prefix = @("-3")
}

$toolArgs = if ($args.Count -eq 0) { @("sync") } else { $args }
& $exe @prefix -m d2rsync @toolArgs
$code = $LASTEXITCODE
if ($env:D2R_SYNC_NO_PAUSE -ne "1") {
    Write-Host ""
    Read-Host "Press Enter to close"
}
exit $code
