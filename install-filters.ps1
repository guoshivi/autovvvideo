$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv = Join-Path $Root '.runtime\filters'
$Py = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path $Py)) { py -3.12 -m venv $Venv }
& $Py -m pip install --disable-pip-version-check --only-binary=:all: -r (Join-Path $Root 'requirements-filters.txt')
& $Py -c "import cv2,numpy; print('filters ok',cv2.__version__,numpy.__version__)"
Write-Host "filters_python=$Py"
