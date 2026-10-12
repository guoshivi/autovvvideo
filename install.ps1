[CmdletBinding()]
param(
    [switch]$PrepareOnly,
    [switch]$EnableAutostart,
    [string]$Root=(Split-Path -Parent $MyInvocation.MyCommand.Path)
)
$ErrorActionPreference='Stop'
$Root=(Resolve-Path -LiteralPath $Root).Path
$ProgressPreference='SilentlyContinue'

function Fail([string]$Message){ throw "autovvvideo install stopped: $Message" }
function Abs([string]$Path){ if([IO.Path]::IsPathRooted($Path)){return [IO.Path]::GetFullPath($Path)};return [IO.Path]::GetFullPath((Join-Path $Root $Path)) }
function Read-Json([string]$Path){ return ([IO.File]::ReadAllText($Path,[Text.Encoding]::UTF8) | ConvertFrom-Json) }
function Write-Json($Object,[string]$Path){ $json=$Object | ConvertTo-Json -Depth 12; [IO.File]::WriteAllText($Path,$json,(New-Object Text.UTF8Encoding($true))) }
function Hash([string]$Path){ return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Download([string]$Url,[string]$Destination,[string]$Expected){
    if((Test-Path -LiteralPath $Destination) -and (Hash $Destination) -eq $Expected){ return }
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Destination
    if((Hash $Destination) -ne $Expected){ Remove-Item -Force -LiteralPath $Destination -ErrorAction SilentlyContinue; Fail "SHA-256 mismatch for downloaded asset" }
}
function Expand-ZipSafe([string]$Zip,[string]$Destination){
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive=[IO.Compression.ZipFile]::OpenRead($Zip)
    try {
        foreach($entry in $archive.Entries){
            $name=$entry.FullName.Replace('\','/')
            if([string]::IsNullOrWhiteSpace($name) -or $name.StartsWith('/') -or $name -match '^[A-Za-z]:') { Fail "zip contains an absolute path" }
            $parts=$name.Split('/')
            if($parts | Where-Object { $_ -eq '..' -or $_ -eq '' -and $name -notlike '*/' }) { Fail "zip contains a traversal path" }
            $target=[IO.Path]::GetFullPath((Join-Path $Destination $name))
            $base=[IO.Path]::GetFullPath($Destination)
            if(-not $target.StartsWith($base,[StringComparison]::OrdinalIgnoreCase)){ Fail "zip path escapes destination" }
            if($name.EndsWith('/')){ New-Item -ItemType Directory -Force -Path $target | Out-Null; continue }
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
            $input=$entry.Open();$output=[IO.File]::Open($target,[IO.FileMode]::Create,[IO.FileAccess]::Write,[IO.FileShare]::None)
            try{$input.CopyTo($output)}finally{$output.Dispose();$input.Dispose()}
        }
    } finally { $archive.Dispose() }
}
function Find-ChineseFont{
    $fontRoot=Join-Path $env:WINDIR 'Fonts'
    $names=@('msyh.ttc','msyhbd.ttc','msyhl.ttc','simhei.ttf','simsun.ttc','Deng.ttf','Dengb.ttf','NotoSansCJK-Regular.ttc')
    foreach($name in $names){$p=Join-Path $fontRoot $name;if(Test-Path -LiteralPath $p){return $p}}
    $candidate=Get-ChildItem -LiteralPath $fontRoot -File -ErrorAction SilentlyContinue | Where-Object {$_.Name -match '(?i)(yahei|simsun|simhei|deng|noto.*cjk)' } | Select-Object -First 1
    if($candidate){return $candidate.FullName}
    Fail 'no Chinese system font found in the target Windows Fonts directory; install/enable a system font, then rerun. No font is copied by this installer.'
}
function Ensure-Shortcut([string]$Target,[string]$Arguments,[string]$Link){
    $shell=New-Object -ComObject WScript.Shell;$shortcut=$shell.CreateShortcut($Link)
    $shortcut.TargetPath=$Target;$shortcut.Arguments=$Arguments;$shortcut.WorkingDirectory=$Root;$shortcut.Description='autovvvideo local workbench';$shortcut.Save()
}

$dirs=@('config','data','materials','output','logs','models','models\embedding','models\tts','tools','.runtime','.uv-cache','.uv-python')
foreach($d in $dirs){New-Item -ItemType Directory -Force -Path (Join-Path $Root $d) | Out-Null}
$configPath=Join-Path $Root 'config\workbench.config.json';$example=Join-Path $Root 'config.example.json'
$created=$false
if(-not (Test-Path -LiteralPath $configPath)) { Copy-Item -LiteralPath $example -Destination $configPath; $created=$true }
$cfg=Read-Json $configPath
# Resolve paths into this checkout while preserving a user's host, port, roots,
# output choice and stored key on every subsequent run.
$cfg.project_src=$Root
$cfg.data_root=Abs ([string]$cfg.data_root)
$cfg.output_root=Abs ([string]$cfg.output_root)
$cfg.tools_dir=Abs ([string]$cfg.tools_dir)
$cfg.model_dir=Abs ([string]$cfg.model_dir)
$cfg.tts_model=Abs ([string]$cfg.tts_model)
$cfg.gpu_lock_path=Abs ([string]$cfg.gpu_lock_path)
$cfg.ffmpeg=Abs ([string]$cfg.ffmpeg);$cfg.ffprobe=Abs ([string]$cfg.ffprobe);$cfg.fdk_encoder=Abs ([string]$cfg.fdk_encoder)
$cfg.embedding_python=Abs ([string]$cfg.embedding_python);$cfg.tts_python=Abs ([string]$cfg.tts_python)
if($created -or -not $cfg.nas_roots -or $cfg.nas_roots.Count -eq 0){$cfg.nas_roots=@((Join-Path $Root 'materials'))}else{$cfg.nas_roots=@($cfg.nas_roots | ForEach-Object {Abs ([string]$_)})}
$font=if($cfg.font_path -and (Test-Path -LiteralPath ([string]$cfg.font_path))){[string]$cfg.font_path}else{Find-ChineseFont}
$cfg.font_path=$font
if(-not $cfg.host){$cfg.host='127.0.0.1'};if(-not $cfg.port){$cfg.port=8765};if(-not $cfg.ai_model){$cfg.ai_model='deepseek-chat'};if(-not $cfg.ai_base_url){$cfg.ai_base_url='https://api.deepseek.com'}
Write-Json $cfg $configPath
$lock=Join-Path $Root 'data\gpu.lock';if(-not (Test-Path -LiteralPath $lock)){[IO.File]::WriteAllBytes($lock,[byte[]](0))}
if($PrepareOnly){Write-Output 'PREPARED_ONLY: configuration/directories/font reference created; no downloads, runtimes, models, tasks, shortcut or service started.';exit 0}

$manifest=Read-Json (Join-Path $Root 'deploy\dependencies.json')
if($manifest.status -ne 'VERIFIED_BY_PARENT'){Fail 'deploy/dependencies.json is not VERIFIED_BY_PARENT'}
foreach($a in @($manifest.assets.uv,$manifest.assets.ffmpeg_shared_gpl,$manifest.assets.fdkaac)){
    if([string]::IsNullOrWhiteSpace($a.url) -or [string]::IsNullOrWhiteSpace($a.sha256)){Fail 'required asset URL/SHA-256 is missing; refusing an unverifiable download'}
}
$downloadDir=Join-Path $Root 'tools\downloads';New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null
$uvZip=Join-Path $downloadDir 'uv.zip';$ffZip=Join-Path $downloadDir 'ffmpeg.zip';$fdkZip=Join-Path $downloadDir 'fdkaac.zip'
Download $manifest.assets.uv.url $uvZip $manifest.assets.uv.sha256
Download $manifest.assets.ffmpeg_shared_gpl.url $ffZip $manifest.assets.ffmpeg_shared_gpl.sha256
Download $manifest.assets.fdkaac.url $fdkZip $manifest.assets.fdkaac.sha256
$extract=Join-Path $Root 'tools\.extract';if(Test-Path $extract){Remove-Item -Recurse -Force $extract};New-Item -ItemType Directory -Force -Path $extract | Out-Null
Expand-ZipSafe $uvZip (Join-Path $extract 'uv');Expand-ZipSafe $ffZip (Join-Path $extract 'ffmpeg');Expand-ZipSafe $fdkZip (Join-Path $extract 'fdkaac')
$uvExe=Get-ChildItem -Path (Join-Path $extract 'uv') -Filter uv.exe -Recurse | Select-Object -First 1
if(-not $uvExe){Fail 'uv.exe missing from the verified archive'};Copy-Item -LiteralPath $uvExe.FullName -Destination (Join-Path $Root 'tools\uv.exe') -Force
$ffExe=Get-ChildItem -Path (Join-Path $extract 'ffmpeg') -Filter ffmpeg.exe -Recurse | Select-Object -First 1
$probeExe=Get-ChildItem -Path (Join-Path $extract 'ffmpeg') -Filter ffprobe.exe -Recurse | Select-Object -First 1
if(-not $ffExe -or -not $probeExe){Fail 'verified FFmpeg archive lacks ffmpeg.exe or ffprobe.exe'}
$bin=$ffExe.Directory;Get-ChildItem -LiteralPath $bin -File | Copy-Item -Destination (Join-Path $Root 'tools') -Force
$fdkExe=Get-ChildItem -Path (Join-Path $extract 'fdkaac') -Filter fdkaac.exe -Recurse | Select-Object -First 1
if(-not $fdkExe -or (Hash $fdkExe.FullName) -ne $manifest.assets.fdkaac.executable_sha256){Fail 'fdkaac executable SHA-256 does not match the parent manifest'};Copy-Item -LiteralPath $fdkExe.FullName -Destination (Join-Path $Root 'tools\fdkaac.exe') -Force

$uv=Join-Path $Root 'tools\uv.exe';$env:UV_CACHE_DIR=Join-Path $Root '.uv-cache';$env:UV_PYTHON_INSTALL_DIR=Join-Path $Root '.uv-python'
& $uv python install $manifest.python_version --install-dir $env:UV_PYTHON_INSTALL_DIR;if($LASTEXITCODE -ne 0){Fail 'uv Python installation failed'}
$basePython=Get-ChildItem -Path $env:UV_PYTHON_INSTALL_DIR -Filter python.exe -Recurse | Select-Object -First 1;if(-not $basePython){Fail 'uv did not install a Python executable'}
foreach($envName in @('embedding','tts')){
    $venv=Join-Path $Root ('.runtime\'+$envName);$venvPy=Join-Path $venv 'Scripts\python.exe'
    if(-not (Test-Path -LiteralPath $venvPy)){& $uv venv --python $basePython.FullName $venv;if($LASTEXITCODE -ne 0){Fail "uv venv failed for $envName"}}
    $req=Join-Path $Root ('requirements-'+$envName+'.txt');& $uv pip install --python $venvPy --requirement $req --index-url 'https://pypi.org/simple';if($LASTEXITCODE -ne 0){Fail "dependency installation failed for $envName"}
    & $uv pip check --python $venvPy;if($LASTEXITCODE -ne 0){Fail "pip check failed for $envName (ABI compatibility is separately checked by doctor)"}
}

$modelScript=Join-Path $Root 'data\download_models.py'
@'
import hashlib,json,sys
from pathlib import Path
from huggingface_hub import snapshot_download
root=Path(sys.argv[1]); manifest=json.loads((root/'deploy'/'dependencies.json').read_text(encoding='utf-8-sig'))
for name,target in (('embedding',root/'models'/'embedding'),('tts',root/'models'/'tts')):
    spec=manifest['assets']['models'][name]; target.mkdir(parents=True,exist_ok=True)
    snapshot_download(repo_id=spec['repo_id'],revision=spec['revision'],local_dir=str(target))
    for rel,digest in spec['files_sha256'].items():
        p=target/rel
        if not p.is_file() or (spec.get('files_bytes',{}).get(rel) is not None and p.stat().st_size != spec['files_bytes'][rel]):
            raise SystemExit(f'{name}: missing or wrong length: {rel}')
        h=hashlib.sha256(p.read_bytes()).hexdigest()
        if h != digest: raise SystemExit(f'{name}: SHA-256 mismatch: {rel}')
    (target/'REVISION').write_text(json.dumps({'repo_id':spec['repo_id'],'revision':spec['revision'],'verified_files':len(spec['files_sha256'])},ensure_ascii=False,indent=2),encoding='utf-8')
'@ | Set-Content -LiteralPath $modelScript -Encoding UTF8
try {
    $embPy=Join-Path $Root '.runtime\embedding\Scripts\python.exe';& $embPy $modelScript $Root
    if($LASTEXITCODE -ne 0){Fail 'model snapshot/download or complete manifest verification failed; REVISION is written only after success'}
} finally {Remove-Item -Force -LiteralPath $modelScript -ErrorAction SilentlyContinue}
if(Test-Path $extract){Remove-Item -Recurse -Force $extract};Remove-Item -Recurse -Force $downloadDir

$desktop=[Environment]::GetFolderPath('Desktop');if($desktop){Ensure-Shortcut (Join-Path $env:WINDIR 'System32\wscript.exe') ('"'+(Join-Path $Root 'launch.vbs')+'"') (Join-Path $desktop 'autovvvideo.lnk')}
if($EnableAutostart){
    $taskRun='wscript.exe "'+(Join-Path $Root 'launch.vbs')+'"'
    & schtasks.exe /Create /TN 'autovvvideo' /SC ONLOGON /TR $taskRun /RL LIMITED /F
    if($LASTEXITCODE -ne 0){Fail 'could not create the current-user ONLOGON task'}
    Write-Output 'Autostart enabled for the interactive logon user; this is not an S4U/NAS task.'
}
Write-Output 'INSTALL_COMPLETE: tools, isolated runtimes, pip checks, and model manifest verification completed. Run doctor.ps1 before real media output.'
