"""Settings precedence for the two optional Gemini auth paths."""

from __future__ import annotations

from app.config import Settings


def fresh(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_no_credentials_means_no_backend():
    s = fresh()
    assert s.ai_backend is None
    assert s.ai_configured is False


def test_api_key_alone_selects_api_key_backend():
    s = fresh(gemini_api_key="test-key")
    assert s.ai_backend == "api_key"
    assert s.ai_configured is True


def test_vertex_project_alone_selects_vertex_backend():
    s = fresh(vertex_project="my-project")
    assert s.ai_backend == "vertex"
    assert s.ai_configured is True


def test_vertex_takes_priority_when_both_are_set():
    """Arbitrary but needs to be deterministic -- a developer who has both
    configured (e.g. testing a migration) should get one predictable answer,
    not whichever happened to be checked first by accident."""
    s = fresh(vertex_project="my-project", gemini_api_key="test-key")
    assert s.ai_backend == "vertex"


def test_blank_strings_do_not_count_as_configured():
    s = fresh(vertex_project="   ", gemini_api_key="")
    assert s.ai_backend is None
    assert s.ai_configured is False


def test_flags_still_gate_availability_even_when_configured():
    """Having a credential is necessary but not sufficient -- each feature
    needs its own explicit enable flag."""
    s = fresh(vertex_project="my-project")
    assert s.ai_configured is True
    assert s.ai_segmentation_available is False
    assert s.ai_render_available is False

    s = fresh(vertex_project="my-project", enable_ai_segmentation=True)
    assert s.ai_segmentation_available is True
    assert s.ai_render_available is False
