[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$AccountName,

    [string]$AuthorHint,

    [string[]]$SeedUrl,

    [string[]]$BootstrapArchiveRoot,

    [int]$MaxArticles = 0,

    [int]$SearchPages = 3,

    [int]$SearchLimit = 20,

    [string]$OutputRoot,

    [switch]$DownloadImages,

    [switch]$AllowHeadfulFallback,

    [switch]$NoHeadless,

    [switch]$Force,

    [switch]$NoSearch
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $scriptRoot '..'))
$python = Join-Path $projectRoot 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $agentRuntimePython = Join-Path $repoRoot '.runtime\agent-reach\venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $agentRuntimePython) { $python = $agentRuntimePython }
    else { $python = (Get-Command python -ErrorAction SilentlyContinue).Source }
}
$ctx = @{ VenvPython = $python; RepoRoot = $projectRoot }

$helper = Join-Path $PSScriptRoot 'wechat_account_archive.py'
if (-not (Test-Path $helper)) {
    throw "Archive helper not found: $helper"
}

if (-not $OutputRoot) {
    $safeAccount = ($AccountName -replace '[\\/:*?""<>|]', '_')
    $dataRoot = Join-Path $projectRoot 'data'
    New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null
    $OutputRoot = Join-Path $dataRoot "ingestion\10-Raw\WeChat\$safeAccount"
}

$resolvedOutput = [System.IO.Path]::GetFullPath($OutputRoot)
if (-not (Test-Path $resolvedOutput)) {
    New-Item -ItemType Directory -Force -Path $resolvedOutput | Out-Null
}

$argsList = @(
    $helper,
    '--repo-root', $ctx.RepoRoot,
    '--runtime-home', $ctx.Home,
    '--account-name', $AccountName,
    '--output-root', $resolvedOutput,
    '--search-pages', [string]$SearchPages,
    '--search-limit', [string]$SearchLimit
)

if ($AuthorHint) {
    $argsList += @('--author-hint', $AuthorHint)
}

if ($SeedUrl) {
    foreach ($seed in $SeedUrl) {
        if ($seed) {
            $argsList += @('--seed-url', $seed)
        }
    }
}

if ($BootstrapArchiveRoot) {
    foreach ($root in $BootstrapArchiveRoot) {
        if ($root) {
            $argsList += @('--bootstrap-archive-root', $root)
        }
    }
}

if ($MaxArticles -gt 0) {
    $argsList += @('--max-articles', [string]$MaxArticles)
}

if ($DownloadImages) {
    $argsList += '--download-images'
}

if ($AllowHeadfulFallback) {
    $argsList += '--allow-headful-fallback'
}

if ($NoHeadless) {
    $argsList += '--no-headless'
}

if ($Force) {
    $argsList += '--force'
}

if ($NoSearch) {
    $argsList += '--no-search'
}

& $ctx.VenvPython @argsList
exit $LASTEXITCODE
