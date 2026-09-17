$ErrorActionPreference = 'Stop'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
}
Push-Location $PSScriptRoot
try {
    & $compiler /nologo /target:winexe /optimize+ /out:OASInputRecorder.exe /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Core.dll Recorder.cs
    if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
    $testProcess = Start-Process -FilePath (Join-Path $PSScriptRoot 'OASInputRecorder.exe') -ArgumentList '--self-test','test-results.txt' -WindowStyle Hidden -Wait -PassThru
    if ($testProcess.ExitCode -ne 0) { throw 'Self-test failed; see test-results.txt' }
    Get-Content -LiteralPath 'test-results.txt'
} finally { Pop-Location }
