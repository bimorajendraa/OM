# Usage: .\measure_resources.ps1 -Command "python -m partrisk.engines.survival.train"
param(
    [Parameter(Mandatory=$true)][string]$Command,
    [int]$IntervalMs = 500
)

function Get-ProcessTreeIds {
    param([int]$RootId, $AllProcs)
    $result = [System.Collections.Generic.List[int]]::new()
    $result.Add($RootId)
    $queue = [System.Collections.Generic.Queue[int]]::new()
    $queue.Enqueue($RootId)
    while ($queue.Count -gt 0) {
        $current = $queue.Dequeue()
        foreach ($child in ($AllProcs | Where-Object { $_.ParentProcessId -eq $current })) {
            if (-not $result.Contains([int]$child.ProcessId)) {
                $result.Add([int]$child.ProcessId)
                $queue.Enqueue([int]$child.ProcessId)
            }
        }
    }
    return $result
}

$logicalCores = (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors

# Split "exe arg1 arg2 ..." into exe + argument string, launch directly (no shell wrapper)
# so the tracked PID is the real process doing the work, not an idle cmd/powershell host.
$parts = $Command -split ' ', 2
$exe = $parts[0]
$argStr = if ($parts.Count -gt 1) { $parts[1] } else { "" }

# Resolve to the project's .venv interpreter when the command says "python" -
# Start-Process resolves PATH independently of any venv activated in the caller's
# shell, so plain "python" can otherwise silently pick the global interpreter.
if ($exe -eq "python" -or $exe -eq "python.exe") {
    $repoRoot = Split-Path -Parent $PSScriptRoot
    $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        $exe = $venvPython
    }
}

$proc = Start-Process -FilePath $exe -ArgumentList $argStr -PassThru -NoNewWindow
Start-Sleep -Milliseconds 200  # give it a moment to spawn

$peakRamMB = 0
$peakCpuPct = 0
$lastCpuTimes = @{}
$lastSampleTime = $null
$samples = @()

Write-Host "Monitoring PID $($proc.Id) (+ child processes): $Command"

while (-not $proc.HasExited) {
    try {
        $allProcs = Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId
        $treeIds = Get-ProcessTreeIds -RootId $proc.Id -AllProcs $allProcs
        $now = Get-Date

        $totalRamBytes = 0
        $totalCpuDeltaSec = 0
        foreach ($id in $treeIds) {
            try {
                $p = Get-Process -Id $id -ErrorAction Stop
                $totalRamBytes += $p.WorkingSet64
                if ($lastCpuTimes.ContainsKey($id)) {
                    $totalCpuDeltaSec += ($p.TotalProcessorTime - $lastCpuTimes[$id]).TotalSeconds
                }
                $lastCpuTimes[$id] = $p.TotalProcessorTime
            } catch { }
        }

        $ramMB = [math]::Round($totalRamBytes / 1MB, 1)
        if ($ramMB -gt $peakRamMB) { $peakRamMB = $ramMB }

        if ($lastSampleTime) {
            $wallDelta = ($now - $lastSampleTime).TotalSeconds
            if ($wallDelta -gt 0) {
                $cpuPct = [math]::Round(($totalCpuDeltaSec / $wallDelta / $logicalCores) * 100, 1)
                $samples += $cpuPct
                if ($cpuPct -gt $peakCpuPct) { $peakCpuPct = $cpuPct }
            }
        }
        $lastSampleTime = $now
    } catch {
        break
    }
    Start-Sleep -Milliseconds $IntervalMs
}

$avgCpuPct = if ($samples.Count -gt 0) { [math]::Round(($samples | Measure-Object -Average).Average, 1) } else { 0 }

Write-Host ""
Write-Host "=== Resource usage summary ==="
Write-Host "Peak RAM   : $peakRamMB MB"
Write-Host "Peak CPU   : $peakCpuPct % (of total, $logicalCores logical cores)"
Write-Host "Avg CPU    : $avgCpuPct %"
