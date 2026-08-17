[CmdletBinding()]
param(
    [string] $OpenTofuCommand = "tofu"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$InfrastructureRoot = Split-Path -Parent $PSScriptRoot | Join-Path -ChildPath "infra"
$Executable = Get-Command $OpenTofuCommand -ErrorAction SilentlyContinue
if ($null -eq $Executable) {
    throw "OpenTofu is required. Install the pinned supported release and rerun this script."
}

Push-Location $InfrastructureRoot
try {
    & $Executable.Source fmt -check -recursive
    if ($LASTEXITCODE -ne 0) {
        throw "OpenTofu formatting failed."
    }

    & $Executable.Source init -backend=false -input=false
    if ($LASTEXITCODE -ne 0) {
        throw "OpenTofu provider initialization failed."
    }

    & $Executable.Source validate
    if ($LASTEXITCODE -ne 0) {
        throw "OpenTofu validation failed."
    }
}
finally {
    Pop-Location
}
