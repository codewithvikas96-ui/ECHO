# ECHO

ECHO is a local personal search engine and an Avishkar prototype. It finds passages in the documents, notes, and webpages you choose to index. You can search by meaning, switch to a keyword baseline, and open the source that a result came from.

## Run on Windows

Use **Python 3.13** and **Node.js 22 or later**. From PowerShell in this folder, run the first-time setup and launch:

~~~powershell
.\start.ps1 -Setup
~~~

Open [ECHO on localhost](http://127.0.0.1:8000). After setup, run **.\start.ps1** to start ECHO again. Use **.\start.ps1 -Port 8001** if port 8000 is already in use. Press Ctrl+C in the terminal to stop it. Setup downloads the dependencies and the roughly 91 MB embedding model. Once they are installed, importing and searching work offline.

## Try the demo

To test from another device, follow the [phone setup and port guide](docs/phone.md).

1. Sign in or select **Create an account**, then select **Load sample archive** on Search or Settings.
2. Try **How can I remember what I study for longer?** Open a result to see the passage that matched.
3. Switch between **Context-aware**, **Semantic**, and **Keyword**. Use **Filters** to restrict results by source type or source date.
4. Select **Add source** to upload a PDF or text file, write a note, or paste webpage text. You can attach the original webpage URL or upload saved HTML. DOCX and Markdown are also supported. PNG, JPEG, and WebP imports use Tesseract OCR when it is installed and available on PATH.
5. Use **Library** to inspect, open, or remove indexed sources. ECHO retains a local copy of uploaded PDFs, text files, DOCX files, and screenshots. Uploaded HTML is represented by its extracted content and optional original URL.
6. In **Evaluation**, select **Run benchmark** to compare the search modes. The benchmark uses 12 authored sources and 12 labelled queries in an isolated archive and reports Precision@3, Recall@3, MRR@3, nDCG@3, and search latency. These scores describe the sample corpus.

## How retrieval works

- The FastAPI backend extracts text, normalizes it, and preserves PDF page numbers. For longer sections it uses overlapping windows of up to 192 model tokens.
- [all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) runs through ONNX Runtime on the CPU. ECHO mean-pools the token outputs and normalizes the resulting 384-dimensional vectors.
- SQLite stores sources, extracts, and vectors together. The FAISS index is rebuilt from the committed rows when the archive changes. Keyword search uses SQLite FTS5 and BM25. Context-aware ranking combines cosine similarity, BM25, and title overlap, with explicit source-type and date filters. Each source appears once, with its best-ranked passage.
- The React and TypeScript interface is built with Vite and served from the same loopback server as the API. Each account has a separate archive and search index. Data is stored under **.echo/users/** and the shared model under **.echo/model/**. Old installations keep their original **.echo/archive.sqlite3** on disk. Since earlier sources have no recorded owner, ECHO leaves them offline. Users can import their own original files into their new accounts. The app stores no query history.

The MVP includes manual webpage capture and local file imports. The Chrome extension, automatic browser-history import, and mobile app are future work. The embedding model is intended for English. PDFs need a text layer, and screenshot quality affects OCR.

## Development

In Context-aware and Keyword modes, enter a full filename or title to prioritize an exact match. Put a phrase in double quotes to require that word sequence in a passage, title, or filename. Context-aware search also gives matching sources a small boost for requests such as “find my note about ventilation.” The source-type filter restricts results when you need a specific format.

Run the backend using **.\start.ps1** and run **npm.cmd run dev** in a second terminal for the Vite development server. The Vite proxy expects the backend on port 8000. Run **npm.cmd run build** to type-check and rebuild the production frontend. The local API documentation is available at [ECHO API docs](http://127.0.0.1:8000/docs).

To run the benchmark without the browser, use **.\.venv\Scripts\python.exe -m echo.demo**.

The dashboard artwork was made with the built-in ImageGen tool from a prompt for an electric-cobalt sculpture of concentric grooves, viewed at an angle against a deep indigo backdrop.

After first-time setup, install the test dependencies with `.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt`, then run `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`.
