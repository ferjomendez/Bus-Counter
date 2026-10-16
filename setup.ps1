<#
  Instalador de Bus Counter para Windows (Windows PowerShell 5.1 o superior).

  Uso:
    powershell -ExecutionPolicy Bypass -File .\setup.ps1 [-CheckOnly] [-Start] [-Sim] [-Firewall]

  -CheckOnly  Solo reporta el estado, no instala nada.
  -Start      Al terminar arranca el sistema (broker + servidor).
  -Sim        Junto con -Start, arranca tambien el simulador.
  -Firewall   Crea reglas de entrada para los puertos 8000 y 8883 (pide confirmacion).

  Si se ejecuta fuera del repositorio (por ejemplo descargado con irm), instala git,
  clona o actualiza el repo en $HOME\Bus-Counter y sigue desde esa carpeta.

  Nota: este archivo es solo ASCII a proposito, para que PowerShell 5.1 lo lea bien
  sin importar la codificacion.
#>
[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$Start,
    [switch]$Sim,
    [switch]$Firewall
)

$ErrorActionPreference = 'Stop'
$RepoUrl = 'https://github.com/ferjomendez/Bus-Counter.git'
$Branch = 'main'
$CloneDir = Join-Path $HOME 'Bus-Counter'
$MinPython = [version]'3.10'
$FirewallPorts = @(8000, 8883)
$MosquittoTools = @('mosquitto', 'mosquitto_passwd', 'mosquitto_pub', 'mosquitto_sub')

$Results = New-Object System.Collections.Generic.List[object]
$AnyFailed = $false

function Add-Result([string]$Name, [string]$Version, [string]$State) {
    $Results.Add([pscustomobject]@{ Dependencia = $Name; Version = $Version; Estado = $State })
    if ($State -like 'fallo*' -or $State -like 'falta*') { $script:AnyFailed = $true }
}
function Write-Step([string]$Msg) { Write-Host "==> $Msg" -ForegroundColor Cyan }
function Write-Warn([string]$Msg) { Write-Host "AVISO: $Msg" -ForegroundColor Yellow }

function Update-SessionPath {
    # Recarga el PATH del sistema y del usuario para no tener que reabrir la terminal.
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user"
}

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal $id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-Native([string]$Exe, [string[]]$Arguments, [switch]$AnyExitCode) {
    # Ejecuta un programa y devuelve su salida (stdout + stderr) como texto, o $null si falla.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $Exe @Arguments 2>&1 | ForEach-Object { "$_" }
        if (-not $AnyExitCode -and $LASTEXITCODE -ne 0) { return $null }
        return (($out | Out-String).Trim())
    } catch {
        return $null
    } finally {
        $ErrorActionPreference = $prev
    }
}

function Test-Winget { [bool](Get-Command winget -ErrorAction SilentlyContinue) }

function Show-WingetHelp {
    Write-Warn 'No se encontro winget (Administrador de paquetes de Windows).'
    Write-Host '  Instala o actualiza "App Installer" desde Microsoft Store:'
    Write-Host '  https://apps.microsoft.com/detail/9NBLGGH4NNS1'
    Write-Host '  Luego cierra y abre PowerShell y vuelve a ejecutar este script.'
}

function Install-WingetPackage([string]$Id, [string]$Scope, [switch]$Elevated) {
    $wargs = @('install', '--id', $Id, '-e', '--silent', '--accept-package-agreements',
        '--accept-source-agreements', '--disable-interactivity')
    if ($Scope) { $wargs += @('--scope', $Scope) }
    Write-Step "Instalando $Id con winget..."
    if ($Elevated -and -not (Test-IsAdmin)) {
        Write-Host "  Windows pedira permiso de administrador solo para este paso."
        $winget = (Get-Command winget).Source
        $p = Start-Process -FilePath $winget -ArgumentList $wargs -Verb RunAs -Wait -PassThru
        $code = $p.ExitCode
    } else {
        & winget @wargs | Out-Host
        $code = $LASTEXITCODE
    }
    Update-SessionPath
    return $code
}

