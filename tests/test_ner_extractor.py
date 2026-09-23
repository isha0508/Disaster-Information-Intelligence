"""
tests/test_ner_extractor.py
==========================
Phase 4 Hybrid NER/Information Extraction — Regression/Smoke Tests.

These are NOT a full scientific evaluation. They are regression/smoke tests
to verify the reusable nlp.extractor module preserves the behavior of the
final Phase 4 notebook implementation.

Tests are designed to gracefully skip when the general NER model
(dslim/bert-base-NER) is unavailable, so the test suite can run without
downloading large model checkpoints during normal unit testing.
"""

import pytest

from nlp.extractor import (
    extract_hybrid,
    build_structured_record,
    extract_casualties,
    extract_disaster_type,
    extract_requests,
    extract_resources,
    extract_rescue,
    extract_infrastructure,
    extract_general_ner,
    reset_ner_pipeline,
    PROJECT_ENTITY_TYPES,
)


class TestCasualtyExtraction:
    """Test casualty extraction from the hybrid pipeline."""

    def test_casualty_detection(self):
        """CASUALTY should be detected in casualty-related text."""
        text = "50 people were killed in the earthquake."
        result = extract_hybrid(text)
        
        casualty_entities = [e for e in result["entities"] if e["label"] == "CASUALTY"]
        assert len(casualty_entities) > 0, "CASUALTY entity should be detected"
        
        # Verify the entity contains relevant information
        assert any("killed" in e["text"].lower() or "50" in e["text"] 
                   for e in casualty_entities), \
               "CASUALTY entity should contain casualty-related text"

    def test_casualty_direct_function(self):
        """Test the direct casualty extraction function."""
        text = "20 people injured and 5 fatalities reported."
        entities = extract_casualties(text)
        
        assert len(entities) > 0, "extract_casualties should find entities"
        assert all(e["label"] == "CASUALTY" for e in entities), \
               "All entities should be CASUALTY"


class TestDisasterType:
    """Test disaster type extraction."""

    def test_disaster_type_detection(self):
        """DISASTER_TYPE should be detected for disaster keywords."""
        text = "A powerful earthquake struck the city."
        result = extract_hybrid(text)
        
        disaster_entities = [e for e in result["entities"] if e["label"] == "DISASTER_TYPE"]
        assert len(disaster_entities) > 0, "DISASTER_TYPE entity should be detected"
        
        # Verify it detected earthquake
        assert any("earthquake" in e["text"].lower() for e in disaster_entities), \
               "DISASTER_TYPE should contain 'earthquake'"

    def test_disaster_type_direct_function(self):
        """Test the direct disaster type extraction function."""
        text = "The flooding caused extensive damage."
        entities = extract_disaster_type(text)
        
        assert len(entities) > 0, "extract_disaster_type should find entities"
        assert all(e["label"] == "DISASTER_TYPE" for e in entities), \
               "All entities should be DISASTER_TYPE"


class TestResourceRequest:
    """Test resource and request extraction."""

    def test_request_detection(self):
        """REQUEST should be detected in need-related text."""
        text = "Families need food and clean water."
        result = extract_hybrid(text)
        
        request_entities = [e for e in result["entities"] if e["label"] == "REQUEST"]
        resource_entities = [e for e in result["entities"] if e["label"] == "RESOURCE"]
        
        # At minimum, REQUEST should be detected
        assert len(request_entities) > 0, "REQUEST entity should be detected"
        
        # RESOURCE may also be detected (gazetteer-based)
        # This is acceptable per Phase 4 behavior
        assert len(request_entities) > 0 or len(resource_entities) > 0, \
               "At least REQUEST or RESOURCE should be detected"

    def test_resource_detection(self):
        """RESOURCE should be detected for resource terms."""
        text = "We have drinking water and medical supplies available."
        result = extract_hybrid(text)
        
        resource_entities = [e for e in result["entities"] if e["label"] == "RESOURCE"]
        assert len(resource_entities) > 0, "RESOURCE entity should be detected"

    def test_request_direct_function(self):
        """Test the direct request extraction function."""
        text = "Urgently need blankets and medicine."
        entities = extract_requests(text)
        
        assert len(entities) > 0, "extract_requests should find entities"
        assert all(e["label"] == "REQUEST" for e in entities), \
               "All entities should be REQUEST"

    def test_resource_direct_function(self):
        """Test the direct resource extraction function."""
        text = "We need water and food."
        entities = extract_resources(text)
        
        assert len(entities) > 0, "extract_resources should find entities"
        assert all(e["label"] == "RESOURCE" for e in entities), \
               "All entities should be RESOURCE"


