[CmdletBinding(PositionalBinding = $false)]
param(
    [ValidateSet('show-albums', 'prepare', 'run', 'start', 'progress', 'audit', 'repair', 'show-command')]
    [string]$Action = 'show-albums',

    [string]$OutputRoot,

    [switch]$Fresh,

    [int]$MaxArticles = 0,

    [switch]$DownloadImages,

    [switch]$AllowHeadfulFallback,

    [switch]$NoHeadless
)

if ($PSVersionTable.PSEdition -ne 'Core') {
    $pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
    if (-not $pwsh) {
        throw 'pwsh is required for Unicode-safe Qingbian WeChat archive routing but was not found in PATH.'
    }

    $forwardArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath, '-Action', $Action)
    if ($OutputRoot) { $forwardArgs += @('-OutputRoot', $OutputRoot) }
    if ($Fresh) { $forwardArgs += '-Fresh' }
    if ($MaxArticles -gt 0) { $forwardArgs += @('-MaxArticles', [string]$MaxArticles) }
    if ($DownloadImages) { $forwardArgs += '-DownloadImages' }
    if ($AllowHeadfulFallback) { $forwardArgs += '-AllowHeadfulFallback' }
    if ($NoHeadless) { $forwardArgs += '-NoHeadless' }

    & $pwsh.Source @forwardArgs
    exit $LASTEXITCODE
}

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

try {
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [Console]::InputEncoding = $utf8NoBom
    [Console]::OutputEncoding = $utf8NoBom
    $OutputEncoding = $utf8NoBom
}
catch {
    # Some hosts do not allow console encoding changes. Continue with best effort.
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $scriptRoot '..'))
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $scriptRoot '..\..\..\..'))

# Python fallback: 1) project venv  2) agentv2 .runtime  3) system PATH
$python = Join-Path $projectRoot 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $agentRuntimePython = Join-Path $repoRoot '.runtime\agent-reach\venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $agentRuntimePython) { $python = $agentRuntimePython }
    else { $python = (Get-Command python -ErrorAction SilentlyContinue).Source }
}

# Load config
$configPath = Join-Path $projectRoot 'config.yaml'
if (-not (Test-Path -LiteralPath $configPath)) {
    throw "config.yaml not found at $configPath. Copy and edit config.yaml before running."
}
$configLines = Get-Content -LiteralPath $configPath -Raw
$config = if ($python) {
    $yamlLoader = @"
import sys, yaml, json
with open(r'$configPath', 'r', encoding='utf-8') as f:
    print(json.dumps(yaml.safe_load(f), ensure_ascii=False))
"@
    (& $python -c $yamlLoader 2>$null) | ConvertFrom-Json
} else { $null }
if (-not $config) { throw "Failed to load config.yaml" }

$accountName = $config.account.name
$biz = $config.account.biz
$sourceLabel = 'config.yaml account.albums'

function Get-QingbianAlbums {
    $albums = @()
    foreach ($album in $config.account.albums) {
        $albums += [ordered]@{
            biz = $biz
            album_id = $album.id
            title = $album.title
            content_size = 0
            source_url = $sourceLabel
            is_complete = $false
        }
    }
    return $albums
}

function Get-DefaultOutputRoot {
    if ($OutputRoot) {
        return [System.IO.Path]::GetFullPath($OutputRoot)
    }

    $dataRoot = if ($config -and $config.paths.data_root) { [System.IO.Path]::GetFullPath((Join-Path $projectRoot $config.paths.data_root)) } else { Join-Path $projectRoot 'data' }

    if ($Fresh) {
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        return Join-Path $dataRoot "ingestion\10-Raw\WeChat\请辩-full-rerun-direct-$stamp"
    }

    return Join-Path $dataRoot 'ingestion\10-Raw\WeChat\请辩'
}

