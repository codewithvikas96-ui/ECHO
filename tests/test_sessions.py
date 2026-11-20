from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from starlette.requests import Request
from starlette.responses import Response
from http.cookies import SimpleCookie

from echo import accounts


class SessionExpiryTests(unittest.TestCase):
    def test_session_expires_at_the_deadline(self):
        with TemporaryDirectory() as folder, patch.object(accounts, "DATA_DIR", Path(folder)):
            accounts.initialize()
            scope = {"type": "http", "scheme": "http", "path": "/", "headers": [], "query_string": b""}
            response = Response()
            with closing(accounts.connect()) as db, db, patch.object(accounts, "time", return_value=100):
                db.execute("INSERT INTO users VALUES (?,?,?,?)", ("local-id", "reader", b"", b""))
                accounts.issue_session(db, "local-id", Request(scope), response)
            cookie = SimpleCookie(response.headers["set-cookie"])[accounts.COOKIE]
            request = Request({**scope, "headers": [(b"cookie", cookie.OutputString(attrs=[]).encode())]})
            deadline = 100 + accounts.SESSION_SECONDS
            with patch.object(accounts, "time", return_value=deadline - 1):
                self.assertEqual(accounts.session_user(request), ("local-id", "reader"))
            with patch.object(accounts, "time", return_value=deadline):
                self.assertIsNone(accounts.session_user(request))
