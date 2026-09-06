"""Regression cases for the optional machine retry check, without a scheduler."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import unittest

from runhand.submission import retry_decision


class SubmissionContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 5, 2, 0, 2, tzinfo=timezone.utc)
        self.identity = {
            "site": "test-site",
            "cluster": "cluster-a",
            "job_id": "1234",
            "submit_time": "2026-09-05T01:00:00+00:00",
        }
        self.query = {
            "schema": 2,
            "target": "/runs/example",
            "site": "test-site",
            "cluster": "cluster-a",
            "observed_at": "2026-09-05T02:00:01+00:00",
            "result": "available",
            "coverage": "complete",
            "attempts": [],
            "resolved_submission_keys": [],
        }
        self.request = {
            "target": "/runs/example",
            "scopes": [{"site": "test-site", "cluster": "cluster-a"}],
            "not_before": "2026-09-05T02:00:00+00:00",
            "queries": [self.query],
            "pending_keys": [],
            "unresolved_without_key": False,
        }

    def simulate_authorized_retry(self, request: dict) -> int:
        """The handoff calls its submit primitive only after an allowed result."""
        calls = []
        if retry_decision(request, now=self.now)["decision"] == "allowed":
            calls.append("submit")
        return len(calls)

    def test_fresh_complete_target_query_permits_one_call(self) -> None:
        self.assertEqual(self.simulate_authorized_retry(self.request), 1)

    def test_empty_queue_or_unavailable_query_cannot_authorize_retry(self) -> None:
        for result, coverage in (
            ("available", "partial"),
            ("available", "unknown"),
            ("unavailable", "complete"),
            ("unavailable", "unknown"),
        ):
            with self.subTest(result=result, coverage=coverage):
                self.query.update(result=result, coverage=coverage)
                self.assertEqual(self.simulate_authorized_retry(self.request), 0)
                self.assertEqual(
                    retry_decision(self.request, now=self.now)["decision"], "unknown"
                )

    def test_active_or_unresolved_attempt_prevents_another_submission(self) -> None:
        for state in ("active", "unresolved", "unknown"):
            with self.subTest(state=state):
                self.query["attempts"] = [
                    {"job_identity": self.identity, "state": state}
                ]
                self.assertEqual(self.simulate_authorized_retry(self.request), 0)
        self.query["attempts"][0]["state"] = "terminal"
        self.assertEqual(self.simulate_authorized_retry(self.request), 1)

    def test_lost_response_requires_positive_key_reconciliation(self) -> None:
        self.request["pending_keys"] = [
            {"site": "test-site", "cluster": "cluster-a", "key": "lost-call-key"}
        ]
        self.assertEqual(self.simulate_authorized_retry(self.request), 0)
        self.query["resolved_submission_keys"] = ["some-other-key"]
        self.assertEqual(self.simulate_authorized_retry(self.request), 0)
        self.query["resolved_submission_keys"] = ["lost-call-key"]
        self.assertEqual(self.simulate_authorized_retry(self.request), 1)

    def test_same_token_on_another_cluster_does_not_resolve_a_lost_call(self) -> None:
        self.request["pending_keys"] = [
            {"site": "test-site", "cluster": "cluster-a", "key": "lost-call-key"}
        ]
        self.request["scopes"].append({"site": "test-site", "cluster": "cluster-b"})
        other = copy.deepcopy(self.query)
        other.update(cluster="cluster-b", resolved_submission_keys=["lost-call-key"])
        self.request["queries"].append(other)
        self.assertEqual(self.simulate_authorized_retry(self.request), 0)

    def test_unknown_without_searchable_key_stays_unknown(self) -> None:
        self.request["unresolved_without_key"] = True
        self.assertEqual(self.simulate_authorized_retry(self.request), 0)
        self.assertEqual(
            retry_decision(self.request, now=self.now)["reason"],
            "unkeyed_submission_unresolved",
        )

    def test_query_must_cover_every_relevant_cluster(self) -> None:
        self.request["scopes"].append({"site": "test-site", "cluster": "cluster-b"})
        self.assertEqual(self.simulate_authorized_retry(self.request), 0)
        other = copy.deepcopy(self.query)
        other["cluster"] = "cluster-b"
        self.request["queries"].append(other)
        self.assertEqual(self.simulate_authorized_retry(self.request), 1)
        other["attempts"] = [
            {"job_identity": {**self.identity, "cluster": "cluster-b"}, "state": "active"}
        ]
        self.assertEqual(self.simulate_authorized_retry(self.request), 0)

    def test_wrong_target_cluster_or_old_observation_stays_unknown(self) -> None:
        for field, value in (
            ("target", "/runs/other"),
            ("cluster", "cluster-b"),
            ("observed_at", "2026-09-05T01:59:59+00:00"),
            ("observed_at", "2026-09-05T02:00:01"),
            ("schema", 1),
        ):
            with self.subTest(field=field, value=value):
                request = copy.deepcopy(self.request)
                request["queries"][0][field] = value
                self.assertEqual(self.simulate_authorized_retry(request), 0)

    def test_inconsistent_job_scope_cannot_authorize_retry(self) -> None:
        self.query["attempts"] = [
            {"job_identity": {**self.identity, "cluster": "cluster-b"}, "state": "terminal"}
        ]
        self.assertEqual(self.simulate_authorized_retry(self.request), 0)

    def test_incomplete_job_identity_cannot_clear_a_previous_attempt(self) -> None:
        for field in self.identity:
            with self.subTest(field=field):
                incomplete = dict(self.identity)
                del incomplete[field]
                self.query["attempts"] = [
                    {"job_identity": incomplete, "state": "terminal"}
                ]
                self.assertEqual(self.simulate_authorized_retry(self.request), 0)

    def test_missing_or_malformed_evidence_fails_closed(self) -> None:
        for evidence in (None, {}, {"target": "/runs/example"}):
            self.assertEqual(retry_decision(evidence)["decision"], "unknown")
        for field in ("result", "coverage", "attempts", "resolved_submission_keys"):
            request = copy.deepcopy(self.request)
            del request["queries"][0][field]
            self.assertEqual(self.simulate_authorized_retry(request), 0)
        for field in ("pending_keys", "unresolved_without_key"):
            request = copy.deepcopy(self.request)
            del request[field]
            self.assertEqual(self.simulate_authorized_retry(request), 0)

    def test_future_query_or_preflight_stays_unknown(self) -> None:
        self.query["observed_at"] = "2026-09-05T02:00:03+00:00"
        self.assertEqual(self.simulate_authorized_retry(self.request), 0)
        self.request["not_before"] = "2026-09-05T02:00:03+00:00"
        self.assertEqual(self.simulate_authorized_retry(self.request), 0)


if __name__ == "__main__":
    unittest.main()
