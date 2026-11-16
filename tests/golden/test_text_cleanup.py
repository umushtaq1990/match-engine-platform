"""Pure-logic tests for the text-cleanup helpers in app/golden/generate_cvs.py
and app/golden/generate_jobs.py -- no DB/LLM involved. These exist because a
real run hit both bugs live: fpdf2's core Helvetica font can't encode
Unicode bullets/smart quotes the LLM routinely writes (latin-1 only), and
the LLM writes Markdown even though the render target is a plain-text PDF
cell, not a Markdown renderer.
"""
from app.golden.generate_cvs import _sanitize_for_core_font
from app.golden.generate_cvs import _strip_markdown as _strip_markdown_cv
from app.golden.generate_jobs import _strip_markdown as _strip_markdown_job


def test_sanitize_replaces_common_unicode_punctuation():
    text = "Skilled in Python • Docker • React ’s ecosystem — expert level"

    result = _sanitize_for_core_font(text)

    assert "•" not in result
    assert "’" not in result
    assert "—" not in result
    assert result.encode("latin-1")  # does not raise


def test_sanitize_never_raises_on_arbitrary_unicode():
    text = "Emoji test \U0001F600 and CJK 中文"

    result = _sanitize_for_core_font(text)

    assert result.encode("latin-1")  # does not raise, even for untranslated chars


def test_strip_markdown_removes_bold_markers():
    text = "**Senior Engineer**\nWorked with **Python** and **Docker**."

    for strip in (_strip_markdown_cv, _strip_markdown_job):
        result = strip(text)
        assert "**" not in result
        assert "Senior Engineer" in result
        assert "Python" in result


def test_strip_markdown_removes_heading_markers():
    text = "# Professional Summary\nSome text here.\n## Skills\nMore text."

    for strip in (_strip_markdown_cv, _strip_markdown_job):
        result = strip(text)
        assert "#" not in result
        assert "Professional Summary" in result
        assert "Skills" in result
