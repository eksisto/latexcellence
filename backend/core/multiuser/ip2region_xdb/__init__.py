# Copyright 2022 The Ip2Region Authors. All rights reserved.
# Use of this source code is governed by a Apache2.0-style
# license that can be found in the LICENSE file.

"""ip2region .xdb 离线库读取器（vendored 官方 Python binding）。

源码 vendored 自 https://github.com/lionsoul2014/ip2region 的
binding/python/ip2region（searcher.py + util.py，Apache-2.0 OR MIT 双许可，
版权与许可头保留在各自文件内）。官方 binding 未发布到 PyPI，故随仓内嵌；
升级上游时直接覆盖 searcher.py / util.py 并保持本入口不变即可。

仅暴露注册自动审批所需的最小接口：以 content 缓存策略（整库载入内存）
打开 xdb 搜索器——加载一次、查询零 I/O，且 Searcher 不持共享文件句柄，
天然线程安全（注册审批在线程池中并发调用）。
"""

import io

from . import searcher, util

__all__ = ["open_content_searcher", "searcher", "util"]


def open_content_searcher(db_path: str) -> "searcher.Searcher":
    """以 content 缓存策略打开 ip2region xdb 文件。

    打开前执行一次官方 verify()（结构版本与文件尺寸校验），失败抛出
    ValueError/Exception 由调用方兜底降级；成功后整库读入内存并返回
    Searcher（支持 IPv4 与 IPv6 xdb，版本自文件头探测）。
    """
    with io.open(db_path, "rb") as handle:
        util.verify(handle)
        header = util.load_header(handle)
        version = util.version_from_header(header)
        if version is None:
            raise ValueError("failed to get ip version from xdb header")
        c_buffer = util.load_content(handle)
    return searcher.new_with_buffer(version, c_buffer)
