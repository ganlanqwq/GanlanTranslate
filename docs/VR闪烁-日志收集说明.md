# 甘岚翻译 VR 闪烁问题：日志收集说明

> 这份说明是给**这台电脑上的 agent**看的。请按步骤收集日志，放到桌面，交给用户转回给开发者。

## 背景

这台电脑装了「甘岚翻译」安装版。它包含两个程序（正式版 1.0 起改了英文名和安装目录；之前的 2.x 是测试版）：

| | 正式版（1.0 起） | 测试版（2.x） |
|---|---|---|
| 默认安装目录 | `C:\Program Files (x86)\Ganlan Translate` | `C:\Program Files (x86)\GanLan` |
| 主程序（托盘图标，网页控制面板在 `http://127.0.0.1:5001`） | `GanlanTranslate.exe` | `GanLan.exe` |
| SteamVR 附属程序 | `GanlanTranslateVR.exe` | `GanLanVR.exe` |

**现象：** 在 SteamVR 里只要手一动，下面两个画面就会一直闪：

- SteamVR 菜单里的「甘岚翻译」页面
- 手柄上的手腕字幕卡片

开发者那边拿不到这台电脑的日志，需要你帮忙收集。

**现在是验证修复。** 2.4.7 修好了手腕卡片（共享内存块用满的问题），2.4.8 把画面改成用显卡贴图传给 SteamVR（但真机上窗口整个看不见），2.4.9 修了看不见的问题（之后的测试版和正式版 1.0 都包含）。收集前先确认装的是正式版，或测试版 2.4.9 及以后：

- 看注册表 `HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\GanlanTranslate`（测试版是 `...\Uninstall\GanLan`）的 `DisplayVersion`。
- 如果还是旧版本，先停下来，请用户自己运行开发者给的最新安装包覆盖安装，并重启 SteamVR。你不要替用户安装。旧版本的日志开发者已经有了，不用再收。
- 安装目录下 `vr_overlay\overlay.log` 开头会写「贴图方式：……」，收集时会一起带上，不用单独处理。

## 规则（请遵守）

- **只读。**
  - 不改任何设置，不卸载或重装软件，不改 SteamVR 配置。
  - **不要结束主程序（`GanlanTranslate.exe` / 旧版 `GanLan.exe`）**：主程序的运行日志只存在内存里，关了就没了。
- **只放到桌面。** 不要上传、发送或分享到任何地方，用户会自己转交。
- **敏感信息。**
  - 脚本会自动把字段名里带 key、token、secret、password 的值替换成 `<hidden>`。
  - 主程序日志里可能有用户说过的话（语音识别和翻译记录）。收集完请告诉用户这一点，由用户决定要不要发出去。

## 步骤

### 1. 先请用户复现一次（如果用户在场）

请用户确认主程序在运行（右下角托盘有图标），然后：

1. 戴上头显、打开 SteamVR，确保附属程序在运行（SteamVR 菜单里能看到「甘岚翻译」页）。
2. 打开「甘岚翻译」页，用手柄激光在页面上来回晃动 10 秒左右。
3. 抬起手腕看一下手腕卡片，同样晃动手柄几秒。
4. 再正常用几分钟（开着菜单页、抬腕、转手）。旧版本大约两分钟后开始闪。
5. 摘下头显后，**不要关闭甘岚翻译和 SteamVR**，告诉你大概是几点几分复现的，还闪不闪。

用户不在场或没法复现，就跳过这一步，直接收集。

**一定要先复现、再运行第 2 步的脚本。** 脚本只抓运行那一刻的日志。用户在收集之后又复现了一次，就要**重新运行脚本**，生成新的 `GanLan-logs-<时间>` 文件夹。不要把旧文件夹加个备注重新打包，旧文件夹里没有那次复现的日志。

### 2. 运行收集脚本

