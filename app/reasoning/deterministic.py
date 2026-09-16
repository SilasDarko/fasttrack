"""Deterministic, network-free reasoning provider.

This is not a single hardcoded response: it inspects the tool outputs
accumulated so far in the investigation and branches accordingly, so it
genuinely exercises tool orchestration, evidence citation, and confidence
scoring the same way a real LLM-driven agent would -- just with a fixed
(and therefore fully reproducible) decision policy instead of a model call.

Policy, evaluated fresh on each call from the full history so far:
  1. search_telemetry(service)                         -- always first
  2. if telemetry shows error/critical events:
         search_deployments(service)
     else:
         retrieve_runbook(service, alert.message)
  3. if a correlated deployment was found: inspect_change(commit_sha)
  4. ensure retrieve_runbook has been called
  5. ensure search_prior_incidents has been called
  6. finalize once retrieve_runbook and search_prior_incidents both ran,
     or once 5 tool calls have been made (hard bound)
"""

from app.reasoning.base import AgentAction, FinalizeAction, InvestigationContext, ToolCallAction
from app.schemas import Diagnosis, EvidenceRef

_ELEVATED_LEVELS = {"error", "critical"}
_MAX_TOOL_CALLS = 5


def _called(context: InvestigationContext, tool_name: str) -> ToolCallAction | None:
    for record in context.history:
        if record.tool_name == tool_name:
            return record
    return None


def _successful_output(context: InvestigationContext, tool_name: str) -> dict | None:
    for record in context.history:
        if record.tool_name == tool_name and record.status == "success":
            return record.output
    return None


class DeterministicReasoningProvider:
    async def decide_next_action(self, context: InvestigationContext) -> AgentAction:
        if len(context.history) >= _MAX_TOOL_CALLS:
            return self._finalize(context)

        called_names = {record.tool_name for record in context.history}

        if "search_telemetry" not in called_names:
            return ToolCallAction(
                tool_name="search_telemetry",
                arguments={"service": context.alert.service, "since_minutes": 60, "limit": 10},
            )

        telemetry_out = _successful_output(context, "search_telemetry")
        has_elevated = bool(telemetry_out) and any(
            event["level"] in _ELEVATED_LEVELS for event in telemetry_out.get("results", [])
        )

        if has_elevated and "search_deployments" not in called_names:
            return ToolCallAction(
                tool_name="search_deployments",
                arguments={"service": context.alert.service, "within_minutes": 120, "limit": 10},
            )

        deployments_out = _successful_output(context, "search_deployments")
        correlated_deployment = None
        if deployments_out and deployments_out.get("results"):
            correlated_deployment = deployments_out["results"][0]

        if correlated_deployment and "inspect_change" not in called_names:
            return ToolCallAction(
                tool_name="inspect_change",
                arguments={"commit_sha": correlated_deployment["commit_sha"]},
            )

        if "retrieve_runbook" not in called_names:
            return ToolCallAction(
                tool_name="retrieve_runbook",
                arguments={
                    "service": context.alert.service,
                    "query": context.alert.message,
                    "limit": 5,
                },
            )

        if "search_prior_incidents" not in called_names:
            return ToolCallAction(
                tool_name="search_prior_incidents",
                arguments={
                    "service": context.alert.service,
                    "query": context.alert.message,
                    "limit": 5,
                },
            )

        return self._finalize(context)

    def _finalize(self, context: InvestigationContext) -> FinalizeAction:
        return FinalizeAction(diagnosis=_build_diagnosis(context))


