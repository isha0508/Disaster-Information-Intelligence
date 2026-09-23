"""
intelligence/clustering.py
==========================
Phase 5 deduplication and clustering for incident intelligence.

Implements lightweight deterministic methods for identifying duplicate
incidents and clustering related reports. No geographic clustering is
performed (that's Phase 6).
"""

import re
from typing import Dict, List, Any, Set, Tuple
from collections import defaultdict
from difflib import SequenceMatcher

from intelligence.config import CLUSTERING_CONFIG


def _normalize_text(text: str) -> str:
    """Normalize text for similarity comparison."""
    text = re.sub(r'\s+', ' ', str(text).lower())
    text = re.sub(r'[^\w\s]', '', text)  # Remove punctuation
    return text.strip()


def _text_similarity(text1: str, text2: str) -> float:
    """Compute normalized text similarity (0-1)."""
    norm1 = _normalize_text(text1)
    norm2 = _normalize_text(text2)
    
    if not norm1 or not norm2:
        return 0.0
    
    return SequenceMatcher(None, norm1, norm2).ratio()


def _location_similarity(loc1: List[str], loc2: List[str]) -> float:
    """Compute location similarity based on overlap."""
    if not loc1 or not loc2:
        return 0.0
    
    loc1_set = set(_normalize_text(loc) for loc in loc1)
    loc2_set = set(_normalize_text(loc) for loc in loc2)
    
    if not loc1_set or not loc2_set:
        return 0.0
    
    intersection = loc1_set & loc2_set
    union = loc1_set | loc2_set
    
    return len(intersection) / len(union) if union else 0.0


def _entity_overlap(entities1: List[Dict], entities2: List[Dict]) -> float:
    """Compute entity overlap based on text and label."""
    if not entities1 or not entities2:
        return 0.0
    
    # Create sets of (text, label) pairs
    set1 = {(e.get("text", "").lower(), e.get("label", "")) for e in entities1}
    set2 = {(e.get("text", "").lower(), e.get("label", "")) for e in entities2}
    
    if not set1 or not set2:
        return 0.0
    
    intersection = set1 & set2
    union = set1 | set2
    
    return len(intersection) / len(union) if union else 0.0


def _disaster_type_match(dt1: Any, dt2: Any) -> bool:
    """Check if disaster types match."""
    # Normalize to list for comparison
    dt1_list = [dt1] if isinstance(dt1, str) else (dt1 if isinstance(dt1, list) else [])
    dt2_list = [dt2] if isinstance(dt2, str) else (dt2 if isinstance(dt2, list) else [])
    
    if not dt1_list or not dt2_list:
        return False
    
    dt1_set = set(_normalize_text(dt) for dt in dt1_list)
    dt2_set = set(_normalize_text(dt) for dt in dt2_list)
    
    return bool(dt1_set & dt2_set)


