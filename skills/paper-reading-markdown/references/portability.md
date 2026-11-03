# 迁移、复用与运行

## 整个目录一起带走

复制 `paper-reading-markdown/` 全目录，保持 `SKILL.md`、`references/`、`assets/`、`scripts/` 的相对结构。不要只复制入口提示词；入口会按问题引用详细规则。

具备 skill 功能的 agent：把目录放到它的技能目录，按该产品支持的发现方式调用。没有 skill 系统的 agent：先读取目录中的 `SKILL.md`，再按入口引用读取参考文件并执行任务。脚本没有对当前电脑的固定路径依赖。

本包的 `agents/openai.yaml` 仅提供 Codex 显示名、介绍和示例调用；其他 agent 不支持该文件时仍可使用核心规范。复制文件不等于自动授予文件访问、联网或执行权限，遵守目标环境权限。

## 典型调用

```text
使用 $paper-reading-markdown，把这篇论文整理成我和 AI 都能顺序阅读的 Markdown。
保留完整正文、图表和注释；脚注要能对应原文位置并双向跳转，方便在 VS Code 阅读。
输入：<PDF路径>
```

不支持 `$skill` 调用的 agent：

```text
请先读取 <技能目录>/paper-reading-markdown/SKILL.md，并按其中工作流与引用规则处理。
输入：<PDF路径>
输出：<目标目录>
```

修复已有结果：

```text
使用 paper-reading-markdown 对照原 PDF 检查已有 Markdown，修复串栏、跨页断句和脚注错配。
保留已有人工修改与稳定锚点；报告无法确认的原文位置。
```

批量处理：

```text
使用 paper-reading-markdown 整理指定目录中的论文，每篇独立生成阅读版和必要图片目录。
先验证代表页的提取规则，再逐篇核对源文清单与注释映射；不能批量候选稿生成后就报完成。
```

低 token 验收已有稿：

```text
使用 paper-reading-markdown 验收 <Markdown目录>，原 PDF 在 <PDF目录>。
先批量脚本筛查，只精读疑点；逐篇给已符合项、问题、Markdown行号与PDF页号。
不要修改原稿。自动通过不等于全文通过；未检查的部分如实说明。
```

## 依赖和命令

Python 3.10+。检查器只用标准库。PDF 检查脚本需要 PyMuPDF：先检查现有库，必要时用目标环境允许的方法安装 `pymupdf`；渲染没有本地 TeX、Office 或浏览器硬依赖。

以下命令中的 `scripts/` 相对技能目录；若工作目录不同，改用脚本绝对路径：

```text
python scripts/inspect_pdf.py "input.pdf" --out "work/inspect" --render contact
python scripts/inspect_pdf.py "input.pdf" --out "work/inspect-detail" --render selected --pages "1,3-5"
python scripts/check_markdown.py "input.md"
python scripts/check_markdown.py "input.md" --manifest "work/manifest.json" --report "work/check.json"
python scripts/audit_directory.py "markdown" --pdf-dir "pdf" --out "work/batch-audit"
python scripts/audit_directory.py "markdown" --structure-only --out "work/structure-audit"
python scripts/self_test.py
python scripts/test_audit_directory.py
```

检查命令支持中文和空格路径。脚本报缺依赖或加密时指出具体原因，不自动上传文献到其他服务。已有 OCR/转换器可按需使用，它们不是本包强制组件。

`self_test.py` 在系统临时目录测试错误注释往返、同注多引用、每页重编号、缺图、清单遗漏、代码块隔离和只读PDF检查；不会修改用户论文。未安装 PyMuPDF 时仅跳过PDF检查测试，其余标准库测试照常执行。

## 交接给下一个 agent

说明输入、正在使用的阅读版、工作目录、原文哈希、已完成项和未解决位置。若已有 manifest，交接该文件；不要只交一句“基本完成”。不要把 `review` 中未做的项目改成已完成。

已有任务结果用来参考风格，不能作为新论文的内容来源。前两篇实例的页数、脚注数、表图数量只是各自事实，不成为本技能的固定规则。
