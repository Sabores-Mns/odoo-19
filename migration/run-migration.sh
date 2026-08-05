#!/usr/bin/env bash
#
# Ejecuta la migración completa de Profit Plus a Odoo 19.
#
# Espejo de run-migration.ps1 para Git Bash, WSL y Linux. Mismas fases, mismas
# comprobaciones y mismos códigos de salida:
#     0  todo bien
#     1  error: la migración se detuvo
#     2  la migración terminó pero hay pruebas en rojo
#
# Es re-ejecutable de principio a fin: cada fase detecta si ya se hizo y la
# salta. Nunca continúa en silencio sobre un error.
#
# Uso:
#   ./run-migration.sh                       migración completa
#   ./run-migration.sh --skip-extract --steps reconcile,crossapply,trueup,verify
#   ./run-migration.sh --force               vuelve a restaurar el .bak
#   ./run-migration.sh --skip-tests          sin la suite T1-T12
#
set -euo pipefail

# --------------------------------------------------------------- constantes
MIGRATION_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$MIGRATION_DIR")"
MIGRATION_YML="$MIGRATION_DIR/docker-compose.migration.yml"

ODOO_CONTAINER='odoo-19-mns'
RUNNER_CONTAINER='migration_runner'
MSSQL_CONTAINER='migration_mssql'
ODOO_DB='profit_migrado19'
ODOO_PORT=10020
PG_USER='odoo'
PG_PASSWORD='odoo19@2025'
# Módulos necesarios en la base destino. l10n_ve trae el plan de cuentas
# venezolano; sin él las cuentas por cobrar no cuadran con Profit.
ODOO_MODULES='base,contacts,product,sale_management,stock,account,l10n_ve'

STEPS=''
SKIP_EXTRACT=0
FORCE=0
SKIP_TESTS=0

while [ $# -gt 0 ]; do
    case "$1" in
        --steps)        STEPS="$2"; shift 2 ;;
        --steps=*)      STEPS="${1#*=}"; shift ;;
        --skip-extract) SKIP_EXTRACT=1; shift ;;
        --force)        FORCE=1; shift ;;
        --skip-tests)   SKIP_TESTS=1; shift ;;
        -h|--help)      sed -n '2,19p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *)              echo "Opción desconocida: $1" >&2; exit 1 ;;
    esac
done

# ----------------------------------------------------------------- utilidad
if [ -t 1 ]; then
    C_CYAN=$'\033[36m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'
    C_RED=$'\033[31m'; C_GRAY=$'\033[90m'; C_OFF=$'\033[0m'
else
    C_CYAN=''; C_GREEN=''; C_YELLOW=''; C_RED=''; C_GRAY=''; C_OFF=''
fi

RULE="$(printf '=%.0s' $(seq 1 72))"
PHASE_NUM=0

phase() {
    PHASE_NUM=$((PHASE_NUM + 1))
    printf '\n%s%s%s\n' "$C_CYAN" "$RULE" "$C_OFF"
    printf '%s  FASE %d - %s%s\n' "$C_CYAN" "$PHASE_NUM" "$1" "$C_OFF"
    printf '%s%s%s\n' "$C_CYAN" "$RULE" "$C_OFF"
}
ok()   { printf '%s  [OK] %s%s\n'    "$C_GREEN"  "$1" "$C_OFF"; }
info() { printf '%s  %s%s\n'         "$C_GRAY"   "$1" "$C_OFF"; }
warn() { printf '%s  [AVISO] %s%s\n' "$C_YELLOW" "$1" "$C_OFF"; }

