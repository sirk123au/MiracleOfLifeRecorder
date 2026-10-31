param([string]$Compiler = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $Compiler) {
    $discoveredCompiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($discoveredCompiler) { $Compiler = $discoveredCompiler.Source }
}
if (-not $Compiler) {
    foreach ($location in @("$PSScriptRoot\.tools\inno\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")) {
        if (Test-Path -LiteralPath $location) { $Compiler = $location; break }
    }
}
if (-not $Compiler -or -not (Test-Path -LiteralPath $Compiler)) {
    throw 'Install Inno Setup 6 from https://jrsoftware.org/isdl.php, or pass -Compiler with the full ISCC.exe path.'
}
& cmd.exe /c build_exe.bat --no-pause
if ($LASTEXITCODE -ne 0) { throw 'EXE build failed. Installer was not created.' }
$releaseVersion = & '.venv\Scripts\python.exe' -c 'from version import __version__; print(__version__)'
if ($LASTEXITCODE -ne 0) { throw 'Could not read release version.' }
& $Compiler "/DAppVersion=$releaseVersion" 'packaging\installer.iss'
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed.' }
Write-Host "Installer ready: dist\installer\MiracleOfLifeRecorder-Setup-$releaseVersion.exe"