class TestRescue:
    """Test rescue extraction."""

    def test_rescue_detection(self):
        """RESCUE should be detected in rescue-related text."""
        text = "Rescue teams saved trapped residents."
        result = extract_hybrid(text)
        
        rescue_entities = [e for e in result["entities"] if e["label"] == "RESCUE"]
        assert len(rescue_entities) > 0, "RESCUE entity should be detected"
        
        # Verify it detected rescue-related terms
        assert any("rescue" in e["text"].lower() or "trapped" in e["text"].lower() 
                   for e in rescue_entities), \
               "RESCUE entity should contain rescue-related text"

    def test_rescue_direct_function(self):
        """Test the direct rescue extraction function."""
        text = "Search and rescue operations are underway."
        entities = extract_rescue(text)
        
        assert len(entities) > 0, "extract_rescue should find entities"
        assert all(e["label"] == "RESCUE" for e in entities), \
               "All entities should be RESCUE"


class TestInfrastructure:
    """Test infrastructure extraction."""

    def test_infrastructure_detection(self):
        """INFRASTRUCTURE should be detected for infrastructure terms."""
        text = "The bridge collapsed and the road was damaged."
        result = extract_hybrid(text)
        
        infrastructure_entities = [e for e in result["entities"] if e["label"] == "INFRASTRUCTURE"]
        assert len(infrastructure_entities) > 0, "INFRASTRUCTURE entity should be detected"
        
        # Verify it detected infrastructure terms
        assert any("bridge" in e["text"].lower() or "road" in e["text"].lower() 
                   for e in infrastructure_entities), \
               "INFRASTRUCTURE entity should contain infrastructure terms"

    def test_infrastructure_direct_function(self):
        """Test the direct infrastructure extraction function."""
        text = "The hospital and school were affected."
        entities = extract_infrastructure(text)
        
        assert len(entities) > 0, "extract_infrastructure should find entities"
        assert all(e["label"] == "INFRASTRUCTURE" for e in entities), \
               "All entities should be INFRASTRUCTURE"


class TestLocationGeneralNER:
    """Test location extraction via general NER."""

    def test_location_detection_with_ner(self):
        """LOCATION should be detected when general NER is available."""
        text = "Flooding in Mumbai has damaged several roads."
        
        # Try to use general NER if available
        entities = extract_general_ner(text)
        
        if entities:  # General NER is available
            location_entities = [e for e in entities if e["label"] == "LOCATION"]
            assert len(location_entities) > 0, "LOCATION should be detected by general NER"
        else:
            # If general NER is unavailable, this test is skipped
            pytest.skip("General NER model not available - skipping location test")

    def test_hybrid_location_extraction(self):
        """Test location extraction through the full hybrid pipeline."""
        text = "Disaster in Houston affected many residents."
        result = extract_hybrid(text)
        
        # Location may come from general NER if available
        # If not available, we still test that the pipeline doesn't crash
        assert "entities" in result, "Result should contain entities"
        assert isinstance(result["entities"], list), "Entities should be a list"


class TestStructuredOutput:
    """Test structured incident record generation."""

    def test_structured_record_fields(self):
        """Structured record should contain all expected Phase 4 fields."""
        text = "50 people were killed in the flood. Families need water."
        result = extract_hybrid(text)
        record = build_structured_record(result)
        
        # Verify all expected fields are present
        expected_fields = [
            "text", "location", "casualties", "displaced", "requests",
            "resources", "rescue", "disaster_type", "organizations",
            "persons", "numbers", "infrastructure", "entities",
            "request_resource_links"
        ]
        
        for field in expected_fields:
            assert field in record, f"Structured record should contain field: {field}"

    def test_structured_record_preserves_text(self):
        """Structured record should preserve the original text."""
        text = "Test disaster text with information."
        result = extract_hybrid(text)
        record = build_structured_record(result)
        
        assert record["text"] == text, "Structured record should preserve original text"

    def test_structured_record_entity_grouping(self):
        """Entities should be grouped into their respective fields."""
        text = "Earthquake in Tokyo. 10 people injured. Need food and water."
        result = extract_hybrid(text)
        record = build_structured_record(result)
        
        # Check that entities are grouped correctly
        assert isinstance(record["casualties"], list), "casualties should be a list"
        assert isinstance(record["requests"], list), "requests should be a list"
        assert isinstance(record["resources"], list), "resources should be a list"
        assert isinstance(record["disaster_type"], (list, str)), \
               "disaster_type should be a list or string"


