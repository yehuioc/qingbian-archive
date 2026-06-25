[CmdletBinding(PositionalBinding = $false)]
param(
    [ValidateSet('doctor', 'run', 'build-index', 'build-corpus', 'audit', 'status', 'progress', 'publish-time', 'register-task', 'start-task', 'stop-task', 'task-info', 'unregister-task')]
    [string]$Action = 'doctor',

    [string]$ZhihuCookieFile = '',

    [switch]$SkipZhihu,

    [int]$ZhihuMaxItems = 0,

    [ValidateSet('markdown', 'json')]
    [string]$Format = 'markdown',

    [string]$TaskName = 'agentv2-qingbian-multisource-daily-archive',

    [string]$TaskTime = '15:40'
)

if ($PSVersionTable.PSEdition -ne 'Core') {
    $pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
    if (-not $pwsh) {
        throw 'pwsh is required for Unicode-safe Qingbian multi-source routing but was not found in PATH.'
    }

    $forwardArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath, '-Action', $Action)
    if ($ZhihuCookieFile) { $forwardArgs += @('-ZhihuCookieFile', $ZhihuCookieFile) }
    if ($SkipZhihu) { $forwardArgs += '-SkipZhihu' }
    if ($ZhihuMaxItems -gt 0) { $forwardArgs += @('-ZhihuMaxItems', [string]$ZhihuMaxItems) }
    $forwardArgs += @('-Format', $Format, '-TaskName', $TaskName, '-TaskTime', $TaskTime)
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

# Python fallback: 1) project venv  2) agentv2 .runtime  3) system PATH
$python = Join-Path $projectRoot 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $agentRuntimePython = Join-Path $repoRoot '.runtime\agent-reach\venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $agentRuntimePython) { $python = $agentRuntimePython }
    else { $python = (Get-Command python -ErrorAction SilentlyContinue).Source }
}

# Load config for paths
$configPath = Join-Path $projectRoot 'config.yaml'
$config = if ((Test-Path -LiteralPath $configPath) -and $python) {
    $yamlLoader = @"
import sys, yaml, json
with open(r'$configPath', 'r', encoding='utf-8') as f:
    print(json.dumps(yaml.safe_load(f), ensure_ascii=False))
"@
    (& $python -c $yamlLoader 2>$null) | ConvertFrom-Json
} else { $null }

