import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    base_dir = Path(__file__).resolve().parent
    monitor_py = base_dir / "monitor.py"
    monitor_mxf_py = base_dir / "monitor_mxf.py"

    processes = [
        subprocess.Popen([sys.executable, str(monitor_py)]),
        subprocess.Popen([sys.executable, str(monitor_mxf_py)]),
    ]

    try:
        while True:
            for proc in processes:
                exit_code = proc.poll()
                if exit_code is not None:
                    for other in processes:
                        if other != proc:
                            other.terminate()
                    for other in processes:
                        other.wait()
                    return exit_code
            time.sleep(0.5)
    except KeyboardInterrupt:
        for proc in processes:
            proc.terminate()
        for proc in processes:
            proc.wait()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
