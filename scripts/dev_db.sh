#!/bin/bash
# dev_db.sh: agent databases only (docs/contracts.md#scripts; decisions L3.4, L12.5).
#
#   dev_db.sh createdb <name>   create <name> as mp_agent on every dialect whose URL the env file
#                               sets and whose container runs (an existing database is left as is)
#   dev_db.sh dropdb <name>     drop it there (a missing database is ignored)
#   dev_db.sh url pg|mssql      print PG_URL or MSSQL_URL (the agent URLs); exit 1 when unset
#
# It provisions nothing: the postgres and mssql environment items create the containers, roles,
# passwords and pg_hba lines. It reads only the env file, never data.env, never prints a password,
# and needs Docker, so it never runs inside mp-run. Names starting with mpdata_ are refused.
set -u

die() {
  echo "dev_db.sh: $1" >&2
  exit 2
}

ENV_FILE="$HOME/.config/modernization/env"
PG_CONTAINER=mp-pg16
MSSQL_CONTAINER=mp-mssql22
SQLCMD=/opt/mssql-tools18/bin/sqlcmd

[ $# -ge 1 ] || die "usage: dev_db.sh createdb|dropdb <name> | url pg|mssql"
sub=$1
shift
case "$sub" in
  createdb | dropdb | url) ;;
  *) die "unknown subcommand (allowed: createdb, dropdb, url; provisioning belongs to the postgres and mssql environment items)" ;;
esac

[ -f "$ENV_FILE" ] || die "env file $ENV_FILE is missing: run the environment preflight"
# shellcheck disable=SC1090
. "$ENV_FILE"

if [ "$sub" = url ]; then
  [ $# -eq 1 ] || die "usage: dev_db.sh url pg|mssql"
  case "$1" in
    pg)
      [ -n "${PG_URL:-}" ] || exit 1
      printf '%s\n' "$PG_URL"
      ;;
    mssql)
      [ -n "${MSSQL_URL:-}" ] || exit 1
      printf '%s\n' "$MSSQL_URL"
      ;;
    *) die "usage: dev_db.sh url pg|mssql" ;;
  esac
  exit 0
fi

[ $# -eq 1 ] || die "usage: dev_db.sh $sub <name>"
name=$1
[[ "$name" =~ ^[a-z][a-z0-9_]{1,62}$ ]] || die "name must match ^[a-z][a-z0-9_]{1,62}$"
case "$name" in
  mpdata_*) die "mpdata_ databases hold engagement rows; only mp-data and the operator create them" ;;
esac

# The password part of scheme://user:password@host:port (generated values are URL-safe).
url_password() {
  local rest=${1#*://}
  rest=${rest%%@*}
  case "$rest" in
    *:*) printf '%s' "${rest#*:}" ;;
    *) printf '' ;;
  esac
}
running() {
  [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = true ]
}

status=0
did=0
if [ -n "${PG_URL:-}" ] && running "$PG_CONTAINER"; then
  did=1
  pw=$(url_password "$PG_URL")
  if [ "$sub" = createdb ]; then
    out=$(PGPASSWORD="$pw" docker exec -e PGPASSWORD "$PG_CONTAINER" createdb -h 127.0.0.1 -U mp_agent "$name" 2>&1)
    rc=$?
    if [ $rc -ne 0 ] && ! grep -q 'already exists' <<<"$out"; then
      echo "postgresql: createdb failed (exit $rc)" >&2
      status=1
    else
      echo "postgresql: $name ready" >&2
    fi
  else
    if PGPASSWORD="$pw" docker exec -e PGPASSWORD "$PG_CONTAINER" dropdb --if-exists -h 127.0.0.1 -U mp_agent "$name" >&2; then
      echo "postgresql: $name dropped" >&2
    else
      echo "postgresql: dropdb failed" >&2
      status=1
    fi
  fi
  unset pw
else
  echo "postgresql: skipped (PG_URL unset or $PG_CONTAINER not running)" >&2
fi

if [ -n "${MSSQL_URL:-}" ] && running "$MSSQL_CONTAINER"; then
  did=1
  pw=$(url_password "$MSSQL_URL")
  if [ "$sub" = createdb ]; then
    out=$(SQLCMDPASSWORD="$pw" docker exec -e SQLCMDPASSWORD "$MSSQL_CONTAINER" "$SQLCMD" -C -S localhost -U mp_agent -b -Q "create database [$name]" 2>&1)
    rc=$?
    if [ $rc -ne 0 ] && ! grep -q 'already exists' <<<"$out"; then
      echo "mssql: create database failed (exit $rc)" >&2
      status=1
    else
      echo "mssql: $name ready" >&2
    fi
  else
    if SQLCMDPASSWORD="$pw" docker exec -e SQLCMDPASSWORD "$MSSQL_CONTAINER" "$SQLCMD" -C -S localhost -U mp_agent -b -Q "drop database if exists [$name]" >&2; then
      echo "mssql: $name dropped" >&2
    else
      echo "mssql: drop database failed" >&2
      status=1
    fi
  fi
  unset pw
else
  echo "mssql: skipped (MSSQL_URL unset or $MSSQL_CONTAINER not running)" >&2
fi

if [ $did = 0 ]; then
  echo "no dialect available: neither URL is set with its container running" >&2
  exit 1
fi
exit $status
