#!/usr/bin/env python3

from collections.abc import Callable
from typing import Any, Generator
import bcrypt
import os
from string import Template
from pathlib import Path
from argparse import ArgumentParser
from functools import wraps
from contextlib import contextmanager

import psycopg


ROOT_DIR = Path(__file__).parent
SALT = bcrypt.gensalt()
UID = 1000

SERVICES: dict[str, list[Callable]] = {'all': []}
DBS: dict[str, list[Callable]] = {'all': []}

def service(service_name: str, service_dict: dict = SERVICES):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            print(f'Setup {service_name}')
            func(*args, **kwargs)
            print(f'Complete {service_name}')

        services = service_dict.setdefault(service_name, [])
        services.append(wrapper)

        service_dict['all'].append(wrapper)

        return wrapper
    return decorator

def load_env(path: Path) -> dict:
    env = {}

    with path.open("r") as f:
        for line in f:
            if line.startswith("#"):
                continue

            elif line:
                line = line.strip()
                tokens = line.split("=", 1)
                if len(tokens) == 2:
                    env[tokens[0].strip()] = tokens[1].strip()
    return env


def gen_sample_env(path: Path) -> None:
    with path.open("r") as env_f:
        with (path.parent / ".env.sample").open("w") as env_sample_f:
            for line in env_f:
                line = line.strip()

                if line.startswith("#") or not line:
                    env_sample_f.write(f"{line}\n")
                    continue

                tokens = line.split("=", 1)
                if 0 < len(tokens) <= 2:
                    env_sample_f.write(f"{tokens[0]}=\n")


def get_dst(path: str | Path) -> Path:
    return ROOT_DIR / "data" / path


def get_src(path: str | Path) -> Path:
    return ROOT_DIR / "templates" / path


def copy(
    file: str | Path, env: dict, uid=UID, gid=UID, mode=0o644, is_template=True
) -> None:
    src = get_src(file)
    dst = get_dst(file)
    if not src.exists():
        print(f"[ERROR] Template: {src} not exists.")
        return

    if not dst.exists():
        with src.open() as s:
            with dst.open("w") as d:
                if is_template:
                    t = Template(s.read())
                    d.write(t.safe_substitute(env))
                else:
                    d.write(s.read())
            dst.chmod(mode)
            os.chown(dst, uid, gid)


def touch(file: str | Path, uid=UID, gid=UID, mode=0o644):
    dst = get_dst(file)
    if not dst.exists():
        open(dst, "w").close()
        dst.chmod(mode)
        os.chown(dst, uid, gid)


def mkdir(file: str | Path, uid=UID, gid=UID, mode=0o755):
    dst = get_dst(file)
    dst.mkdir(mode=mode, parents=True, exist_ok=True)
    os.chown(dst, uid, gid)


def create_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), SALT).decode("utf-8")


class PostgresExecutor:
    def __init__(self, env: dict, run_out_docker: bool = True):
        self._env = env

        self.host = 'localhost' if run_out_docker else env['POSTGRES_HOST']
        self.port = env['POSTGRES_OUT_PORT'] if run_out_docker else env["POSTGRES_PORT"]
        self.db = env['POSTGRES_DB']
        self.user = env['POSTGRES_USER']
        self.password = env['POSTGRES_PASSWORD']

    @contextmanager
    def get_connection(self, db: str | None = None, autocommit: bool = False) -> Generator[psycopg.Connection, None, None]:
        conn = psycopg.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            dbname=db or self.db,
            autocommit=autocommit
        )
        try:
            yield conn
            if not autocommit:
                conn.commit()
        finally:
            conn.close()

    def _execute(self,
                 query: str,
                 params: tuple | None = None,
                 fetch: bool = False,
                 autocommit: bool = False
                ) -> list[Any]:

        with self.get_connection(autocommit=autocommit) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                if fetch and cur.description:
                    return cur.fetchall()
                return []

    def execute(self, query_template: str):
        template = Template(query_template)
        query = template.safe_substitute(self._env)

        statements = [q.strip() for q in query.split(';') if q.strip()]
        for statement in statements:
            is_need_autocommit = any(cmd in statement.upper() for cmd in (
                'CREATE DATABASE',
                'DROP DATABASE',
                'ALTER SYSTEM'
            ))

            try:
                self._execute(
                    statement,
                    autocommit=is_need_autocommit
                )
            except Exception as e:
                print(f"Error executing '{statement}': {e}")
                raise


