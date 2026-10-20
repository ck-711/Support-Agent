"""The larger support-document limits remain usable and bounded."""

import asyncio
from types import SimpleNamespace

import pytest

from app.services import support_documents


def test_upload_accepts_file_larger_than_old_ten_mebibyte_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """An image above the former upload cap reaches OCR."""
    monkeypatch.setattr(support_documents, "_ocr_image", lambda raw: "Cloud save settings")
    image = b"x" * (11 * 1024 * 1024)

    assert support_documents.parse_document_pages("large.png", image) == [(None, "Cloud save settings")]
    assert support_documents.MAX_UPLOAD_BYTES == 50 * 1024 * 1024


def test_pdf_above_old_page_limit_retains_page_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 150-page document parses while a 201-page document is rejected."""
    pages = [SimpleNamespace(extract_text=lambda: "Verified fact", images=[]) for _ in range(150)]
    monkeypatch.setattr(support_documents, "PdfReader", lambda raw: SimpleNamespace(pages=pages))

    parsed = support_documents.parse_document_pages("guide.pdf", b"pdf")

    assert len(parsed) == 150
    assert parsed[-1] == (150, "Verified fact")
    pages.extend(SimpleNamespace(extract_text=lambda: "Verified fact", images=[]) for _ in range(51))
    with pytest.raises(ValueError, match="200-page limit"):
        support_documents.parse_document_pages("guide.pdf", b"pdf")


def test_more_than_old_chunk_limit_can_be_indexed() -> None:
    """Long extracted text can produce more than 250 retrieval passages."""
    chunks = support_documents.split_document("Verified game fact. " * 20_000)

    assert 250 < len(chunks) <= support_documents.MAX_CHUNKS


def test_embedding_requests_respect_provider_ten_item_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Long documents reach the provider in batches of at most ten passages."""
    batches: list[int] = []
    updates: list[tuple[int, int]] = []

    class FakeClient:
        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    async def fake_batch(_client: FakeClient, texts: list[str]) -> list[list[float]]:
        batches.append(len(texts))
        return [[0.0] * 1024 for _ in texts]

    async def progress(done: int, total: int) -> None:
        updates.append((done, total))

    monkeypatch.setattr(support_documents.settings, "SUPPORT_EMBEDDING_API_KEY", "test-key")
    monkeypatch.setattr(support_documents, "AsyncOpenAI", lambda **_options: FakeClient())
    monkeypatch.setattr(support_documents, "_embed_batch", fake_batch)

    vectors = asyncio.run(support_documents.embed_texts(["passage"] * 25, progress=progress))

    assert len(vectors) == 25
    assert batches == [10, 10, 5]
    assert updates == [(10, 25), (20, 25), (25, 25)]
