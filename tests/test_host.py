from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from blendlet.host import Host, get_host, reset_host


class HostLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.host = Host()
        self.directory = Path(tempfile.mkdtemp()).resolve()

    def tearDown(self) -> None:
        if self.host.running:
            self.host.stop()
        reset_host()

    def test_start_exposes_running_directory(self):
        self.host.start(self.directory)
        self.assertTrue(self.host.running)
        self.assertEqual(self.host.blendlets_directory, self.directory)

    def test_stop_clears_running_state_and_directory(self):
        self.host.start(self.directory)
        self.host.stop()
        self.assertFalse(self.host.running)
        self.assertIsNone(self.host.blendlets_directory)

    def test_stop_is_idempotent(self):
        self.host.start(self.directory)
        self.host.stop()
        self.host.stop()
        self.assertFalse(self.host.running)
        self.assertIsNone(self.host.blendlets_directory)

    def test_start_while_running_goes_through_cleanup(self):
        other = Path(tempfile.mkdtemp()).resolve()
        self.host.start(self.directory)
        self.host.start(other)
        self.assertTrue(self.host.running)
        self.assertEqual(self.host.blendlets_directory, other)
        self.host.stop()
        self.assertFalse(self.host.running)
        self.assertIsNone(self.host.blendlets_directory)

    def test_start_rejects_relative_directory(self):
        with self.assertRaises(ValueError):
            self.host.start(Path("relative-blendlets"))
        self.assertFalse(self.host.running)
        self.assertIsNone(self.host.blendlets_directory)

    def test_set_directory_while_running(self):
        other = Path(tempfile.mkdtemp()).resolve()
        self.host.start(self.directory)
        self.host.set_blendlets_directory(other)
        self.assertEqual(self.host.blendlets_directory, other)
        self.assertTrue(self.host.running)

    def test_set_directory_while_stopped_raises(self):
        with self.assertRaises(RuntimeError):
            self.host.set_blendlets_directory(self.directory)

    def test_get_host_returns_process_singleton(self):
        reset_host()
        first = get_host()
        second = get_host()
        self.assertIs(first, second)
        first.start(self.directory)
        self.assertTrue(second.running)
        reset_host()
        self.assertFalse(first.running)
        self.assertIsNot(get_host(), first)
