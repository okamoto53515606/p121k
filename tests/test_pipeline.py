import pipeline




def test_prune_versions_keeps_current_and_tolerates_in_use(monkeypatch):
    class Fake:
        def list_solution_versions(self, solutionArn):
            return {"solutionVersions": [{"solutionVersionArn": "arn:keep"},
                                         {"solutionVersionArn": "arn:old1"},
                                         {"solutionVersionArn": "arn:old2"}]}

        def delete_solution_version(self, solutionVersionArn):
            if solutionVersionArn == "arn:old2":
                raise RuntimeError("ResourceInUse")

    monkeypatch.setattr(pipeline, "_p", Fake())
    out = pipeline.prune_versions({"solution_arn": "arn:solution", "solution_version_arn": "arn:keep"})
    assert out["pruned"]["deleted"] == 1 and len(out["pruned"]["failed"]) == 1
