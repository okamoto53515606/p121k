import pipeline




def test_reset_solution_deletes_the_whole_solution(monkeypatch):
    """Personalize has no DeleteSolutionVersion API; a full reset is the only way
    to stop old (pre-withdrawal) models from lingering."""
    calls = {}

    class Fake:
        def delete_solution(self, solutionArn):
            calls["arn"] = solutionArn

    monkeypatch.setattr(pipeline, "_p", Fake())
    out = pipeline.reset_solution({"solution_arn": "arn:solution"})
    assert calls["arn"] == "arn:solution" and out["solution_reset"]["deleted"] is True


def test_reset_solution_tolerates_a_concurrent_delete(monkeypatch):
    class Fake:
        def delete_solution(self, solutionArn):
            raise RuntimeError("ResourceInUseException")

    monkeypatch.setattr(pipeline, "_p", Fake())
    out = pipeline.reset_solution({"solution_arn": "arn:solution"})
    assert out["solution_reset"]["deleted"] is False and "ResourceInUse" in out["solution_reset"]["error"]
