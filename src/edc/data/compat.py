"""Compatibility import without modifying interpreter search paths."""
def legacy():
    from edc.data.readers import base
    return base
