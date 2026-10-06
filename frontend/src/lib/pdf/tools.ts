// PDF 工具清单：顺序即工具页里的展示顺序。每个工具的界面单独按需加载，
// engines 列出它运行时需要的引擎（体积与缓存状态见 engines.ts），首页据此提示首次使用要下载多少。
import type { Component } from 'svelte';
import { FileText, Lock } from '../icons';
import {
  Combine,
  Droplets,
  FileImage,
  FileSpreadsheet,
  Hash,
  Images,
  LayoutGrid,
  LockOpen,
  PackageCheck,
  RotateCw,
  ScanText,
  Shrink,
  Split,
  Tags,
} from './icons';
import type { EngineId } from './engines.svelte';

export type CategoryId = 'pages' | 'edit' | 'convert' | 'optimize' | 'security';

export interface ToolDef {
  id: string;
  name: string;
  desc: string;
  category: CategoryId;
  icon: Component;
  engines: EngineId[];
  load: () => Promise<{ default: Component }>;
}

export const CATEGORIES: { id: CategoryId; name: string }[] = [
  { id: 'pages', name: '页面整理' },
  { id: 'edit', name: '编辑' },
  { id: 'convert', name: '格式转换' },
  { id: 'optimize', name: '优化与识别' },
  { id: 'security', name: '安全' },
];

export const TOOLS: ToolDef[] = [
  {
    id: 'merge',
    name: '合并 PDF',
    desc: '把多个 PDF 按顺序合成一个，可只取每个文件的部分页',
    category: 'pages',
    icon: Combine,
    engines: ['qpdf'],
    load: () => import('../../pages/pdf/tools/Merge.svelte'),
  },
  {
    id: 'split',
    name: '拆分 PDF',
    desc: '按页码范围、每 N 页或逐页拆分，也可提取指定页',
    category: 'pages',
    icon: Split,
    engines: ['qpdf'],
    load: () => import('../../pages/pdf/tools/Split.svelte'),
  },
  {
    id: 'organize',
    name: '页面整理',
    desc: '看着缩略图拖动排序、旋转、删除页面，插入空白页',
    category: 'pages',
    icon: LayoutGrid,
    engines: ['qpdf', 'render'],
    load: () => import('../../pages/pdf/tools/Organize.svelte'),
  },
  {
    id: 'rotate',
    name: '旋转 PDF',
    desc: '把全部或部分页面旋转 90°、180° 或 270°',
    category: 'pages',
    icon: RotateCw,
    engines: ['qpdf'],
    load: () => import('../../pages/pdf/tools/Rotate.svelte'),
  },
  {
    id: 'watermark',
    name: '添加水印',
    desc: '文字（支持中文）或图片水印，可调透明度、角度和平铺',
    category: 'edit',
    icon: Droplets,
    engines: ['qpdf'],
    load: () => import('../../pages/pdf/tools/Watermark.svelte'),
  },
  {
    id: 'page-numbers',
    name: '添加页码',
    desc: '在页眉或页脚加页码，支持“第 1 页”“1 / 10”等格式',
    category: 'edit',
    icon: Hash,
    engines: ['qpdf'],
    load: () => import('../../pages/pdf/tools/PageNumbers.svelte'),
  },
  {
    id: 'metadata',
    name: '文档属性',
    desc: '查看、修改或清除标题、作者、关键词等元数据',
    category: 'edit',
    icon: Tags,
    engines: ['qpdf'],
    load: () => import('../../pages/pdf/tools/Metadata.svelte'),
  },
  {
    id: 'pdf-to-image',
    name: 'PDF 转图片',
    desc: '把页面导出为 PNG 或 JPG，可选分辨率',
    category: 'convert',
    icon: FileImage,
    engines: ['qpdf', 'render'],
    load: () => import('../../pages/pdf/tools/PdfToImage.svelte'),
  },
  {
    id: 'image-to-pdf',
    name: '图片转 PDF',
    desc: '把 JPG、PNG、WebP 等图片合成一个 PDF',
    category: 'convert',
    icon: Images,
    engines: ['core'],
    load: () => import('../../pages/pdf/tools/ImageToPdf.svelte'),
  },
  {
    id: 'pdf-to-word',
    name: 'PDF 转 Word',
    desc: '转成可编辑的 .docx，适合文字为主的文档',
    category: 'convert',
    icon: FileText,
    engines: ['qpdf', 'pymupdf', 'pdf2docx'],
    load: () => import('../../pages/pdf/tools/PdfToWord.svelte'),
  },
  {
    id: 'office-to-pdf',
    name: 'Office 转 PDF',
    desc: 'Word、Excel、PowerPoint 转 PDF，中文字体按常用字体替换',
    category: 'convert',
    icon: FileSpreadsheet,
    engines: ['libreoffice'],
    load: () => import('../../pages/pdf/tools/OfficeToPdf.svelte'),
  },
  {
    id: 'pdfa',
    name: '转为 PDF/A',
    desc: '转成长期归档格式，常用于学位论文、档案提交',
    category: 'convert',
    icon: PackageCheck,
    engines: ['qpdf', 'ghostscript'],
    load: () => import('../../pages/pdf/tools/PdfA.svelte'),
  },
  {
    id: 'compress',
    name: '压缩 PDF',
    desc: '压缩图片、清理冗余对象，文字仍可选中和搜索',
    category: 'optimize',
    icon: Shrink,
    engines: ['qpdf', 'ghostscript'],
    load: () => import('../../pages/pdf/tools/Compress.svelte'),
  },
  {
    id: 'ocr',
    name: 'OCR 文字识别',
    desc: '识别扫描件中的中英文，生成可搜索、可复制的 PDF',
    category: 'optimize',
    icon: ScanText,
    engines: ['qpdf', 'render', 'ocr'],
    load: () => import('../../pages/pdf/tools/Ocr.svelte'),
  },
  {
    id: 'protect',
    name: '加密 PDF',
    desc: '设置打开密码，限制打印、复制和修改',
    category: 'security',
    icon: Lock,
    engines: ['qpdf'],
    load: () => import('../../pages/pdf/tools/Protect.svelte'),
  },
  {
    id: 'unlock',
    name: '解除密码',
    desc: '用已知密码去掉打开密码，或解除打印、复制限制',
    category: 'security',
    icon: LockOpen,
    engines: ['qpdf'],
    load: () => import('../../pages/pdf/tools/Unlock.svelte'),
  },
];

export function findTool(id: string) {
  return TOOLS.find((t) => t.id === id);
}
