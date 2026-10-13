[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [string]$Root=(Split-Path -Parent $MyInvocation.MyCommand.Path)
)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$Root=(Resolve-Path -LiteralPath $Root).Path
function Fail([string]$Message){throw "Optional Vox setup stopped: $Message"}
function Read-Json([string]$Path){return ([IO.File]::ReadAllText($Path,[Text.Encoding]::UTF8) | ConvertFrom-Json)}
function Ensure-Unlinked([string]$Path){
    $p=[IO.Path]::GetFullPath($Path)
    while($p){
        if(Test-Path -LiteralPath $p){
            $item=Get-Item -Force -LiteralPath $p
            if(($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){Fail 'linked setup paths are not allowed'}
        }
        $parent=Split-Path -Parent $p
        if($parent -eq $p){break};$p=$parent
    }
}
function Native-Check([string]$Message){if($LASTEXITCODE -ne 0){Fail $Message}}
$configPath=Join-Path $Root 'config\workbench.config.json'
if(-not (Test-Path -LiteralPath $configPath)){Fail 'Run the normal install.ps1 first; no configuration found.'}
$cfg=Read-Json $configPath
$manifest=Read-Json (Join-Path $Root 'deploy\vox_dependencies.json')
if($manifest.status -ne 'VERIFIED_BY_PARENT'){Fail 'the optional Vox manifest is unverified'}
$uv=Join-Path $Root 'tools\uv.exe'
$voxRoot=Join-Path $Root '.runtime\vox'
$voxPython=Join-Path $voxRoot 'Scripts\python.exe'
$setup=Join-Path $Root 'deploy\vox_setup.py'
Ensure-Unlinked $Root;Ensure-Unlinked $voxRoot;Ensure-Unlinked (Join-Path $Root 'models\vox');Ensure-Unlinked $configPath
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONPATH=$Root
$env:HF_HUB_DISABLE_TELEMETRY='1'
if($CheckOnly){
    if(-not (Test-Path -LiteralPath $voxPython)){Fail 'Vox is not installed; run this script without -CheckOnly first.'}
    & $voxPython -B $setup --check
    Native-Check 'Vox model/runtime check failed'
    Write-Output 'VOX_CHECK_COMPLETE: imports, model hashes and CPU audio checked; real voice preview and full film still required.'
    exit 0
}
# Never stop a running service or mutate its environment under it.
$listeners=Get-NetTCPConnection -LocalPort ([int]$cfg.port) -State Listen -ErrorAction SilentlyContinue
if($listeners){Fail 'Close the workbench first. This installer will not stop it for you.'}
if(-not (Test-Path -LiteralPath $uv)){Fail 'tools\uv.exe is missing; run the normal install.ps1 first.'}
$env:UV_CACHE_DIR=Join-Path $Root '.uv-cache'
$env:UV_PYTHON_INSTALL_DIR=Join-Path $Root '.uv-python'
Ensure-Unlinked $env:UV_CACHE_DIR;Ensure-Unlinked $env:UV_PYTHON_INSTALL_DIR
& $uv python install $manifest.python_version --install-dir $env:UV_PYTHON_INSTALL_DIR
Native-Check 'the isolated Vox Python installation failed'
$basePython=(& $uv python find $manifest.python_version --managed-python | Out-String).Trim()
Native-Check 'could not locate managed Vox Python'
if(-not (Test-Path -LiteralPath $basePython)){Fail 'managed Python was not found'}
if(-not (Test-Path -LiteralPath $voxPython)){
    & $uv venv --python $basePython $voxRoot
    Native-Check 'could not create the optional Vox environment'
}
# Matched pair, not unrelated latest torch/torchaudio versions.
& $uv pip install --python $voxPython "torch==$($manifest.torch_version)" "torchaudio==$($manifest.torchaudio_version)" --index-url $manifest.torch_index --extra-index-url 'https://pypi.org/simple' --index-strategy 'unsafe-best-match'
Native-Check 'the matched CUDA torch/torchaudio installation failed'
$sdkDir=Join-Path $Root 'tools\vox-downloads'
Ensure-Unlinked $sdkDir
New-Item -ItemType Directory -Force -Path $sdkDir | Out-Null
$sdk=Join-Path $sdkDir ("voxcpm-$($manifest.sdk.version)-py3-none-any.whl")
Ensure-Unlinked $sdk
if(-not (Test-Path -LiteralPath $sdk)){
    Invoke-WebRequest -UseBasicParsing -Uri $manifest.sdk.url -OutFile $sdk
}
if((Get-FileHash -LiteralPath $sdk -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.sdk.sha256){Fail 'SDK SHA-256 mismatch; no unverified SDK will be executed.'}
if((Get-Item -LiteralPath $sdk).Length -ne $manifest.sdk.bytes){Fail 'SDK byte length mismatch'}
& $uv pip install --python $voxPython $sdk --requirement (Join-Path $Root 'requirements-vox.txt') --index-url 'https://pypi.org/simple' --extra-index-url $manifest.torch_index --index-strategy 'unsafe-best-match'
Native-Check 'optional Vox dependencies failed to install'
& $uv pip check --python $voxPython
Native-Check 'optional Vox dependency metadata check failed'
# Files are downloaded from the fixed snapshot and SHA-256 checked before config is changed.
& $voxPython -B $setup --download --configure
Native-Check 'model download, hash check, runtime check or optional configuration failed'
$receiptDir=Join-Path $Root 'data\vox-install'
Ensure-Unlinked $receiptDir
New-Item -ItemType Directory -Force -Path $receiptDir | Out-Null
& $uv pip freeze --python $voxPython | Set-Content -LiteralPath (Join-Path $receiptDir 'installed-requirements.txt') -Encoding UTF8
Native-Check 'could not record installed optional dependency versions'
Write-Output 'VOX_OPTIONAL_INSTALLED: Qwen and embedding environments unchanged. Open the workbench, select Vox, and make a real preview. GPU synthesis/end-to-end output are not certified by installation alone.'
