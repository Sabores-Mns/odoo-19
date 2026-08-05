<#
.SYNOPSIS
    Ejecuta la migración completa de Profit Plus a Odoo 19.

.DESCRIPTION
    Un solo comando: levanta la infraestructura, restaura el respaldo de Profit,
    crea la base de Odoo 19, corre el ETL completo y verifica el resultado.

    Es re-ejecutable de principio a fin: cada fase detecta si ya se hizo y la
    salta. Si algo falla, se detiene con un mensaje que dice qué pasó y cómo
    seguir; nunca continúa en silencio sobre un error.

.PARAMETER Steps
    Sólo estos pasos del loader, en vez de la migración entera. Útil para
    reintentar una parte:
        setup masters invoices payments fix_payments
        reconcile crossapply trueup stock verify

.PARAMETER SkipExtract
    No re-extrae desde SQL Server ni re-transforma; reutiliza los CSV que ya
    estén en export/. Ahorra ~10 min al reintentar sólo la carga.

.PARAMETER Force
    Rehace lo que normalmente se saltaría: vuelve a restaurar el .bak aunque
    MULTI_A ya exista.

.PARAMETER SkipTests
    No corre la suite de verificación al final.

.EXAMPLE
    .\migration\run-migration.ps1
    Migración completa desde cero.

.EXAMPLE
    .\migration\run-migration.ps1 -SkipExtract -Steps reconcile,crossapply,trueup,verify
    Reintenta sólo la conciliación sobre los datos ya cargados.
