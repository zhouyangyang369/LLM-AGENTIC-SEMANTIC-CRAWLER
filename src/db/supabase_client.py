"""
Supabase 客户端单例
统一管理数据库连接，供各模块复用
"""
import os
from functools import lru_cache
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    """返回 Supabase 客户端单例（线程安全，进程内缓存）"""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")  # 使用 service_role key，绕过 RLS

    if not url or not key:
        raise EnvironmentError(
            "请设置环境变量 SUPABASE_URL 和 SUPABASE_SERVICE_ROLE_KEY"
        )

    return create_client(url, key)