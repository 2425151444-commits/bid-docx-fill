from __future__ import annotations

import re

from schemas import normalize_field_name


DATE_PLACEHOLDER_CHARS = "_.·•…○〇Xx×-—年月日 "
PLACEHOLDER_CHARS = "_.·•…○〇()[]Xx×-— "


FIELD_TYPE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("ORG_NAME", ("供应商名称", "磋商供应商名称", "单位名称", "企业名称", "磋商单位名称", "磋商单位的名称", "乙方")),
    ("PERSON_NAME", ("姓名", "联系人", "法定代表人", "负责人", "授权代表", "被授权人")),
    ("TITLE", ("职务", "职称")),
    ("ADDRESS", ("地址", "通讯地址", "注册地址", "项目地址")),
    ("POSTAL_CODE", ("邮政编码",)),
    ("PHONE", ("电话", "联系电话", "手机")),
    ("FAX", ("传真",)),
    ("WEBSITE", ("网址", "网站")),
    ("BANK_NAME", ("开户银行", "银行名称")),
    ("BANK_ACCOUNT", ("账号", "银行账号", "开户账号")),
    ("UNIFIED_CODE", ("统一社会信用代码", "法人证书号")),
    ("DATE", ("日期", "时间", "签订时间", "签约日期", "磋商时间")),
    ("AMOUNT", ("报价", "金额", "小写", "大写")),
    ("PROJECT_NAME", ("项目名称",)),
    ("PROJECT_CODE", ("项目编号", "编号")),
    ("SERVICE_PERIOD", ("项目服务期限", "服务期限", "项目完成时间", "磋商有效期")),
    ("TEXT_DYNAMIC", ("组织结构", "经营范围", "备注")),
]


UPLOAD_FIELD_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("项目编号", ("项目编号", "编号")),
    ("项目名称", ("项目名称",)),
    ("采购人", ("采购人",)),
    ("采购代理机构", ("采购代理机构", "采购代理机构名称")),
    ("递交响应文件截止时间", ("递交响应文件截止时间", "响应文件开启时间")),
    ("磋商地点", ("磋商地点",)),
    ("项目服务期限", ("项目服务期限", "服务期限")),
    ("日期", ("日期", "签订时间", "签约日期", "磋商时间")),
]


def infer_field_type(field_name: str) -> str:
    normalized = normalize_field_name(field_name)
    for field_type, keywords in FIELD_TYPE_RULES:
        if any(normalize_field_name(keyword) in normalized for keyword in keywords):
            return field_type
    return "TEXT"


def canonical_upload_field_name(field_name: str) -> str | None:
    normalized = normalize_field_name(field_name)
    for canonical_name, keywords in UPLOAD_FIELD_RULES:
        if any(normalize_field_name(keyword) in normalized for keyword in keywords):
            return canonical_name
    return None


def looks_like_placeholder(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return False
    return bool(re.fullmatch(rf"[{re.escape(PLACEHOLDER_CHARS)}]+", compact))


def looks_like_date_placeholder(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return False
    patterns = [
        rf"^[{re.escape(DATE_PLACEHOLDER_CHARS)}]+$",
        r"^[0-9Xx×_.·•…○〇-]*年[0-9Xx×_.·•…○〇-]*月[0-9Xx×_.·•…○〇-]*日$",
        r"^XX年XX月XX日$",
    ]
    return any(re.fullmatch(pattern, compact) for pattern in patterns)


def is_value_compatible(field_type: str, field_name: str, value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    if looks_like_placeholder(text) or looks_like_date_placeholder(text):
        return False

    normalized_value = normalize_field_name(text)
    normalized_field = normalize_field_name(field_name)
    if normalized_value == normalized_field:
        return False

    if field_type == "ORG_NAME":
        return len(text) >= 4
    if field_type == "PERSON_NAME":
        return 2 <= len(text) <= 32 and not re.search(r"\d{5,}", text)
    if field_type == "TITLE":
        return 2 <= len(text) <= 40
    if field_type == "ADDRESS":
        return len(text) >= 4
    if field_type == "POSTAL_CODE":
        return bool(re.fullmatch(r"\d{6}", text))
    if field_type in {"PHONE", "FAX"}:
        return bool(re.fullmatch(r"(?:0\d{2,3}-?\d{7,8}|1\d{10})", text))
    if field_type == "WEBSITE":
        return "." in text
    if field_type == "BANK_NAME":
        return "银行" in text
    if field_type == "BANK_ACCOUNT":
        return bool(re.fullmatch(r"\d{6,30}", text))
    if field_type == "UNIFIED_CODE":
        return bool(re.fullmatch(r"[0-9A-Z]{8,24}", text))
    if field_type == "DATE":
        if "成立时间" in field_name:
            return bool(re.search(r"\d{4}年\d{1,2}月(?:\d{1,2}日)?", text))
        return bool(re.search(r"\d{4}年\d{1,2}月\d{1,2}日", text))
    if field_type == "AMOUNT":
        return bool(re.search(r"\d", text))
    if field_type == "PROJECT_CODE":
        return bool(re.search(r"[A-Za-z0-9._/-]{4,}", text))
    if field_type == "PROJECT_NAME":
        return len(text) >= 4
    if field_type == "SERVICE_PERIOD":
        return len(text) >= 2
    return len(text) >= 1
