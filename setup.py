#!/usr/bin/env python3

from collections.abc import Callable
import bcrypt
import os
from string import Template
from pathlib import Path
from argparse import ArgumentParser
from functools import wraps

ROOT_DIR = Path(__file__).parent
SALT = bcrypt.gensalt()
UID = 1000

SERVICES: dict[str, list[Callable]] = {'all': []}
DBS: dict[str, list[Callable]] = {'all': []}

def service(service_name: str, service_dict: dict = SERVICES):
    def decorator(func):
        @wraps(func)
        def wrapper():
            print(f'Setup {service_name}')
            func()
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

@service('postgres')
def setup_postgres(env):
    # postgres
    mkdir("postgres/data")
    mkdir("postgres/init")
    copy("postgres/init/services.sql", env)


@service('traefik')
def setup_traefik(env):
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
def setup_matrix(env):
    # matrix
    mkdir("matrix/synapse")
    copy("matrix/synapse/homeserver.yaml", env)

    # matrix nginx
    mkdir("matrix/nginx/www/.well-known/matrix")
    copy("matrix/nginx/matrix.conf", env)
    copy("matrix/nginx/www/.well-known/matrix/client", env)
    copy("matrix/nginx/www/.well-known/matrix/server", env)


@service('trilium')
def setup_trilium(env):
    # trilium
    mkdir("trilium/trilium-data")


@service('qbittorrent')
def setup_qbittorrent(env):
    # qbittorrent
    mkdir("qbittorrent/config")


@service('gitea')
def setup_gitea(env):
    pass


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

    service_to_setup = args.setup
    if service_to_setup:
        for setup_func in SERVICES[service_to_setup]:
            setup_func(env)


if __name__ == "__main__":
    main()

