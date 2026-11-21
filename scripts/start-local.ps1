param([ValidateRange(1024, 65535)][int]$Port = 8000)

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

function Invoke-Compose {
    & docker compose @args
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed: $args" }
}

function New-LocalSecret {
    $bytes = New-Object byte[] 48
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}

if (-not (Test-Path -LiteralPath '.env')) {
    $databasePassword = New-LocalSecret
    $settings = Get-Content -LiteralPath '.env.example' -Raw
    $settings = $settings -replace '(?m)^SECRET_KEY=.*$', ('SECRET_KEY=' + (New-LocalSecret))
    $settings = $settings -replace '(?m)^POSTGRES_PASSWORD=.*$', ('POSTGRES_PASSWORD=' + $databasePassword)
    $settings = $settings -replace '(?m)^DATABASE_URL=.*$', ('DATABASE_URL=postgres://retail:' + $databasePassword + '@localhost:5432/retail')
    $settings = $settings -replace '(?m)^HTTP_BIND=.*$', 'HTTP_BIND=127.0.0.1'
    $settings = $settings -replace '(?m)^HTTP_PORT=.*$', "HTTP_PORT=$Port"
    $settings = $settings -replace '(?m)^CSRF_TRUSTED_ORIGINS=.*$', "CSRF_TRUSTED_ORIGINS=http://localhost:$Port,http://127.0.0.1:$Port"
    $settings = $settings -replace '(?m)^SESSION_COOKIE_SECURE=.*$', 'SESSION_COOKIE_SECURE=false'
    $settings = $settings -replace '(?m)^CSRF_COOKIE_SECURE=.*$', 'CSRF_COOKIE_SECURE=false'
    $settings += "`nBOOTSTRAP_ADMIN_USERNAME=admin`nBOOTSTRAP_ADMIN_PASSWORD=$(New-LocalSecret)`n"
    [System.IO.File]::WriteAllText((Join-Path (Get-Location) '.env'), $settings)
    Write-Host 'Created local .env with random secrets. Administrator credentials are in .env.'
} else {
    Write-Host 'Using existing .env unchanged; its port, credentials and HTTP settings take precedence.'
}

Invoke-Compose config --quiet
Invoke-Compose build
Invoke-Compose up -d --wait db
Invoke-Compose run --rm migrate
Invoke-Compose run --rm --no-deps web python manage.py bootstrap_admin
Invoke-Compose up -d --wait
Invoke-Compose exec -T web python manage.py check
Invoke-Compose ps
Write-Host 'Local stack is running. Use HTTP_PORT from .env (default 8000).'
