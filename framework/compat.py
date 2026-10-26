"""Compatibility import without modifying interpreter search paths."""
def legacy():
    from framework.readers import base
    return base
