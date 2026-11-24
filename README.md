# ECHO

ECHO is a local-first search engine for a personal archive, built as an undergraduate Avishkar project in Engineering and Technology. Import a document, screenshot, note, or saved webpage, then search for the passage you remember. Each account has its own library on the computer running ECHO.

## Clone and run on Windows

Use **Git**, **64-bit Python 3.13**, and **Node.js 22.12 or later with npm**. The first setup needs internet access to install dependencies and download the English embedding model (about 91 MB). Importing and searching then run locally on the CPU. Screenshot import also needs Tesseract with English language data.

**1. Check the tools.** Open PowerShell or the PowerShell terminal in VS Code.

```powershell
git --version
python --version
node --version
npm.cmd --version
```

**2. Clone the repository.** Run these commands from the folder where you keep projects.

```powershell
git clone https://github.com/codewithvikas96-ui/ECHO.git
cd ECHO
```

**3. Install and start ECHO.** From the cloned folder, run the setup script.

```powershell
.\start.ps1 -Setup
```

The script creates `.venv`, installs the Python requirements, downloads the model and tokenizer, installs the locked frontend dependencies, builds the React app, and starts the server. Leave that terminal open. When Uvicorn reports that it is running, open [ECHO on your PC](http://127.0.0.1:8000).

If Windows has several Python versions and `python --version` points to a different one, run `py -3.13 -m venv .venv` from the clone before starting setup. The script will use that environment.

**4. Create your account.** Choose **Create an account**, enter a username and a password of at least 12 characters, and open your library. A fresh clone starts with an empty library.

**5. Try a search.** Choose **Load sample archive**, select **Context-aware**, and search for `How can I remember what I study for longer?`. Open a result such as **Sleep and memory consolidation** to read the matched passage.

### Start, stop, or change ports

On later runs, start ECHO from the repository folder with `.\start.ps1`. Press **Ctrl+C** in its terminal to stop it. To use a different local port, run `.\start.ps1 -Port 8010` and open [localhost on port 8010](http://127.0.0.1:8010).

Check the default server from a second PowerShell terminal.

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

A healthy setup reports `model_ready` as true. The separate `ocr_available` field reports whether ECHO found the Tesseract executable.

### Run on macOS or Linux

From a clone of the repository, the equivalent manual setup uses the same Python and Node.js versions. Run the following in a shell with Python 3.13 available as `python3.13`.

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m echo.model_setup
npm ci
npm run build
python -m uvicorn echo.server:app --host 127.0.0.1 --port 8000 --no-access-log
```

Open the localhost address and follow the account and first-search steps above. Windows is the platform used to verify this project.

## Use your own sources

| Source | How to add it |
| --- | --- |
| PDF, TXT, Markdown, Word (.docx) | Choose **Add source → Upload files**. Select one or several files, then choose **Add to library**. PDFs need selectable text. |
| Screenshot (PNG, JPEG, WebP) | Install Tesseract with English language data on the host PC. Check **Settings → Screenshot OCR**, then upload the image and search for a visible phrase. |
| Saved webpage | Upload a saved HTML/HTM file, or use **Save a webpage** to paste the text and optionally record its URL. |
| Note | Choose **Write a note**, give it a title and some text, then add it to the library. |

Each uploaded file can be up to 16 MiB. Use the source-type and date filters to narrow a search. Open **Library** to inspect indexed passages, retrieve a retained original, or remove a source. For a scanned PDF, export the relevant page as an image and import it with OCR. Search covers the sources you add to ECHO.

### Choose a search mode

| Mode | When to use it |
| --- | --- |
| **Context-aware** | Start here for a remembered idea. It combines passage and title meaning with keyword matches and source metadata. A request such as `find my note about ventilation` can favor notes. |
| **Semantic** | Use a paraphrase when you forget the original wording. Both descriptive titles and passage text contribute to the result. |
| **Keyword** | Use distinctive words, an exact filename, or a quoted phrase such as `"cross ventilation"`. |

Context-aware and Keyword modes prioritize an exact filename or title and respect quoted phrases. Each source appears once with its best-scoring passage. The **Title meaning** label indicates a semantic match driven by the title.

## Access from a phone

Connect the phone and the host PC to the same trusted Wi-Fi. From PowerShell in the project folder, enter the PC’s Wi-Fi IPv4 address when prompted.

```powershell
$echoAddress = Read-Host "PC Wi-Fi IPv4 address"
.\.venv\Scripts\python.exe -m echo.phone $echoAddress --port 8001
```

Open the HTTP URL printed in the terminal. First use the gateway credentials shown on the PC, then sign in to your personal ECHO account. Using the same account on the PC and phone opens the same library. Sign out through **Settings** on a shared device. [Phone setup and port troubleshooting](docs/phone.md)

## How retrieval works

1. **Extract passages.** The Python backend reads document text or invokes Tesseract for images. Long sections become passages of up to 192 tokens with a 32-token overlap. PDF passages retain their page number.
2. **Build the account index.** MiniLM (`all-MiniLM-L6-v2`) runs through ONNX Runtime. ECHO mean-pools and normalizes its output into 384-dimensional vectors for passages and titles. SQLite stores the originals where supported, metadata, passages, and vectors. FTS5 indexes titles and passage text for BM25 keyword ranking.
3. **Retrieve evidence.** FAISS compares the query vector with the account’s indexed vectors. For each passage, ECHO uses the stronger of its passage similarity and a blend of 80% title similarity and 20% passage similarity. A descriptive title can surface a relevant file even when its body contains boilerplate.
4. **Rank and display.** Context-aware mode combines that evidence with lexical and metadata scores. ECHO applies the selected source filters and relevance thresholds, then displays the best passage per source. React and TypeScript provide the browser interface, and FastAPI serves the UI and API.

### Storage and upgrades

The default data folder is `.echo/`. Each account’s archive is under `.echo/users/`; the shared model is under `.echo/model/`. The phone and desktop connect to the host’s data. ECHO does not save query history. Keep the data directory private when sharing a project copy.

For an existing checkout, stop ECHO, run `git pull --ff-only`, then `.\start.ps1 -Setup` to apply code, dependency, and frontend updates. The first Semantic or Context-aware search in an older account computes its missing title vectors. This can take longer on a large library. Existing passages and originals are preserved. Pre-account installations retain their unassigned archive in `.echo/archive.sqlite3`; users can import their own originals into their personal accounts.

## Evaluate and develop

Choose **Evaluation → Run benchmark** to compare all three search modes on the same 12 authored sources and 12 labelled queries in a temporary archive. The report includes Precision@3, Recall@3, MRR@3, nDCG@3, and latency. Run the same benchmark from PowerShell with `.\.venv\Scripts\python.exe -m echo.demo`.

| Retrieval check | Result |
| --- | --- |
| Six title-paraphrase queries against documents with generic bodies | Correct source ranked first in 6/6 queries in both Semantic and Context-aware modes, up from 0/6 with passage-only embeddings. Three unrelated queries still returned zero results in both modes. |
| Built-in benchmark, Recall@3 | Keyword 87.5%, Semantic 100%, Context-aware 100%, before and after the title change. |

These are small, authored evaluations. The title cases exercise a specific failure mode; OCR quality, language, archive size, and the kinds of questions people ask can change real-world results.

Install the test dependencies after first-time setup and run the suite.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

For frontend development, run `.\start.ps1` on port 8000, then `npm.cmd run dev` in a second terminal and open the Vite URL it prints. Vite proxies API calls to the backend on port 8000. Use `npm.cmd run build` to type-check and rebuild the production UI. [Local API documentation](http://127.0.0.1:8000/docs)

| Project path | Contents |
| --- | --- |
| `src/` | React interface, account screens, API client, and styles |
| `echo/server.py`, `echo/accounts.py` | FastAPI routes, account sessions, and access to private archives |
| `echo/extract.py`, `echo/ocr.py` | Document parsing, screenshot recognition, and passage extraction |
| `echo/embeddings.py`, `echo/archive.py` | Local inference, storage, indexing, and ranking |
| `echo/demo.py`, `tests/` | Sample corpus, retrieval evaluation, and regression tests |

### Troubleshooting

| Symptom | What to do |
| --- | --- |
| Windows reports error 10048 | Another process is listening on the port. Choose a different port with `.\start.ps1 -Port 8010`, or stop the earlier ECHO terminal with Ctrl+C. |
| The model is missing or setup was interrupted | From the clone, rerun `.\start.ps1 -Setup` with internet access. To fetch just the model after installing Python dependencies, run `.\.venv\Scripts\python.exe -m echo.model_setup`. |
| Screenshot OCR is unavailable | Install Tesseract with English language data on the host, make its executable available on PATH or in its standard Windows install folder, then restart ECHO. |
| A search returns no results | Open **Library** under the correct account and confirm the source was indexed. Clear any date or type filters. For an image, inspect the extracted passage to see which words OCR recognized. |

The dashboard ripple artwork was created with ImageGen for ECHO.
