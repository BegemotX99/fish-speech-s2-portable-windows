param(
    [ValidateSet('InstallAndRun','Install','Run','Doctor','VerifyModel')]
    [string]$Mode = 'InstallAndRun'
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

# Only this process and its children inherit these settings. No setx/registry edits.
$folders = @{
    TEMP='temp'; TMP='temp'; TMPDIR='temp'; UV_CACHE_DIR='cache\uv';
    UV_PYTHON_INSTALL_DIR='runtime'; UV_TOOL_DIR='cache\uv-tools';
    PIP_CACHE_DIR='cache\pip'; HF_HOME='cache\huggingface';
    HF_HUB_CACHE='cache\huggingface\hub'; HF_XET_CACHE='cache\huggingface\xet';
    TORCH_HOME='cache\torch'; TORCHINDUCTOR_CACHE_DIR='cache\inductor';
    TORCH_EXTENSIONS_DIR='cache\torch-extensions'; LIBROSA_CACHE_DIR='cache\librosa';
    TRITON_CACHE_DIR='cache\triton'; CUDA_CACHE_PATH='cache\cuda';
    NUMBA_CACHE_DIR='cache\numba'; MPLCONFIGDIR='cache\matplotlib';
    XDG_CACHE_HOME='cache'; TIKTOKEN_CACHE_DIR='cache\tiktoken';
    MODELSCOPE_CACHE='cache\modelscope'; IPYTHONDIR='cache\ipython'
}
foreach ($key in $folders.Keys) {
    $folder = Join-Path $Root $folders[$key]
    [IO.Directory]::CreateDirectory($folder) | Out-Null
    [Environment]::SetEnvironmentVariable($key, $folder, 'Process')
}
foreach ($folder in @('logs','output','voices','checkpoints\s2-pro','cache\downloads')) {
    [IO.Directory]::CreateDirectory((Join-Path $Root $folder)) | Out-Null
}
$env:PYTHONNOUSERSITE='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
$env:HF_HUB_DISABLE_TELEMETRY='1'
$env:DO_NOT_TRACK='1'
$env:UV_NO_PROGRESS='1'
$env:UV_LINK_MODE='copy'
$env:UV_PYTHON_DOWNLOADS='manual'
# Do not inherit another Python/Conda installation's search paths.
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
$env:PATH=(Join-Path $Root 'runtime\ffmpeg\bin') + ';' + $env:PATH
$Python = Join-Path $Root 'runtime\cpython-3.12.12-windows-x86_64-none\python.exe'
$Uv = Join-Path $Root 'runtime\uv\uv.exe'
$Revision='0f8a12e8a651062fdee9c33a894bdccd3a8513f1'
$Downloads=Join-Path $Root 'cache\downloads'
$TranscriptStarted=$false

function Check-Exit([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed (exit $LASTEXITCODE). See logs." }
}

function Download-Verified([string]$Url, [string]$Target, [string]$Hash) {
    if ((Test-Path -LiteralPath $Target) -and ((Get-FileHash -LiteralPath $Target -Algorithm SHA256).Hash -eq $Hash)) {
        return
    }
    $part=$Target+'.part'
    for ($attempt=1; $attempt -le 4; $attempt++) {
        Write-Host "Downloading $([IO.Path]::GetFileName($Target)) [$attempt/4]"
        try {
            $curl=Get-Command curl.exe -ErrorAction SilentlyContinue
            if ($curl) {
                & $curl.Source --fail --location --retry 3 --retry-delay 2 --connect-timeout 30 --speed-time 120 --speed-limit 1024 --continue-at - --output $part $Url
                if ($LASTEXITCODE -eq 33) {
                    Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue
                    & $curl.Source --fail --location --retry 3 --output $part $Url
                }
                Check-Exit 'Download'
            } else {
                [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
                Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $part
            }
            if ((Get-FileHash -LiteralPath $part -Algorithm SHA256).Hash -ne $Hash) {
                Remove-Item -LiteralPath $part -Force
                throw 'SHA-256 mismatch. The download has been removed; retrying.'
            }
            Move-Item -LiteralPath $part -Destination $Target -Force
            return
        } catch {
            if ($attempt -eq 4) { throw }
            Write-Host $_.Exception.Message
            Start-Sleep -Seconds 2
        }
    }
}

function Install-Runtime {
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'Windows x64 is required.' }
    if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { throw 'This build targets x64 Windows, not ARM64.' }
    $free=(Get-Item -LiteralPath $Root).PSDrive.Free
    if ($null -ne $free -and $free -lt 30GB -and -not (Test-Path -LiteralPath (Join-Path $Root 'checkpoints\s2-pro\.complete'))) {
        throw 'Please use a drive with at least 30 GB of free space for the first installation.'
    }
    if (-not (Test-Path -LiteralPath $Uv)) {
        $zip=Join-Path $Downloads 'uv-0.13.8.zip'
        Download-Verified 'https://github.com/astral-sh/uv/releases/download/0.13.8/uv-x86_64-pc-windows-msvc.zip' $zip '529e78a9cec6cd7e3e014f7ecd8bda0c113969d4ed0f2efeedb85c30943325b8'
        $dest=Join-Path $Root 'runtime\uv'
        [IO.Directory]::CreateDirectory($dest) | Out-Null
        Expand-Archive -LiteralPath $zip -DestinationPath $dest -Force
        if (-not (Test-Path -LiteralPath $Uv)) { throw 'uv.exe missing after extraction.' }
    }
    if (-not (Test-Path -LiteralPath $Python)) {
        & $Uv --no-config python install 3.12.12 --no-bin --no-registry
        Check-Exit 'Portable Python installation'
    }
    # uv creates an absolute minor-version junction. We always invoke the exact
    # interpreter path, so remove this unused alias to keep directory copies portable.
    $alias=Join-Path $Root 'runtime\cpython-3.12-windows-x86_64-none'
    $aliasItem=Get-Item -LiteralPath $alias -Force -ErrorAction SilentlyContinue
    if ($null -ne $aliasItem -and ($aliasItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        [IO.Directory]::Delete($alias)
    }
    if (-not (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $Python) '.vc-runtime-ready'))) {
        $redist=Join-Path $Downloads 'vc-redist-x64.exe'
        Download-Verified 'https://download.visualstudio.microsoft.com/download/pr/e9604e21-4b79-4096-a4b3-1b63d4b55b4d/CC0FF0EB1DC3F5188AE6300FAEF32BF5BEEBA4BDD6E8E445A9184072096B713B/VC_redist.x64.exe' $redist 'cc0ff0eb1dc3f5188ae6300faef32bf5beeba4bdd6e8e445a9184072096b713b'
        & $Python -u -B (Join-Path $Root 'portable\extract_vc_runtime.py') $redist
        Check-Exit 'Local Visual C++ Runtime extraction'
    }
    $app=Join-Path $Root 'app'
    $sourceMarker=Join-Path $app '.portable-source-revision'
    if (-not (Test-Path -LiteralPath $sourceMarker)) {
        if (Test-Path -LiteralPath $app) {
            if (Get-ChildItem -LiteralPath $app -Force | Select-Object -First 1) {
                throw 'app exists without its version marker. Rename app to app-backup and run again.'
            }
        }
        $zip=Join-Path $Downloads 'fish-speech-source.zip'
        Download-Verified "https://codeload.github.com/fishaudio/fish-speech/zip/$Revision" $zip '24487df7d512e991d5834c26bf74ce138ea64bb695eb61285a1136423cead549'
        $unpack=Join-Path $Root 'temp\source-unpack'
        [IO.Directory]::CreateDirectory($unpack) | Out-Null
        Expand-Archive -LiteralPath $zip -DestinationPath $unpack -Force
        [IO.Directory]::CreateDirectory($app) | Out-Null
        Get-ChildItem -LiteralPath (Join-Path $unpack "fish-speech-$Revision") -Force | Copy-Item -Destination $app -Recurse -Force
        [IO.File]::WriteAllText($sourceMarker,$Revision)
    }
    if ((Get-Content -LiteralPath $sourceMarker -Raw).Trim() -ne $Revision) { throw 'Source revision mismatch.' }
    $ffmpeg=Join-Path $Root 'runtime\ffmpeg\bin\ffmpeg.exe'
    if (-not (Test-Path -LiteralPath $ffmpeg)) {
        $zip=Join-Path $Downloads 'ffmpeg-9.0.2.zip'
        Download-Verified 'https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip' $zip '60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba'
        $unpack=Join-Path $Root 'temp\ffmpeg-unpack'
        Expand-Archive -LiteralPath $zip -DestinationPath $unpack -Force
        $src=Join-Path $unpack 'ffmpeg-9.0.2-essentials_build'
        $dest=Join-Path $Root 'runtime\ffmpeg'
        [IO.Directory]::CreateDirectory($dest) | Out-Null
        Get-ChildItem -LiteralPath $src -Force | Copy-Item -Destination $dest -Recurse -Force
    }
    $requirements=Join-Path $Root 'requirements.lock'
    $expected=(Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash
    $marker=Join-Path $Root 'runtime\.packages-ready'
    if (-not (Test-Path -LiteralPath $marker) -or (Get-Content -LiteralPath $marker -Raw).Trim() -ne $expected) {
        & $Uv --no-config pip sync $requirements --python $Python --torch-backend cu128 --link-mode copy --break-system-packages --require-hashes
        Check-Exit 'Python packages installation'
        & $Python -u -B (Join-Path $Root 'portable\doctor.py')
        Check-Exit 'Import and FFmpeg checks'
        [IO.File]::WriteAllText($marker,$expected)
    }
    $manifest=Get-Content -LiteralPath (Join-Path $Root 'portable\model-manifest.json') -Raw | ConvertFrom-Json
    $complete=Join-Path $Root 'checkpoints\s2-pro\.complete'
    $needModel=-not (Test-Path -LiteralPath $complete)
    if (-not $needModel) { $needModel=(Get-Content -LiteralPath $complete -Raw).Trim() -ne $manifest.revision }
    foreach ($file in $manifest.files) {
        $path=Join-Path $Root ('checkpoints\s2-pro\'+$file.name)
        if (-not (Test-Path -LiteralPath $path) -or (Get-Item -LiteralPath $path).Length -ne $file.size) { $needModel=$true }
    }
    if ($needModel) {
        & $Python -u -B (Join-Path $Root 'portable\download_model.py')
        Check-Exit 'S2-Pro model download'
    }
}

try {
    Start-Transcript -LiteralPath (Join-Path $Root ('logs\setup-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.log')) | Out-Null
    $TranscriptStarted=$true
    Write-Host 'Fish Speech S2-Pro Portable | Built with Fish Audio'
    Write-Host "Folder: $Root"
    if ($Mode -in @('Install','InstallAndRun')) { Install-Runtime }
    if (-not (Test-Path -LiteralPath $Python)) { throw 'Run 1_INSTALL_AND_RUN.bat first.' }
    if ($Mode -eq 'Doctor') {
        & $Python -u -B (Join-Path $Root 'portable\doctor.py') --require-cuda
        Check-Exit 'Diagnostics'
    }
    if ($Mode -eq 'VerifyModel') {
        & $Python -u -B (Join-Path $Root 'portable\download_model.py')
        Check-Exit 'Model verification'
    }
    if ($Mode -in @('Run','InstallAndRun')) {
        $settings=Get-Content -LiteralPath (Join-Path $Root 'settings.json') -Raw | ConvertFrom-Json
        if ($settings.port -lt 1024 -or $settings.port -gt 65535) { throw 'Invalid port in settings.json.' }
        if ($settings.context -notin @(4096,8192)) { throw 'context must be 4096 or 8192.' }
        if ($settings.device -notin @('cuda','cpu')) { throw 'device must be cuda or cpu.' }
        $env:HF_HUB_OFFLINE='1'
        $env:TRANSFORMERS_OFFLINE='1'
        & $Python -u -B (Join-Path $Root 'portable\server.py') --port $settings.port --context $settings.context --device $settings.device
        Check-Exit 'S2-Pro server'
    }
} catch {
    Write-Host ''
    Write-Host ('ERROR: '+$_.Exception.Message) -ForegroundColor Red
    Write-Host 'Details: logs folder. Manual: MANUAL_RU.html / README.md'
    exit 1
} finally {
    if ($TranscriptStarted) { Stop-Transcript | Out-Null }
}
