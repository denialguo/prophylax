import os
import unittest
import pytest
import shutil

from config import verify_engine
from config.settings import get_limits, get_stockfish_path, PINNED_STOCKFISH_VERSION

class TestConfig(unittest.TestCase):

    def setUp(self):
        # Store original environment to restore in tearDown
        self.original_env = dict(os.environ)

    def tearDown(self):
        # Restore original environment
        os.environ.clear()
        os.environ.update(self.original_env)

    def set_env(self, key, value):
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = str(value)

    def test_verify_engine_missing_path(self):
        self.set_env("STOCKFISH_PATH", None)
        with self.assertRaises(RuntimeError) as context:
            verify_engine()
        self.assertIn("STOCKFISH_PATH environment variable is not set", str(context.exception))

    def test_verify_engine_invalid_path(self):
        self.set_env("STOCKFISH_PATH", "/invalid/path/to/stockfish")
        with self.assertRaises(RuntimeError) as context:
            verify_engine()
        self.assertIn("Stockfish binary was not found or is not executable", str(context.exception))

    def test_verify_engine_mock_success(self):
        # Verify success when version matches (mocked output)
        self.set_env("STOCKFISH_PATH", "dummy_path")
        mock_output = "Stockfish 18 by the Stockfish developers\nuciok\n"
        version_line = verify_engine(mock_output=mock_output)
        self.assertIn("Stockfish 18", version_line)

    def test_verify_engine_mock_version_mismatch(self):
        # Verify failure when version mismatches (mocked output)
        self.set_env("STOCKFISH_PATH", "dummy_path")
        mock_output = "Stockfish 15 by the Stockfish developers\nuciok\n"
        with self.assertRaises(RuntimeError) as context:
            verify_engine(mock_output=mock_output)
        self.assertIn("Stockfish version mismatch!", str(context.exception))

    def test_limits_precedence_eval_path(self):
        # Evaluation path requires STOCKFISH_NODES
        self.set_env("STOCKFISH_NODES", "100000")
        self.set_env("STOCKFISH_MOVETIME", "1000")
        
        limits = get_limits(interactive=False)
        self.assertEqual(limits, {"nodes": 100000})

    def test_limits_precedence_eval_path_missing_nodes(self):
        # Evaluation path raises if STOCKFISH_NODES is missing (even if movetime is set)
        self.set_env("STOCKFISH_NODES", None)
        self.set_env("STOCKFISH_MOVETIME", "1000")
        
        with self.assertRaises(ValueError) as context:
            get_limits(interactive=False)
        self.assertIn("STOCKFISH_NODES must be configured for the evaluation/golden-dataset path", str(context.exception))

    def test_limits_precedence_interactive_path_movetime(self):
        # Interactive analysis prefers STOCKFISH_MOVETIME
        self.set_env("STOCKFISH_NODES", "100000")
        self.set_env("STOCKFISH_MOVETIME", "1000")
        
        limits = get_limits(interactive=True)
        self.assertEqual(limits, {"movetime": 1000})

    def test_limits_precedence_interactive_path_nodes_fallback(self):
        # Interactive analysis falls back to nodes if movetime is missing
        self.set_env("STOCKFISH_NODES", "50000")
        self.set_env("STOCKFISH_MOVETIME", None)
        
        limits = get_limits(interactive=True)
        self.assertEqual(limits, {"nodes": 50000})

    def test_limits_precedence_interactive_path_missing_both(self):
        # Raises error if neither is set
        self.set_env("STOCKFISH_NODES", None)
        self.set_env("STOCKFISH_MOVETIME", None)
        
        with self.assertRaises(ValueError) as context:
            get_limits(interactive=True)
        self.assertIn("Either STOCKFISH_NODES or STOCKFISH_MOVETIME must be configured", str(context.exception))

# Skip decorator condition for real binary execution tests
def has_real_stockfish():
    path = os.environ.get("STOCKFISH_PATH")
    if not path:
        return False
    return shutil.which(path) is not None

@pytest.mark.golden
@pytest.mark.skipif(not has_real_stockfish(), reason="skip-unless-STOCKFISH_PATH-is-set")
class TestRealStockfish(unittest.TestCase):
    def test_real_engine_verification(self):
        # Only run if a valid path to real Stockfish is set
        version_line = verify_engine()
        self.assertIn("Stockfish", version_line)
