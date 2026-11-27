<#
.SYNOPSIS
    Windows task runner for multi-agent-customer-ops (no GNU Make required).

.EXAMPLE
    .\tasks.ps1 check
    powershell -ExecutionPolicy Bypass -File .\tasks.ps1 check
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('help', 'lock', 'install', 'migrate', 'run', 'lint', 'format', 'typecheck',
        'test', 'test-unit', 'test-live', 'check')]
    [string]$Task = 'help'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-Uv {
    # A simple (non-advanced) function so flags such as -m and --locked pass through via $args.
    & uv @args
    if ($LASTEXITCODE -ne 0) {
        throw "uv $($args -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Invoke-WithTestEnv {
    param([scriptblock]$Body)
    $previous = $env:APP_ENV
    $env:APP_ENV = 'test'
    try { & $Body } finally { $env:APP_ENV = $previous }
}

function Show-Help {
    @'
Usage: .\tasks.ps1 <task>

  lock        Resolve dependencies and write uv.lock (public PyPI)
  install     Install the locked environment (uv sync --locked)
  migrate     alembic upgrade head against DATABASE_URL
  run         Start the API at http://127.0.0.1:8000 (reload enabled)
  lint        ruff check + ruff format --check
  format      ruff format + ruff check --fix
  typecheck   mypy (strict) on src/
  test        pytest -m "not live_llm" (unit + integration via TEST_DATABASE_URL)
  test-unit   Unit tests only (no network, database, or LLM)
  test-live   Live Gemini smoke tests (-m live_llm); needs GEMINI_API_KEY
  check       lint + typecheck + test
'@ | Write-Host
}

Push-Location -LiteralPath $PSScriptRoot
try {
    switch ($Task) {
        'help' { Show-Help }
        'lock' { Invoke-Uv lock }
        'install' { Invoke-Uv sync --locked }
        'migrate' { Invoke-Uv run --locked alembic upgrade head }
        'run' { Invoke-Uv run --locked uvicorn support_ops.api.main:app --reload --host 127.0.0.1 --port 8000 }
        'lint' {
            Invoke-Uv run --locked ruff check .
            Invoke-Uv run --locked ruff format --check .
        }
        'format' {
            Invoke-Uv run --locked ruff format .
            Invoke-Uv run --locked ruff check --fix .
        }
        'typecheck' { Invoke-Uv run --locked mypy }
        'test' { Invoke-WithTestEnv { Invoke-Uv run --locked pytest -m 'not live_llm' } }
        'test-unit' { Invoke-Uv run --locked pytest tests/unit -m 'not live_llm' }
        'test-live' { Invoke-Uv run --locked pytest -m live_llm -rA }
        'check' {
            Invoke-Uv run --locked ruff check .
            Invoke-Uv run --locked ruff format --check .
            Invoke-Uv run --locked mypy
            Invoke-WithTestEnv { Invoke-Uv run --locked pytest -m 'not live_llm' }
        }
    }
}
finally {
    Pop-Location
}
