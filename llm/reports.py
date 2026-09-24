"""Human-readable report rendering from validated Phase 7 structured results."""

import json


def _section_values(items, prefix):
    values = [item.split(": ", 1)[1] for item in items if item.startswith(prefix + ": ")]
    return "\n".join(f"- {value}" for value in values) if values else "Not available from structured evidence."


def _render_resource_items(items):
    if not items:
        return "Not available."
    lines = []
    for item in items:
        if isinstance(item, dict):
            label = item.get("raw_text") or item.get("category") or json.dumps(item, ensure_ascii=False)
            quantity = item.get("quantity")
            lines.append(f"- {label}" + (f" (explicit quantity: {quantity})" if quantity is not None else ""))
        else:
            lines.append(f"- {item}")
    return "\n".join(lines)


def _render_estimates(value):
    if value is None:
        return "Not available."
    return "HEURISTIC ESTIMATES — planning aids only, not confirmed requests:\n```json\n" + json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n```"


def _spatial_description(spatial):
    location = spatial.get("normalized_location") or spatial.get("location_text")
    status = spatial.get("geocoding_status", "unavailable")
    lines = [f"Location mention: {location}" if location else "Location: Not available."]
    if status == "success" and spatial.get("coordinates"):
        coords = spatial["coordinates"]
        lines.append(f"Coordinates: {coords['latitude']}, {coords['longitude']} (provider reported success; coordinate range validated as WGS84).")
    elif status == "pending":
        lines.append("Coordinates are pending geocoding and are not verified.")
    elif status == "ambiguous":
        lines.append("The location match is ambiguous; coordinates are not verified.")
    elif status == "failed":
        lines.append("The location is mentioned, but geocoding failed; no verified coordinates are available.")
    else:
        lines.append("Geocoding status or verified coordinates are unavailable.")
    if spatial.get("spatial_cluster_id"):
        lines.append(f"Spatial cluster: {spatial['spatial_cluster_id']} (incident count: {spatial.get('spatial_cluster_size', 'unavailable')}).")
    if spatial.get("hotspot"):
        lines.append(f"Hotspot: yes (hotspot ID: {spatial.get('hotspot_id') or 'available'}; incidents: {spatial.get('hotspot_incident_count', 'unavailable')}).")
    elif spatial.get("spatial_cluster_id"):
        lines.append("Hotspot: no hotspot membership was supplied.")
    if spatial.get("spatial_priority_score") is not None:
        lines.append(f"Spatial area priority score: {spatial['spatial_priority_score']}.")
    return "\n".join(lines)


def render_situation_report(result):
    """Render all required sections, filling absent evidence with plain statements."""
    evidence = result.get("key_evidence", [])
    intelligence = result.get("deterministic_intelligence", {})
    resource = result.get("resource_summary", {})
    spatial = result.get("spatial_context", {})
    scores = []
    for name in ("severity", "urgency", "priority", "confidence"):
        score, level = intelligence.get(f"{name}_score"), intelligence.get(f"{name}_level")
        scores.append(f"- {name.title()}: {level or 'Not available'}" + (f" ({score}/100)" if score is not None else ""))
    explanations = result.get("priority_explanation") or ["No priority factor breakdown was provided."]
    recommendations = result.get("recommended_actions") or ["No recommendations were generated."]
    uncertainties = result.get("uncertainties") or ["No additional uncertainties were recorded."]
    casualty = _section_values(evidence, "Casualty evidence")
    displacement = _section_values(evidence, "Displacement evidence")
    infra = _section_values(evidence, "Infrastructure evidence")
    rescue = _section_values(evidence, "Rescue evidence")
    incident_id = result.get("incident_id") or "Not available"
    return "\n".join([
        "# DISASTER SITUATION REPORT",
        "",
        "## 1. Incident Overview",
        f"- Incident ID: {incident_id}",
        f"- Disaster type: {_section_values(evidence, 'Disaster type').replace(chr(10), ' ').replace('- ', '')}",
        "",
        "## 2. Situation Summary",
        result.get("summary") or "Not available.",
        "",
        "## 3. Human Impact",
        "- Casualties:\n" + casualty,
        "- Displacement:\n" + displacement,
        "",
        "## 4. Infrastructure Impact",
        infra,
        "",
        "## 5. Rescue / Immediate Danger",
        rescue,
        "",
        "## 6. Resource Requests",
        _render_resource_items(resource.get("explicit_requests", [])),
        "",
        "## 7. Estimated Resource Needs",
        _render_estimates(resource.get("estimated_needs")),
        "",
        "## 8. Severity / Urgency / Priority",
        "\n".join(scores),
        "",
        "## 9. Why This Incident Is Prioritized",
        "\n".join(f"- {item}" for item in explanations),
        "",
        "## 10. Spatial Context",
        _spatial_description(spatial),
        "",
        "## 11. Recommended Actions",
        "\n".join(f"- {item}" for item in recommendations),
        "",
        "## 12. Uncertainties / Information Gaps",
        "\n".join(f"- {item}" for item in uncertainties),
        "",
        f"Confidence note: {result.get('confidence_note') or 'Not available.'}",
        "",
        "Recommendations are decision support for human review, not autonomous commands.",
    ])
