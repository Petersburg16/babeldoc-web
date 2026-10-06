"""BabelDOC 适配层：后端以子进程方式调用，项目里只有这个文件直接依赖 babeldoc。

协议 v1
  argv[1]  任务描述 JSON（见 backend/app/engine.py 的 build_spec），不含密钥。各键：
           input / output_dir / working_dir / lang_in / lang_out：文件位置与语言
           skip_translation：跳过翻译走完整流水线（engine-check 冒烟用）
           model：{base_url, model, qps, pool_max_workers, send_temperature, json_mode, term_model}
           model.term 可选：术语提取单独用的模型配置（字段同 model），没有时用 model.term_model 或翻译模型
           options：页码、输出、水印、术语表等开关，键名见 build_spec 与下面的 run()
           job_id：只为落盘的 spec.json 便于排查，runner 不读；mock：只给 mock_runner.py 用
           ignore_cache / preflight：后端目前不写，缺省分别是 false（用翻译缓存）、true（开工前先打一次接口）
  env      BDW_API_KEY / BDW_TERM_API_KEY 传模型密钥；model.term 只用 BDW_TERM_API_KEY，绝不回退到主密钥
  输出     每行一个 JSON 事件，写到启动时复制出来的原 stdout；
           babeldoc 及其 fork 出的子进程的一切输出都被重定向到 stderr（后端落盘为 engine.log）
           started 里的 protocol 即 PROTOCOL_VERSION；后端目前只读 version，不校验 protocol

  {"event": "started", "engine": "babeldoc", "version": "0.6.4", "pid": 123, "protocol": 1}
  {"event": "progress", "overall": 12.5, "stage": "Parse Page Layout", "current": 3, "total": 15, "part": 1, "parts": 1}
  {"event": "finished", "files": {"mono": "...", "dual": "...", "glossary": null}, "stats": {...}, "warning": null}
  {"event": "failed", "kind": "preflight|input|translate|internal", "message": "..."}

升级 babeldoc 时：改 pyproject.toml 版本 → uv sync → 对照本文件核对 TranslationConfig 参数与事件格式 →
在 backend 目录跑 `uv run python -m app.cli engine-check <pdf>`（跳过翻译走完整流水线）冒烟。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1
PROGRESS_MIN_INTERVAL = 0.5

_channel = None


class EngineError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


def open_channel() -> None:
    global _channel
    sys.stdout.flush()
    fd = os.dup(1)
    os.dup2(2, 1)
    _channel = os.fdopen(fd, "w", encoding="utf-8", buffering=1)


def emit(event: str, **payload: Any) -> None:
    line = json.dumps({"event": event, **payload}, ensure_ascii=False, default=str)
    if _channel is None:
        print(line, flush=True)
        return
    _channel.write(line + "\n")
    _channel.flush()


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for name in ("httpx", "httpcore", "openai", "peewee", "pdfminer"):
        logging.getLogger(name).setLevel(logging.WARNING)


def describe_error(err: Any) -> EngineError:
    from babeldoc.babeldoc_exception.BabelDOCException import (
        ContentFilterError,
        ExtractTextError,
        InputFileGeneratedByBabelDOCError,
        ScannedPDFError,
    )

    if isinstance(err, type):
        return EngineError("internal", f"翻译被中断（{err.__name__}）")
    if isinstance(err, EngineError):
        return err
    if isinstance(err, InputFileGeneratedByBabelDOCError):
        return EngineError("input", "这个文件本身就是 BabelDOC 生成的译文，不能再次翻译")
    if isinstance(err, ScannedPDFError):
        return EngineError("input", "检测到扫描件 PDF。可在高级选项中开启「扫描件兼容」后重试")
    if isinstance(err, ExtractTextError):
        return EngineError("input", "没有从文档中提取到可翻译的文字（可能是纯图片扫描件或字体编码异常）")
    if isinstance(err, ContentFilterError):
        return EngineError("translate", "内容被模型服务的安全审查拦截")
    text = str(err).strip() or err.__class__.__name__
    return EngineError("translate", f"{err.__class__.__name__}: {text}"[:2000])


def make_translator_class():
    from babeldoc.translator.translator import OpenAITranslator

    class CountingTranslator(OpenAITranslator):
        """记录真实 API 调用的成败。babeldoc 会吞掉段落级异常并保留原文，不统计就发现不了“翻译成功但没翻”。"""

        def __init__(self, *args: Any, **kwargs: Any):
            super().__init__(*args, **kwargs)
            self._lock = threading.Lock()
            self.api_ok = 0
            self.api_errors = 0
            self.last_error: str | None = None

        def _record(self, ok: bool, err: Exception | None = None) -> None:
            with self._lock:
                if ok:
                    self.api_ok += 1
                else:
                    self.api_errors += 1
                    self.last_error = f"{err.__class__.__name__}: {err}"[:500]

        def do_translate(self, text, rate_limit_params: dict | None = None):
            try:
                out = super().do_translate(text, rate_limit_params)
            except Exception as e:
                self._record(False, e)
                raise
            self._record(True)
            return out

        def do_llm_translate(self, text, rate_limit_params: dict | None = None):
            if text is None:
                return super().do_llm_translate(text, rate_limit_params)
            try:
                out = super().do_llm_translate(text, rate_limit_params)
            except Exception as e:
                self._record(False, e)
                raise
            self._record(True)
            return out

    return CountingTranslator


def build_translator(cls, spec: dict, model: dict, api_key: str):
    # 不传 reasoning / thinking：翻译不开思考，开了就不能发 temperature=0
    return cls(
        lang_in=spec["lang_in"],
        lang_out=spec["lang_out"],
        model=model["model"],
        base_url=model.get("base_url") or None,
        api_key=api_key,
        ignore_cache=bool(spec.get("ignore_cache")),
        enable_json_mode_if_requested=bool(model.get("json_mode")),
        send_temperature=model.get("send_temperature", True),
    )


def preflight(translator) -> float:
    import openai

    options: dict[str, Any] = {"temperature": 0} if translator.send_temperature else {}
    client = translator.client.with_options(timeout=60, max_retries=1)
    started = time.monotonic()
    try:
        client.chat.completions.create(
            model=translator.model,
            messages=[{"role": "user", "content": "Reply with OK."}],
            max_tokens=32,
            extra_body=translator.extra_body,
            **options,
        )
    except openai.RateLimitError:
        logging.warning("preflight hit rate limit, continue anyway")
    except openai.AuthenticationError as e:
        raise EngineError("preflight", f"模型接口认证失败（API Key 无效或已过期）：{e.message}") from e
    except openai.PermissionDeniedError as e:
        raise EngineError("preflight", f"模型接口拒绝访问（余额不足或无权限）：{e.message}") from e
    except openai.NotFoundError as e:
        raise EngineError("preflight", f"模型不存在或接口地址错误：{e.message}") from e
    except openai.APIConnectionError as e:
        raise EngineError("preflight", f"连接不上模型接口：{e}") from e
    except openai.APIStatusError as e:
        raise EngineError("preflight", f"模型接口返回错误 {e.status_code}：{e.message}") from e
    return time.monotonic() - started


def load_glossaries(paths: list[str], lang_out: str) -> list:
    from babeldoc.glossary import Glossary

    glossaries = []
    for p in paths:
        try:
            g = Glossary.from_csv(Path(p), lang_out)
        except Exception as e:
            raise EngineError("input", f"术语表解析失败：{e}") from e
        if g.entries:
            glossaries.append(g)
    return glossaries


async def run(spec: dict) -> None:
    import babeldoc
    from babeldoc.docvision.doclayout import DocLayoutModel
    from babeldoc.format.pdf import high_level
    from babeldoc.format.pdf.translation_config import TranslationConfig, WatermarkOutputMode
    from babeldoc.translator.translator import set_translate_rate_limiter

    emit("started", engine="babeldoc", version=babeldoc.__version__, pid=os.getpid(), protocol=PROTOCOL_VERSION)

    opts: dict = spec.get("options", {})
    model: dict = spec["model"]
    skip_translation = bool(spec.get("skip_translation"))
    api_key = os.environ.get("BDW_API_KEY") or ("sk-skip" if skip_translation else "")
    if not api_key:
        raise EngineError("preflight", "模型未配置 API Key")

    high_level.init()
    cls = make_translator_class()
    translator = build_translator(cls, spec, model, api_key)
    auto_extract = bool(opts.get("auto_extract_glossary", True)) and not skip_translation
    term_translator = translator
    if auto_extract and model.get("term"):
        # 另一个接口的配置：主密钥可能发给别人的服务器，只认它自己的密钥
        term_key = os.environ.get("BDW_TERM_API_KEY", "")
        if not term_key:
            raise EngineError("preflight", "术语提取模型未配置 API Key")
        term_translator = build_translator(cls, spec, model["term"], term_key)
    elif model.get("term_model"):
        term_translator = build_translator(
            cls, spec, {**model, "model": model["term_model"]}, os.environ.get("BDW_TERM_API_KEY") or api_key
        )
    separate_term = term_translator is not translator

    preflight_seconds = None
    if not skip_translation and spec.get("preflight", True):
        emit("progress", overall=0, stage="Check model API", current=0, total=1, part=1, parts=1)
        preflight_seconds = preflight(translator)
        if separate_term and auto_extract:
            try:
                preflight(term_translator)
            except EngineError as e:
                raise EngineError(e.kind, f"术语提取模型：{e.message}") from e

    set_translate_rate_limiter(max(1, int(model.get("qps") or 4)))
    doc_layout_model = DocLayoutModel.load_onnx()
    table_model = None
    if opts.get("translate_table_text"):
        from babeldoc.docvision.table_detection.rapidocr import RapidOCRModel

        table_model = RapidOCRModel()

    split_strategy = None
    if opts.get("max_pages_per_part"):
        split_strategy = TranslationConfig.create_max_pages_per_part_split_strategy(int(opts["max_pages_per_part"]))

    watermark = {
        "watermarked": WatermarkOutputMode.Watermarked,
        "both": WatermarkOutputMode.Both,
    }.get(opts.get("watermark_mode", ""), WatermarkOutputMode.NoWatermark)

    config = TranslationConfig(
        translator=translator,
        term_extraction_translator=term_translator,
        input_file=spec["input"],
        lang_in=spec["lang_in"],
        lang_out=spec["lang_out"],
        doc_layout_model=doc_layout_model,
        pages=opts.get("pages") or None,
        output_dir=spec["output_dir"],
        working_dir=spec.get("working_dir"),
        no_dual=bool(opts.get("no_dual")),
        no_mono=bool(opts.get("no_mono")),
        qps=max(1, int(model.get("qps") or 4)),
        pool_max_workers=model.get("pool_max_workers") or None,
        use_rich_pbar=False,
        report_interval=PROGRESS_MIN_INTERVAL,
        dual_translate_first=bool(opts.get("dual_translate_first")),
        use_alternating_pages_dual=bool(opts.get("use_alternating_pages_dual")),
        enhance_compatibility=bool(opts.get("enhance_compatibility")),
        watermark_output_mode=watermark,
        split_strategy=split_strategy,
        table_model=table_model,
        skip_scanned_detection=bool(opts.get("skip_scanned_detection")),
        ocr_workaround=bool(opts.get("ocr_workaround")),
        auto_enable_ocr_workaround=bool(opts.get("auto_enable_ocr_workaround", True)),
        custom_system_prompt=opts.get("custom_system_prompt") or None,
        glossaries=load_glossaries(opts.get("glossary_files") or [], spec["lang_out"]),
        auto_extract_glossary=auto_extract,
        save_auto_extracted_glossary=True,
        primary_font_family=opts.get("primary_font_family") or None,
        only_include_translated_page=bool(opts.get("only_include_translated_page")),
        skip_translation=skip_translation,
    )
    getattr(doc_layout_model, "init_font_mapper", lambda _c: None)(config)

    result = None
    last_emit = 0.0
    last_stage = None
    async for event in high_level.async_translate(config):
        kind = event["type"]
        if kind in ("progress_start", "progress_update", "progress_end"):
            now = time.monotonic()
            stage = event.get("stage")
            if kind == "progress_update" and stage == last_stage and now - last_emit < PROGRESS_MIN_INTERVAL:
                continue
            last_emit, last_stage = now, stage
            emit(
                "progress",
                overall=round(float(event.get("overall_progress", 0) or 0), 2),
                stage=stage,
                current=event.get("stage_current"),
                total=event.get("stage_total"),
                part=event.get("part_index", 1),
                parts=event.get("total_parts", 1),
            )
        elif kind == "error":
            raise describe_error(event.get("error"))
        elif kind == "finish":
            result = event["translate_result"]
            break

    if result is None:
        raise EngineError("internal", "翻译流程结束但没有产出结果")

    def path_or_none(p) -> str | None:
        return str(p) if p else None

    files = {
        "mono": path_or_none(getattr(result, "no_watermark_mono_pdf_path", None) or result.mono_pdf_path),
        "dual": path_or_none(getattr(result, "no_watermark_dual_pdf_path", None) or result.dual_pdf_path),
        "glossary": path_or_none(getattr(result, "auto_extracted_glossary_path", None)),
    }
    term_usage = dict(getattr(config, "term_extraction_token_usage", {}) or {})
    total_tokens = translator.token_count.value
    if separate_term:
        total_tokens += term_translator.token_count.value
    stats = {
        "total_tokens": total_tokens,
        "seconds": round(float(getattr(result, "total_seconds", 0) or 0), 2),
        "peak_memory_mb": round(float(getattr(result, "peak_memory_usage", 0) or 0), 1),
        "valid_chars": getattr(result, "total_valid_character_count", None),
        "preflight_seconds": round(preflight_seconds, 2) if preflight_seconds is not None else None,
        "tokens": {
            "total": translator.token_count.value,
            "prompt": translator.prompt_token_count.value,
            "completion": translator.completion_token_count.value,
            "cache_hit_prompt": translator.cache_hit_prompt_token_count.value,
        },
        "term_tokens": term_usage,
        "calls": {
            "total": translator.translate_call_count,
            "cached": translator.translate_cache_call_count,
            "api_ok": translator.api_ok,
            "api_errors": translator.api_errors,
        },
    }
    if separate_term:
        stats["term_calls"] = {"api_ok": term_translator.api_ok, "api_errors": term_translator.api_errors}

    if translator.api_errors and not translator.api_ok and not skip_translation:
        raise EngineError(
            "translate",
            f"所有 {translator.api_errors} 次翻译请求都失败了，译文无效。最后一次错误：{translator.last_error}",
        )
    warnings = []
    if translator.api_errors:
        warnings.append(f"{translator.api_errors} 次翻译请求失败，部分段落可能保留了原文")
    if separate_term and term_translator.api_errors:
        # 术语只影响一致性，不让任务失败
        warnings.append(f"术语提取有 {term_translator.api_errors} 次请求失败，术语表可能不完整")
    emit("finished", files=files, stats=stats, warning="；".join(warnings) or None)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: runner.py <spec.json>", file=sys.stderr)
        return 2
    open_channel()
    setup_logging()
    try:
        spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    except Exception as e:
        emit("failed", kind="internal", message=f"任务描述读取失败：{e}")
        return 2
    try:
        asyncio.run(run(spec))
    except EngineError as e:
        logging.error("engine error (%s): %s", e.kind, e.message)
        emit("failed", kind=e.kind, message=e.message)
        return 1
    except Exception as e:
        logging.exception("unexpected engine failure")
        try:
            err = describe_error(e)
            kind, message = ("internal" if err.kind == "translate" else err.kind), err.message
        except Exception:
            kind, message = "internal", f"{e.__class__.__name__}: {e}"[:2000]
        emit("failed", kind=kind, message=message)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