@service('postgres')
def setup_postgres(env: dict):
    # postgres
    mkdir("postgres/data")
    mkdir("postgres/init")
    copy("postgres/init/services.sql", env)


@service('traefik')
def setup_traefik(env: dict):
    # traefik
    env["T_ADMIN_PASSWORD_HASH"] = create_password(env["T_ADMIN_PASSWORD"])

    mkdir("traefik/config")
    touch("traefik/acme.json", mode=0o600)
    copy("traefik/traefik.yml", env)
    copy("traefik/usersfile", env)
    copy("traefik/config/middlewares.yml", env)
    copy("traefik/config/routers.yml", env)
    copy("traefik/config/tls.yml", env, is_template=False)


@service('matrix')
def setup_matrix(env: dict):
    # matrix
    mkdir("matrix/synapse")
    copy("matrix/synapse/homeserver.yaml", env)

    # matrix nginx
    mkdir("matrix/nginx/www/.well-known/matrix")
    copy("matrix/nginx/matrix.conf", env)
    copy("matrix/nginx/www/.well-known/matrix/client", env)
    copy("matrix/nginx/www/.well-known/matrix/server", env)


@service('matrix', DBS)
def setup_matrix_db(env: dict):
    query = """
    CREATE ROLE ${MATRIX_POSTGRES_USER};
    ALTER ROLE ${MATRIX_POSTGRES_USER} WITH PASSWORD '${MATRIX_DB_ROLE_PASSWORD}';
    ALTER ROLE ${MATRIX_POSTGRES_USER} WITH LOGIN;
    CREATE DATABASE ${MATRIX_POSTGRES_DB} ENCODING 'UTF8' LC_COLLATE='C' LC_CTYPE='C' template=template0 OWNER ${MATRIX_POSTGRES_USER};
    GRANT ALL PRIVILEGES ON DATABASE ${MATRIX_POSTGRES_DB} TO ${MATRIX_POSTGRES_USER};
    """
    
    psql = PostgresExecutor(env)
    psql.execute(query)


@service('trilium')
def setup_trilium(env: dict):
    # trilium
    mkdir("trilium/data")


@service('qbittorrent')
def setup_qbittorrent(env: dict):
    # qbittorrent
    mkdir("qbittorrent/config")


@service('gitea')
def setup_gitea(env: dict):
    # gitea
    mkdir("gitea/data")


@service('gitea', DBS)
def setup_gitea_db(env: dict):
    query = """
    CREATE ROLE ${GITEA_USER_NAME};
    ALTER ROLE ${GITEA_USER_NAME} WITH PASSWORD '${GITEA_PASSWORD}';
    ALTER ROLE ${GITEA_USER_NAME} WITH LOGIN;
    CREATE DATABASE ${GITEA_DB_NAME} ENCODING 'UTF8' LC_COLLATE='C' LC_CTYPE='C' template=template0 OWNER ${GITEA_USER_NAME};
    GRANT ALL PRIVILEGES ON DATABASE ${GITEA_DB_NAME} TO ${GITEA_USER_NAME};
    """
    
    psql = PostgresExecutor(env)
    psql.execute(query)

    
@service('forgejo')
def setup_forgejo(env: dict):
    # forgejo
    mkdir("forgejo/data")


@service('forgejo', DBS)
def setup_forgejo_db(env: dict):
    query = """
    CREATE ROLE ${FORGEJO_USER_NAME};
    ALTER ROLE ${FORGEJO_USER_NAME} WITH PASSWORD '${FORGEJO_PASSWORD}';
    ALTER ROLE ${FORGEJO_USER_NAME} WITH LOGIN;
    CREATE DATABASE ${FORGEJO_DB_NAME} ENCODING 'UTF8' LC_COLLATE='C' LC_CTYPE='C' template=template0 OWNER ${FORGEJO_USER_NAME};
    GRANT ALL PRIVILEGES ON DATABASE ${FORGEJO_DB_NAME} TO ${FORGEJO_USER_NAME};
    """
    
    psql = PostgresExecutor(env)
    psql.execute(query)
    

@service('linkwarden')
def setup_linkwarden(env: dict):
    mkdir("linkwarden/data")


