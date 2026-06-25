param([string]$Account="<non-qingbian-keyword>", [string]$OutFile="")

if ($Account -eq "请辩") {
    throw 'Do not use generic WeChat search for 请辩 account archive discovery. Use: powershell -File E:\agentv2\scripts\wechat-qingbian-album-archive.ps1 -Action start'
}

$search = "E:\agentv2\scripts\agent-reach-wechat-search.ps1"
$raw = & $search -Query $Account -Limit 5

$parsed = @()
$title = ""
$url = ""
foreach ($line in $raw) {
    if ($line -match "title.*?:\s*""(.+?)""") { $title = $matches[1] }
    if ($line -match "url.*?:\s*""(https.+?)""") {
        $url = $matches[1]
        if ($title -and $url) { $parsed += @{title=$title; url=$url}; $title=""; $url="" }
    }
}

$parsed | ConvertTo-Json -Depth 5 | Out-File -FilePath $OutFile -Encoding UTF8
Write-Host "Saved $($parsed.Count) results"