# ---------------------------------------------------------------- Git

function Find-Git {
    $cmd = Get-Command git -ErrorAction SilentlyContinue
    if (-not $cmd) {
        $guess = Join-Path $env:ProgramFiles 'Git\cmd\git.exe'
        if (Test-Path $guess) { $env:Path = "$env:Path;$(Split-Path $guess)"; $cmd = Get-Command git -ErrorAction SilentlyContinue }
    }
    if (-not $cmd) { return $null }
    $v = Invoke-Native $cmd.Source @('--version')
    if ($v -match '(\d+\.\d+\.\d+)') { return $Matches[1] }
    return 'desconocida'
}

function Confirm-Git {
    $v = Find-Git
    if ($v) { Add-Result 'Git' $v 'ya estaba'; return $true }
    if ($CheckOnly) { Add-Result 'Git' '-' 'falta'; return $false }
    if (-not (Test-Winget)) { Show-WingetHelp; Add-Result 'Git' '-' 'fallo (sin winget)'; return $false }
    [void](Install-WingetPackage 'Git.Git')
    $v = Find-Git
    if ($v) { Add-Result 'Git' $v 'instalada'; return $true }
    Add-Result 'Git' '-' 'fallo'
    return $false
}

# ---------------------------------------------------------------- Python

function Get-PythonInfo([string]$Exe, [string[]]$Prefix) {
    $code = "import sys;print(sys.executable);print('.'.join(map(str,sys.version_info[:3])))"
    $out = Invoke-Native $Exe ($Prefix + @('-c', $code))
    if (-not $out) { return $null }
    $lines = $out -split "`r?`n"
    if ($lines.Count -lt 2) { return $null }
    try { $ver = [version]$lines[1].Trim() } catch { return $null }
    return [pscustomobject]@{ Path = $lines[0].Trim(); Version = $ver }
}

function Find-Python {
    # Prefiere versiones probadas (3.10 a 3.13); acepta otras >= 3.10 como respaldo.
    $candidates = @(
        @{ Exe = 'py'; Prefix = @('-3.12') }, @{ Exe = 'py'; Prefix = @('-3.11') },
        @{ Exe = 'py'; Prefix = @('-3.13') }, @{ Exe = 'py'; Prefix = @('-3.10') },
        @{ Exe = 'python'; Prefix = @() }, @{ Exe = 'py'; Prefix = @('-3') }
    )
    $fallback = $null
    foreach ($c in $candidates) {
        if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
        $info = Get-PythonInfo $c.Exe $c.Prefix
        if (-not $info -or $info.Path -like '*WindowsApps*') { continue }
        if ($info.Version -lt $MinPython) { continue }
        if ($info.Version -lt [version]'3.14') { return $info }
        if (-not $fallback) { $fallback = $info }
    }
    return $fallback
}

function Confirm-Python {
    $py = Find-Python
    if ($py) { Add-Result 'Python' $py.Version.ToString() 'ya estaba'; return $py }
    if ($CheckOnly) { Add-Result 'Python' '-' "falta (>= $MinPython)"; return $null }
    if (-not (Test-Winget)) { Show-WingetHelp; Add-Result 'Python' '-' 'fallo (sin winget)'; return $null }
    [void](Install-WingetPackage 'Python.Python.3.12' -Scope 'user')
    $py = Find-Python
    if ($py) { Add-Result 'Python' $py.Version.ToString() 'instalada'; return $py }
    Add-Result 'Python' '-' 'fallo'
    return $null
}

# ---------------------------------------------------------------- Mosquitto

