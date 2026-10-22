from io import BytesIO

import pytest
from docx import Document

from app.worker import extract_text


def test_text_and_docx_extract():
    assert extract_text('note.md', '中文内容'.encode()) == '中文内容'
    document = Document()
    document.add_paragraph('合同第一条')
    output = BytesIO()
    document.save(output)
    assert '合同第一条' in extract_text('contract.docx', output.getvalue())


def test_unsupported_image_does_not_fake_text():
    with pytest.raises(ValueError):
        extract_text('image.png', b'not-image')
