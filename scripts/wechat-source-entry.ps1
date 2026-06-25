[CmdletBinding(PositionalBinding = $false)]
param(
    [ValidateSet('doctor', 'search', 'read', 'archive', 'maintain')]
    [string]$Mode = 'doctor',

    [string]$Query,

    [int]$Limit = 5,

    [string]$Url,

    [string]$AccountName,

    [string]$AuthorHint,

    [string[]]$SeedUrl,

    [string[]]$BootstrapArchiveRoot,

    [string]$OutputDir,

    [string]$OutputRoot,

    [int]$MaxArticles = 0,

    [int]$SearchPages = 3,

    [int]$SearchLimit = 20,

    [switch]$DownloadImages,

    [switch]$AllowHeadfulFallback,

    [switch]$NoHeadless,

    [switch]$NoImages,

    [switch]$NoSearch
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $scriptRoot '..'))
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $scriptRoot '..\..\..\..'))
$rootScripts = $scriptRoot

function Require-Value {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value,

        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    if (-not $Value) {
        throw "Parameter '$Name' is required when Mode=$Mode"
    }
}

function Get-StableAccountRoot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $safeAccount = ($Name -replace '[\\/:*?""<>|]', '_')
    return Join-Path $repoRoot "ingestion\10-Raw\WeChat\$safeAccount"
}

$doctorScript = Join-Path $rootScripts 'agent-reach.ps1'
$searchScript = Join-Path $rootScripts 'agent-reach-wechat-search.ps1'
$readScript = Join-Path $rootScripts 'agent-reach-wechat-read.ps1'
$archiveScript = Join-Path $rootScripts 'wechat-account-archive.ps1'

switch ($Mode) {
    'doctor' {
        & $doctorScript doctor
        exit $LASTEXITCODE
    }

    'search' {
        Require-Value -Value $Query -Name 'Query'
        & $searchScript -Query $Query -Limit $Limit
        exit $LASTEXITCODE
    }

    'read' {
        Require-Value -Value $Url -Name 'Url'

        if (-not $OutputDir) {
            $OutputDir = Join-Path $repoRoot '.runtime\agent-reach\tmp\wechat'
        }

        $invokeParams = @{
            Url = $Url
            OutputDir = $OutputDir
        }

        if ($NoImages) {
            $invokeParams.NoImages = $true
        }

        if ($NoHeadless) {
            $invokeParams.NoHeadless = $true
        }

        & $readScript @invokeParams
        exit $LASTEXITCODE
    }

    'archive' {
        Require-Value -Value $AccountName -Name 'AccountName'

        if (-not $OutputRoot) {
            $OutputRoot = Get-StableAccountRoot -Name $AccountName
        }

        $invokeParams = @{
            AccountName = $AccountName
            OutputRoot = $OutputRoot
            SearchPages = $SearchPages
            SearchLimit = $SearchLimit
        }

        if ($AuthorHint) {
            $invokeParams.AuthorHint = $AuthorHint
        }

        if ($SeedUrl) {
            $invokeParams.SeedUrl = @($SeedUrl | Where-Object { $_ })
        }

        if ($BootstrapArchiveRoot) {
            $invokeParams.BootstrapArchiveRoot = @($BootstrapArchiveRoot | Where-Object { $_ })
        }

        if ($MaxArticles -gt 0) {
            $invokeParams.MaxArticles = $MaxArticles
        }

        if ($DownloadImages) {
            $invokeParams.DownloadImages = $true
        }

        if ($AllowHeadfulFallback) {
            $invokeParams.AllowHeadfulFallback = $true
        }

        if ($NoHeadless) {
            $invokeParams.NoHeadless = $true
        }

        if ($NoSearch) {
            $invokeParams.NoSearch = $true
        }

        & $archiveScript @invokeParams
        exit $LASTEXITCODE
    }

    'maintain' {
        Require-Value -Value $AccountName -Name 'AccountName'

        if (-not $OutputRoot) {
            $OutputRoot = Get-StableAccountRoot -Name $AccountName
        }

        $effectiveBootstrap = New-Object System.Collections.Generic.List[string]
        if ($BootstrapArchiveRoot) {
            foreach ($root in $BootstrapArchiveRoot) {
                if ($root) {
                    $effectiveBootstrap.Add($root) | Out-Null
                }
            }
        }

        $bootstrapIndex = Join-Path $OutputRoot 'archive-index.json'
        if ((Test-Path -LiteralPath $bootstrapIndex) -and ($effectiveBootstrap.Count -eq 0)) {
            $effectiveBootstrap.Add($OutputRoot) | Out-Null
        }

        $invokeParams = @{
            AccountName = $AccountName
            OutputRoot = $OutputRoot
            SearchPages = $SearchPages
            SearchLimit = $SearchLimit
        }

        if ($AuthorHint) {
            $invokeParams.AuthorHint = $AuthorHint
        }

        if ($SeedUrl) {
            $invokeParams.SeedUrl = @($SeedUrl | Where-Object { $_ })
        }

        if ($effectiveBootstrap.Count -gt 0) {
            $invokeParams.BootstrapArchiveRoot = @($effectiveBootstrap)
        }

        if ($MaxArticles -gt 0) {
            $invokeParams.MaxArticles = $MaxArticles
        }

        if ($DownloadImages) {
            $invokeParams.DownloadImages = $true
        }

        if ($AllowHeadfulFallback) {
            $invokeParams.AllowHeadfulFallback = $true
        }

        if ($NoHeadless) {
            $invokeParams.NoHeadless = $true
        }

        if ($NoSearch) {
            $invokeParams.NoSearch = $true
        }

        & $archiveScript @invokeParams
        exit $LASTEXITCODE
    }
}