die() {
    printf '\n%sERROR: %s%s\n' "$C_RED" "$1" "$C_OFF" >&2
    if [ $# -gt 1 ]; then
        printf '%s       %s%s\n' "$C_YELLOW" "$2" "$C_OFF" >&2
    fi
    echo >&2
    exit 1
}

runner() {
    # Ejecuta un comando dentro del migrador y propaga el fallo. Los scripts del
    # ETL imprimen su propio progreso, así que la salida va directa a consola.
    local what="$1"; shift
    info "-> $what"
    if ! docker exec "$RUNNER_CONTAINER" "$@"; then
        die "$what falló." \
            "Revisa la salida de arriba. Puedes reintentar sólo esta parte; todo es idempotente."
    fi
}

# ============================================================ FASE 1: preflight
phase 'Comprobaciones previas'

DOCKER_VERSION="$(docker version --format '{{.Server.Version}}' 2>/dev/null)" \
    || die 'Docker no responde.' \
           'Abre Docker Desktop, espera a que arranque del todo y vuelve a intentarlo.'
ok "Docker $DOCKER_VERSION"

[ -f "$MIGRATION_YML" ] \
    || die "No encuentro $MIGRATION_YML" 'Ejecuta el script desde el repositorio odoo-19.'

# El .bak: sin él no hay nada que migrar.
DB_DIR="$MIGRATION_DIR/db"
BAKS=()
while IFS= read -r archivo; do
    BAKS+=("$archivo")
done < <(find "$DB_DIR" -maxdepth 1 -type f -name '*.bak' 2>/dev/null | sort)

if [ "${#BAKS[@]}" -eq 0 ]; then
    die "No hay ningún archivo .bak en $DB_DIR" \
        "Copia ahí el respaldo de Profit. Instrucciones en $DB_DIR/README.md"
fi
if [ "${#BAKS[@]}" -gt 1 ]; then
    die "Hay ${#BAKS[@]} archivos .bak en $DB_DIR: $(basename -a "${BAKS[@]}" | tr '\n' ' ')" \
        'Deja sólo el que quieras migrar.'
fi
ok "Respaldo: $(basename "${BAKS[0]}") ($(( $(wc -c < "${BAKS[0]}") / 1048576 )) MB)"

# ======================================================== FASE 2: infraestructura
phase 'Infraestructura'

info '-> stack Odoo 19 (crea la red que usará el migrador)'
( cd "$REPO_DIR" && docker compose up -d ) \
    || die 'No se pudo levantar el stack de Odoo 19.'
ok 'odoo-19-mns y db-odoo-19-mns arriba'

info '-> stack de migración (SQL Server + runner)'
( cd "$REPO_DIR" && docker compose -f "$MIGRATION_YML" up -d ) \
    || die 'No se pudo levantar el stack de migración.' \
           'Si dice "network odoo-19-mns_default not found", el stack raíz no arrancó bien.'
ok "$MSSQL_CONTAINER y $RUNNER_CONTAINER arriba"

# Odoo tarda en responder tras arrancar; sin esto la creación de la BD falla.
info "-> esperando a que Odoo responda en :$ODOO_PORT"
READY=0
for i in $(seq 1 60); do
    if curl -fsS -m 5 -o /dev/null "http://localhost:$ODOO_PORT/web/database/selector"; then
        READY=1; break
    fi
    if [ $((i % 10)) -eq 0 ]; then info "   ...($i/60)"; fi
    sleep 5
done
[ "$READY" -eq 1 ] \
    || die "Odoo no respondió en http://localhost:$ODOO_PORT tras 5 minutos." \
           "Revisa: docker logs $ODOO_CONTAINER --tail 50"
ok "Odoo responde en http://localhost:$ODOO_PORT"

# ============================================================ FASE 3: dependencias
phase 'Dependencias de Python'
runner 'pip install (python-tds, beautifulsoup4)' \
       pip install --quiet --no-input -r /migration/requirements.txt
ok 'dependencias instaladas'

# =================================================== FASE 4: restaurar Profit
phase 'Restaurar el respaldo de Profit en SQL Server'
if [ "$FORCE" -eq 1 ]; then
    runner 'RESTORE DATABASE MULTI_A' python /migration/etl/restore_mssql.py --force
else
    runner 'RESTORE DATABASE MULTI_A' python /migration/etl/restore_mssql.py
fi
ok 'base MULTI_A lista'

# ================================================== FASE 5: crear base de Odoo
phase 'Base de datos de Odoo 19'

# ¿Existe ya? Se consulta a Postgres directamente: más fiable que la API web.
EXISTING="$(docker exec -e PGPASSWORD="$PG_PASSWORD" db-odoo-19-mns \
    psql -U "$PG_USER" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname='$ODOO_DB'" 2>/dev/null || true)"

