throw 'Do not use generic WeChat search for 请辩 account archive discovery. Use: powershell -File E:\agentv2\scripts\wechat-qingbian-album-archive.ps1 -Action start'

# Debug: Output search results to file for non-请辩 accounts only.
$output = & "E:\agentv2\scripts\agent-reach-wechat-search.ps1" -Query "<non-qingbian-keyword>" -Limit 3
$log = "E:\agentv2\.runtime\temp\debug.txt"
"Output lines: $($output.Count)" | Out-File -FilePath $log -Encoding UTF8
foreach ($o in $output) { $o | Out-File -FilePath $log -Append -Encoding UTF8 }
Write-Host "Done: $($output.Count) lines"
