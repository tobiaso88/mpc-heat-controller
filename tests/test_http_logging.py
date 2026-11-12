import unittest
from unittest.mock import Mock

from app.server import Handler


class HTTPLoggingTests(unittest.TestCase):
    def test_successful_get_requests_are_quiet(self):
        handler = object.__new__(Handler)
        handler.command = 'GET'
        handler.log_message = Mock()
        for code in (200, '204', 304):
            handler.log_request(code)
        handler.log_message.assert_not_called()

    def test_failed_get_and_other_requests_are_logged(self):
        handler = object.__new__(Handler)
        handler.requestline = 'GET /api/model/export HTTP/1.1'
        handler.log_message = Mock()
        for method, code in (('GET', 404), ('GET', '503'), ('GET', '-'), ('POST', 200)):
            handler.command = method
            handler.log_request(code)
        self.assertEqual(handler.log_message.call_count, 4)

    def test_direct_error_messages_are_preserved(self):
        handler = object.__new__(Handler)
        handler.log_message = Mock()
        handler.log_error('problem: %s', 'invalid request')
        handler.log_message.assert_called_once_with('problem: %s', 'invalid request')
