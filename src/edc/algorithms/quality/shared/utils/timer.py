import time
from functools import wraps


def measure_time(func):
    """装饰器：返回 (result, duration_seconds)

    用法：
        result, duration = measure_time(some_func)(*args)
    或者：
        @measure_time
        def f(...):
            return value

    装饰器会使被装饰函数在被调用时返回一个二元组 (原始返回值, 耗时秒)
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        end = time.time()
        duration = end - start
        return result, duration

    return wrapper
