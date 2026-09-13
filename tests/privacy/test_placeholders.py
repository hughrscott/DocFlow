"""P02: typed random placeholders are stable within a job and differ across jobs."""
from __future__ import annotations

import copy
import pickle

import pytest

from docflow.privacy.placeholders import PLACEHOLDER_RE, PlaceholderMap
from docflow.privacy.types import SensitiveType
from tests.privacy.sentinels import USER_NAME


def test_tokens_are_typed_random_and_stable_within_one_job() -> None:
    job = PlaceholderMap()
    token = job.token_for(SensitiveType.PERSON, USER_NAME)
    assert PLACEHOLDER_RE.fullmatch(token)
    assert token.startswith("PERSON_")
    assert job.token_for(SensitiveType.PERSON, USER_NAME.upper().replace(" ", "  ")) == token
    assert job.token_for(SensitiveType.ENTITY, USER_NAME) != token
    assert PlaceholderMap().token_for(SensitiveType.PERSON, USER_NAME) != token
    assert job.rehydrate(f"for {token}.") == f"for {USER_NAME}."
    assert job.type_of(token) is SensitiveType.PERSON
    assert job.type_of("PERSON_bcdfghjkmnpq") is None


def test_lookup_is_memory_only_and_never_rendered() -> None:
    job = PlaceholderMap()
    job.token_for(SensitiveType.PERSON, USER_NAME)
    assert USER_NAME not in repr(job) and USER_NAME not in str(job)
    with pytest.raises(TypeError):
        pickle.dumps(job)
    with pytest.raises(TypeError):
        copy.deepcopy(job)
