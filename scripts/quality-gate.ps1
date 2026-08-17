[CmdletBinding()]
param(
    [ValidateSet("backend-quality", "backend-tests", "frontend", "production", "all")]
    [string] $Scope = "all"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$BackendRoot = Join-Path $RepositoryRoot "backend"
$FrontendRoot = Join-Path $RepositoryRoot "frontend/admin"

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Command,
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]] $Arguments
    )

    Write-Host "> $Command $($Arguments -join ' ')"
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Command"
    }
}

function Get-PythonCommand {
    $Candidates = if ($IsWindows) {
        @(
            (Join-Path $BackendRoot ".venv/Scripts/python.exe"),
            (Join-Path $RepositoryRoot ".venv/Scripts/python.exe")
        )
    }
    else {
        @(
            (Join-Path $BackendRoot ".venv/bin/python"),
            (Join-Path $RepositoryRoot ".venv/bin/python")
        )
    }

    foreach ($Candidate in $Candidates) {
        if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
            return $Candidate
        }
    }
    return "python"
}

function Invoke-BackendQuality {
    Push-Location $BackendRoot
    try {
        Invoke-Checked $script:PythonCommand -m ruff check app tests scripts ../scripts
        Invoke-Checked $script:PythonCommand -m mypy app
    }
    finally {
        Pop-Location
    }
}

function Invoke-BackendTests {
    if ([string]::IsNullOrWhiteSpace($env:POSTGRES_TEST_DATABASE_URL)) {
        throw "POSTGRES_TEST_DATABASE_URL is required; PostgreSQL tests must not be skipped."
    }
    if (-not $env:POSTGRES_TEST_DATABASE_URL.StartsWith("postgresql+psycopg://")) {
        throw "POSTGRES_TEST_DATABASE_URL must use the postgresql+psycopg driver."
    }

    Push-Location $BackendRoot
    try {
        Invoke-Checked $script:PythonCommand -m pytest
    }
    finally {
        Pop-Location
    }
}

function Invoke-FrontendQuality {
    Push-Location $FrontendRoot
    try {
        Invoke-Checked npm ci
        Invoke-Checked npm run check
        Invoke-Checked npm run build
    }
    finally {
        Pop-Location
    }

    $GitDirectory = Join-Path $RepositoryRoot ".git"
    if (Test-Path -LiteralPath $GitDirectory) {
        Invoke-Checked git -C $RepositoryRoot diff --exit-code -- backend/app/webui/static/admin-vue
    }
    elseif ($env:GITHUB_ACTIONS -eq "true") {
        throw "Git metadata is required in CI to verify the committed frontend artifact."
    }
    else {
        Write-Warning "No .git directory is present; the frontend artifact drift check is available only in a Git checkout."
    }
}

function Invoke-ProductionContract {
    Push-Location $RepositoryRoot
    try {
        $Heads = & $script:PythonCommand -m alembic -c backend/alembic.ini heads
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to inspect Alembic heads."
        }
        $HeadCount = @($Heads | Where-Object { $_ -match "\(head\)\s*$" }).Count
        if ($HeadCount -ne 1) {
            throw "Expected exactly one Alembic head, found $HeadCount."
        }

        $PreviousEnvironment = @{
            CREATIVE_STUDIO_IMAGE = $env:CREATIVE_STUDIO_IMAGE
            DATABASE_URL = $env:DATABASE_URL
            PLATFORM_ADMIN_EMAILS = $env:PLATFORM_ADMIN_EMAILS
            GENERATED_MEDIA_HOST_PATH = $env:GENERATED_MEDIA_HOST_PATH
            PROVIDER_SECRETS_HOST_PATH = $env:PROVIDER_SECRETS_HOST_PATH
        }
        try {
            $env:CREATIVE_STUDIO_IMAGE = "creative-studio:quality-gate"
            $env:DATABASE_URL = "postgresql+psycopg://quality_gate:quality_gate@db/quality_gate"
            $env:PLATFORM_ADMIN_EMAILS = "quality-gate@example.com"
            $env:GENERATED_MEDIA_HOST_PATH = "/tmp/creative-studio/generated-media"
            $env:PROVIDER_SECRETS_HOST_PATH = "/tmp/creative-studio/provider-secrets"
            Invoke-Checked docker compose -f deploy/compose.production.yml config --quiet
        }
        finally {
            foreach ($Name in $PreviousEnvironment.Keys) {
                $Value = $PreviousEnvironment[$Name]
                if ($null -eq $Value) {
                    Remove-Item -LiteralPath "Env:$Name" -ErrorAction SilentlyContinue
                }
                else {
                    Set-Item -LiteralPath "Env:$Name" -Value $Value
                }
            }
        }

        Invoke-Checked docker build --tag creative-studio:quality-gate .
    }
    finally {
        Pop-Location
    }
}

$script:PythonCommand = Get-PythonCommand

switch ($Scope) {
    "backend-quality" { Invoke-BackendQuality }
    "backend-tests" { Invoke-BackendTests }
    "frontend" { Invoke-FrontendQuality }
    "production" { Invoke-ProductionContract }
    "all" {
        Invoke-BackendQuality
        Invoke-BackendTests
        Invoke-FrontendQuality
        Invoke-ProductionContract
    }
}
