import os
import platform
import signal
import stat
import subprocess
from dataclasses import dataclass
import urllib.request
from pathlib import Path
import socket
from typing import TYPE_CHECKING

from weaviate.exceptions import WeaviateStartUpError

if TYPE_CHECKING:
    from weaviate.connect import Connection


@dataclass
class EmbeddedOptions:
    persistence_data_path: str = str((Path.home() / ".local/share/weaviate"))
    binary_path: str = str((Path.home() / ".local/bin/weaviate-embedded"))
    binary_url: str = (
        "https://github.com/samos123/weaviate/releases/download/v1.17.3/weaviate-server"
    )
    port: int = 6666
    cluster_hostname: str = "embedded"


def get_random_port() -> int:
    sock = socket.socket()
    sock.bind(("", 0))
    port_num = int(sock.getsockname()[1])
    sock.close()
    return port_num


class EmbeddedDB:
    # TODO add a stop function that gets called when python process exits
    def __init__(self, options: EmbeddedOptions, connection: "Connection"):
        self.port = options.port
        self.data_bind_port = get_random_port()
        self.options = options
        self.pid = 0
        self.ensure_paths_exist()
        self.check_supported_platform()
        self.connection = connection

    def __del__(self):
        self.stop()

    def ensure_paths_exist(self):
        Path(self.options.binary_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.options.persistence_data_path).mkdir(parents=True, exist_ok=True)

    def ensure_weaviate_binary_exists(self):
        file = Path(self.options.binary_path)
        if not file.exists():
            print(
                f"Binary {self.options.binary_path} did not exist. "
                f"Downloading binary from {self.options.binary_url}"
            )
            urllib.request.urlretrieve(self.options.binary_url, self.options.binary_path)
            # Ensuring weaviate binary is executable
            file.chmod(file.stat().st_mode | stat.S_IEXEC)

    def is_listening(self) -> bool:
        self.connection.get
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.connect(("127.0.0.1", self.port))
            s.close()
            return True
        except socket.error:
            s.close()
            return False

    def wait_for_weaviate(self):
        self.connection.wait_for_weaviate(startup_period=30, sleep_period=0.01)

    def check_supported_platform(self):
        if platform.system() in ["Darwin", "Windows"]:
            raise WeaviateStartUpError(
                f"{platform.system()} is not supported with EmbeddedDB. Please upvote the feature request if "
                f"you want this: https://github.com/weaviate/weaviate-python-client/issues/239"
            )

    def start(self):
        if self.is_listening():
            print(f"embedded weaviate is already listing on port {self.port}")
            return

        self.ensure_weaviate_binary_exists()
        my_env = os.environ.copy()
        my_env.setdefault("AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED", "true")
        my_env.setdefault("QUERY_DEFAULTS_LIMIT", "20")
        my_env.setdefault("PERSISTENCE_DATA_PATH", self.options.persistence_data_path)
        my_env.setdefault("CLUSTER_HOSTNAME", self.options.cluster_hostname)
        # Bug with weaviate requires setting gossip and data bind port
        my_env.setdefault("CLUSTER_GOSSIP_BIND_PORT", str(get_random_port()))
        my_env.setdefault(
            "ENABLE_MODULES",
            "text2vec-openai,text2vec-cohere,text2vec-huggingface,ref2vec-centroid,generative-openai,qna-openai",
        )
        process = subprocess.Popen(
            [
                f"{self.options.binary_path}",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--scheme",
                "http",
            ],
            env=my_env,
        )
        self.pid = process.pid
        print(f"Started {self.options.binary_path}: process ID {self.pid}")
        self.wait_for_weaviate()

    def stop(self):
        if self.pid > 0:
            try:
                os.kill(self.pid, signal.SIGTERM)
            except ProcessLookupError:
                print(
                    f"Tried to stop embedded weaviate process {self.pid}. Process {self.pid} "
                    f"was not found. So not doing anything"
                )

    def ensure_running(self):
        if not self.is_listening():
            print(
                f"Embedded weaviate wasn't listening on port {self.port}, so starting embedded weaviate again"
            )
            self.start()
