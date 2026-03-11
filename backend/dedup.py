"""
Semantic task deduplication for Delega.

Uses TF-IDF + cosine similarity for fast, local, zero-API-cost dedup.
No external LLM calls needed. Can be upgraded to embeddings later.

Usage:
    from dedup import find_similar_tasks
    
    similar = find_similar_tasks(
        new_content="Research competitor pricing",
        existing_tasks=open_tasks,
        threshold=0.6,
    )
"""
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import re
from typing import Optional


def normalize_text(text: str) -> str:
    """Normalize text for comparison: lowercase, strip noise."""
    text = text.lower().strip()
    # Remove common prefixes agents add
    text = re.sub(r'^(task|todo|action|item):?\s*', '', text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text)
    return text


def find_similar_tasks(
    new_content: str,
    existing_tasks: list,  # List of Task ORM objects
    threshold: float = 0.6,
    max_results: int = 5,
) -> list[dict]:
    """
    Find existing tasks that are semantically similar to the new content.
    
    Args:
        new_content: The content of the task being created
        existing_tasks: List of Task objects to compare against
        threshold: Minimum similarity score (0-1). 0.6 is conservative.
        max_results: Maximum number of similar tasks to return
    
    Returns:
        List of dicts: [{"task_id": int, "content": str, "score": float}]
    """
    if not existing_tasks or not new_content.strip():
        return []
    
    new_normalized = normalize_text(new_content)
    existing_texts = [normalize_text(t.content) for t in existing_tasks]
    
    # Combine for TF-IDF fitting
    all_texts = [new_normalized] + existing_texts
    
    try:
        vectorizer = TfidfVectorizer(
            stop_words='english',
            ngram_range=(1, 2),  # Unigrams + bigrams for better matching
            min_df=1,
            max_df=0.95,
        )
        tfidf_matrix = vectorizer.fit_transform(all_texts)
    except ValueError:
        # All docs are empty after preprocessing
        return []
    
    # Compare new task (index 0) against all existing (index 1+)
    similarities = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:])[0]
    
    results = []
    for i, score in enumerate(similarities):
        if score >= threshold:
            results.append({
                "task_id": existing_tasks[i].id,
                "content": existing_tasks[i].content,
                "score": round(float(score), 3),
            })
    
    # Sort by similarity score descending
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:max_results]
