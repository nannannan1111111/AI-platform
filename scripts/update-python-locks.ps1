[CmdletBinding()]
param(
    [switch] $Check
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$BackendRoot = Join-Path $RepositoryRoot "backend"
$PythonImage = "python:3.12-alpine@sha256:d09d15e60962ca365d1cd544a48773bac9d33f2fb1b00f2aa0deec78ade7dc31"
$Arguments = @(
    "run",
    "--rm",
    "--mount", "type=bind,source=$BackendRoot,target=/workspace",
    "--mount", "type=bind,source=$PSScriptRoot,target=/scripts,readonly",
    "--workdir", "/workspace",
    $PythonImage,
    "python",
    "/scripts/compile-python-locks.py"
)
if ($Check) {
    $Arguments += "--check"
}

& docker @Arguments
if ($LASTEXITCODE -ne 0) {
    throw "Python lock generation failed with exit code $LASTEXITCODE."
}
