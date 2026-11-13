#requires -Version 5.1
<#
Windows Storage Cleanup - Review First
- Scans large files in Downloads and old files in the current user's Temp.
- Reports C:\Games folder sizes for uninstall review.
- Does NOT delete game folders, directories, AI models, projects, BlueStacks, or Windows files.
- File deletion is optional, individually selected by ID, and requires typing DELETE.
#>

Set-StrictMode -Version 2
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

function Get-GB {
    param([long]$Bytes)
    [math]::Round(($Bytes / 1GB), 2)
}

function Test-IsProtectedFile {
    param([string]$FullName)

    $p = $FullName.ToLowerInvariant()
    $protectedFolderTokens = @(
        '\.lmstudio\', '\comfyui\', '\bluestacks_nxt\', '\lionos\',
        '\taranom-workplace\', '\bounce-blade\', '\deadman-draw\',
        '\python\', '\cuda\', '\visual studio\', '\windows\',
        '\program files\', '\program files (x86)\'
    )
    foreach ($token in $protectedFolderTokens) {
        if ($p.Contains($token)) { return $true }
    }

    # Model/checkpoint and common ML weight formats are never candidates.
    $protectedExtensions = @(
        '.safetensors', '.gguf', '.ggml', '.ckpt', '.pt', '.pth',
        '.onnx', '.engine', '.tflite', '.model'
    )
    $ext = [IO.Path]::GetExtension($p)
    if ($protectedExtensions -contains $ext) { return $true }

    return $false
}

function Get-ReviewFiles {
    param(
        [string]$Root,
        [long]$MinimumBytes,
        [datetime]$OlderThan,
        [switch]$RequireOld
    )

    $results = New-Object System.Collections.Generic.List[object]
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        return $results.ToArray()
    }

    Get-ChildItem -LiteralPath $Root -File -Recurse -Force -ErrorAction SilentlyContinue |
        ForEach-Object {
            $file = $_
            if ($file.Length -lt $MinimumBytes) { return }
            if ($RequireOld -and $file.LastWriteTime -gt $OlderThan) { return }
            if (($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { return }
            if (Test-IsProtectedFile -FullName $file.FullName) { return }

            $results.Add([pscustomobject]@{
                Type = if ($RequireOld) { 'Old Temp file' } else { 'Large Downloads file' }
                Name = $file.Name
                SizeGB = Get-GB -Bytes $file.Length
                SizeBytes = [long]$file.Length
                LastModified = $file.LastWriteTime
                Path = $file.FullName
                Root = $Root
            })
        }

    return $results.ToArray()
}

function Get-GameFolders {
    param([string]$Root)

    $results = New-Object System.Collections.Generic.List[object]
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        return $results.ToArray()
    }

    Get-ChildItem -LiteralPath $Root -Directory -Force -ErrorAction SilentlyContinue |
        ForEach-Object {
            $folder = $_
            $bytes = [long]0
            Get-ChildItem -LiteralPath $folder.FullName -File -Recurse -Force -ErrorAction SilentlyContinue |
                ForEach-Object { $bytes += [long]$_.Length }

            $results.Add([pscustomobject]@{
                Name = $folder.Name
                SizeGB = Get-GB -Bytes $bytes
                Path = $folder.FullName
                Action = 'Uninstall through Windows Settings > Apps; do not delete this folder directly.'
            })
        }

    return $results.ToArray()
}

Write-Host ''
Write-Host '=== WINDOWS STORAGE CLEANUP: REVIEW MODE ===' -ForegroundColor Cyan
Write-Host 'The scan itself does not modify or delete files.' -ForegroundColor Yellow
Write-Host ''

$cDrive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'" -ErrorAction SilentlyContinue
if ($cDrive) {
    Write-Host ('C: {0:N2} GB free / {1:N2} GB total' -f (Get-GB $cDrive.FreeSpace), (Get-GB $cDrive.Size))
}

$downloads = Join-Path $env:USERPROFILE 'Downloads'
$temp = Join-Path $env:LOCALAPPDATA 'Temp'
$cutoff = (Get-Date).AddDays(-30)

Write-Host '[1/3] Scanning Downloads files >= 250 MB...' -ForegroundColor Cyan
$downloadFiles = @(Get-ReviewFiles -Root $downloads -MinimumBytes 250MB -OlderThan ([datetime]::MinValue))

Write-Host '[2/3] Scanning Temp files >= 50 MB and older than 30 days...' -ForegroundColor Cyan
$tempFiles = @(Get-ReviewFiles -Root $temp -MinimumBytes 50MB -OlderThan $cutoff -RequireOld)

Write-Host '[3/3] Measuring immediate subfolders of C:\Games...' -ForegroundColor Cyan
$games = @(Get-GameFolders -Root 'C:\Games')

$allFiles = @($downloadFiles) + @($tempFiles)
$review = @()
$id = 0
foreach ($item in $allFiles) {
    $id++
    $review += [pscustomobject]@{
        ID = $id
        Type = $item.Type
        Name = $item.Name
        SizeGB = $item.SizeGB
        LastModified = $item.LastModified
        Path = $item.Path
        Root = $item.Root
        SizeBytes = $item.SizeBytes
    }
}

$downloadsGB = ($downloadFiles | Measure-Object -Property SizeGB -Sum).Sum
$tempGB = ($tempFiles | Measure-Object -Property SizeGB -Sum).Sum
$gamesGB = ($games | Measure-Object -Property SizeGB -Sum).Sum
if ($null -eq $downloadsGB) { $downloadsGB = 0 }
if ($null -eq $tempGB) { $tempGB = 0 }
if ($null -eq $gamesGB) { $gamesGB = 0 }

