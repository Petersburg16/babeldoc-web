# 第三方组件

本项目以 AGPL-3.0 发布。「PDF 处理」功能在浏览器里运行，下列组件随前端打包，或由本站 `/pdf-assets/` 按需分发给浏览器（版本与校验和见 `frontend/pdf-assets.lock.json`，来源见 `frontend/pdf-assets.json`）。各组件保留其原有许可证。

## 移植的代码

| 组件 | 许可证 | 说明 |
| --- | --- | --- |
| [BentoPDF](https://github.com/alam00000/bentopdf) v2.8.8 | AGPL-3.0 | PDF 工具的处理逻辑参考并移植自其 `src/js/logic`、`src/js/utils`，界面按本站设计重写；移植文件开头有注明 |

## 随前端打包的库

| 组件 | 许可证 |
| --- | --- |
| [@cantoo/pdf-lib](https://github.com/cantoo-scribe/pdf-lib) | MIT |
| [@cantoo/fontkit](https://github.com/cantoo-scribe/fontkit) | MIT |
| [PDF.js](https://github.com/mozilla/pdf.js)（pdfjs-dist） | Apache-2.0 |
| [qpdf-wasm](https://github.com/neslinesli93/qpdf-wasm) 的 JS 胶水代码 | ISC |
| [tesseract.js](https://github.com/naptha/tesseract.js) | Apache-2.0 |
| [LibreOffice WASM 转换器](https://github.com/matbeedotcom/libreoffice-document-converter)（@matbee/libreoffice-converter） | MPL-2.0 |
| [fflate](https://github.com/101arrowz/fflate) | MIT |
| [zod](https://github.com/colinhacks/zod) | MIT |
| [markdown-it](https://github.com/markdown-it/markdown-it)（会议纪要与对话的渲染） | MIT |

## 由本站分发的引擎与数据

| 引擎 | 组件 | 许可证 |
| --- | --- | --- |
| render | PDF.js 的 CMap、标准字体（含 Liberation Sans，页码工具在 PDF/A 等文件里会嵌入其子集）、OpenJPEG / JBIG2 / qcms 解码器 | Apache-2.0；CMap 为 BSD-3-Clause（Adobe）；Liberation 字体为 SIL OFL 1.1；其余字体与解码器见其中的 LICENSE 文件 |
| qpdf | [qpdf](https://github.com/qpdf/qpdf) 12.2.0 | Apache-2.0 |
| ocr | [Tesseract](https://github.com/tesseract-ocr/tesseract)（tesseract.js-core）、[tessdata_best](https://github.com/tesseract-ocr/tessdata_best) 英文与简体中文模型 | Apache-2.0 |
| ghostscript | [Ghostscript](https://ghostscript.com/) 10.06.0（[@bentopdf/gs-wasm](https://github.com/alam00000/bentopdf-gs-wasm)） | AGPL-3.0 |
| pymupdf | [Pyodide](https://github.com/pyodide/pyodide) | MPL-2.0 |
| pymupdf | [PyMuPDF](https://github.com/pymupdf/PyMuPDF) / MuPDF（[@bentopdf/pymupdf-wasm](https://github.com/alam00000/bentopdf-pymupdf-wasm)） | AGPL-3.0 |
| pdf2docx | [pdf2docx](https://github.com/ArtifexSoftware/pdf2docx) | GPL-3.0 |
| pdf2docx | python-docx、fonttools | MIT |
| pdf2docx | numpy、lxml | BSD-3-Clause |
| pdf2docx | opencv-python | Apache-2.0 |
| pdf2docx | typing_extensions | PSF-2.0 |
| libreoffice | [LibreOffice](https://www.libreoffice.org/) WASM 构建（@matbee/libreoffice-converter） | MPL-2.0 |
| font-sans / font-serif | [思源黑体 / 思源宋体](https://github.com/adobe-fonts)（取自 [BabelDOC-Assets](https://github.com/funstory-ai/BabelDOC-Assets)） | SIL OFL 1.1 |
| font-kai | [霞鹜文楷](https://github.com/lxgw/LxgwWenKai) | SIL OFL 1.1 |
| font-fang | [朱雀仿宋](https://github.com/TrionesType/zhuque) | SIL OFL 1.1 |

GPL-3.0 与 AGPL-3.0 第 13 条允许与本项目组合发布；对应源码见本仓库及上表各组件的上游仓库。

## 会议记录（后端）

| 组件 | 许可证 | 说明 |
| --- | --- | --- |
| [python-docx](https://github.com/python-openxml/python-docx) | MIT | 导出 Word |
| [lxml](https://github.com/lxml/lxml) | BSD-3-Clause | python-docx 的依赖 |
| [OpenTypeless](https://github.com/tover0314-w/opentypeless) | MIT | 逐字稿整理提示词的规则参考了它的 `BASE_PROMPT`、`THOUGHT_AWARE_RULES`，中文提示词为本项目重新编写，没有复制代码 |
| [Cherry Studio](https://github.com/CherryHQ/cherry-studio) | AGPL-3.0 | 会议大模型参数的规则参考了它的 `packages/provider-registry`：OpenAI 系列模型的思考档位表（`backend/app/meeting/llm_config.py` 的 `EFFORT_RULES`）、档位就近映射、温度等参数的开关与自定义参数的合并方式；代码为本项目重新编写 |

语音识别由阿里云百炼、阿里云通义听悟、腾讯云的在线接口完成，本项目只按官方文档调用其 HTTP 接口，不包含它们的 SDK 或代码。
