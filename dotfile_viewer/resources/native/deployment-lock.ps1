function Invoke-DeploymentLocked {
    param([bool]$DryRun, [scriptblock]$Action)
    if ($DryRun) { & $Action; return }
    Initialize-DotfilesPython
    $LeaseText = & $env:DOTFILES_PYTHON -m dotfile_viewer.lease acquire --owner-pid $PID
    if ($LASTEXITCODE -ne 0) { throw 'Deployment lock exists: acquisition failed; inspect its owner' }
    $Lease = $LeaseText | ConvertFrom-Json
    $OldPath = $env:DOTFILES_DEPLOYMENT_LOCK; $OldToken = $env:DOTFILES_DEPLOYMENT_TOKEN
    try {
        $env:DOTFILES_DEPLOYMENT_LOCK = $Lease.path
        $env:DOTFILES_DEPLOYMENT_TOKEN = $Lease.token
        & $Action
    } finally {
        $env:DOTFILES_DEPLOYMENT_LOCK = $OldPath; $env:DOTFILES_DEPLOYMENT_TOKEN = $OldToken
        if ($Lease.owned) {
            & $env:DOTFILES_PYTHON -m dotfile_viewer.lease release --path $Lease.path --token $Lease.token
            if ($LASTEXITCODE -ne 0) { throw 'Could not release deployment lease' }
        }
    }
}