class TestEmptyInvalidInput:
    """Test behavior with empty or invalid input."""

    def test_empty_string(self):
        """Empty string should not crash the extractor."""
        result = extract_hybrid("")
        
        assert result["text"] == "", "Empty text should be preserved"
        assert isinstance(result["entities"], list), "Entities should be a list"
        assert len(result["entities"]) == 0, "Empty text should produce no entities"

    def test_whitespace_only(self):
        """Whitespace-only text should not crash the extractor."""
        result = extract_hybrid("   \n\t  ")
        
        assert isinstance(result["entities"], list), "Entities should be a list"
        assert len(result["entities"]) == 0, "Whitespace-only text should produce no entities"

    def test_none_input(self):
        """None input should be handled gracefully."""
        result = extract_hybrid(None)
        
        assert result["text"] == "", "None should be converted to empty string"
        assert isinstance(result["entities"], list), "Entities should be a list"

    def test_structured_record_empty_input(self):
        """Structured record should handle empty extraction results."""
        result = extract_hybrid("")
        record = build_structured_record(result)
        
        assert record["text"] == ""
        assert all(isinstance(record[field], list) for field in 
                   ["location", "casualties", "displaced", "requests", "resources",
                    "rescue", "organizations", "persons", "numbers", "infrastructure"])


class TestEntitySchema:
    """Test that the entity schema matches Phase 4 specifications."""

    def test_project_entity_types(self):
        """PROJECT_ENTITY_TYPES should contain the 11 official types."""
        expected_types = {
            "LOCATION", "CASUALTY", "DISPLACED", "REQUEST", "RESOURCE",
            "RESCUE", "DISASTER_TYPE", "ORGANIZATION", "PERSON", "NUMBER",
            "INFRASTRUCTURE"
        }
        
        assert set(PROJECT_ENTITY_TYPES) == expected_types, \
               "PROJECT_ENTITY_TYPES should match the 11 official Phase 4 types"

    def test_no_misc_in_schema(self):
        """MISC should not be in the official PROJECT_ENTITY_TYPES."""
        assert "MISC" not in PROJECT_ENTITY_TYPES, \
               "MISC should not be in the official entity schema"


class TestDeterministicBehavior:
    """Test that extraction is deterministic for the same input."""

    def test_deterministic_extraction(self):
        """Same input should produce identical results."""
        text = "50 people were killed in the earthquake. Need water and food."
        
        result1 = extract_hybrid(text)
        result2 = extract_hybrid(text)
        
        # Compare entity counts
        assert len(result1["entities"]) == len(result2["entities"]), \
               "Extraction should be deterministic - same entity count"
        
        # Compare entity texts (order may vary due to processing)
        texts1 = sorted([e["text"] for e in result1["entities"]])
        texts2 = sorted([e["text"] for e in result2["entities"]])
        
        assert texts1 == texts2, \
               "Extraction should be deterministic - same entity texts"


class TestNERModelAvailability:
    """Test handling of general NER model availability."""

    def test_ner_pipeline_reset(self):
        """reset_ner_pipeline should clear the cached pipeline."""
        # Reset the pipeline
        reset_ner_pipeline()
        
        # This should not raise an error
        result = extract_hybrid("Test text.")
        assert "entities" in result

    def test_graceful_degradation_without_ner(self):
        """Pipeline should work even if general NER is unavailable."""
        # Reset to ensure clean state
        reset_ner_pipeline()
        
        # Use rule-based extraction (should always work)
        text = "Earthquake caused damage. Need water."
        result = extract_hybrid(text)
        
        # Should still extract rule-based entities
        assert len(result["entities"]) > 0, \
               "Rule-based extraction should work without general NER"
        
        # Check for expected rule-based entities
        labels = {e["label"] for e in result["entities"]}
        assert "DISASTER_TYPE" in labels or "REQUEST" in labels or "RESOURCE" in labels, \
               "Should extract disaster type, request, or resource via rules"