$reportDir = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Storage-Cleanup-Review'
New-Item -ItemType Directory -Path $reportDir -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$filesCsv = Join-Path $reportDir "Files-To-Review-$stamp.csv"
$gamesCsv = Join-Path $reportDir "Games-To-Uninstall-Review-$stamp.csv"
$summaryTxt = Join-Path $reportDir "Summary-$stamp.txt"

if ($review.Count -gt 0) {
    $review | Select-Object ID,Type,Name,SizeGB,LastModified,Path | Export-Csv -LiteralPath $filesCsv -NoTypeInformation -Encoding UTF8
}
if ($games.Count -gt 0) {
    $games | Export-Csv -LiteralPath $gamesCsv -NoTypeInformation -Encoding UTF8
}

Write-Host ''
Write-Host '=== RESULTS ===' -ForegroundColor Green
Write-Host ('Downloads: {0} files / {1:N2} GB (candidates for review)' -f $downloadFiles.Count, $downloadsGB)
Write-Host ('Temp:      {0} old files / {1:N2} GB (candidates for review)' -f $tempFiles.Count, $tempGB)
Write-Host ('C:\Games:  {0} folders / {1:N2} GB (uninstall review only)' -f $games.Count, $gamesGB)

if ($review.Count -gt 0) {
    Write-Host ''
    $review | Format-Table ID,Type,Name,SizeGB,LastModified -AutoSize
} else {
    Write-Host 'No files matched the Downloads/Temp scan thresholds.'
}

if ($games.Count -gt 0) {
    Write-Host ''
    Write-Host 'Game folders (NOT deleted by this script):' -ForegroundColor Yellow
    $games | Format-Table Name,SizeGB,Path -AutoSize
}

@"
Windows Storage Cleanup Review
Date: $(Get-Date)
Downloads candidate files: $($downloadFiles.Count) / $downloadsGB GB
Old Temp candidate files: $($tempFiles.Count) / $tempGB GB
C:\Games folders for uninstall review: $($games.Count) / $gamesGB GB

No files were deleted during scanning.
Game folders must be uninstalled through Windows Settings > Apps.
Protected: AI model formats, known AI/project folders, Python/CUDA, BlueStacks, and Windows system locations.

Files CSV: $filesCsv
Games CSV: $gamesCsv
"@ | Set-Content -LiteralPath $summaryTxt -Encoding UTF8

Write-Host ''
Write-Host "Reports saved to: $reportDir" -ForegroundColor Green
Write-Host "Files report: $filesCsv"
Write-Host "Games report: $gamesCsv"
Write-Host ''
Write-Host 'OPTIONAL: Delete only selected individual files listed above.' -ForegroundColor Yellow
Write-Host 'Enter file IDs separated by commas (example: 2,4,7), or press Enter to skip.'
$choice = Read-Host 'Selected file IDs'
$deletedBytes = [long]0
$deletedCount = 0

if (-not [string]::IsNullOrWhiteSpace($choice)) {
    $ids = @()
    foreach ($part in ($choice -split ',')) {
        $number = 0
        if ([int]::TryParse($part.Trim(), [ref]$number)) { $ids += $number }
    }

    $selected = @($review | Where-Object { $ids -contains $_.ID })
    if ($selected.Count -eq 0) {
        Write-Host 'No valid file IDs selected.'
    } else {
        Write-Host ''
        $selected | Format-Table ID,Type,Name,SizeGB,Path -AutoSize
        Write-Host 'Check every path carefully. This action permanently deletes selected files.' -ForegroundColor Red
        $confirm = Read-Host 'Type DELETE to confirm these exact file selections'
        if ($confirm -ceq 'DELETE') {
            foreach ($item in $selected) {
                try {
                    # Safety: require the file to remain under its original scan root.
                    $fullPath = [IO.Path]::GetFullPath($item.Path)
                    $rootPath = [IO.Path]::GetFullPath($item.Root).TrimEnd('\') + '\'
                    if (-not $fullPath.StartsWith($rootPath, [StringComparison]::OrdinalIgnoreCase)) {
                        throw 'Path is outside the scanned root; refusing deletion.'
                    }
                    if (Test-Path -LiteralPath $fullPath -PathType Leaf) {
                        $current = Get-Item -LiteralPath $fullPath -Force -ErrorAction Stop
                        if (($current.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                            throw 'Reparse point detected; refusing deletion.'
                        }
                        if (Test-IsProtectedFile -FullName $fullPath) {
                            throw 'Protected file type/location; refusing deletion.'
                        }
                        $bytes = [long]$current.Length
                        Remove-Item -LiteralPath $fullPath -Force -ErrorAction Stop
                        $deletedBytes += $bytes
                        $deletedCount++
                        Write-Host "Deleted: $fullPath" -ForegroundColor Green
                    } else {
                        Write-Host "Skipped (no longer exists): $fullPath" -ForegroundColor DarkYellow
                    }
                } catch {
                    Write-Host ("Skipped: {0} | {1}" -f $item.Path, $_.Exception.Message) -ForegroundColor DarkYellow
                }
            }
        } else {
            Write-Host 'Deletion cancelled. Confirmation phrase did not match.'
        }
    }
}

Write-Host ''
Write-Host 'Games were not deleted. Uninstall them via Settings > Apps > Installed apps.'
Write-Host ('Files deleted: {0}; estimated space freed: {1:N2} GB' -f $deletedCount, (Get-GB $deletedBytes))
$cDriveAfter = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'" -ErrorAction SilentlyContinue
if ($cDriveAfter) {
    Write-Host ('C: free space after run: {0:N2} GB' -f (Get-GB $cDriveAfter.FreeSpace))
}
Write-Host 'Finished. AI models, projects, BlueStacks, game folders, and Windows system files were not deleted.'
Read-Host 'Press Enter to close'
