[CmdletBinding()]
param(
    [switch]$NoPwshReexec,

    [string]$ZhihuCookieFile = '',

    [int]$ZhihuMaxItems = 0
)

if (-not $NoPwshReexec -and $PSVersionTable.PSEdition -ne 'Core') {
    $pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
    if (-not $pwsh) {
        throw 'pwsh is required for stable Unicode-safe Qingbian multi-source maintenance but was not found in PATH.'
    }
    $forwardArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath, '-NoPwshReexec')
    if ($ZhihuCookieFile) { $forwardArgs += @('-ZhihuCookieFile', $ZhihuCookieFile) }
    if ($ZhihuMaxItems -gt 0) { $forwardArgs += @('-ZhihuMaxItems', [string]$ZhihuMaxItems) }
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
    # Best effort for non-interactive hosts.
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $scriptRoot '..'))
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $scriptRoot '..\..\..\..'))
$wrapper = Join-Path $scriptRoot 'qingbian-multisource-archive.ps1'
$python = Join-Path $projectRoot 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $agentRuntimePython = Join-Path $repoRoot '.runtime\agent-reach\venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $agentRuntimePython) { $python = $agentRuntimePython }
    else { $python = (Get-Command python -ErrorAction SilentlyContinue).Source }
}
$statusScript = Join-Path $scriptRoot 'qingbian_status.py'
$runStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$runPrefix = Join-Path $repoRoot "runs\$runStamp-N-qingbian-multisource-cycle"
$stdoutPath = "$runPrefix-maintain-stdout.txt"
$stderrPath = "$runPrefix-maintain-stderr.txt"
$statusPath = "$runPrefix-status.md"
$reportPath = "$runPrefix-report.md"

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content
    )
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    [System.IO.File]::WriteAllText($Path, $Content, [System.Text.UTF8Encoding]::new($false))
}

$effectiveCookie = if ($ZhihuCookieFile) { $ZhihuCookieFile } elseif ($env:QINGBIAN_ZHIHU_COOKIE_FILE) { $env:QINGBIAN_ZHIHU_COOKIE_FILE } else { '' }
$argumentList = @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-File', $wrapper,
    '-Action', 'run'
)
if ($effectiveCookie) {
    $argumentList += @('-ZhihuCookieFile', $effectiveCookie)
}
if ($ZhihuMaxItems -gt 0) {
    $argumentList += @('-ZhihuMaxItems', [string]$ZhihuMaxItems)
}
$childTimeoutMinutes = 45

$pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
if (-not $pwsh) {
    throw 'pwsh is required for stable Unicode-safe Qingbian multi-source maintenance but was not found in PATH.'
}

$startedAt = Get-Date
$exitCode = 999
$exceptionText = ''

try {
    $process = Start-Process -FilePath $pwsh.Source -ArgumentList $argumentList -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -WindowStyle Hidden -PassThru
    $completed = $process.WaitForExit($childTimeoutMinutes * 60 * 1000)
    if (-not $completed) {
        $exceptionText = "maintenance child process exceeded $childTimeoutMinutes minutes and was terminated"
        try {
            $process.Kill($true)
        }
        catch {
            $process.Kill()
        }
        $exitCode = 124
    }
    else {
        $process.WaitForExit()
        $exitCode = [int]$process.ExitCode
    }
}
catch {
    $exceptionText = $_.Exception.Message
    $exitCode = 998
}

$finishedAt = Get-Date
$statusText = ''
try {
    $statusText = & $python $statusScript --format markdown | Out-String -Width 4096
    Write-Utf8NoBom -Path $statusPath -Content $statusText
}
catch {
    $statusText = "status generation failed: $($_.Exception.Message)"
    Write-Utf8NoBom -Path $statusPath -Content ($statusText + [Environment]::NewLine)
}

$cookieStatus = if ($effectiveCookie -and (Test-Path -LiteralPath $effectiveCookie)) { 'supplied-file-exists' } elseif ($effectiveCookie) { 'supplied-file-missing' } else { 'not-supplied' }
$commandDisplay = 'pwsh ' + (($argumentList | ForEach-Object {
            if ($_ -match '\s') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
        }) -join ' ')

$reportLines = @(
    '---',
    'producer: openclaw',
    'producer_role: automation',
    "producer_evidence: Scheduled/local runner $PSCommandPath",
    'review_owner: codex-controller',
    'review_state: reviewed',
    'canonical_status: record',
    '---',
    '',
    '## Report',
    '',
    '### Actions',
    "- Ran the Qingbian multi-source maintenance wrapper: ``$wrapper``.",
    "- Scope: WeChat known-album repair, Zhihu cookie-backed archive, unified index rebuild.",
    "- Cookie status: ``$cookieStatus``. Cookie values are not written to this report.",
    "- Command: ``$commandDisplay``",
    '',
    '### Results',
    "- Exit code: ``$exitCode``. This is the scheduled-task success signal. Stored at: ``$reportPath``",
    "- stdout: ``$stdoutPath``. This preserves underlying script output for review.",
    "- stderr: ``$stderrPath``. This preserves error output for debugging.",
    "- Status snapshot: ``$statusPath``. This summarizes local counts, cross-source coverage, and Zhihu pagination completeness.",
    "- Child timeout: ``$childTimeoutMinutes`` minutes. Over-time child process trees are terminated instead of leaving the scheduled task running.",
    "- Started at: ``$($startedAt.ToString('s'))``; finished at: ``$($finishedAt.ToString('s'))``.",
    '',
    '### Evidence',
    "- Qingbian multi-source status snapshot:",
    '',
    $statusText
)

if ($exceptionText) {
    $reportLines += @(
        '',
        '### Exception',
        "- ``$exceptionText``"
    )
}

Write-Utf8NoBom -Path $reportPath -Content (($reportLines -join [Environment]::NewLine) + [Environment]::NewLine)
Write-Output "report=$reportPath; status=$statusPath; exit_code=$exitCode"
exit $exitCode
