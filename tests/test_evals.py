"""Regression guard: the eval suite must stay at or above its current accuracy."""
import pytest


def test_eval_accuracy_does_not_regress():
    psycopg2 = pytest.importorskip("psycopg2")
    try:
        psycopg2.connect("postgresql://postgres:postgres@localhost:5432/postgres").close()
    except psycopg2.OperationalError:
        pytest.skip("Postgres not reachable")
    from evals.run_evals import run
    s = run("pytest")
    assert s["accuracy"] >= 0.95, s["failures"]
    assert s["accuracy_by_type"]["idk"] == 1.0, "never answer when it should say I don't know"
    assert s["citation_hit_sop"] == 1.0
