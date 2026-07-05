import subprocess
import shutil
import sys
from typing import Optional
from .settings import (
    get_stockfish_path,
    PINNED_STOCKFISH_VERSION,
    get_limits,
    STOCKFISH_THREADS
)

def verify_engine(mock_output: Optional[str] = None) -> str:
    """
    Verifies that the Stockfish binary is present at STOCKFISH_PATH, reports its version,
    and checks if it matches the PINNED_STOCKFISH_VERSION. Fails loudly with an informative
    exception if validation fails.
    
    If mock_output is provided, it bypasses the subprocess execution and uses mock_output
    for testing version checks.
    """
    stockfish_path = get_stockfish_path()
    if not stockfish_path:
        raise RuntimeError(
            "STOCKFISH_PATH environment variable is not set. "
            "Please configure the absolute path to your Stockfish binary."
        )

    if mock_output is None:
        # Check if the path is executable
        resolved_path = shutil.which(stockfish_path)
        if not resolved_path:
            raise RuntimeError(
                f"Stockfish binary was not found or is not executable at: '{stockfish_path}'"
            )

        # Run Stockfish to retrieve its version information
        try:
            # Stockfish outputs its version header immediately upon startup.
            # We write "uci" and "quit" to gracefully query and exit.
            process = subprocess.Popen(
                [resolved_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            stdout, stderr = process.communicate(input="uci\nquit\n", timeout=5)
        except subprocess.TimeoutExpired as e:
            process.kill()
            raise RuntimeError(f"Stockfish binary timed out during startup check: {e}")
        except Exception as e:
            raise RuntimeError(f"Failed to execute Stockfish binary at '{resolved_path}': {e}")

        if process.returncode != 0:
            raise RuntimeError(
                f"Stockfish exited with code {process.returncode}. stderr: {stderr.strip()}"
            )
    else:
        stdout = mock_output

    # The first line of Stockfish output usually contains "Stockfish <version>".
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("Stockfish startup check failed: stdout was empty.")

    version_line = ""
    for line in lines:
        if "Stockfish" in line:
            version_line = line
            break

    if not version_line:
        raise RuntimeError(
            f"Could not parse Stockfish version from output. Output head:\n{lines[:3]}"
        )

    # Check if the pinned version string is present in the version line
    if PINNED_STOCKFISH_VERSION not in version_line:
        raise RuntimeError(
            f"Stockfish version mismatch! Pinned version is '{PINNED_STOCKFISH_VERSION}', "
            f"but detected binary version is: '{version_line}'."
        )

    # Successfully verified, report it to stderr/stdout
    print(f"Stockfish verification successful: {version_line}", file=sys.stderr)
    return version_line
