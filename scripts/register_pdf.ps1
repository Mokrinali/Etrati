# Offers ETRATI to Windows as a program for PDF files, for the current user only.
# The default itself can only be chosen by the user, in Windows Settings.
#   register_pdf.ps1             register and open the Settings page
#   register_pdf.ps1 -Source     register the sources (pythonw + etrati.pyw) even when an exe is built
#   register_pdf.ps1 -Remove     undo everything this script wrote
# A built program (dist-folder\ETRATI\ETRATI.exe, see ETRATI-folder.spec) is used when it is there.
param([switch]$Remove, [switch]$NoSettings, [switch]$Source)

$root = Split-Path $PSScriptRoot
$prog = 'HKCU:\Software\Classes\ETRATI.pdf'
$caps = 'HKCU:\Software\ETRATI'
$apps = 'HKCU:\Software\RegisteredApplications'
$with = 'HKCU:\Software\Classes\.pdf\OpenWithProgids'

function Update-Shell {
    $code = '[DllImport("shell32.dll")] public static extern void SHChangeNotify(int e, int f, System.IntPtr a, System.IntPtr b);'
    Add-Type -MemberDefinition $code -Name Shell -Namespace Etrati -ErrorAction SilentlyContinue
    [Etrati.Shell]::SHChangeNotify(0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)
}

if ($Remove) {
    foreach ($key in $prog, "$caps\Capabilities") {
        if (Test-Path $key) { Remove-Item $key -Recurse -Confirm:$false }
    }
    if ((Test-Path $caps) -and -not (Get-ChildItem $caps) -and -not (Get-Item $caps).Property) {
        Remove-Item $caps -Confirm:$false  # only when nothing else is kept there
    }
    foreach ($pair in @(@($apps, 'ETRATI'), @($with, 'ETRATI.pdf'))) {
        if ((Test-Path $pair[0]) -and ((Get-Item $pair[0]).Property -contains $pair[1])) {
            Remove-ItemProperty $pair[0] -Name $pair[1] -Confirm:$false
        }
    }
    Update-Shell
    'ETRATI is no longer offered for PDF files.'
    return
}

$exe = Join-Path $root 'dist-folder\ETRATI\ETRATI.exe'
if ((Test-Path -LiteralPath $exe) -and -not $Source) {
    $icon = $exe
    $open = "`"$exe`" `"%1`""
} else {
    $python = Join-Path $root '.venv\Scripts\pythonw.exe'
    $start = Join-Path $root 'etrati.pyw'
    $icon = Join-Path $root 'furceli\assets\furceli.ico'
    foreach ($file in $python, $start, $icon) { if (-not (Test-Path -LiteralPath $file)) { throw "Missing: $file" } }
    $open = "`"$python`" `"$start`" `"%1`""
}

foreach ($key in "$prog\DefaultIcon", "$prog\Application", "$prog\shell\open\command",
                 "$caps\Capabilities\FileAssociations", $apps, $with) {
    if (-not (Test-Path $key)) { New-Item $key -Force | Out-Null }
}
Set-Item $prog 'PDF (ETRATI)'
Set-Item "$prog\DefaultIcon" "`"$icon`",0"
Set-Item "$prog\shell\open\command" $open
Set-ItemProperty "$prog\Application" ApplicationName 'ETRATI'
Set-ItemProperty "$prog\Application" ApplicationIcon "$icon,0"
Set-ItemProperty "$prog\Application" ApplicationDescription 'PDF viewer and editor'
Set-ItemProperty "$prog\Application" AppUserModelID 'ETRATI'
Set-ItemProperty "$caps\Capabilities" ApplicationName 'ETRATI'
Set-ItemProperty "$caps\Capabilities" ApplicationIcon "$icon,0"
Set-ItemProperty "$caps\Capabilities" ApplicationDescription 'PDF viewer and editor'
Set-ItemProperty "$caps\Capabilities\FileAssociations" '.pdf' 'ETRATI.pdf'
Set-ItemProperty $apps 'ETRATI' 'Software\ETRATI\Capabilities'
New-ItemProperty $with -Name 'ETRATI.pdf' -PropertyType String -Value '' -Force | Out-Null
Update-Shell
"ETRATI is registered for PDF files: $open"
if (-not $NoSettings) { Start-Process 'ms-settings:defaultapps?registeredAppUser=ETRATI' }
