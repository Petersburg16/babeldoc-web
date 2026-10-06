"""OpenAI 兼容接口的公共小工具：拼地址、鉴权头、报错文字、拉模型列表。

翻译（llm_check、routers/admin.py）和会议（llm.py、routers/admin_meeting_llm.py）两边都用，这里不依赖任何一边；
HTTP 客户端由调用方传入（会议用会议管理器的客户端，测试时能换成假的传输层）。
"""

from __future__ import annotations

import httpx

DEFAULT_BASE_URL = "https://api.openai.com/v1"
TIMEOUT = httpx.Timeout(60.0, connect=10.0)


def endpoint(base_url: str, path: str) -> str:
    """地址留空时用 OpenAI 官方地址。表单提交的地址已经去掉末尾的斜杠，这里再去一次兜底：
    会议模型的地址可能是从翻译模型原样复制来的旧数据。"""
    return (base_url or DEFAULT_BASE_URL).rstrip("/") + path


def auth_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def error_text(resp: httpx.Response) -> str:
    """从报错回复里取出给人看的原因，截到 300 字。"""
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:300] or resp.reason_phrase
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err)[:300]
    return str(err or data)[:300]


def failure_text(prefix: str, e: BaseException) -> str:
    """例如“连接失败：ConnectError: ...”，截到 300 字。"""
    return f"{prefix}：{e.__class__.__name__}: {e}"[:300]


async def fetch_models(http: httpx.AsyncClient, base_url: str, api_key: str) -> list[str]:
    """拉取接口上的模型名（去重排序）。出错抛 ValueError，文字可以直接给管理员看。"""
    try:
        resp = await http.get(endpoint(base_url, "/models"), headers=auth_headers(api_key), timeout=TIMEOUT)
    except Exception as e:  # 代理配置错误等也只当作连接失败汇报，不让接口 500
        raise ValueError(failure_text("连接失败", e)) from e
    if resp.status_code >= 400:
        raise ValueError(f"接口返回 {resp.status_code}：{error_text(resp)}")
    try:
        items = resp.json().get("data") or []
    except (ValueError, AttributeError) as e:
        raise ValueError("返回内容不是 OpenAI 兼容格式") from e
    return sorted({str(item.get("id")) for item in items if isinstance(item, dict) and item.get("id")})
