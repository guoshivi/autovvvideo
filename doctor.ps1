[CmdletBinding()]
param([switch]$CheckConfigOnly,[switch]$HeavyGPU,[string]$PythonExe,[string]$Root=(Split-Path -Parent $MyInvocation.MyCommand.Path))
$ErrorActionPreference='Stop'
if(-not $PythonExe){$PythonExe=Join-Path $Root '.runtime\embedding\Scripts\python.exe'}
if(-not (Test-Path -LiteralPath $PythonExe)){throw 'Project Python is missing. Follow docs/DEPLOY.md to create the isolated environments. Do not use a random system Python.'}
$probeArgs=@();if($CheckConfigOnly){$probeArgs+='--check-config-only'};if($HeavyGPU){$probeArgs+='--heavy-gpu'}
$env:PYTHONIOENCODING='utf-8'
& $PythonExe (Join-Path $Root 'deploy\doctor.py') @probeArgs
exit $LASTEXITCODE
