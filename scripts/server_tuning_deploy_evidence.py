"""Classify whether 'Server update' actually deployed, from its own jobs list.

Reads the JSON body of GET /repos/{owner}/{repo}/actions/runs/{run_id}/jobs
(the GitHub Actions API) from stdin and prints deploy=true/false.

Only "server-update / Upload and restart server" succeeding counts as
positive evidence of an actual live restart. Deliberately NOT "server-update
/ deploy-method" (pure validation, never touches the server) and NOT
"server-update / Update server" (the Modrinth-API deploy method's own job can
succeed on a no-op "not configured, skipping" path without deploying
anything -- see server-update.yml's reusable-server-update.yml). Any sibling
job in a failure/cancelled/timed_out/action_required state disqualifies the
deploy even if the restart job itself reports success. Anything unreadable,
empty, or missing the expected shape fails CLOSED (deploy=false).
"""
import json
import sys

BAD_CONCLUSIONS = {"failure", "cancelled", "timed_out", "action_required"}
RESTART_JOB_NAME = "server-update / Upload and restart server"
SKIPPED_CALL_JOB_NAME = "server-update"


def deployed(jobs_json: str) -> bool:
    # Any unexpected shape at all -- not just the parse failures named
    # below -- fails CLOSED. This function decides whether a live SFTP
    # write to a running server happens; an exception must never be
    # allowed to propagate into "deploy=true" by accident.
    try:
        jobs = json.loads(jobs_json)["jobs"]
        if not isinstance(jobs, list) or not all(isinstance(j, dict) for j in jobs):
            return False

        # Unambiguous non-deployment: the reusable-workflow CALL job
        # itself (named exactly "server-update", never "server-update /
        # ...") only appears, and only skipped, when deployment-policy
        # decided deploy=false and the call never ran.
        if any(
            j.get("name") == SKIPPED_CALL_JOB_NAME and j.get("conclusion") == "skipped"
            for j in jobs
        ):
            return False

        restart_succeeded = any(
            j.get("name") == RESTART_JOB_NAME and j.get("conclusion") == "success"
            for j in jobs
        )
        bad_sibling = any(
            str(j.get("name", "")).startswith("server-update /")
            and j.get("conclusion") in BAD_CONCLUSIONS
            for j in jobs
        )
        return restart_succeeded and not bad_sibling
    except Exception:
        return False


if __name__ == "__main__":
    print("deploy=" + str(deployed(sys.stdin.read())).lower())