function Find-MosquittoDir {
    $dirs = @()
    $cmd = Get-Command mosquitto -ErrorAction SilentlyContinue
    if ($cmd) { $dirs += (Split-Path $cmd.Source) }
    $dirs += (Join-Path $env:ProgramFiles 'mosquitto')
    if (${env:ProgramFiles(x86)}) { $dirs += (Join-Path ${env:ProgramFiles(x86)} 'mosquitto') }
    foreach ($d in $dirs) {
        $ok = $true
        foreach ($t in $MosquittoTools) { if (-not (Test-Path (Join-Path $d "$t.exe"))) { $ok = $false } }
        if ($ok) { return $d }
    }
    return $null
}

function Get-MosquittoVersion([string]$Dir) {
    $out = Invoke-Native (Join-Path $Dir 'mosquitto.exe') @('-h') -AnyExitCode
    if ($out -match 'version\s+(\d+\.\d+\.\d+)') { return $Matches[1] }
    return 'desconocida'
}

function Confirm-Mosquitto {
    $dir = Find-MosquittoDir
    if ($dir) {
        $v = Get-MosquittoVersion $dir
        if ($v -ne 'desconocida' -and [version]$v -lt [version]'2.0') {
            Add-Result 'Mosquitto' $v 'version antigua (se necesita 2.0 o superior)'
        } else {
            Add-Result 'Mosquitto' $v 'ya estaba'
        }
        return $dir
    }
    if ($CheckOnly) { Add-Result 'Mosquitto' '-' 'falta'; return $null }
    if (-not (Test-Winget)) { Show-WingetHelp; Add-Result 'Mosquitto' '-' 'fallo (sin winget)'; return $null }
    [void](Install-WingetPackage 'EclipseFoundation.Mosquitto' -Elevated)
    $dir = Find-MosquittoDir
    if ($dir) { Add-Result 'Mosquitto' (Get-MosquittoVersion $dir) 'instalada'; return $dir }
    Add-Result 'Mosquitto' '-' 'fallo'
    return $null
}

# ---------------------------------------------------------------- Entorno virtual

function Confirm-Venv([string]$Root, $Py) {
    $venvPy = Join-Path $Root '.venv\Scripts\python.exe'
    $req = Join-Path $Root 'requirements.txt'
    $stamp = Join-Path $Root '.venv\requirements.sha256'
    $created = $false

    $info = $null
    if (Test-Path $venvPy) { $info = Get-PythonInfo $venvPy @() }
    if ($info -and $info.Version -lt $MinPython) {
        Write-Warn ".venv usa Python $($info.Version), se necesita >= $MinPython. Borra la carpeta .venv y vuelve a ejecutar."
        Add-Result 'Entorno .venv' $info.Version.ToString() 'fallo (Python antiguo)'
        return $null
    }
    if (-not $info) {
        if ($CheckOnly) {
            Add-Result 'Entorno .venv' '-' 'falta'; Add-Result 'Paquetes Python' '-' 'falta'; Add-Result 'PlatformIO' '-' 'falta'
            return $null
        }
        if (-not $Py) { Add-Result 'Entorno .venv' '-' 'fallo (sin Python)'; return $null }
        Write-Step "Creando entorno virtual .venv con Python $($Py.Version)..."
        & $Py.Path -m venv (Join-Path $Root '.venv') | Out-Host
        $info = Get-PythonInfo $venvPy @()
        if (-not $info) { Add-Result 'Entorno .venv' '-' 'fallo'; return $null }
        $created = $true
    }
    Add-Result 'Entorno .venv' $info.Version.ToString() $(if ($created) { 'instalada' } else { 'ya estaba' })

    # Paquetes: solo se reinstalan si cambio requirements.txt
    $hash = (Get-FileHash $req -Algorithm SHA256).Hash
    $old = if (Test-Path $stamp) { (Get-Content $stamp -Raw).Trim() } else { '' }
    if ($hash -eq $old) {
        Add-Result 'Paquetes Python' 'requirements.txt' 'ya estaba'
    } elseif ($CheckOnly) {
        Add-Result 'Paquetes Python' 'requirements.txt' 'falta (o desactualizados)'
    } else {
        Write-Step 'Instalando paquetes de Python en .venv (puede tardar unos minutos)...'
        & $venvPy -m pip install --disable-pip-version-check -q --upgrade pip | Out-Host
        & $venvPy -m pip install --disable-pip-version-check -q -r $req | Out-Host
        if ($LASTEXITCODE -eq 0) {
            Set-Content -Path $stamp -Value $hash -Encoding ASCII
            Add-Result 'Paquetes Python' 'requirements.txt' 'instalada'
        } else {
            Add-Result 'Paquetes Python' 'requirements.txt' 'fallo'
        }
    }

    $pio = Join-Path $Root '.venv\Scripts\pio.exe'
    if (Test-Path $pio) {
        $out = Invoke-Native $pio @('--version')
        $v = if ($out -match '(\d+\.\d+\.\d+)') { $Matches[1] } else { 'desconocida' }
        Add-Result 'PlatformIO' $v $(if ($hash -eq $old) { 'ya estaba' } else { 'instalada' })
    } else {
        Add-Result 'PlatformIO' '-' $(if ($CheckOnly) { 'falta' } else { 'fallo' })
    }
    return $venvPy
}

