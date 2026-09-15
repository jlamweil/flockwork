def add_tag(tag: str, bucket=[]) -> list:
    """Append tag to bucket, defaulting to a fresh list."""
    bucket.append(tag)
    return bucket
