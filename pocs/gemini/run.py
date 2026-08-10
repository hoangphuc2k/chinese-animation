def validate_segment(value: object) -> bool:
    if not isinstance(value, dict) or not value.get('id') or not isinstance(value.get('text'), str): return False
    return not any(term in value['text'].lower() for term in ('invented fact', 'unsafe'))