把下面的脚本保存为 `collect_ganlan_logs.ps1`，保存成 UTF-8 文件。脚本内容全是英文字符，编码不会出问题。然后运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\collect_ganlan_logs.ps1
```

脚本会在桌面生成 `GanLan-logs-<时间>` 文件夹和同名 `.zip`，内容如下：

| 位置 | 内容 |
|---|---|
| `vr_overlay\` | 附属程序的 `overlay.log`、`settings.json`、`ganlan_vr.vrmanifest` |
| `main_app\` | 主程序状态、VR 连接状态、配置（敏感字段已打码），以及最近 2000 条运行日志（`logs.json` 和按时间排好的 `logs.txt`） |
| `steamvr\` | SteamVR 最近 7 天的日志（`vr*.txt`、`driver_*.txt`），以及 `appconfig.json`、`steamvr.vrsettings`、SteamVR 版本信息（`appmanifest_250820.acf`） |
| `system.txt` | 系统版本、显卡和驱动、相关进程（甘岚翻译、SteamVR、串流软件、VRChat） |
| `install_dir_listing.txt` | 安装目录里的文件列表和修改时间 |
| `summary.txt` | 收集过程摘要，哪些找到了、哪些没找到 |

```powershell
# Collect GanLan Translate + SteamVR diagnostics into a folder (and zip) on the Desktop.
# Read-only: copies files and queries the local app API. Changes no settings and stops nothing.
param([string]$OutRoot = [Environment]::GetFolderPath('Desktop'))

$ErrorActionPreference = 'Continue'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$out = Join-Path $OutRoot "GanLan-logs-$stamp"
New-Item -ItemType Directory -Force -Path $out | Out-Null
$summary = New-Object System.Collections.Generic.List[string]
function Note([string]$s) { $summary.Add($s); Write-Host $s }

# Hide values of any JSON field whose name contains key/token/secret/password
function Redact([string]$json) {
    return [regex]::Replace($json, '"([^"]*(key|token|secret|password)[^"]*)"\s*:\s*"[^"]*"',
        '"$1": "<hidden>"', 'IgnoreCase')
}

# ---------- 1. Install dir and version ----------
$reg = $null
# 正式版的注册表键是 GanlanTranslate，测试版是 GanLan
foreach ($p in @('HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\GanlanTranslate',
                 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\GanlanTranslate',
                 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\GanLan',
                 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\GanLan')) {
    if (Test-Path $p) { $reg = Get-ItemProperty $p; break }
}
$inst = if ($reg -and $reg.InstallLocation) { $reg.InstallLocation }
        elseif (Test-Path 'C:\Program Files (x86)\Ganlan Translate') { 'C:\Program Files (x86)\Ganlan Translate' }
        else { 'C:\Program Files (x86)\GanLan' }
Note "Install dir: $inst"
Note "Installed version: $(if ($reg) { $reg.DisplayVersion } else { 'not found in registry' })"
if (Test-Path $inst) {
    Get-ChildItem $inst | Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize |
        Out-String -Width 200 | Set-Content -Encoding UTF8 (Join-Path $out 'install_dir_listing.txt')
}

# ---------- 2. VR companion files (overlay.log, settings.json, manifest) ----------
$vrDir = Join-Path $inst 'vr_overlay'
if (Test-Path $vrDir) {
    Copy-Item $vrDir (Join-Path $out 'vr_overlay') -Recurse -Force
    Note "Copied $vrDir"
} else {
    Note "MISSING: $vrDir (the VR companion has never run from this install dir)"
}

# ---------- 3. Main app API (only while the main app is running; logs live in memory) ----------
$api = Join-Path $out 'main_app'
New-Item -ItemType Directory -Force -Path $api | Out-Null
$endpoints = [ordered]@{
    'status.json'    = '/api/status'
    'vr_status.json' = '/api/vr/status'
    'config.json'    = '/api/config'
    'logs.json'      = '/api/logs?limit=2000'
}
$mainUp = $false
foreach ($name in $endpoints.Keys) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 ("http://127.0.0.1:5001" + $endpoints[$name])
        Set-Content -Encoding UTF8 -Path (Join-Path $api $name) -Value (Redact $r.Content)
        $mainUp = $true
    } catch {
        Note "API $($endpoints[$name]) failed: $($_.Exception.Message)"
    }
}
if ($mainUp) {
    Note "Main app API reachable, saved to main_app\"
    try {
        $logs = (Get-Content -Raw -Encoding UTF8 (Join-Path $api 'logs.json') | ConvertFrom-Json).logs
        [array]::Reverse($logs)   # API returns newest first
        $epoch = Get-Date '1970-01-01T00:00:00Z'
        $lines = foreach ($l in $logs) {
            $t = $epoch.AddSeconds([double]$l.t).ToLocalTime().ToString('HH:mm:ss.ff')
            "$t $($l.line)"
        }
        Set-Content -Encoding UTF8 -Path (Join-Path $api 'logs.txt') -Value (Redact ($lines -join "`r`n"))
    } catch { Note "Could not convert logs.json to text: $($_.Exception.Message)" }
} else {
    Note "Main app not reachable on 127.0.0.1:5001 (not running?)"
}