function Initialize-QingbianAlbumQueue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedOutputRoot
    )

    New-Item -ItemType Directory -Force -Path $ResolvedOutputRoot | Out-Null
    $metadataPath = Join-Path $ResolvedOutputRoot 'crawl-metadata.json'

    if (Test-Path -LiteralPath $metadataPath) {
        $metadata = Get-Content -Raw -LiteralPath $metadataPath | ConvertFrom-Json -AsHashtable
    }
    else {
        $metadata = [ordered]@{}
    }

    if (-not $metadata.Contains('archive') -or -not $metadata['archive']) {
        $metadata['archive'] = [ordered]@{}
    }
    if (-not $metadata.Contains('failures') -or -not $metadata['failures']) {
        $metadata['failures'] = @()
    }

    $archive = $metadata['archive']
    if (-not ($archive -is [System.Collections.IDictionary])) {
        $archive = [ordered]@{}
    }

    if (-not $archive.Contains('queued_albums') -or -not $archive['queued_albums']) {
        $archive['queued_albums'] = @()
    }
    if (-not $archive.Contains('processed_albums') -or -not $archive['processed_albums']) {
        $archive['processed_albums'] = @()
    }
    if (-not $archive.Contains('frontier_pending') -or -not $archive['frontier_pending']) {
        $archive['frontier_pending'] = @()
    }

    $existingQueued = @{}
    foreach ($row in @($archive['queued_albums'])) {
        $albumId = if ($row -is [System.Collections.IDictionary]) { $row['album_id'] } else { $row.album_id }
        if ($albumId) {
            $existingQueued[[string]$albumId] = $true
        }
    }

    $processedComplete = @{}
    foreach ($row in @($archive['processed_albums'])) {
        $rowBiz = if ($row -is [System.Collections.IDictionary]) { $row['biz'] } else { $row.biz }
        $albumId = if ($row -is [System.Collections.IDictionary]) { $row['album_id'] } else { $row.album_id }
        if (-not ($rowBiz -and $albumId)) {
            continue
        }

        $isComplete = $true
        if ($row -is [System.Collections.IDictionary]) {
            if ($row.Contains('is_complete')) {
                $isComplete = [bool]$row['is_complete']
            }
        }
        elseif ($null -ne $row.PSObject.Properties['is_complete']) {
            $isComplete = [bool]$row.is_complete
        }

        if ($isComplete) {
            $processedComplete["$rowBiz|$albumId"] = $true
        }
    }

    $queued = New-Object System.Collections.Generic.List[object]
    foreach ($row in @($archive['queued_albums'])) {
        $queued.Add($row) | Out-Null
    }

    # Re-queue all known albums unconditionally. The archive command's bootstrap
    # mechanism handles deduplication — it won't re-download articles already in
    # the index. This ensures daily cron picks up newly published articles.
    foreach ($album in Get-QingbianAlbums) {
        $albumKey = "$($album.biz)|$($album.album_id)"
        if (-not $existingQueued.ContainsKey($album.album_id)) {
            $queued.Add([pscustomobject]$album) | Out-Null
        }
    }

    $archive['account_name'] = $accountName
    $archive['queued_albums'] = [object[]]$queued.ToArray()
    $archive['queued_album_count'] = $queued.Count
    $archive['target_bizs'] = [object[]]@($biz)
    $archive['target_account_names'] = [object[]]@($accountName)
    $archive['output_root'] = $ResolvedOutputRoot
    $metadata['archive'] = $archive

    $json = $metadata | ConvertTo-Json -Depth 20
    [System.IO.File]::WriteAllText($metadataPath, $json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))

    $indexPath = Join-Path $ResolvedOutputRoot 'archive-index.json'
    if (-not (Test-Path -LiteralPath $indexPath)) {
        [System.IO.File]::WriteAllText($indexPath, '[]' + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
    }

    return $metadataPath
}

function Get-ArchiveCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedOutputRoot
    )

    $entry = Join-Path $scriptRoot 'wechat-source-entry.ps1'
    $args = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', $entry,
        '-Mode', 'maintain',
        '-AccountName', $accountName,
        '-OutputRoot', $ResolvedOutputRoot,
        '-SearchPages', '0',
        '-SearchLimit', '0',
        '-NoSearch'
    )

    $bootstrapIndex = Join-Path $ResolvedOutputRoot 'archive-index.json'
    if (Test-Path -LiteralPath $bootstrapIndex) {
        $args += @('-BootstrapArchiveRoot', $ResolvedOutputRoot)
    }

    if ($MaxArticles -gt 0) {
        $args += @('-MaxArticles', [string]$MaxArticles)
    }
    if ($DownloadImages) {
        $args += '-DownloadImages'
    }
    if ($AllowHeadfulFallback) {
        $args += '-AllowHeadfulFallback'
    }
    if ($NoHeadless) {
        $args += '-NoHeadless'
    }

    [pscustomobject]@{
        FilePath = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
        ArgumentList = $args
        Display = 'powershell ' + (($args | ForEach-Object {
                    if ($_ -match '\s') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
                }) -join ' ')
    }
}

