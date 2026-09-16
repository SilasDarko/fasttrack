"""Postmortem generation is a deterministic template over the already-validated,
structured Diagnosis -- not a fresh free-form LLM call. This keeps the document
auditable (every sentence traces back to a field the diagnosis validator already
checked) and keeps the approval step fast and provider-independent."""

from app.models import Alert, Investigation


def generate_postmortem(alert: Alert, investigation: Investigation) -> str:
    diagnosis = investigation.diagnosis or {}
    evidence_lines = "\n".join(
        f"- {ref['type']}#{ref['id']}" for ref in diagnosis.get("evidence_refs", [])
    ) or "- (no evidence cited)"

    return (
        f"# Postmortem: {alert.service} — {alert.severity} alert\n\n"
        f"**Alert**: {alert.message}\n\n"
        f"**Root cause**: {diagnosis.get('root_cause', 'unknown')}\n\n"
        f"**Confidence**: {diagnosis.get('confidence', 0.0):.2f}\n\n"
        f"**Suggested action (approved)**: {diagnosis.get('suggested_action', '')}\n\n"
        f"**Reasoning**: {diagnosis.get('reasoning', '')}\n\n"
        f"**Evidence cited**:\n{evidence_lines}\n"
    )