if [ "$(echo "$EXISTING" | tr -d '[:space:]')" = '1' ]; then
    ok "La base $ODOO_DB ya existe; no se recrea"
else
    info "-> creando $ODOO_DB con los módulos: $ODOO_MODULES"
    info '   (tarda varios minutos la primera vez)'
    # odoo.conf no trae db_user/db_password —los inyecta entrypoint.sh desde el
    # entorno—, así que en docker exec hay que pasarlos explícitamente.
    docker exec "$ODOO_CONTAINER" odoo \
        -c /etc/odoo/odoo.conf -r "$PG_USER" -w "$PG_PASSWORD" \
        -d "$ODOO_DB" -i "$ODOO_MODULES" \
        --load-language=es_419 --stop-after-init --no-http --log-level=warn \
        || die "No se pudo crear la base $ODOO_DB." \
               "Revisa: docker logs $ODOO_CONTAINER --tail 50"
    ok "$ODOO_DB creada"
fi

# ====================================================== FASE 6: ETL
phase 'ETL: extraer, transformar y cargar'

if [ "$SKIP_EXTRACT" -eq 1 ]; then
    warn 'se omiten extract y transform (--skip-extract); se reutilizan los CSV de export/'
else
    runner 'extract: MULTI_A -> export/raw/*.csv'         python -u /migration/etl/extract.py
    runner 'transform: raw -> export/odoo_csv/ + planes'  python -u /migration/etl/transform.py
    runner 'transform19: adaptación al modelo de Odoo 19' python -u /migration/etl/transform19.py
fi

if [ -n "$STEPS" ]; then
    info "pasos solicitados: ${STEPS//,/ }"
    # La separación en palabras es intencionada: --steps a,b,c -> tres argumentos.
    # shellcheck disable=SC2086
    runner 'load19: carga en Odoo 19' python -u /migration/etl/load19.py ${STEPS//,/ }
else
    info 'migración completa (~2-3 h)'
    runner 'load19: carga en Odoo 19' python -u /migration/etl/load19.py
fi
ok 'ETL terminado'

# ============================================== FASE 7: verificación
TESTS_FAILED=0
if [ "$SKIP_TESTS" -eq 1 ]; then
    phase 'Verificación'
    warn 'omitida (--skip-tests)'
else
    phase 'Verificación contra los datos de Profit'
    info '-> suite de pruebas T1-T12'
    # Un test en rojo no invalida la migración: se reporta y se sigue para que
    # el resumen final se imprima igual.
    if docker exec "$RUNNER_CONTAINER" python -u /migration/etl/tests_migracion.py 19; then
        ok 'todas las pruebas pasaron'
    else
        TESTS_FAILED=1
        warn 'hay pruebas en rojo (detalle arriba y en export/test_odoo19.md)'
    fi
fi

# ================================================================ RESUMEN
phase 'Resumen'

VERIF="$MIGRATION_DIR/export/verificacion19.md"
if [ -f "$VERIF" ]; then
    echo
    sed 's/^/  /' "$VERIF"
fi

printf '\n%s  Acceso a Odoo 19:%s\n' "$C_CYAN" "$C_OFF"
printf '    http://localhost:%s   base: %s   usuario: admin   contraseña: admin\n' \
       "$ODOO_PORT" "$ODOO_DB"
printf '\n%s  Informes generados:%s\n' "$C_CYAN" "$C_OFF"
echo '    migration/export/verificacion19.md   (conteos y saldos)'
echo '    migration/export/test_odoo19.md      (pruebas T1-T12)'
echo

if [ "$TESTS_FAILED" -eq 1 ]; then
    printf '%s  Migración terminada CON PRUEBAS EN ROJO.%s\n' "$C_YELLOW" "$C_OFF"
    printf '%s  Revisa test_odoo19.md antes de dar la migración por buena.%s\n\n' "$C_YELLOW" "$C_OFF"
    exit 2
fi

printf '%s  Migración completada correctamente.%s\n\n' "$C_GREEN" "$C_OFF"
exit 0