# ---------------------------------------------------------------- Firewall

function Confirm-Firewall {
    $missing = @()
    foreach ($port in $FirewallPorts) {
        $name = "Bus Counter TCP $port"
        if (-not (Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue)) { $missing += $port }
    }
    if ($missing.Count -eq 0) { Add-Result 'Firewall 8000/8883' '-' 'ya estaba'; return }
    if ($CheckOnly) { Add-Result 'Firewall 8000/8883' '-' "falta ($($missing -join ', '))"; return }

    Write-Host ''
    Write-Host "Se crearan reglas de entrada en el firewall de Windows para los puertos $($missing -join ' y ')"
    Write-Host '(solo desde la red local). El ESP32 y otros equipos las necesitan para conectarse.'
    $ans = Read-Host 'Crear reglas ahora? Necesita permiso de administrador (s/n)'
    if ($ans -notmatch '^[sSyY]') { Add-Result 'Firewall 8000/8883' '-' 'omitido por el usuario'; return }

    $cmds = foreach ($port in $missing) {
        "New-NetFirewallRule -DisplayName 'Bus Counter TCP $port' -Direction Inbound -Protocol TCP -LocalPort $port -Action Allow -RemoteAddress LocalSubnet -Profile Any | Out-Null"
    }
    $script = $cmds -join '; '
    if (Test-IsAdmin) {
        Invoke-Expression $script
    } else {
        Start-Process powershell.exe -Verb RunAs -Wait -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $script)
    }
    $still = @($missing | Where-Object { -not (Get-NetFirewallRule -DisplayName "Bus Counter TCP $_" -ErrorAction SilentlyContinue) })
    if ($still.Count -eq 0) { Add-Result 'Firewall 8000/8883' '-' 'instalada' } else { Add-Result 'Firewall 8000/8883' '-' 'fallo' }
}

# ---------------------------------------------------------------- Modo fuera del repo

function Get-ForwardArgs {
    $a = @()
    if ($CheckOnly) { $a += '-CheckOnly' }
    if ($Start) { $a += '-Start' }
    if ($Sim) { $a += '-Sim' }
    if ($Firewall) { $a += '-Firewall' }
    return $a
}

