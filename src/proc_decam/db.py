from .refcats import popen

db_vars = """
export REPO={repo}
export PG_DATA_PATH={repo}/_registry
export PG_PORT={port}
export DB_NAME={db_name}
"""

create_db = """
# set -eux
mkdir -p ${PG_DATA_PATH}
pushd ${PG_DATA_PATH}

initdb -D ${PG_DATA_PATH}
escaped_path=$(printf "'%s'\n" "${PG_DATA_PATH}" | sed -e 's/[\/&]/\\\&/g')
sed --in-place "s/#unix_socket_directories.*/unix_socket_directories = ${escaped_path}/g" ${PG_DATA_PATH}/postgresql.conf

sed --in-place "s/#port.*/port = ${PG_PORT}/g" ${PG_DATA_PATH}/postgresql.conf
sed --in-place "s/max_connections.*/max_connections = 8192/g" ${PG_DATA_PATH}/postgresql.conf
sed --in-place "s/#listen_addresses.*/listen_addresses = '*'/g" ${PG_DATA_PATH}/postgresql.conf

# allow all connections as anyone to any database on any interface
echo "host    all             all             0.0.0.0/0               trust" >> ${PG_DATA_PATH}/pg_hba.conf

pg_ctl -D ${PG_DATA_PATH} -w start

createdb -p ${PG_PORT} -h ${PG_DATA_PATH} ${DB_NAME}

psql -h localhost -p ${PG_PORT} ${DB_NAME} -c "CREATE USER butler;"
psql -h localhost -p ${PG_PORT} ${DB_NAME} -c "ALTER USER butler WITH PASSWORD 'lsst';"
psql -h localhost -p ${PG_PORT} ${DB_NAME} -c "ALTER ROLE butler WITH CREATEDB;"
psql -h localhost -p ${PG_PORT} ${DB_NAME} -c "ALTER DATABASE ${DB_NAME} OWNER TO butler;"
psql -h localhost -p ${PG_PORT} ${DB_NAME} -c "CREATE EXTENSION btree_gist;"

printf "registry:\n  db: postgresql://butler:lsst@localhost:${PG_PORT}/${DB_NAME}\n" > ${REPO}/butler_seed.yaml

butler create ${REPO} --seed-config ${REPO}/butler_seed.yaml

pg_ctl -D ${PG_DATA_PATH} -w stop

mv ${REPO}/butler.yaml ${REPO}/butler.yaml.bak
sed --in-place "s/localhost/PGHOST/g" $REPO/butler.yaml.bak
mv ${PG_DATA_PATH}/postgresql.conf ${PG_DATA_PATH}/postgresql.conf.bak
sed --in-place "s/unix_socket_directories.*/unix_socket_directories = PGDATADIR/g" ${PG_DATA_PATH}/postgresql.conf.bak

"""

start_db = """
set -eux
start_file="${PG_DATA_PATH}/repo.started"
if test -f "${start_file}"; then
    echo "database already started! check contents of ${start_file} for host" 1>&2
    exit 1
fi
hostname > "${start_file}"
escaped_path=$(printf "'%s'\n" "${REPO}/_registry" | sed -e 's/[\/&]/\\\&/g')
sed "s/PGDATADIR/${escaped_path}/g" "${PG_DATA_PATH}/postgresql.conf.bak" > "${PG_DATA_PATH}/postgresql.conf"

pg_ctl -D ${PG_DATA_PATH} -w start

sed "s/PGHOST/$(hostname)/g" "${REPO}/butler.yaml.bak" > "${REPO}/butler.yaml"
"""

stop_db = """
set -eux
start_file="${PG_DATA_PATH}/repo.started"
pg_ctl -D ${PG_DATA_PATH} -w stop
rm -f "${REPO}/butler.yaml"
rm -f "${PG_DATA_PATH}/postgresql.conf"
rm -f "${start_file}"
"""

def main():
    import argparse
    from pathlib import Path
    from tempfile import NamedTemporaryFile

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["create", "start", "stop"], help="The command to execute")
    parser.add_argument("repo", type=Path, help="Path to the repository")
    parser.add_argument("--port", type=int, default=55432, help="Port number for the database server")
    parser.add_argument("--db-name", type=str, default="proc_decam_registry", help="Name of the database to create")
    args = parser.parse_args()

    db_vars_formatted = db_vars.format(repo=args.repo.absolute(), port=args.port, db_name=args.db_name)
    with NamedTemporaryFile("w", delete=False) as tf:
        tf.write(db_vars_formatted)
        if args.command == "create":
            tf.write(create_db)
        elif args.command == "start":
            tf.write(start_db)
        elif args.command == "stop":
            tf.write(stop_db)
        
        tf.flush()
        cmd = ["bash", tf.name]
        p = popen(cmd)
        if p.wait():
            raise RuntimeError(f"Database {args.command} command failed")

if __name__ == "__main__":
    main()
