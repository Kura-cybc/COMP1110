# ExchangePrep - dependency modelling and timeline generation: one-click demo
#
# Runs the planner module end to end and writes the deliverables next to this
# script, so the result can be read without a server:
#
#   output\timeline_main_case.json   machine-readable plan (main case)
#   output\timeline_main_case.md     report appendix (Markdown tables)
#   output\timeline_valid_score.md   control case: same pathway, valid score
#   output\timeline_cycle_example.md negative case: circular prerequisite
#   output\plan_for_viewer.json      paste this into static\planner.html
#
# Usage (from the repository root):
#   .\run_planner_demo.ps1
#   .\run_planner_demo.ps1 -Serve      # also start the local web UI

param(
    [switch]$Serve,
    [string]$Python = ".\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Test-Path $Python)) {
    Write-Host "!! Python not found at $Python" -ForegroundColor Red
    Write-Host "   Create it first:" -ForegroundColor Yellow
    Write-Host "   python -m venv .venv"
    Write-Host "   .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
    exit 1
}

$out = Join-Path $PSScriptRoot "output"
New-Item -ItemType Directory -Force -Path $out | Out-Null

# The planner exits with 1 when a case is infeasible, which is a *result*, not a
# failure, so the exit code is reported instead of raised.
function Invoke-Planner {
    param([string]$Label, [string[]]$PlannerArgs)
    Write-Host ""
    Write-Host "=== $Label ===" -ForegroundColor Cyan
    $arguments = @("-m", "planner.cli", "plan") + $PlannerArgs
    & $Python $arguments
    Write-Host ("[exit code {0}]" -f $LASTEXITCODE) -ForegroundColor DarkGray
}

Write-Host "ExchangePrep planner demo" -ForegroundColor Green
Write-Host "python: $Python"

Invoke-Planner "1/3 main case - expired language score must be flagged" @(
    "--case", "planner\cases\hkuww_main_round_2027.json",
    "--json", "output\timeline_main_case.json",
    "--markdown", "output\timeline_main_case.md", "--quiet"
)

Invoke-Planner "2/3 control case - same pathway, score still valid" @(
    "--case", "planner\cases\hkuww_main_round_2027_feasible.json",
    "--markdown", "output\timeline_valid_score.md", "--quiet"
)

Invoke-Planner "3/3 negative case - circular prerequisite must be refused" @(
    "--case", "planner\cases\cycle_example.json",
    "--markdown", "output\timeline_cycle_example.md", "--quiet"
)

# Bridge demo: the research module's offline snapshot -> plan.
Invoke-Planner "bridge - research sample.json + anchors" @(
    "--research", "sample.json", "--anchors", "planner\sample_anchors.json",
    "--as-of", "2026-10-04", "--json", "output\timeline_from_research.json", "--quiet"
)

Copy-Item "output\timeline_main_case.json" "output\plan_for_viewer.json" -Force

Write-Host ""
Write-Host "--- tests ---" -ForegroundColor Cyan
& $Python -m unittest tests.test_planner
Write-Host ""

Write-Host "Deliverables written to $out" -ForegroundColor Green
Get-ChildItem $out | ForEach-Object { "  {0,10:N0}  {1}" -f $_.Length, $_.Name }
Write-Host ""
Write-Host "Read them:" -ForegroundColor Yellow
Write-Host "  Start-Process output\timeline_main_case.md          # report appendix"
Write-Host "  Start-Process output\timeline_main_case.json        # machine-readable plan"
Write-Host ""

if ($Serve) {
    Write-Host "Starting the local web UI (Ctrl+C to stop) ..." -ForegroundColor Green
    Write-Host "  URL: http://127.0.0.1:8000/planner" -ForegroundColor Green
    & $Python -m uvicorn app:app --host 127.0.0.1 --port 8000
} else {
    # No server was started in this run - say so explicitly, because a browser
    # opened at the URL below would otherwise show ERR_CONNECTION_REFUSED.
    Write-Host "NOTE: no server was started by this run." -ForegroundColor Yellow
    Write-Host "The web view is NOT running yet, so http://127.0.0.1:8000/planner will refuse the connection." -ForegroundColor Yellow
    Write-Host "Choose one of these:" -ForegroundColor Yellow
    Write-Host "  a) start it now:      .\run_planner_demo.ps1 -Serve"
    Write-Host "     (or)               $Python -m uvicorn app:app --host 127.0.0.1 --port 8000"
    Write-Host "     then open          http://127.0.0.1:8000/planner"
    Write-Host "  b) no server needed:  open static\planner.html in a browser and paste"
    Write-Host "                        output\plan_for_viewer.json into the page"
    Write-Host "  c) just read the files in output\ (see above) - this needs no server either"
}
