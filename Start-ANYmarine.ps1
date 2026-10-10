param([string]$Python, [string]$Project, [ValidateSet('software','auto','gpu')][string]$ViewerBackend)
$ErrorActionPreference = 'Stop'
$launchArgs = @((Join-Path $PSScriptRoot 'run_gui.py'))
if ($Project) { $launchArgs += @('--project', $Project) }
if ($ViewerBackend) { $launchArgs += @('--viewer-backend', $ViewerBackend) }
$previousRuntime = $env:ANYMARINE_PYTHON
try {
    if ($Python) { $env:ANYMARINE_PYTHON = $Python }
    $interpreter = if ($Python) { $Python } else { 'python' }
    & $interpreter @launchArgs
    $code = $LASTEXITCODE
} finally {
    $env:ANYMARINE_PYTHON = $previousRuntime
}
exit $code
