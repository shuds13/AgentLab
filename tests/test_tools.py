import asyncio
import json


def payload(result):
    return json.loads(result["content"][0]["text"])


def test_remote_submit_and_collect_success(tools_lab):
    lab = tools_lab(remote=True)
    lab.globus.plan_success({"metric": 7}, task_id="task-7")

    submitted = asyncio.run(lab.tools.submit_job({"value": 7}))
    assert payload(submitted)["key"] == "value=7"
    assert lab.globus.submits[0][3]["nranks"] == 4
    assert len(lab.globus.executor_inits) == 1

    completed = payload(asyncio.run(lab.tools.get_completed_jobs({})))
    assert completed["completed"] == [{"metric": 7, "job_id": 1, "key": "value=7"}]
    assert completed["pending"] == []
    assert lab.tools.jobs_in_flight() == 0
    assert '"event": "submit"' in (lab.workspace / "jobs.jsonl").read_text()


def test_submit_failure_releases_claim(tools_lab):
    lab = tools_lab(remote=True)
    lab.globus.plan_submit_error(RuntimeError("endpoint down"))

    result = asyncio.run(lab.tools.submit_job({"value": 1}))

    assert result["is_error"]
    assert lab.tools.submit_count() == 0
    claims = (lab.workspace / "claims.jsonl").read_text()
    assert '"state": "claimed"' in claims
    assert '"state": "done"' in claims


def test_local_only_needs_no_user_file_and_exposes_local_tools(tools_lab):
    lab = tools_lab(remote=False, local=True)

    assert lab.tools.HAS_LOCAL
    assert not lab.tools.HAS_REMOTE
    assert lab.tools.ENDPOINT_ID == ""
    assert lab.tools.tool_names() == [
        "mcp__cas__submit_local",
        "mcp__cas__get_local_completed",
        "mcp__cas__notify",
        "mcp__cas__cycle_done",
        "mcp__cas__goal_met",
    ]


def test_backend_trouble_requires_all_pending_tasks_to_fail(tools_lab):
    lab = tools_lab(remote=True)
    lab.globus.plan_pending("failed-task")
    asyncio.run(lab.tools.submit_job({"value": 1}))
    lab.globus.task_statuses["failed-task"] = {"status": "failed"}
    assert "all 1 in-flight task(s) failed/lost" in lab.tools.backend_trouble()


def test_goal_met_stops_new_work(tools_lab):
    lab = tools_lab(remote=True)
    asyncio.run(lab.tools.goal_met({"reason": "enough evidence"}))
    refused = asyncio.run(lab.tools.submit_job({"value": 3}))
    assert lab.tools.goal_is_met() == "enough evidence"
    assert refused["is_error"]
    assert "winding down" in refused["content"][0]["text"]
