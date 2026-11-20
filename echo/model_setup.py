from pathlib import Path
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


def download_model():
    directory = DATA_DIR / "model"
    directory.mkdir(parents=True, exist_ok=True)
    for name, remote in MODEL_FILES.items():
        target = directory / name
        if target.is_file():
            continue
        partial = target.with_suffix(".download")
        print(f"Downloading {name}...", flush=True)
        try:
            with urlopen(f"{BASE}/{remote}", timeout=90) as response, partial.open("wb") as output:
                copyfileobj(response, output)
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
    print("Local embedding model ready.")


if __name__ == "__main__":
    download_model()