@service('linkwarden', DBS)
def setup_linkwarden_db(env: dict):
    # linkwarden
    query = """
    CREATE ROLE ${LINKWARDEN_PSQL_USER};
    ALTER ROLE ${LINKWARDEN_PSQL_USER} WITH PASSWORD '${LINKWARDEN_PSQL_PASSWORD}';
    ALTER ROLE ${LINKWARDEN_PSQL_USER} WITH LOGIN;
    CREATE DATABASE ${LINKWARDEN_PSQL_DB_NAME} ENCODING 'UTF8' LC_COLLATE='C' LC_CTYPE='C' template=template0 OWNER ${LINKWARDEN_PSQL_USER};
    GRANT ALL PRIVILEGES ON DATABASE ${LINKWARDEN_PSQL_DB_NAME} TO ${LINKWARDEN_PSQL_USER};
    """
    
    psql = PostgresExecutor(env)
    psql.execute(query)


@service('grafana')
def setup_grafana(env: dict):
    mkdir('grafana/datasources')
    copy('grafana/datasources/datasource.yml', env)


@service('prometheus')
def setup_prometheus(env: dict):
    mkdir('prometheus/config')
    mkdir('prometheus/data', mode=0o0777)
    print('NEED TO EXEC: "sudo chown -R 65534:65534 ./data/prometheus/data"')
    copy('prometheus/config/prometheus.yml', env)


@service('calibre')
def setup_calibre(env: dict):
    mkdir('calibre/config')


@service('nextcloud')
def setup_nextcloud(env: dict):
    # nextcloud
    mkdir("nextcloud/data")


@service('nextcloud', DBS)
def setup_nextcloud_db(env: dict):
    query = """
    CREATE ROLE ${NEXTCLOUD_PG_USER};
    ALTER ROLE ${NEXTCLOUD_PG_USER} WITH PASSWORD '${NEXTCLOUD_PG_PASSWORD}';
    ALTER ROLE ${NEXTCLOUD_PG_USER} WITH LOGIN;
    CREATE DATABASE ${NEXTCLOUD_PG_DB} ENCODING 'UTF8' LC_COLLATE='C' LC_CTYPE='C' template=template0 OWNER ${NEXTCLOUD_PG_USER};
    GRANT ALL PRIVILEGES ON DATABASE ${NEXTCLOUD_PG_DB} TO ${NEXTCLOUD_PG_USER};
    """
    
    psql = PostgresExecutor(env)
    psql.execute(query)

@service('owncloud')
def setup_owncloud(env: dict):
    # owncloud
    mkdir("owncloud/data")
    mkdir("owncloud_valkey/data")


@service('owncloud', DBS)
def setup_owncloud_db(env: dict):
    query = """
    CREATE ROLE ${OWNCLOUD_PG_USER};
    ALTER ROLE ${OWNCLOUD_PG_USER} WITH PASSWORD '${OWNCLOUD_PG_PASSWORD}';
    ALTER ROLE ${OWNCLOUD_PG_USER} WITH LOGIN;
    CREATE DATABASE ${OWNCLOUD_PG_DB} ENCODING 'UTF8' LC_COLLATE='C' LC_CTYPE='C' template=template0 OWNER ${OWNCLOUD_PG_USER};
    GRANT ALL PRIVILEGES ON DATABASE ${OWNCLOUD_PG_DB} TO ${OWNCLOUD_PG_USER};
    """
    
    psql = PostgresExecutor(env)
    psql.execute(query)


@service('collabora')
def setup_collabora(env: dict):
    # collabora
    mkdir("collabora/data", mode=0o755)
    print('NEED TO EXEC: "sudo chown -R 101:101 ./data/collabora/data"')


def main():
    env_path = ROOT_DIR / ".env"
    env = load_env(env_path)


    parser = ArgumentParser(prog='Docker setup')

    parser.add_argument('--setup', choices=list(SERVICES.keys()))
    parser.add_argument('--setup_db', choices=list(DBS.keys()))
    parser.add_argument('--gen_sample_env', action='store_true')

    args = parser.parse_args()


    if args.gen_sample_env:
        gen_sample_env(env_path)

    
    if (service_to_setup := args.setup):
        for setup_func in SERVICES[service_to_setup]:
            setup_func(env)

    
    if (db_to_setup := args.setup_db):
        for setup_db in DBS[db_to_setup]:
            setup_db(env)

if __name__ == "__main__":
    main()

