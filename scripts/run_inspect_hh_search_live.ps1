$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
py -3 -c "import sys, runpy; sys.path.insert(0, r'$ProjectRoot'); runpy.run_path(r'$PSScriptRoot\inspect_hh_search_live.py', run_name='__main__')"