$accountName = if ($config) { $config.account.name } else { ([string][char]0x8BF7) + [char]0x8FA9 }
$dataRoot = if ($config -and $config.paths.data_root) { [System.IO.Path]::GetFullPath((Join-Path $projectRoot $config.paths.data_root)) } else { Join-Path $projectRoot 'data' }
$wechatScript = Join-Path $scriptRoot 'wechat-qingbian-album-archive.ps1'
$zhihuScript = Join-Path $scriptRoot 'zhihu_account_archive.py'
$indexScript = Join-Path $scriptRoot 'qingbian_unified_index.py'
$corpusScript = Join-Path $scriptRoot 'qingbian_unified_corpus.py'
$statusScript = Join-Path $scriptRoot 'qingbian_status.py'
$publishTimeScript = Join-Path $scriptRoot 'qingbian_publish_time.py'
$jobScript = Join-Path $scriptRoot 'openclaw-job-qingbian-multisource.ps1'
$wechatRoot = Join-Path $dataRoot ('ingestion\10-Raw\WeChat\' + $accountName)
$zhihuRoot = Join-Path $dataRoot ('ingestion\10-Raw\Zhihu\' + $accountName)
$unifiedRoot = Join-Path $dataRoot ('ingestion\80-Maps\Qingbian\' + $accountName)
$effectiveZhihuCookieFile = if ($ZhihuCookieFile) { $ZhihuCookieFile } elseif ($env:QINGBIAN_ZHIHU_COOKIE_FILE) { $env:QINGBIAN_ZHIHU_COOKIE_FILE } elseif ($config -and $config.zhihu.cookie_file) { [System.IO.Path]::GetFullPath((Join-Path $projectRoot $config.zhihu.cookie_file)) } else { '' }

function Invoke-Zhihu {
    param([Parameter(Mandatory = $true)][string]$ZhihuAction)

    $args = @(
        $zhihuScript,
        '--action', $ZhihuAction,
        '--user-token', 'qingbian',
        '--output-root', $zhihuRoot
    )
    if ($effectiveZhihuCookieFile) { $args += @('--cookie-file', $effectiveZhihuCookieFile) }
    if ($ZhihuMaxItems -gt 0) { $args += @('--max-items', [string]$ZhihuMaxItems) }
    & $python @args | Out-Host
    return $LASTEXITCODE
}

function Invoke-UnifiedIndex {
    & $python $indexScript --repo-root $dataRoot --strict | Out-Host
    return $LASTEXITCODE
}

function Invoke-UnifiedCorpus {
    & $python $corpusScript --repo-root $dataRoot --strict | Out-Host
    return $LASTEXITCODE
}

function Invoke-Status {
    $args = @($statusScript, '--repo-root', $dataRoot, '--format', $Format)
    & $python @args | Out-Host
    return $LASTEXITCODE
}

function Invoke-PublishTime {
    $args = @($publishTimeScript, '--repo-root', $dataRoot, '--format', $Format)
    & $python @args | Out-Host
    return $LASTEXITCODE
}

function Get-TaskPayload {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        return [ordered]@{
            task_name = $TaskName
            exists = $false
            state = 'missing'
        }
    }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
    return [ordered]@{
        task_name = $TaskName
        exists = $true
        state = [string]$task.State
        task_path = [string]$task.TaskPath
        last_run_time = if ($info) { [string]$info.LastRunTime } else { '' }
        last_task_result = if ($info) { [string]$info.LastTaskResult } else { '' }
        next_run_time = if ($info) { [string]$info.NextRunTime } else { '' }
        number_of_missed_runs = if ($info) { [string]$info.NumberOfMissedRuns } else { '' }
        actions = @($task.Actions | ForEach-Object { [ordered]@{ execute = $_.Execute; arguments = $_.Arguments; working_directory = $_.WorkingDirectory } })
        triggers = @($task.Triggers | ForEach-Object { [ordered]@{ enabled = $_.Enabled; start_boundary = $_.StartBoundary } })
        settings = [ordered]@{
            execution_time_limit = [string]$task.Settings.ExecutionTimeLimit
            multiple_instances = [string]$task.Settings.MultipleInstances
        }
    }
}

function Write-TaskPayload {
    $payload = Get-TaskPayload
    if ($Format -eq 'json') {
        $payload | ConvertTo-Json -Depth 6
    }
    else {
        "# Qingbian multi-source scheduled task status"
        ""
        "- Task name: $($payload.task_name)"
        "- Exists: $($payload.exists)"
        "- State: $($payload.state)"
        if ($payload.exists) {
            "- Last run time: $($payload.last_run_time)"
            "- Last result: $($payload.last_task_result)"
            "- Next run time: $($payload.next_run_time)"
            ""
            "## Actions"
            foreach ($action in @($payload.actions)) {
                "- $($action.execute) $($action.arguments)"
            }
            ""
            "## Settings"
            "- Execution time limit: $($payload.settings.execution_time_limit)"
            "- Multiple instances: $($payload.settings.multiple_instances)"
        }
    }
}

function Register-QingbianTask {
    if (-not $effectiveZhihuCookieFile -or -not (Test-Path -LiteralPath $effectiveZhihuCookieFile)) {
        throw 'Registering the multi-source daily task requires -ZhihuCookieFile or QINGBIAN_ZHIHU_COOKIE_FILE pointing to an existing local cookie export.'
    }
    if (-not (Test-Path -LiteralPath $jobScript)) {
        throw "Missing task runner: $jobScript"
    }
    $timeOfDay = [datetime]::ParseExact($TaskTime, 'HH:mm', [System.Globalization.CultureInfo]::InvariantCulture)
    $at = [datetime]::Today.Add($timeOfDay.TimeOfDay)
    $argumentList = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', ('"' + $jobScript + '"'),
        '-ZhihuCookieFile', ('"' + $effectiveZhihuCookieFile + '"')
    ) -join ' '
    $taskAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argumentList -WorkingDirectory $repoRoot
    $trigger = New-ScheduledTaskTrigger -Daily -At $at
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 50) -MultipleInstances IgnoreNew
    $taskObject = New-ScheduledTask -Action $taskAction -Trigger $trigger -Settings $settings -Description 'Daily Qingbian WeChat + Zhihu archive maintenance and unified index rebuild.'
    Register-ScheduledTask -TaskName $TaskName -InputObject $taskObject -Force | Out-Null
    Write-TaskPayload
}

