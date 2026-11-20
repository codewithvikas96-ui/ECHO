import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from echo import accounts, server
from echo.archive import Archive
from echo.embeddings import Embedder
from echo.extract import Document
from echo.model_setup import DATA_DIR


class PrivateArchiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_account_boundary(self):
        folder = self.enterContext(TemporaryDirectory())
        root = Path(folder)
        model = Embedder(DATA_DIR)
        # The legacy database contains unowned data even before the first account registers.
        Archive(root, model).add(Document("Legacy file", "note", [(None, "Earlier shared source")]))
        for module in (accounts, server):
            self.enterContext(patch.object(module, "DATA_DIR", root))
        self.enterContext(patch.object(server, "shared_model", return_value=model))
        self.enterContext(patch.object(server, "require_model"))
        server.account_archive.cache_clear()
        self.addCleanup(server.account_archive.cache_clear)
        accounts.initialize()

        def client():
            return httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost",
                                     headers={"X-Echo-Request": "1"})

        alice = await self.enterAsyncContext(client())
        bob = await self.enterAsyncContext(client())
        anonymous = await self.enterAsyncContext(client())
        account = {"username": "alice", "password": "temporary test passphrase"}
        registered = await alice.post("/api/account/register", json=account)
        self.assertEqual(registered.status_code, 201)
        self.assertIn("httponly", registered.headers["set-cookie"].lower())
        self.assertEqual((await alice.get("/api/sources")).json(), [])
        self.assertEqual((await bob.post("/api/account/register", json={**account, "username": "bob"})).status_code, 201)
        file = {"file": ("private.txt", b"Alpine glacier field notes")}
        response = await alice.post("/api/sources/import", files=file)
        self.assertEqual(response.status_code, 200, response.text)
        source_id = response.json()["id"]
        path = f"/api/sources/{source_id}"
        for method, url in (("GET", "/api/sources"), ("GET", path), ("GET", path + "/original"),
                            ("DELETE", path), ("POST", "/api/demo"), ("POST", "/api/evaluation")):
            self.assertEqual((await anonymous.request(method, url)).status_code, 401)
        self.assertEqual((await bob.get("/api/sources")).json(), [])
        for method, url in (("GET", path), ("GET", path + "/original"), ("DELETE", path)):
            self.assertEqual((await bob.request(method, url)).status_code, 404)
        # Both databases assign the same first ID. Each request must still use its own archive.
        other_file = b"Library opening hours"
        uploaded = await bob.post("/api/sources/import", files={"file": ("schedule.txt", other_file)})
        self.assertEqual(uploaded.json()["id"], source_id)
        self.assertEqual((await bob.get(path + "/original")).content, other_file)
        self.assertEqual((await bob.delete(path)).status_code, 204)
        self.assertEqual((await alice.get(path + "/original")).content, file["file"][1])
        for mode in ("contextual", "semantic", "keyword"):
            query = {"query": "Alpine glacier field notes", "mode": mode}
            self.assertEqual((await anonymous.post("/api/search", json=query)).status_code, 401)
            self.assertEqual((await bob.post("/api/search", json=query)).json()["results"], [])
            self.assertEqual((await alice.post("/api/search", json=query)).json()["results"][0]["id"], source_id)
        self.assertEqual((await bob.post("/api/demo")).status_code, 200)
        self.assertEqual(len((await alice.get("/api/sources")).json()), 1)
        self.assertTrue((await bob.post("/api/sources/import", files=file)).json()["created"])
        self.assertEqual((await bob.get(path, headers={"X-Echo-Account": "alice"})).status_code, 401)
        self.assertEqual((await alice.get(path + "/original")).content, file["file"][1])
        old_cookie = alice.cookies.get(accounts.COOKIE)
        self.assertEqual((await alice.post("/api/account/logout")).status_code, 204)
        anonymous.cookies.set(accounts.COOKIE, old_cookie)
        self.assertEqual((await anonymous.get(path)).status_code, 401)
        self.assertEqual((await alice.post("/api/account/login", json={**account, "password": "wrong"})).status_code, 401)
        self.assertEqual((await alice.post("/api/account/login", json=account)).status_code, 200)
        self.assertEqual((await alice.get(path + "/original")).content, file["file"][1])
