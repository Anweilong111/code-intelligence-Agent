from code_intelligence_agent.search.candidate_diversity import candidate_diversity


def test_candidate_diversity_rejects_failed_source_fingerprint():
    old_source = "def f(value):\n    return value + 1\n"
    failed_source = "def f(value):\n    return value + 0\n"

    diversity = candidate_diversity(
        old_source=old_source,
        new_source=failed_source,
        failed_sources=[failed_source],
    )

    assert diversity.accepted is False
    assert diversity.reason == "matches_failed_source_fingerprint"
    assert diversity.novelty_score == 0.0


def test_candidate_diversity_scores_batch_edit_novelty():
    old_source = "def f(value):\n    return value + 1\n"
    first_source = "def f(value):\n    return value + 2\n"
    duplicate_source = "def f(value):\n    return value + 2  \n"
    distinct_source = (
        "def f(value):\n"
        "    if value is None:\n"
        "        return 0\n"
        "    return value + 2\n"
    )

    duplicate = candidate_diversity(
        old_source=old_source,
        new_source=duplicate_source,
        accepted_sources=[first_source],
    )
    distinct = candidate_diversity(
        old_source=old_source,
        new_source=distinct_source,
        accepted_sources=[first_source],
    )

    assert duplicate.accepted is False
    assert duplicate.reason == "low_novelty_against_batch"
    assert distinct.accepted is True
    assert distinct.novelty_score > duplicate.novelty_score
