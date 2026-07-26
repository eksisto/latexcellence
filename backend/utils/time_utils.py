"""
时间工具函数。

提供当前日期/时间/星期的格式化方法，供 LLM prompt 注入使用。
"""

from datetime import datetime

_WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def get_current_time_context() -> dict[str, str]:
    """
    返回当前日期、时间、星期的格式化字符串。

    返回：
        {
            "date": "2026-07-22",
            "time": "20:20",
            "weekday": "周三",
        }
    """
    now = datetime.now()
    return {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "weekday": _WEEKDAY_CN[now.weekday()],
    }
