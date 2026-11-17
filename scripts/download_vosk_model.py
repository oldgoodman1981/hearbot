"""Скачивает и распаковывает офлайн-модель Vosk для русского языка.

Примеры:

    python scripts/download_vosk_model.py
    python scripts/download_vosk_model.py --model vosk-model-ru-0.42
    python scripts/download_vosk_model.py --force

Модели и их размеры: https://alphacephei.com/vosk/models
"""

from __future__ import annotations

import argparse
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = "vosk-model-small-ru-0.22"
DEFAULT_URL = "https://alphacephei.com/vosk/models/{model}.zip"


def _human_size(size: float) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ГБ"


def download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as response, target.open("wb") as handle:
        total = int(response.headers.get("Content-Length") or 0)
        received = 0
        while True:
            chunk = response.read(1024 * 256)
            if not chunk:
                break
            handle.write(chunk)
            received += len(chunk)
            if total:
                percent = received * 100 / total
                print(
                    f"\r{percent:5.1f}%  {_human_size(received)} из {_human_size(total)}",
                    end="",
                    flush=True,
                )
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Скачать модель Vosk")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="имя модели")
    parser.add_argument("--url", default=None, help="прямая ссылка на zip")
    parser.add_argument(
        "--dest",
        default=str(PROJECT_ROOT / "models"),
        help="каталог для распаковки (по умолчанию models/)",
    )
    parser.add_argument(
        "--force", action="store_true", help="скачать заново, если модель уже есть"
    )
    args = parser.parse_args()

    dest = Path(args.dest)
    model_dir = dest / args.model
    if model_dir.is_dir() and not args.force:
        print(f"Модель уже на месте: {model_dir}")
        return 0

    url = args.url or DEFAULT_URL.format(model=args.model)
    archive = dest / f"{args.model}.zip"

    print(f"Скачивание: {url}")
    try:
        download(url, archive)
    except Exception as exc:  # noqa: BLE001 - показываем причину пользователю
        print(f"Не удалось скачать модель: {exc}", file=sys.stderr)
        return 1

    print(f"Распаковка в {dest} …")
    try:
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(dest)
    except zipfile.BadZipFile as exc:
        print(f"Архив повреждён: {exc}", file=sys.stderr)
        return 1
    finally:
        archive.unlink(missing_ok=True)

    if not model_dir.is_dir():
        print(
            f"В архиве нет каталога {args.model}. Проверьте содержимое {dest}.",
            file=sys.stderr,
        )
        return 1

    print(f"Готово: {model_dir}")
    print("Укажите путь в .env:")
    print(f"VOSK_MODEL_PATH={model_dir.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
