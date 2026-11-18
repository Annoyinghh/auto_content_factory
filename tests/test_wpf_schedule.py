import subprocess
import unittest
from unittest.mock import patch

from core.wpf_schedule import _task_status


class WpfScheduleTests(unittest.TestCase):
    def test_reads_redirected_multibyte_task_xml_with_utf16_declaration(self):
        task = ('<?xml version="1.0" encoding="UTF-16"?>\r\n'
                '<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
                '<RegistrationInfo><Description>每日制作</Description></RegistrationInfo>'
                '<Settings><Enabled>false</Enabled></Settings></Task>')
        for encoding in ("utf-16", "utf-8", "cp936"):
            with self.subTest(encoding=encoding), patch("core.wpf_schedule.locale.getencoding", return_value="cp936"), \
                    patch("core.wpf_schedule._task_command", return_value=subprocess.CompletedProcess(
                        "schtasks", 0, task.encode(encoding), b"")):
                self.assertEqual(_task_status(), {"exists": True, "enabled": False})