def detect_duplicates(record: Dict[str, Any], 
                     existing_records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Detect if a record is likely a duplicate of existing records.
    
    Uses text similarity, location overlap, disaster type match, and
    entity overlap to identify potential duplicates.
    
    Returns dict with duplicate information and similarity scores.
    """
    if not existing_records:
        return {
            "is_duplicate": False,
            "duplicate_of": None,
            "similarity_scores": {},
            "matched_records": []
        }
    
    text = record.get("text", "")
    location = record.get("location", [])
    disaster_type = record.get("disaster_type")
    entities = record.get("entities", [])
    
    # If completely empty record, can't determine duplicates
    if not text and not entities and not location:
        return {
            "is_duplicate": False,
            "duplicate_of": None,
            "similarity_scores": {},
            "matched_records": []
        }
    
    matches = []
    
    for i, existing in enumerate(existing_records):
        existing_text = existing.get("text", "")
        existing_location = existing.get("location", [])
        existing_disaster_type = existing.get("disaster_type")
        existing_entities = existing.get("entities", [])
        
        # Compute similarity scores
        text_sim = _text_similarity(text, existing_text)
        loc_sim = _location_similarity(location, existing_location)
        entity_sim = _entity_overlap(entities, existing_entities)
        disaster_match = _disaster_type_match(disaster_type, existing_disaster_type)
        
        # Combined similarity score
        combined_sim = (text_sim * 0.5 + loc_sim * 0.3 + entity_sim * 0.2)
        
        if combined_sim >= CLUSTERING_CONFIG.DUPLICATE_SIMILARITY_THRESHOLD:
            matches.append({
                "index": i,
                "combined_similarity": round(combined_sim, 3),
                "text_similarity": round(text_sim, 3),
                "location_similarity": round(loc_sim, 3),
                "entity_similarity": round(entity_sim, 3),
                "disaster_type_match": disaster_match
            })
    
    if matches:
        # Sort by combined similarity and take the best match
        best_match = max(matches, key=lambda x: x["combined_similarity"])
        return {
            "is_duplicate": True,
            "duplicate_of": best_match["index"],
            "similarity_scores": {
                "combined": best_match["combined_similarity"],
                "text": best_match["text_similarity"],
                "location": best_match["location_similarity"],
                "entity": best_match["entity_similarity"],
                "disaster_type_match": best_match["disaster_type_match"]
            },
            "matched_records": matches
        }
    
    return {
        "is_duplicate": False,
        "duplicate_of": None,
        "similarity_scores": {},
        "matched_records": []
    }


def cluster_incidents(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Cluster incidents based on text similarity, location, and entity overlap.
    
    Each incident receives a cluster_id. Incidents likely describing
    the same event are grouped together.
    
    Returns the input records with cluster_id added to each.
    """
    if not records:
        return records
    
    # Initialize clusters
    clusters = []  # List of lists of record indices
    record_to_cluster = {}  # Map record index -> cluster index
    
    for i, record in enumerate(records):
        text = record.get("text", "")
        location = record.get("location", [])
        disaster_type = record.get("disaster_type")
        entities = record.get("entities", [])
        
        best_cluster = None
        best_similarity = 0.0
        
        # Check against existing clusters
        for cluster_idx, cluster_indices in enumerate(clusters):
            # Compute similarity to cluster representative (first record)
            rep_idx = cluster_indices[0]
            rep_record = records[rep_idx]
            
            rep_text = rep_record.get("text", "")
            rep_location = rep_record.get("location", [])
            rep_entities = rep_record.get("entities", [])
            rep_disaster_type = rep_record.get("disaster_type")
            
            # Similarity calculations
            text_sim = _text_similarity(text, rep_text)
            loc_sim = _location_similarity(location, rep_location)
            entity_sim = _entity_overlap(entities, rep_entities)
            disaster_match = _disaster_type_match(disaster_type, rep_disaster_type)
            
            # Combined similarity
            combined_sim = (text_sim * 0.5 + loc_sim * 0.3 + entity_sim * 0.2)
            
            # Disaster type match is a hard requirement for clustering
            if not disaster_match:
                combined_sim *= 0.5  # Penalize heavily
            
            if combined_sim > best_similarity and combined_sim >= CLUSTERING_CONFIG.CLUSTER_ENTITY_OVERLAP_THRESHOLD:
                best_similarity = combined_sim
                best_cluster = cluster_idx
        
        if best_cluster is not None:
            # Add to existing cluster
            clusters[best_cluster].append(i)
            record_to_cluster[i] = best_cluster
        else:
            # Create new cluster
            clusters.append([i])
            record_to_cluster[i] = len(clusters) - 1
    
    # Add cluster IDs to records
    enriched_records = []
    for i, record in enumerate(records):
        cluster_idx = record_to_cluster.get(i, -1)
        cluster_size = len(clusters[cluster_idx]) if cluster_idx >= 0 else 1
        
        enriched_record = record.copy()
        enriched_record["cluster_id"] = f"CLUSTER_{cluster_idx}" if cluster_idx >= 0 else "CLUSTER_SINGLETON"
        enriched_record["cluster_size"] = cluster_size
        enriched_records.append(enriched_record)
    
    return enriched_records


def find_related_incidents(record: Dict[str, Any], 
                          incident_database: List[Dict[str, Any]],
                          top_k: int = 5) -> List[Dict[str, Any]]:
    """
    Find incidents related to a given record from a database.
    
    Returns top-k most similar incidents with similarity scores.
    """
    text = record.get("text", "")
    location = record.get("location", [])
    disaster_type = record.get("disaster_type")
    entities = record.get("entities", [])
    
    similarities = []
    
    for i, db_record in enumerate(incident_database):
        # Skip self-comparison if same text
        if text == db_record.get("text", ""):
            continue
        
        db_text = db_record.get("text", "")
        db_location = db_record.get("location", [])
        db_entities = db_record.get("entities", [])
        db_disaster_type = db_record.get("disaster_type")
        
        text_sim = _text_similarity(text, db_text)
        loc_sim = _location_similarity(location, db_location)
        entity_sim = _entity_overlap(entities, db_entities)
        disaster_match = _disaster_type_match(disaster_type, db_disaster_type)
        
        combined_sim = (text_sim * 0.5 + loc_sim * 0.3 + entity_sim * 0.2)
        if not disaster_match:
            combined_sim *= 0.5
        
        similarities.append({
            "index": i,
            "combined_similarity": round(combined_sim, 3),
            "text_similarity": round(text_sim, 3),
            "location_similarity": round(loc_sim, 3),
            "entity_similarity": round(entity_sim, 3),
            "disaster_type_match": disaster_match
        })
    
    # Sort by combined similarity and return top-k
    similarities.sort(key=lambda x: x["combined_similarity"], reverse=True)
    return similarities[:top_k]
