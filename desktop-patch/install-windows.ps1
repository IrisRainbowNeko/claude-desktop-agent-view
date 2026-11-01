# Make a development copy; never take ownership of or modify a signed MSIX.
[CmdletBinding()]
param(
    [ValidateSet('install', 'status', 'restore', 'launch')][string]$Command = 'install',
    [string]$Source,
    [string]$Destination = (Join-Path $env:LOCALAPPDATA 'Agent View\Claude'),
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path $PSScriptRoot -Parent
$Node = (Get-Command node -ErrorAction Stop).Source
$Patch = Join-Path $PSScriptRoot 'native.mjs'
$Destination = [IO.Path]::GetFullPath($Destination)
$Resources = Join-Path $Destination 'resources'

if ($Command -eq 'launch') {
    $env:AGENT_VIEW_PYTHON = Get-Content -LiteralPath (Join-Path $Destination 'agent-view-python.json') -Raw | ConvertFrom-Json
    Start-Process -FilePath (Join-Path $Destination 'claude.exe')
    return
}
if ($Command -ne 'install') {
    & $Node $Patch $Command --resources $Resources --platform win32
    if ($LASTEXITCODE -ne 0) { throw "Patch $Command failed." }
    return
}
$PythonJson = (& $Python -c 'import json,sys; assert sys.version_info >= (3, 10); print(json.dumps(sys.executable))')
$PythonExe = $PythonJson | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $PythonExe)) { throw 'Python 3.10+ is required.' }
$PythonExe = $PythonExe.Trim()
if (Test-Path -LiteralPath $Destination) { throw 'Destination exists. Restore it or choose a new, empty destination.' }
if (-not $Source) {
    $Packages = @(Get-AppxPackage -Name Claude | Sort-Object { [version]$_.Version } -Descending)
    if ($Packages.Count) { $Source = Join-Path $Packages[0].InstallLocation 'app' }
    else {
        $Candidates = @(Get-ChildItem -LiteralPath (Join-Path $env:LOCALAPPDATA 'AnthropicClaude') -Directory -Filter 'app-*' -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending)
        if ($Candidates.Count) { $Source = $Candidates[0].FullName }
    }
}
if (-not $Source -or -not (Test-Path -LiteralPath (Join-Path $Source 'resources\app.asar'))) {
    throw 'Cannot find Claude. Pass -Source pointing to the directory containing claude.exe and resources.'
}
$Source = (Resolve-Path -LiteralPath $Source).Path
if ($Destination.StartsWith($Source.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Destination must be outside the source installation.' }
New-Item -ItemType Directory -Path (Split-Path $Destination -Parent) -Force | Out-Null
$Stage = Join-Path (Split-Path $Destination -Parent) ('.agent-view-stage-' + [guid]::NewGuid())
try {
    New-Item -ItemType Directory -Path $Stage | Out-Null
    Get-ChildItem -LiteralPath $Source -Force | Copy-Item -Destination $Stage -Recurse -Force
    & $Node $Patch install --resources (Join-Path $Stage 'resources') --platform win32 --server (Join-Path $Repo 'server.py')
    if ($LASTEXITCODE -ne 0) { throw 'Patch refused; source installation was not changed.' }
    # Use the actual interpreter, not a launcher alias. The patched Desktop can
    # then start Python even when launched from Explorer with a different PATH.
    [IO.File]::WriteAllText((Join-Path $Stage 'agent-view-python.json'), ($PythonExe | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))
    Move-Item -LiteralPath $Stage -Destination $Destination
} finally {
    # Only this invocation's GUID staging directory is eligible for cleanup.
    if (Test-Path -LiteralPath $Stage) {
        $ResolvedStage = [IO.Path]::GetFullPath($Stage)
        $ExpectedParent = [IO.Path]::GetFullPath((Split-Path $Destination -Parent)).TrimEnd('\') + '\'
        if (-not $ResolvedStage.StartsWith($ExpectedParent, [StringComparison]::OrdinalIgnoreCase) -or
            (Split-Path $ResolvedStage -Leaf) -notlike '.agent-view-stage-*') { throw 'Unexpected staging path; cleanup refused.' }
        Remove-Item -LiteralPath $ResolvedStage -Recurse -Force
    }
}
Write-Host "Created $Destination"
Write-Host 'Quit the official Claude app, then run this script with -Command launch.'
Write-Host 'This copy is unsigned. Use the official app for Cowork and managed installations requiring a vendor signature.'
