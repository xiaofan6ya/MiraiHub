$ErrorActionPreference = 'Stop'
$source = $env:WB_UPDATE_SOURCE
$target = $env:WB_UPDATE_TARGET
$backup = "$target.previous"
$temporary = "$target.new"
$taskLog = $env:WB_UPDATE_LOG
try {
    $process = Get-Process -Id ([int]$env:WB_UPDATE_PID) -ErrorAction SilentlyContinue
    if ($process) { $process.WaitForExit() }
    # Give the bootloader time to release the EXE. Never delete the running original first.
    Copy-Item -LiteralPath $source -Destination $temporary -Force
    $replaced = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            Copy-Item -LiteralPath $target -Destination $backup -Force
            Move-Item -LiteralPath $temporary -Destination $target -Force
            $replaced = $true
            break
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $replaced) { throw '无法替换 EXE，已保留当前版本。' }
    # New onefile must extract its own libraries, rather than reusing the old bundle.
    $env:PYINSTALLER_RESET_ENVIRONMENT = '1'
    Start-Process -FilePath $target -WorkingDirectory (Split-Path -Parent $target) -WindowStyle Hidden
    Add-Content -LiteralPath $taskLog -Value "$(Get-Date -Format o) 更新成功，旧版备份：$backup"
} catch {
    Add-Content -LiteralPath $taskLog -Value "$(Get-Date -Format o) 更新失败：$_"
    if ($replaced -and (Test-Path -LiteralPath $backup)) {
        Copy-Item -LiteralPath $backup -Destination $target -Force
    }
    $env:PYINSTALLER_RESET_ENVIRONMENT = '1'
    if (Test-Path -LiteralPath $target) { Start-Process -FilePath $target -WindowStyle Hidden }
}
