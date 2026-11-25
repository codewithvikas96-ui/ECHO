from pathlib import Path
from hashlib import file_digest
from urllib.request import urlopen
from shutil import copyfileobj
import os

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("ECHO_DATA_DIR", ROOT / ".echo")).resolve()
MODEL_ID = "all-MiniLM-L6-v2"
REVISION = "d83dd37"
BASE = f"https://huggingface.co/sentence-transformers/{MODEL_ID}/resolve/{REVISION}"
MODEL_FILES = {
    "tokenizer.json": "tokenizer.json",
    "model.onnx": "onnx/model.onnx?download=true",
}
RERANKER_ID = "cross-encoder/ms-marco-MiniLM-L6-v2"
RERANKER_BASE = f"https://huggingface.co/{RERANKER_ID}/resolve/9cc09816f0a7a8aaceefa40a3cfbb843c1bfa579"
RERANKER_FILES = {
    "tokenizer.json": ("tokenizer.json", "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66"),
    "model.onnx": ("onnx/model.onnx?download=true", "5d3e70fd0c9ff14b9b5169a51e957b7a9c74897afd0a35ce4bd318150c1d4d4a"),
}


def download_file(target, url, checksum=None):
    if target.is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".download")
    print(f"Downloading {target.parent.name}/{target.name}...", flush=True)
    try:
        with urlopen(url, timeout=90) as response, partial.open("wb") as output:
            copyfileobj(response, output)
        if checksum:
            with partial.open("rb") as downloaded:
                if file_digest(downloaded, "sha256").hexdigest() != checksum:
                    raise ValueError(f"Model checksum mismatch for {target.name}.")
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)


def reranker_ready(directory: Path) -> bool:
    return all((directory / "reranker" / name).is_file() for name in RERANKER_FILES)


def download_model():
    for name, remote in MODEL_FILES.items():
        download_file(DATA_DIR / "model" / name, f"{BASE}/{remote}")
    for name, (remote, checksum) in RERANKER_FILES.items():
        download_file(DATA_DIR / "reranker" / name, f"{RERANKER_BASE}/{remote}", checksum)
    print("Local embedding and reranking models ready.")


if __name__ == "__main__":
    download_model()

