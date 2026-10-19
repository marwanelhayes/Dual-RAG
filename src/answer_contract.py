"""Fail-closed source-excerpt answers; retrieval is never behavioral verification.

Flow: Constrain selection -> parse response JSON -> validate source quotes and domains -> render fixed policy text.
"""

import json
import re


def model_response_schema(evidence, required_domains):
    """Constrain source selection to the required domains or an empty abstention."""
    label = {"type": "string", "enum": list(evidence)}
    required = [{"type": "string", "enum": [key for key, item in evidence.items()
                                              if item["domain"] == domain]}
                for domain in ("spec", "rtl") if domain in required_domains]
    # The provider ignores prefixItems when items is present; enumerate bounded tuples.
    found = {"oneOf": [
        {"type": "array", "prefixItems": required + [label] * (count - len(required)),
         "minItems": count, "maxItems": count}
        for count in range(len(required), 4)]}
    return {"oneOf": [
        {"type": "object", "additionalProperties": False,
         "properties": {"status": {"const": status}, "citations": citations},
         "required": ["status", "citations"]}
        for status, citations in (("evidence_found", found),
                                  ("insufficient_evidence", {"type": "array", "maxItems": 0}))]}


def validate_answer(raw, evidence, required_domains):
    """Validate exact source quotes and citations, never purported semantic entailment.

    Evidence is a catalog of complete passages actually included in the prompt.
    Free-form claims and implementation/absence verdicts are deliberately unsupported.
    """
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return None, ["invalid_json"]
    if not isinstance(data, dict) or set(data) != {"status", "claims"}:
        return None, ["invalid_schema"]
    status, claims = data["status"], data["claims"]
    if not isinstance(status, str) or status not in {"evidence_found", "insufficient_evidence"}:
        return None, ["unsupported_verdict"]
    if not isinstance(claims, list) or len(claims) > 16:
        return None, ["invalid_claims"]
    if status == "insufficient_evidence":
        return (data, []) if not claims else (None, ["abstention_with_claims"])
    if not claims:
        return None, ["empty_evidence"]
    domains = set()
    for claim in claims:
        if not isinstance(claim, dict) or set(claim) != {"citation", "quote"}:
            return None, ["unsupported_claim"]
        citation, quote = claim["citation"], claim["quote"]
        if not isinstance(citation, str) or citation not in evidence:
            return None, ["invalid_citation"]
        if not isinstance(quote, str) or not quote.strip() or len(quote) > 2000:
            return None, ["invalid_quote"]
        normalized = lambda value: re.sub(r"\s+", " ", value).strip()
        if normalized(quote) not in normalized(evidence[citation]["text"]):
            return None, ["quote_not_in_evidence"]
        domains.add(evidence[citation]["domain"])
    if not set(required_domains) <= domains:
        return None, ["missing_domain_citations"]
    return data, []


def render_answer(data, comparison=False):
    """Render only validated quotes and fixed policy language."""
    if data["status"] == "insufficient_evidence":
        return "INSUFFICIENT EVIDENCE: the supplied sources do not establish an answer."
    heading = ("UNVERIFIED COMPARISON: source excerpts only; behavioral conformance "
               "and absence are not established." if comparison else "SOURCE EVIDENCE:")
    parts = [heading]
    for claim in data["claims"]:
        parts.append(claim["citation"] + "\n" + "\n".join(
            "> " + line for line in claim["quote"].splitlines()))
    return "\n\n".join(parts)