#>
[CmdletBinding()]
param(
    [string[]]$Steps,
    [switch]$SkipExtract,
    [switch]$Force,
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'

# --------------------------------------------------------------- constantes
$RepoDir       = Split-Path -Parent $PSScriptRoot
$MigrationDir  = $PSScriptRoot
$MigrationYml  = Join-Path $MigrationDir 'docker-compose.migration.yml'

$OdooContainer   = 'odoo-19-mns'
$RunnerContainer = 'migration_runner'
$MssqlContainer  = 'migration_mssql'
$OdooDb          = 'profit_migrado19'
$OdooPort        = 10020
$PgUser          = 'odoo'
$PgPassword      = 'odoo19@2025'
# Módulos necesarios en la base destino. l10n_ve trae el plan de cuentas
# venezolano; sin él las cuentas por cobrar no cuadran con Profit.
$OdooModules     = 'base,contacts,product,sale_management,stock,account,l10n_ve'

$script:PhaseNum = 0

# ----------------------------------------------------------------- utilidad
function Write-Phase {
    param([string]$Text)
    $script:PhaseNum++
    Write-Host ''
    Write-Host ('=' * 72) -ForegroundColor Cyan
    Write-Host ("  FASE $script:PhaseNum - $Text") -ForegroundColor Cyan
    Write-Host ('=' * 72) -ForegroundColor Cyan
}

function Write-Ok   { param([string]$m) Write-Host "  [OK] $m"   -ForegroundColor Green }
function Write-Info { param([string]$m) Write-Host "  $m"        -ForegroundColor Gray }
function Write-Warn { param([string]$m) Write-Host "  [AVISO] $m" -ForegroundColor Yellow }

function Stop-WithError {
    param([string]$Message, [string]$Hint)
    Write-Host ''
    Write-Host "ERROR: $Message" -ForegroundColor Red
    if ($Hint) { Write-Host "       $Hint" -ForegroundColor Yellow }
    Write-Host ''
    exit 1
}

function Invoke-Runner {
    <#  Ejecuta un comando dentro del contenedor migrador y propaga el fallo.
        Los scripts del ETL imprimen su propio progreso, así que la salida va
        directa a la consola en vez de capturarse. #>
    param(
        [Parameter(Mandatory)][string[]]$Command,
        [Parameter(Mandatory)][string]$What
    )
    Write-Info "-> $What"
    & docker exec $RunnerContainer @Command
    if ($LASTEXITCODE -ne 0) {
        Stop-WithError "$What falló (código $LASTEXITCODE)." `
                       "Revisa la salida de arriba. Puedes reintentar sólo esta parte; todo es idempotente."
    }
}

# ============================================================ FASE 1: preflight
Write-Phase 'Comprobaciones previas'

try {
    $dockerVersion = & docker version --format '{{.Server.Version}}' 2>&1
    if ($LASTEXITCODE -ne 0) { throw $dockerVersion }
    Write-Ok "Docker $dockerVersion"
} catch {
    Stop-WithError 'Docker no responde.' 'Abre Docker Desktop, espera a que arranque del todo y vuelve a intentarlo.'
}

if (-not (Test-Path $MigrationYml)) {
    Stop-WithError "No encuentro $MigrationYml" 'Ejecuta el script desde el repositorio odoo-19.'
}

# El .bak: sin él no hay nada que migrar.
$dbDir = Join-Path $MigrationDir 'db'
$baks  = @(Get-ChildItem -Path $dbDir -Filter '*.bak' -File -ErrorAction SilentlyContinue)

if ($baks.Count -eq 0) {
    Stop-WithError "No hay ningún archivo .bak en $dbDir" `
                   "Copia ahí el respaldo de Profit. Instrucciones en $dbDir\README.md"
}
if ($baks.Count -gt 1) {
    Stop-WithError "Hay $($baks.Count) archivos .bak en $dbDir : $($baks.Name -join ', ')" `
                   'Deja sólo el que quieras migrar.'
}
Write-Ok ("Respaldo: {0} ({1:N0} MB)" -f $baks[0].Name, ($baks[0].Length / 1MB))

# ======================================================== FASE 2: infraestructura
Write-Phase 'Infraestructura'

Write-Info '-> stack Odoo 19 (crea la red que usará el migrador)'
Push-Location $RepoDir
try {
    & docker compose up -d
    if ($LASTEXITCODE -ne 0) { Stop-WithError 'No se pudo levantar el stack de Odoo 19.' }
} finally { Pop-Location }
Write-Ok 'odoo-19-mns y db-odoo-19-mns arriba'

Write-Info '-> stack de migración (SQL Server + runner)'
Push-Location $RepoDir
try {
    & docker compose -f $MigrationYml up -d
    if ($LASTEXITCODE -ne 0) {
        Stop-WithError 'No se pudo levantar el stack de migración.' `
                       'Si dice "network odoo-19-mns_default not found", el stack raíz no arrancó bien.'
    }
} finally { Pop-Location }
Write-Ok "$MssqlContainer y $RunnerContainer arriba"

# Odoo tarda en responder tras arrancar; sin esto la creación de la BD falla.
Write-Info "-> esperando a que Odoo responda en :$OdooPort"
$ready = $false
foreach ($i in 1..60) {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:$OdooPort/web/database/selector" `
                               -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch {
        if ($i % 10 -eq 0) { Write-Info "   ...($i/60)" }
        Start-Sleep -Seconds 5
    }
}
if (-not $ready) {
    Stop-WithError "Odoo no respondió en http://localhost:$OdooPort tras 5 minutos." `
                   "Revisa: docker logs $OdooContainer --tail 50"
}
Write-Ok "Odoo responde en http://localhost:$OdooPort"

# ============================================================ FASE 3: dependencias
Write-Phase 'Dependencias de Python'
Invoke-Runner -Command @('pip','install','--quiet','--no-input','-r','/migration/requirements.txt') `
              -What 'pip install (python-tds, beautifulsoup4)'
Write-Ok 'dependencias instaladas'

# =================================================== FASE 4: restaurar Profit
Write-Phase 'Restaurar el respaldo de Profit en SQL Server'
$restoreArgs = @('python','/migration/etl/restore_mssql.py')
if ($Force) { $restoreArgs += '--force' }
Invoke-Runner -Command $restoreArgs -What 'RESTORE DATABASE MULTI_A'
Write-Ok 'base MULTI_A lista'

# ================================================== FASE 5: crear base de Odoo
Write-Phase 'Base de datos de Odoo 19'

# ¿Existe ya? Se consulta a Postgres directamente: más fiable que la API web.
$existing = & docker exec -e PGPASSWORD=$PgPassword db-odoo-19-mns `
    psql -U $PgUser -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$OdooDb'" 2>$null

if ($existing -match '1') {
    Write-Ok "La base $OdooDb ya existe; no se recrea"
} else {
    Write-Info "-> creando $OdooDb con los módulos: $OdooModules"
    Write-Info '   (tarda varios minutos la primera vez)'
    # odoo.conf no trae db_user/db_password —los inyecta entrypoint.sh desde el
    # entorno—, así que en docker exec hay que pasarlos explícitamente.
    & docker exec $OdooContainer odoo `
        -c /etc/odoo/odoo.conf -r $PgUser -w $PgPassword `
        -d $OdooDb -i $OdooModules `
        --load-language=es_419 --stop-after-init --no-http --log-level=warn
    if ($LASTEXITCODE -ne 0) {
        Stop-WithError "No se pudo crear la base $OdooDb (código $LASTEXITCODE)." `
                       "Revisa: docker logs $OdooContainer --tail 50"
    }
    Write-Ok "$OdooDb creada"
}

# ====================================================== FASE 6: ETL
Write-Phase 'ETL: extraer, transformar y cargar'

if ($SkipExtract) {
    Write-Warn 'se omiten extract y transform (-SkipExtract); se reutilizan los CSV de export/'
} else {
    Invoke-Runner -Command @('python','-u','/migration/etl/extract.py') `
                  -What 'extract: MULTI_A -> export/raw/*.csv'
    Invoke-Runner -Command @('python','-u','/migration/etl/transform.py') `
                  -What 'transform: raw -> export/odoo_csv/ + planes JSON'
    Invoke-Runner -Command @('python','-u','/migration/etl/transform19.py') `
                  -What 'transform19: adaptación al modelo de Odoo 19'
}

$loadArgs = @('python','-u','/migration/etl/load19.py')
if ($Steps) {
    $loadArgs += $Steps
    Write-Info "pasos solicitados: $($Steps -join ' ')"
} else {
    Write-Info 'migración completa (~2-3 h)'
}
Invoke-Runner -Command $loadArgs -What 'load19: carga en Odoo 19'
Write-Ok 'ETL terminado'

# ============================================== FASE 7: verificación
$testsFailed = $false
if ($SkipTests) {
    Write-Phase 'Verificación'
    Write-Warn 'omitida (-SkipTests)'
} else {
    Write-Phase 'Verificación contra los datos de Profit'
    Write-Info '-> suite de pruebas T1-T12'
    & docker exec $RunnerContainer python -u /migration/etl/tests_migracion.py 19
    if ($LASTEXITCODE -ne 0) {
        # Un test en rojo no invalida la migración: se reporta y se sigue para
        # que el resumen final se imprima igual.
        $testsFailed = $true
        Write-Warn 'hay pruebas en rojo (detalle arriba y en export/test_odoo19.md)'
    } else {
        Write-Ok 'todas las pruebas pasaron'
    }
}

# ================================================================ RESUMEN
Write-Phase 'Resumen'

$verif = Join-Path $MigrationDir 'export\verificacion19.md'
if (Test-Path $verif) {
    Write-Host ''
    Get-Content $verif | ForEach-Object { Write-Host "  $_" }
}

Write-Host ''
Write-Host '  Acceso a Odoo 19:' -ForegroundColor Cyan
Write-Host "    http://localhost:$OdooPort   base: $OdooDb   usuario: admin   contraseña: admin"
Write-Host ''
Write-Host '  Informes generados:' -ForegroundColor Cyan
Write-Host '    migration\export\verificacion19.md   (conteos y saldos)'
Write-Host '    migration\export\test_odoo19.md      (pruebas T1-T12)'
Write-Host ''

if ($testsFailed) {
    Write-Host '  Migración terminada CON PRUEBAS EN ROJO.' -ForegroundColor Yellow
    Write-Host '  Revisa test_odoo19.md antes de dar la migración por buena.' -ForegroundColor Yellow
    Write-Host ''
    exit 2
}

Write-Host '  Migración completada correctamente.' -ForegroundColor Green
Write-Host ''
exit 0