function Invoke-Bootstrap {
    Write-Step "Ejecutando fuera del repositorio. Carpeta del proyecto: $CloneDir"
    if (-not (Confirm-Git)) {
        $Results | Format-Table -AutoSize | Out-Host
        Write-Warn 'Sin git no se puede descargar el proyecto.'
        return 1
    }
    $repoSetup = Join-Path $CloneDir 'setup.ps1'
    if (Test-Path (Join-Path $CloneDir '.git')) {
        if ($CheckOnly) {
            Write-Host "Repositorio ya existe en $CloneDir (no se actualiza en modo -CheckOnly)."
        } else {
            Write-Step 'Actualizando el repositorio...'
            & git -C $CloneDir pull --ff-only origin $Branch | Out-Host
            if ($LASTEXITCODE -ne 0) { Write-Warn 'No se pudo actualizar (hay cambios locales?). Se sigue con la version actual.' }
        }
    } elseif (Test-Path $CloneDir) {
        Write-Warn "$CloneDir existe pero no es un repositorio git. Muevela o borrala y vuelve a intentar."
        return 1
    } elseif ($CheckOnly) {
        $Results | Format-Table -AutoSize | Out-Host
        Write-Host "El proyecto no esta descargado. Sin -CheckOnly se clonara en $CloneDir."
        return 0
    } else {
        Write-Step "Clonando $RepoUrl..."
        & git clone --branch $Branch $RepoUrl $CloneDir | Out-Host
        if ($LASTEXITCODE -ne 0) { Write-Warn 'No se pudo clonar el repositorio.'; return 1 }
    }
    # Se ejecuta al final, fuera de las funciones, para que la salida vaya directo a la consola
    $script:NextExe = 'powershell.exe'
    $script:NextArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $repoSetup) + (Get-ForwardArgs)
    return 0
}

# ---------------------------------------------------------------- Flujo principal

function Invoke-Main {
    $root = $PSScriptRoot
    if (-not $root -or -not (Test-Path (Join-Path $root 'manage.py'))) {
        return (Invoke-Bootstrap)
    }
    Set-Location $root
    Write-Step "Proyecto en $root"
    if ($CheckOnly) { Write-Host '(modo -CheckOnly: no se instala nada)' }
    if (-not (Test-Winget)) { Show-WingetHelp }

    [void](Confirm-Git)
    $py = Confirm-Python
    $mosqDir = Confirm-Mosquitto
    $venvPy = Confirm-Venv $root $py
    if ($Firewall) { Confirm-Firewall }

    # Guarda la ruta de Mosquitto para manage.py (normalmente no queda en el PATH)
    if ($mosqDir) {
        if (-not $CheckOnly) {
            $cfgPath = Join-Path $root 'local_config.json'
            $cfg = [ordered]@{ mosquitto_dir = $mosqDir }
            $utf8 = New-Object System.Text.UTF8Encoding $false
            [IO.File]::WriteAllText($cfgPath, ($cfg | ConvertTo-Json), $utf8)
        }
        if (($env:Path -split ';') -notcontains $mosqDir) { $env:Path = "$env:Path;$mosqDir" }
    }

    Write-Host ''
    $Results | Format-Table -AutoSize | Out-Host

    if ($script:AnyFailed) {
        if ($CheckOnly) { Write-Warn 'Faltan dependencias. Ejecuta el script sin -CheckOnly para instalarlas.' }
        else { Write-Warn 'Hubo problemas. Revisa los mensajes de arriba.' }
        return 1
    }
    if (-not $Firewall -and -not $CheckOnly) {
        Write-Host 'Para que el ESP32 y otros equipos se conecten, ejecuta una vez con -Firewall.'
    }
    if ($Start) {
        if ($CheckOnly) { Write-Host 'Modo -CheckOnly: no se arranca el sistema.'; return 0 }
        $script:NextExe = $venvPy
        $script:NextArgs = @((Join-Path $root 'manage.py'), 'start')
        if ($Sim) { $script:NextArgs += '--sim' }
        return 0
    }
    Write-Host 'Todo listo. Para encender: .\.venv\Scripts\python.exe manage.py start'
    return 0
}

$NextExe = $null
$NextArgs = @()
$code = Invoke-Main
if ($NextExe) {
    & $NextExe @NextArgs
    $code = $LASTEXITCODE
}
# Solo se usa exit cuando corre como archivo; con irm cerraria la consola del usuario.
if ($PSCommandPath) { exit $code }
