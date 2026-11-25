import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
from reportlab.pdfgen.canvas import Canvas

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
        selected_file = {"file": ("itinerary.txt", b"Sunflower itinerary for the expedition")}
        self.assertEqual((await anonymous.post("/api/sources/index-file", files=selected_file)).status_code, 401)
        self.assertEqual((await alice.post("/api/sources/index-file", files=selected_file,
                                           headers={"X-Echo-Account": "bob"})).status_code, 401)
        indexed = await alice.post("/api/sources/index-file", files=selected_file)
        self.assertEqual(indexed.status_code, 200, indexed.text)
        indexed_id = indexed.json()["id"]
        indexed_path = f"/api/sources/{indexed_id}"
        self.assertFalse((await alice.get(indexed_path)).json()["has_original"])
        self.assertTrue((await alice.get(path)).json()["has_original"])
        self.assertEqual((await alice.get(indexed_path + "/original")).status_code, 404)
        lookup = {"query": "sunflower itinerary", "mode": "semantic"}
        self.assertEqual((await alice.post("/api/search", json=lookup)).json()["results"][0]["id"], indexed_id)
        self.assertEqual((await bob.post("/api/search", json=lookup)).json()["results"], [])
        self.assertFalse((await alice.post("/api/sources/index-file", files=selected_file)).json()["created"])
        self.assertEqual((await alice.delete(indexed_path)).status_code, 204)
        self.assertEqual((await alice.get(indexed_path)).status_code, 404)
        remote = await self.enterAsyncContext(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app, client=("192.0.2.9", 12345)), base_url="http://localhost",
            headers={"X-Echo-Request": "1"}, cookies=alice.cookies))
        self.assertEqual((await remote.post("/api/sources/index-file", files=selected_file)).status_code, 403)
        secure = await remote.post("https://localhost/api/sources/index-file", files=selected_file)
        self.assertEqual(secure.status_code, 200)
        self.assertEqual((await alice.delete(f"/api/sources/{secure.json()['id']}")).status_code, 204)
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

        other = await self.enterAsyncContext(client())
        self.assertEqual((await other.post("/api/account/register", json={**account, "username": "carol"})).status_code, 201)
        for retain in (True, False):
            pdf = BytesIO()
            canvas = Canvas(pdf)
            word = "heliostat" if retain else "anemometer"
            canvas.drawString(50, 400, "Measurements from the " + word)
            canvas.save()
            name = word + ".pdf"
            original = pdf.getvalue()
            files = {"file": (name, original)}
            route = "/api/sources/import" if retain else "/api/sources/index-file"
            legacy = Document(name, "pdf", [(1, "Legacy footer " + word[:3])], filename=name, original=original)
            with patch.object(server, "from_file", return_value=legacy):
                result = await alice.post(route, files=files)
            self.assertEqual(result.status_code, 200, result.text)
            pdf_id = result.json()["id"]
            pdf_path = f"/api/sources/{pdf_id}"
            endpoint = pdf_path + "/reindex-pdf"
            self.assertEqual((await anonymous.post(endpoint)).status_code, 401)
            self.assertEqual((await other.post(endpoint)).status_code, 404)
            if retain:
                refreshed = await alice.post(endpoint)
                self.assertEqual((await alice.get(pdf_path + "/original")).content, original)
            else:
                self.assertEqual((await alice.post(endpoint)).status_code, 422)
                form = {"refresh_pdf_id": str(pdf_id)}
                wrong = await alice.post(route, data=form, files={"file": ("different.pdf", original)})
                self.assertEqual(wrong.status_code, 422)
                self.assertIn("Legacy footer", (await alice.get(pdf_path)).json()["passages"][0]["text"])
                refreshed = await alice.post(route, data=form, files=files)
                self.assertEqual((await alice.get(pdf_path + "/original")).status_code, 404)
            self.assertEqual(refreshed.status_code, 200, refreshed.text)
            self.assertEqual(refreshed.json()["id"], pdf_id)
            detail = (await alice.get(pdf_path)).json()
            self.assertEqual(detail["has_original"], retain)
            self.assertIn(word, detail["passages"][0]["text"])
