"""Đăng nhập + phân quyền qua Supabase (user-management-plan.md)."""

from .manager import FEATURES, LoginError, manager

__all__ = ["FEATURES", "LoginError", "manager"]
