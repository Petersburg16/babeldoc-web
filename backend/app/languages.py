"""目标语言代码要带地区（zh-CN / ko-KR）：babeldoc 按代码里是否含 CN/TW/HK/JP/KR 选字体，写成 zh 会退回英文字体族。"""

LANGUAGES: list[dict[str, str]] = [
    {"code": "en", "label": "英语", "native": "English"},
    {"code": "zh-CN", "label": "简体中文", "native": "简体中文"},
    {"code": "zh-TW", "label": "繁体中文（台湾）", "native": "繁體中文"},
    {"code": "zh-HK", "label": "繁体中文（香港）", "native": "繁體中文（香港）"},
    {"code": "ja", "label": "日语", "native": "日本語"},
    {"code": "ko-KR", "label": "韩语", "native": "한국어"},
    {"code": "fr", "label": "法语", "native": "Français"},
    {"code": "de", "label": "德语", "native": "Deutsch"},
    {"code": "es", "label": "西班牙语", "native": "Español"},
    {"code": "ru", "label": "俄语", "native": "Русский"},
    {"code": "pt", "label": "葡萄牙语", "native": "Português"},
    {"code": "it", "label": "意大利语", "native": "Italiano"},
    {"code": "vi", "label": "越南语", "native": "Tiếng Việt"},
]

LANGUAGE_CODES = {lang["code"] for lang in LANGUAGES}