# ---------- 4. SteamVR logs and config ----------
$steam = $null
try { $steam = (Get-ItemProperty 'HKCU:\Software\Valve\Steam').SteamPath -replace '/', '\' } catch {}
if ($steam -and (Test-Path $steam)) {
    Note "Steam dir: $steam"
    $svr = Join-Path $out 'steamvr'
    New-Item -ItemType Directory -Force -Path $svr | Out-Null
    $since = (Get-Date).AddDays(-7)
    $logFiles = Get-ChildItem (Join-Path $steam 'logs') -Include 'vr*.txt', 'driver_*.txt' -Recurse -Depth 0 -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -gt $since }
    foreach ($f in $logFiles) { Copy-Item $f.FullName $svr -Force }
    Note "Copied $(@($logFiles).Count) SteamVR log files (vr*.txt, driver_*.txt, last 7 days)"
    foreach ($c in @('appconfig.json', 'steamvr.vrsettings')) {
        $src = Join-Path (Join-Path $steam 'config') $c
        if (Test-Path $src) { Copy-Item $src $svr -Force }
    }
    # SteamVR build (app 250820) from any Steam library
    $libs = @("$steam")
    $vdf = Join-Path $steam 'steamapps\libraryfolders.vdf'
    if (Test-Path $vdf) {
        foreach ($m in [regex]::Matches((Get-Content -Raw $vdf), '"path"\s+"([^"]+)"')) {
            $libs += ($m.Groups[1].Value -replace '\\\\', '\')
        }
    }
    foreach ($lib in ($libs | Select-Object -Unique)) {
        $acf = Join-Path $lib 'steamapps\appmanifest_250820.acf'
        if (Test-Path $acf) { Copy-Item $acf $svr -Force; Note "SteamVR manifest: $acf"; break }
    }
} else {
    Note "Steam not found in registry (HKCU\Software\Valve\Steam)"
}

# ---------- 5. System info and related processes ----------
$sys = New-Object System.Collections.Generic.List[string]
$os = Get-CimInstance Win32_OperatingSystem
$sys.Add("OS: $($os.Caption) $($os.Version)")
foreach ($g in Get-CimInstance Win32_VideoController) { $sys.Add("GPU: $($g.Name)  driver $($g.DriverVersion)") }
$sys.Add('')
$sys.Add('Related processes:')
$pattern = 'GanLan|vrserver|vrcompositor|vrmonitor|vrdashboard|vrwebhelper|PICO|Streaming|VirtualDesktop|ALVR|OVRServer|VRChat'
Get-Process | Where-Object { $_.ProcessName -match $pattern } | ForEach-Object {
    $path = try { $_.Path } catch { '' }
    $start = try { $_.StartTime } catch { '' }
    $sys.Add(("{0,-28} pid {1,-7} start {2}  {3}" -f $_.ProcessName, $_.Id, $start, $path))
}
Set-Content -Encoding UTF8 -Path (Join-Path $out 'system.txt') -Value ($sys -join "`r`n")

# ---------- 6. Summary + zip ----------
Note "Collected at: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Set-Content -Encoding UTF8 -Path (Join-Path $out 'summary.txt') -Value ($summary -join "`r`n")
$zip = "$out.zip"
Compress-Archive -Path (Join-Path $out '*') -DestinationPath $zip -Force
Write-Host ""
Write-Host "Done. Folder: $out"
Write-Host "Zip:    $zip"
```

### 3. 收尾

在 `GanLan-logs-<时间>` 文件夹里新建 `notes.txt`，写上：

- 安装的版本（`summary.txt` 里的 `Installed version`）。
- 第 1 步复现的大概时间，还闪不闪、附属程序有没有崩；没复现就写「未复现」。复现时间必须早于 `summary.txt` 里的 `Collected at`，否则回到第 2 步重新收集。
- 头显型号和串流方式，可以问用户，比如 PICO 4 + PICO Connect。
- 任何异常情况，比如脚本报错、某些文件找不到。

写完后重新打包，覆盖脚本生成的那个 zip（把 `<时间>` 换成实际的文件夹名）：

```powershell
$d = Join-Path ([Environment]::GetFolderPath('Desktop')) 'GanLan-logs-<时间>'
Compress-Archive -Path (Join-Path $d '*') -DestinationPath "$d.zip" -Force
```

最后告诉用户 zip 的完整路径，并提醒用户：主程序日志里可能有对话内容，请确认没问题后再发出去。
