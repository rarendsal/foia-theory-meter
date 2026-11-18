<#
  FOIA Theory Meter: collect more documents, rebuild the search index, publish it to the live site.

  Run by hand:     .\update.ps1
  Collect only:    .\update.ps1 -NoPublish
  Bigger batch:    .\update.ps1 -BulkRequests 6000

  Every step is resumable. If the run stops partway (sleep, reboot, Ctrl-C), the next run picks up where it left off.
  Needs: Python 3 on PATH (python or py), and the GitHub CLI (gh) signed in with "gh auth login".
#>
param(
    [int]$BulkRequests = 3000,     # Internet Archive requests per run; roughly 1,000 new documents per 21 requests
    [string]$Collection = "ciareadingroom",
    [switch]$NoPublish
)
$ErrorActionPreference = "Stop"
$repo    = $PSScriptRoot
$scraper = Join-Path $repo "site\scraper"
$py      = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }
Start-Transcript -Path (Join-Path $repo "update.log") -Append | Out-Null

function Step([string]$label, [string[]]$cmd) {
    Write-Host "`n=== $label  ($(Get-Date -Format 'yyyy-MM-dd HH:mm'))"
    & $cmd[0] $cmd[1..($cmd.Length - 1)]
    if ($LASTEXITCODE -ne 0) { throw "$label failed (exit code $LASTEXITCODE)" }
}

try {
    Push-Location $scraper
    # New aliases you add to topics.json are searched here automatically; finished ones are skipped.
    Step "Search Internet Archive for every topic alias"  @($py, "sources.py", "ia-topics")
    Step "Search DocumentCloud for every topic alias"     @($py, "sources.py", "documentcloud")
    Step "Walk the full reading-room mirror (resumes)"    @($py, "sources.py", "ia-bulk", "--collection", $Collection, "--max-requests", "$BulkRequests")
    Step "Database status"                                @($py, "sources.py", "status")
    Step "Rebuild the search index"                       @($py, "build_index.py")
    Step "Suggest new aliases and topics"                 @($py, "phrases.py")
    Pop-Location

    # GitHub Pages serves at most 1 GB per site. Stop before a deploy would fail.
    $mb = [math]::Round(((Get-ChildItem (Join-Path $repo "site\data") -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB)
    Write-Host "`nIndex size: $mb MB"
    if ($mb -gt 900) { throw "Index is $mb MB, too close to GitHub Pages' 1 GB limit. Not publishing." }

    if (-not $NoPublish) {
        Push-Location $repo
        Step "Upload site-data.zip to the 'index' release" @("gh", "release", "upload", "index", "site-data.zip", "--clobber")
        Step "Redeploy the site"                         @("gh", "workflow", "run", "pages.yml")
        Pop-Location
    }
    Write-Host "`nDone. New alias/topic ideas: site\scraper\phrase_candidates.csv"
}
finally {
    Stop-Transcript | Out-Null
}
