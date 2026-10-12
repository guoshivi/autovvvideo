[CmdletBinding()]
param(
    [string]$Root=(Split-Path -Parent $MyInvocation.MyCommand.Path),
    [switch]$Force
)
$ErrorActionPreference='Stop'
$Root=(Resolve-Path -LiteralPath $Root).Path
$py=Join-Path $Root '.runtime\embedding\Scripts\python.exe'
if(-not (Test-Path -LiteralPath $py)){throw 'Project Python is missing. Run install.ps1 first (see docs/DEPLOY.md).'}
$cfgPath=Join-Path $Root 'config\workbench.config.json'
if(Test-Path -LiteralPath $cfgPath){
    $cfg=[IO.File]::ReadAllText($cfgPath,[Text.Encoding]::UTF8)|ConvertFrom-Json
    $port=[int]$cfg.port
    if($port -gt 0){
        $listening=Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        if($listening -and -not $Force){throw "the workbench looks like it is running (port $port). Close its window first, then update."}
    }
}
$dirty=& git -C $Root status --porcelain -- . ':!config' ':!data' ':!models' ':!output' ':!logs' ':!tools' ':!.runtime' ':!.uv-cache' ':!.uv-python' 2>$null
if($dirty -and -not $Force){throw 'local source changes detected. Commit or save them first, or pass -Force to keep a backup and continue.'}
$stamp=Get-Date -Format 'yyyyMMdd-HHmmss'
$backup=Join-Path $Root ('data\backups\'+$stamp)
New-Item -ItemType Directory -Force -Path $backup | Out-Null
foreach($item in @('workbench','static','deploy','docs','tests','reference')){
    $p=Join-Path $Root $item
    if(Test-Path -LiteralPath $p){Copy-Item -LiteralPath $p -Destination $backup -Recurse -Force}
}
& git -C $Root fetch --tags origin
if($LASTEXITCODE -ne 0){throw 'git fetch failed; nothing was changed.'}
$upstream=(& git -C $Root rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>$null)
if(-not $upstream){$upstream='origin/main'}
& git -C $Root merge --ff-only $upstream
if($LASTEXITCODE -ne 0){Write-Output "UPDATE_PARTIAL: could not fast-forward (local commits or diverged history). Source not changed. Backup=$stamp";exit 3}
Write-Output "UPDATE_OK: source updated by fast-forward. config/data/models/tools untouched. Backup=$stamp"
Write-Output 'Close and reopen the workbench (desktop icon) to use the new source.'
