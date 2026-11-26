"""兼容入口：返回旧的 readers.base 模块，不改 Python 的模块搜索路径。"""
def legacy():
    from edc.data.readers import base
    return base
