import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from admin.platform.workbuddy.core.credential_codec import _workbuddy_executable


class WorkbuddyExecutableTests(unittest.TestCase):
    def test_prefers_workbuddy_exe(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'WorkBuddy.exe').write_bytes(b'')
            (root / 'WorkBuddyAI.exe').write_bytes(b'')
            self.assertEqual(_workbuddy_executable(root).name, 'WorkBuddy.exe')

    def test_falls_back_to_workbuddyai_exe(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'WorkBuddyAI.exe').write_bytes(b'')
            self.assertEqual(_workbuddy_executable(root).name, 'WorkBuddyAI.exe')

    def test_missing_executable(self):
        with TemporaryDirectory() as tmp:
            self.assertIsNone(_workbuddy_executable(Path(tmp)))
        self.assertIsNone(_workbuddy_executable(None))
