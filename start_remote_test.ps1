param(
    [string]$BindAddress = '192.168.0.235',
    [string]$Domain = ''
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$testSecret = Read-Host 'Test password (at least 12 characters; username is tester)' -AsSecureString
$testPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($testSecret)
$previousBind = $env:RECORDER_BIND_HOST
$previousHosts = $env:RECORDER_ALLOWED_HOSTS
$previousPassword = $env:RECORDER_TEST_PASSWORD
try {
    $env:RECORDER_TEST_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($testPointer)
    if ($env:RECORDER_TEST_PASSWORD.Length -lt 12) { throw 'Use at least 12 characters.' }
    $env:RECORDER_BIND_HOST = $BindAddress
    $env:RECORDER_ALLOWED_HOSTS = $Domain
    Write-Host "Testing at http://${BindAddress}:8765 (username: tester)."
    Write-Host 'Use an HTTPS reverse proxy for the internet domain, and dummy patient data.'
    if (Test-Path -LiteralPath '.venv\Scripts\python.exe') {
        & '.venv\Scripts\python.exe' app.py
    } else {
        & py -3 app.py
    }
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($testPointer)
    $env:RECORDER_BIND_HOST = $previousBind
    $env:RECORDER_ALLOWED_HOSTS = $previousHosts
    $env:RECORDER_TEST_PASSWORD = $previousPassword
}