function Show-Progress {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedOutputRoot
    )

    $metadataPath = Join-Path $ResolvedOutputRoot 'crawl-metadata.json'
    $mdCount = if (Test-Path -LiteralPath $ResolvedOutputRoot) {
        (Get-ChildItem -LiteralPath $ResolvedOutputRoot -Recurse -Filter '*.md' -File | Measure-Object).Count
    }
    else {
        0
    }

    $metadata = if (Test-Path -LiteralPath $metadataPath) {
        Get-Content -Raw -LiteralPath $metadataPath | ConvertFrom-Json
    }
    else {
        $null
    }

    $processes = Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -like "*$ResolvedOutputRoot*" -or $_.CommandLine -like '*wechat_account_archive.py*' } |
        Select-Object ProcessId, Name

    [pscustomobject]@{
        account_name = $accountName
        output_root = $ResolvedOutputRoot
        markdown_count = $mdCount
        crawl_article_count = if ($metadata) { $metadata.archive.article_count } else { $null }
        queued_album_count = if ($metadata) { ($metadata.archive.queued_albums | Measure-Object).Count } else { $null }
        processed_album_count = if ($metadata) { ($metadata.archive.processed_albums | Measure-Object).Count } else { $null }
        top_failure_count = if ($metadata) { ($metadata.failures | Measure-Object).Count } else { $null }
        running_process_count = ($processes | Measure-Object).Count
    } | Format-List
}

$resolvedOutputRoot = Get-DefaultOutputRoot

switch ($Action) {
    'show-albums' {
        Get-QingbianAlbums | ForEach-Object { [pscustomobject]$_ } | Format-Table -AutoSize
    }

    'prepare' {
        $metadataPath = Initialize-QingbianAlbumQueue -ResolvedOutputRoot $resolvedOutputRoot
        Write-Host "Prepared Qingbian album queue: $metadataPath"
    }

    'show-command' {
        Initialize-QingbianAlbumQueue -ResolvedOutputRoot $resolvedOutputRoot | Out-Null
        $cmd = Get-ArchiveCommand -ResolvedOutputRoot $resolvedOutputRoot
        $cmd.Display
    }

    'run' {
        Initialize-QingbianAlbumQueue -ResolvedOutputRoot $resolvedOutputRoot | Out-Null
        $cmd = Get-ArchiveCommand -ResolvedOutputRoot $resolvedOutputRoot
        & $cmd.FilePath @($cmd.ArgumentList)
        exit $LASTEXITCODE
    }

    'start' {
        Initialize-QingbianAlbumQueue -ResolvedOutputRoot $resolvedOutputRoot | Out-Null
        $cmd = Get-ArchiveCommand -ResolvedOutputRoot $resolvedOutputRoot
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $stdout = Join-Path $repoRoot "runs\$stamp-N-wechat-qingbian-album-archive-stdout.txt"
        $stderr = Join-Path $repoRoot "runs\$stamp-N-wechat-qingbian-album-archive-stderr.txt"
        $process = Start-Process -FilePath $cmd.FilePath -ArgumentList $cmd.ArgumentList -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
        [pscustomobject]@{
            process_id = $process.Id
            output_root = $resolvedOutputRoot
            stdout = $stdout
            stderr = $stderr
        } | Format-List
    }

    'progress' {
        Show-Progress -ResolvedOutputRoot $resolvedOutputRoot
    }

    'audit' {
        $audit = Join-Path $scriptRoot 'wechat_archive_audit.py'
        & $python $audit --archive-root $resolvedOutputRoot --account-name $accountName --review-days 7 --search-limit 0
        exit $LASTEXITCODE
    }

    'repair' {
        $repair = Join-Path $scriptRoot 'wechat_archive_consistency.py'
        $candidateRecoveryRoot = Join-Path $repoRoot ('ingestion\25-Candidates\WeChat\20260401-2156-CAN-wechat-account-archive-recovery-' + $accountName)
        $rawRecoveryRoots = Get-ChildItem -LiteralPath (Join-Path $repoRoot 'ingestion\10-Raw\WeChat') -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like ($accountName + '-full-rerun*') } |
            Select-Object -ExpandProperty FullName
        $runtimeHome = Join-Path $projectRoot 'state'
        New-Item -ItemType Directory -Force -Path $runtimeHome | Out-Null
        $args = @(
            $repair,
            '--archive-root', $resolvedOutputRoot,
            '--account-name', $accountName,
            '--author-hint', $accountName,
            '--repo-root', $repoRoot,
            '--runtime-home', $runtimeHome,
            '--repair',
            '--refetch-missing',
            '--archive-extra',
            '--strict'
        )
        if (Test-Path -LiteralPath $candidateRecoveryRoot) {
            $args += @('--repair-root', $candidateRecoveryRoot)
        }
        foreach ($rawRecoveryRoot in @($rawRecoveryRoots)) {
            $args += @('--repair-root', $rawRecoveryRoot)
        }
        & $python @args
        exit $LASTEXITCODE
    }
}