switch ($Action) {
    'doctor' {
        & pwsh -NoProfile -ExecutionPolicy Bypass -File $wechatScript -Action progress -OutputRoot $wechatRoot
        $wechatCode = $LASTEXITCODE
        if (-not $SkipZhihu) {
            $zhihuCode = Invoke-Zhihu -ZhihuAction doctor
        }
        else {
            $zhihuCode = 0
        }
        $indexCode = Invoke-UnifiedIndex
        $corpusCode = Invoke-UnifiedCorpus
        if ($wechatCode -ne 0 -or $indexCode -ne 0 -or $corpusCode -ne 0) { exit 1 }
        if ($zhihuCode -eq 2) { exit 2 }
        exit $zhihuCode
    }

    'run' {
        & pwsh -NoProfile -ExecutionPolicy Bypass -File $wechatScript -Action run -OutputRoot $wechatRoot
        $wechatCode = $LASTEXITCODE
        if ($wechatCode -notin @(0, 1)) { exit $wechatCode }
        $zhihuCode = 0
        if (-not $SkipZhihu) {
            $zhihuCode = Invoke-Zhihu -ZhihuAction run
            if ($zhihuCode -notin @(0, 2, 3)) { exit $zhihuCode }
        }
        $indexCode = Invoke-UnifiedIndex
        if ($indexCode -ne 0) { exit $indexCode }
        $corpusCode = Invoke-UnifiedCorpus
        if ($corpusCode -ne 0) { exit $corpusCode }
        if ($zhihuCode -eq 2) { exit 2 }
        if ($zhihuCode -eq 3) { exit 0 }
        exit 0
    }

    'build-index' {
        exit (Invoke-UnifiedIndex)
    }

    'build-corpus' {
        exit (Invoke-UnifiedCorpus)
    }

    'audit' {
        & pwsh -NoProfile -ExecutionPolicy Bypass -File $wechatScript -Action audit -OutputRoot $wechatRoot
        $wechatCode = $LASTEXITCODE
        $zhihuCode = Invoke-Zhihu -ZhihuAction audit
        $indexCode = Invoke-UnifiedIndex
        $corpusCode = Invoke-UnifiedCorpus
        if ($wechatCode -ne 0 -or $zhihuCode -ne 0 -or $indexCode -ne 0 -or $corpusCode -ne 0) { exit 1 }
        exit 0
    }

    'status' {
        exit (Invoke-Status)
    }

    'progress' {
        Invoke-Status | Out-Null
        Write-TaskPayload
        exit 0
    }

    'publish-time' {
        exit (Invoke-PublishTime)
    }

    'register-task' {
        Register-QingbianTask
        exit 0
    }

    'start-task' {
        Start-ScheduledTask -TaskName $TaskName
        Write-TaskPayload
        exit 0
    }

    'stop-task' {
        Stop-ScheduledTask -TaskName $TaskName
        Write-TaskPayload
        exit 0
    }

    'task-info' {
        Write-TaskPayload
        exit 0
    }

    'unregister-task' {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-TaskPayload
        exit 0
    }
}