def _build_diagnosis(context: InvestigationContext) -> Diagnosis:
    telemetry_out = _successful_output(context, "search_telemetry") or {}
    deployments_out = _successful_output(context, "search_deployments") or {}
    change_out = _successful_output(context, "inspect_change") or {}
    runbook_out = _successful_output(context, "retrieve_runbook") or {}
    incidents_out = _successful_output(context, "search_prior_incidents") or {}

    evidence_refs: list[EvidenceRef] = []
    for event in telemetry_out.get("results", []):
        evidence_refs.append(EvidenceRef(type="telemetry", id=event["id"]))
    for deployment in deployments_out.get("results", []):
        evidence_refs.append(EvidenceRef(type="deployment", id=deployment["id"]))
    if change_out.get("change"):
        evidence_refs.append(EvidenceRef(type="source_change", id=change_out["change"]["id"]))
    if change_out.get("correlated_deployment"):
        dep_id = change_out["correlated_deployment"]["id"]
        if not any(r.type == "deployment" and r.id == dep_id for r in evidence_refs):
            evidence_refs.append(EvidenceRef(type="deployment", id=dep_id))
    for runbook in runbook_out.get("results", []):
        evidence_refs.append(EvidenceRef(type="runbook", id=runbook["id"]))
    for incident in incidents_out.get("results", []):
        evidence_refs.append(EvidenceRef(type="prior_incident", id=incident["id"]))

    confidence = 0.4
    reasoning_parts = [
        f"Checked telemetry for {context.alert.service} and found "
        f"{len(telemetry_out.get('results', []))} recent event(s)."
    ]

    deployment = deployments_out.get("results", [None])[0] if deployments_out.get("results") else None
    change = change_out.get("change")
    if deployment and change:
        confidence += 0.3
        root_cause = (
            f"Deployment {deployment['version']} (commit {deployment['commit_sha'][:7]}) "
            f"to {context.alert.service} correlates with the alert; changed files: "
            f"{', '.join(change['files_changed'][:5]) or 'unknown'}."
        )
        suggested_action = (
            f"Roll back {context.alert.service} to the version preceding "
            f"{deployment['commit_sha'][:7]} and monitor telemetry for recovery."
        )
        reasoning_parts.append(
            f"Found a correlated deployment ({deployment['commit_sha'][:7]}) and inspected its "
            "source change."
        )
    elif deployment:
        confidence += 0.15
        root_cause = (
            f"Deployment {deployment['version']} to {context.alert.service} occurred shortly "
            "before the alert; source change details were not available."
        )
        suggested_action = f"Roll back the recent deployment to {context.alert.service} and monitor."
        reasoning_parts.append("Found a correlated deployment but no source change record.")
    else:
        root_causes_seen = {
            incident["root_cause"] for incident in incidents_out.get("results", [])
        }
        if len(root_causes_seen) > 1:
            confidence -= 0.15
            reasoning_parts.append(
                "Prior incidents disagree on root cause "
                f"({', '.join(sorted(root_causes_seen))}); treating evidence as conflicting."
            )
            root_cause = (
                "Conflicting prior-incident evidence; no single root cause could be confirmed "
                f"from available data for {context.alert.service}."
            )
            suggested_action = "Escalate for manual investigation; evidence is inconclusive."
        elif incidents_out.get("results"):
            confidence += 0.2
            top = incidents_out["results"][0]
            root_cause = f"Recurrence of a prior incident: {top['root_cause']}."
            suggested_action = f"Apply the prior resolution for '{top['title']}' and monitor."
            reasoning_parts.append(f"Matched prior incident '{top['title']}'.")
        else:
            root_cause = (
                f"No correlated deployment or matching prior incident found for "
                f"{context.alert.service}; alert may be a novel issue."
            )
            suggested_action = "Escalate for manual investigation; no correlated evidence found."
            reasoning_parts.append("No correlated deployment or matching prior incident found.")

    if runbook_out.get("results"):
        top_runbook = runbook_out["results"][0]
        confidence += 0.05 if top_runbook["stale"] else 0.1
        reasoning_parts.append(
            f"Retrieved runbook '{top_runbook['title']}'"
            + (" (stale)." if top_runbook["stale"] else ".")
        )

    confidence = max(0.0, min(1.0, confidence))

    return Diagnosis(
        root_cause=root_cause,
        confidence=round(confidence, 2),
        evidence_refs=evidence_refs,
        suggested_action=suggested_action,
        reasoning=" ".join(reasoning_parts),
    )
