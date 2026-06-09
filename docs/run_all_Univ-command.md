# 在项目根目录执行
$python = "C:\Users\1000302853\Documents\Python311\python.exe"
$logDir = "logs"; if (!(Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

$files = Get-ChildItem -Path 'university_excel/mext' -Filter '*.xlsx' |
         Where-Object { $_.Name -notlike '01*' -and $_.Name -notlike '04*' } |
         Sort-Object Name

Write-Host "=== 准备导入 $($files.Count) 个文件 ===" -ForegroundColor Cyan
Write-Host "Python: $python"
$startAll = Get-Date

# 先验证 supabase 可用
& $python -c "import supabase; print('supabase OK:', supabase.__version__)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Python 环境异常，停止执行" -ForegroundColor Red
    exit 1
}

foreach ($f in $files) {
    $name = $f.BaseName
    $logFile = "$logDir/import_$name.log"
    Write-Host "`n[$(Get-Date -Format 'HH:mm:ss')] 开始导入: $($f.Name)" -ForegroundColor Yellow
    $start = Get-Date

    & $python -X utf8 run_phase3.py import-excel --excel $f.FullName *> $logFile

    $elapsed = (Get-Date) - $start
    $lastLine = (Get-Content $logFile -Tail 3 | Out-String).Trim()
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] 完成 ($([int]$elapsed.TotalSeconds)秒)" -ForegroundColor Green
    Write-Host "  $lastLine"
}

$totalElapsed = (Get-Date) - $startAll
Write-Host "`n=== 全部完成！总耗时: $([int]$totalElapsed.TotalMinutes) 分钟 ===" -ForegroundColor Cyan

& $python -X utf8 -c "from src.db.supabase_client import get_supabase; sb=get_supabase(); r=sb.table('university_units').select('id', count='exact').limit(1).execute(); print(f'>>> total rows: {r.count}')"