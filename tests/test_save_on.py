"""Offline wire-level tests against the exact pinned helper; no network access."""
import contextlib
import importlib.util
import io
from pathlib import Path
import struct
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("save_on", ROOT / "scripts/save-on.py")
save_on = importlib.util.module_from_spec(spec)
spec.loader.exec_module(save_on)
ENV = {"SERVER_RCON_HOST": "offline.invalid", "SERVER_RCON_PORT": "25575",
       "SERVER_RCON_PASSWORD": "secret-sentinel"}


class SaveOnTests(unittest.TestCase):
    def attempt(self, response=None, error=None, auth_failure=False):
        connect = Mock()
        # Replace socket creation BEFORE import (the helper binds its default).
        with patch("socket.create_connection", connect):
            helper = save_on.load_helper()
        received = bytearray(helper.rcon_packet(-1 if auth_failure else 1, 2, ""))
        if response is not None:
            received.extend(helper.rcon_packet(2, 0, response))
        sent = []

        def recv(count):
            if not received and error:
                raise error
            data = bytes(received[:count])
            del received[:count]
            return data

        connection = connect.return_value.__enter__ = Mock()
        sock = Mock()
        connection.return_value = sock
        connect.return_value.__exit__ = Mock(return_value=False)
        sock.recv.side_effect = recv
        sock.sendall.side_effect = sent.append
        out, err = io.StringIO(), io.StringIO()
        with patch.object(save_on, "load_helper", return_value=helper), \
             patch.dict("os.environ", ENV, clear=True), patch("sys.argv", ["save-on.py"]), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = save_on.main()
        connect.assert_called_once_with(("offline.invalid", 25575), timeout=10)
        packets = [(struct.unpack("<ii", data[4:12]), data[12:-2].decode()) for data in sent]
        self.assertEqual(packets, [((1, 3), "secret-sentinel")] +
                         ([] if auth_failure else [((2, 2), "save-on")]))
        self.assertNotIn("secret-sentinel", out.getvalue() + err.getvalue())
        return status, out.getvalue(), err.getvalue()

    def test_enabled(self):
        self.assertEqual(self.attempt("Automatic saving is now enabled"),
                         (0, "save-on: enabled\n", ""))

    def test_already_enabled(self):
        self.assertEqual(self.attempt("Saving is already turned on"),
                         (0, "save-on: already-enabled\n", ""))

    def test_unknown_empty_and_injected_responses_fail_closed(self):
        for response in ("", "Unknown command", "secret-sentinel", "Automatic saving is now enabled\nsecret-sentinel",
                         "Automatic saving is now enabled\n", "Saving is already turned off"):
            with self.subTest(response=response):
                self.assertEqual(self.attempt(response),
                                 (1, "", "save-on: unconfirmed; no retry attempted\n"))

    def test_transport_failure_after_send_never_retries(self):
        for error in (TimeoutError("secret-sentinel"), OSError("secret-sentinel"), EOFError("secret-sentinel")):
            with self.subTest(error=type(error).__name__):
                self.assertEqual(self.attempt(error=error),
                                 (1, "", "save-on: unconfirmed; no retry attempted\n"))

    def test_auth_failure_sends_no_command(self):
        self.assertEqual(self.attempt(auth_failure=True),
                         (1, "", "save-on: unconfirmed; no retry attempted\n"))

    def test_bad_config_and_arguments_do_not_connect(self):
        for env, args in (({}, ["save-on.py"]), (ENV, ["save-on.py", "stop"]),
                          (dict(ENV, SERVER_RCON_PORT="invalid"), ["save-on.py"])):
            with self.subTest(env=bool(env), args=args), patch("socket.create_connection") as connect:
                with patch.dict("os.environ", env, clear=True), patch("sys.argv", args), \
                     contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(save_on.main(), 1)
                connect.assert_not_called()

    def test_helper_integrity_checked_before_import(self):
        with patch.object(Path, "read_bytes", return_value=b"untrusted"), \
             patch("importlib.util.spec_from_file_location") as loader:
            with self.assertRaises(ValueError):
                save_on.load_helper()
            loader.assert_not_called()

    def test_workflow_contract(self):
        workflow = (ROOT / ".github/workflows/save-on.yml").read_text()
        self.assertIn("on:\n  workflow_dispatch:\npermissions:\n  contents: read\n", workflow)
        self.assertIn("github.ref == 'refs/heads/main'", workflow)
        self.assertIn("ref: f27f43679e34a4975e8e1b18cdf6528877c6e66d", workflow)
        self.assertEqual(workflow.count("run:"), 2)
        self.assertIn("run: python3 scripts/save-on.py", workflow)
        self.assertEqual(workflow.count("${{ secrets."), 1)
        self.assertIn("SERVER_RCON_PASSWORD: ${{ secrets.SERVER_RCON_PASSWORD }}", workflow)
        self.assertNotIn("inputs:", workflow)
        self.assertNotIn("secrets: inherit", workflow)


if __name__ == "__main__":
    unittest.main()
