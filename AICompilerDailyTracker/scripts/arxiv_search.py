#!/usr/bin/env python3
"""arXiv AI 编译器主题检索脚本（AICompilerDailyTracker 技能专用）。

按提交日期区间检索 arXiv 上与 AI 编译器相关的论文，输出 JSON Lines（stdout 或 --out）。
只用标准库，无需 pip 依赖。遵守 arXiv API 礼仪：请求间隔 >= 3 秒。

用法：
  python3 arxiv_search.py --start 2026-11-15 --end 2026-11-15
  python3 arxiv_search.py --start 2026-11-01 --end 2026-11-15 --out candidates.jsonl

输出字段（每行一条）：
  arxiv_id / version / url(abs页) / pdf_url / title / authors / published / updated /
  categories / primary_category / abstract / queries(命中的查询组) / score(关键词相关度)

退出码：0 成功（可能 0 条）；2 网络失败；3 参数错误。
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

API = "http://export.arxiv.org/api/query"
NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
UA = "Mozilla/5.0 (AICompilerDailyTracker skill; research use)"

# 检索策略：小类全量扫 + 大类关键词组，全部与日期区间做 AND
QUERY_GROUPS = [
    # (组名, arXiv query 中除日期外的部分)
    ("cs.PL-full", "cat:cs.PL"),
    ("cs.AR-full", "cat:cs.AR"),
    ("compiler-kw",
     '(cat:cs.DC OR cat:cs.LG OR cat:cs.SE OR cat:cs.PF OR cat:cs.OS) AND '
     '(abs:"compiler" OR abs:"MLIR" OR abs:"LLVM" OR abs:"TVM" OR abs:"XLA" '
     'OR abs:"code generation" OR abs:"intermediate representation" '
     'OR abs:"autotuning" OR abs:"auto-tuning" OR abs:"polyhedral" '
     'OR abs:"vectorization" OR abs:"superoptimizer" OR abs:"e-graph" '
     'OR abs:"equality saturation" OR abs:"kernel generation")'),
    ("llm-system-kw",
     '(cat:cs.DC OR cat:cs.LG OR cat:cs.OS OR cat:cs.AR) AND '
     '(abs:"LLM inference" OR abs:"LLM serving" OR abs:"LLM training" '
     'OR abs:"large language model" AND (abs:"serving" OR abs:"inference engine" '
     'OR abs:"scheduling" OR abs:"AI infrastructure" OR abs:"training system") '
     'OR abs:"KV cache" OR abs:"KVCache" OR abs:"PagedAttention" OR abs:"vLLM" '
     'OR abs:"speculative decoding" OR abs:"tensor parallelism" OR abs:"pipeline parallelism")'),
    ("hw-accel-kw",
     '(cat:cs.AR OR cat:cs.DC) AND '
     '(abs:"processing-in-memory" OR abs:"processing in memory" OR abs:"compute-in-memory" '
     'OR abs:"compute in memory" OR abs:"in-memory computing" OR abs:"CGRA" '
     'OR abs:"coarse-grained reconfigurable" OR abs:"DNN accelerator" '
     'OR abs:"neural accelerator" OR abs:"AI accelerator" OR abs:"tensor core" '
     'OR abs:"dataflow accelerator" OR abs:"high-level synthesis")'),
]

# 相关度打分：标题命中权重高于摘要；核心词权重高于泛化词
SCORE_RULES = [
    # (正则, 标题权重, 摘要权重)
    (r"\bcompiler\b|compilation|MLIR|LLVM|TVM|XLA|torch\.compile|inductor|polyhedral", 6, 2),
    (r"intermediate representation|code generation|autotun|auto-tun|superopt|super-optimiz", 5, 2),
    (r"e-graph|equality saturation|vectori[sz]|loop (optimi|transformation)|kernel (fusion|generation|schedul)", 4, 1),
    (r"LLM (inference|serving|training)|KV ?[Cc]ache|PagedAttention|vLLM|speculative decoding", 6, 2),
    (r"(AI|ML|DNN|LLM) (infra|infrastructure|system)|large language model|foundation model", 4, 1),
    (r"(GPU|accelerator|tensor core) (kernel|schedul|optimi)|kernel (launch|orchestration)", 3, 1),
    (r"processing[- ]in[- ]memory|compute[- ]in[- ]memory|in-memory comput|PIM\b|存算", 6, 2),
    (r"CGRA|coarse-grained reconfigurable|reconfigurable array|high-level synthesis|HLS\b", 5, 2),
    (r"(DNN|neural network|deep learning|LLM) (accelerator|mapping|offload)|accelerat(or|ing) (DNN|LLM|transformer)", 4, 1),
    (r"tensor (program|compiler|expression)|Halide|Triton|CUDA kernel", 5, 2),
    (r"machine learning (for|based) (compiler|optimi)|learned (cost model|optimi|heuristi)|LLM[s]? (for|based) (code|compiler)", 5, 2),
]
# 负分：明显无关的高频误命中词（仅用标题与摘要前 400 字符粗筛）
NEG_RULES = [
    (r"quantum compiler|biological|medical|chemistry|climate|protein", -8, -3),
]


def parse_args():
    ap = argparse.ArgumentParser(description="arXiv AI-compiler topic search by date range")
    ap.add_argument("--start", required=True, help="起始日期 YYYY-MM-DD（含）")
    ap.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD（含）")
    ap.add_argument("--out", default=None, help="输出 JSONL 文件路径，缺省写 stdout")
    ap.add_argument("--max-per-query", type=int, default=200, help="每个查询组最大返回条数")
    ap.add_argument("--groups", default=None,
                    help="只跑指定查询组，逗号分隔（如 cs.PL-full,compiler-kw）；缺省跑全部")
    ap.add_argument("--min-score", type=float, default=0.0,
                    help="相关性得分下限，低于该分的不输出（cs.PL/cs.AR 全量组默认豁免）")
    return ap.parse_args()


def daterange_expr(start: str, end: str) -> str:
    s = re.sub(r"-", "", start) + "0000"
    e = re.sub(r"-", "", end) + "2359"
    return f"submittedDate:[{s} TO {e}]"


def fetch(query: str, max_results: int, retries: int = 3):
    params = urllib.parse.urlencode({
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    })
    url = f"{API}?{params}"
    last_err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", "ignore")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            last_err = e
            time.sleep(5 * (i + 1))  # 网络抖动退避
    raise RuntimeError(f"arXiv API 请求失败（已重试 {retries} 次）: {url}\n最后错误: {last_err}")


def parse_feed(xml_text: str):
    root = ET.fromstring(xml_text)
    papers = []
    for entry in root.findall("atom:entry", NS):
        id_full = (entry.findtext("atom:id", "", NS) or "").strip()
        m = re.search(r"abs/([^v]+)(v(\d+))?$", id_full)
        if not m:
            continue
        arxiv_id, ver = m.group(1), m.group(3) or "1"
        authors = [a.findtext("atom:name", "", NS) for a in entry.findall("atom:author", NS)]
        cats = [c.get("term") for c in entry.findall("atom:category", NS) if c.get("term")]
        links = {l.get("title"): l.get("href") for l in entry.findall("atom:link", NS) if l.get("title")}
        papers.append({
            "arxiv_id": arxiv_id,
            "version": ver,
            "url": f"https://arxiv.org/abs/{arxiv_id}",
            "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
            "title": re.sub(r"\s+", " ", entry.findtext("atom:title", "", NS)).strip(),
            "authors": [a.strip() for a in authors if a.strip()],
            "published": entry.findtext("atom:published", "", NS),
            "updated": entry.findtext("atom:updated", "", NS),
            "primary_category": (entry.find("arxiv:primary_category", NS).get("term")
                                 if entry.find("arxiv:primary_category", NS) is not None
                                 else (cats[0] if cats else "")),
            "categories": cats,
            "abstract": re.sub(r"\s+", " ", entry.findtext("atom:summary", "", NS)).strip(),
        })
    return papers


def score_paper(p: dict) -> float:
    title, abstract = p["title"].lower(), p["abstract"].lower()
    abs_head = abstract[:400]
    s = 0.0
    for pat, wt, wa in SCORE_RULES:
        if re.search(pat, title, re.I):
            s += wt
        elif re.search(pat, abstract, re.I):
            s += wa
    for pat, wt, wa in NEG_RULES:
        if re.search(pat, title, re.I):
            s += wt
        elif re.search(pat, abs_head, re.I):
            s += wa
    return s


def in_range(published: str, start: str, end: str) -> bool:
    d = published[:10]
    return start <= d <= end


def main():
    args = parse_args()
    for tag, name in (("start", args.start), ("end", args.end)):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", name):
            sys.exit(f"3|参数错误: --{tag} 需要 YYYY-MM-DD 格式，收到 {name!r}")
    if args.start > args.end:
        sys.exit("3|参数错误: --start 晚于 --end")

    date_expr = daterange_expr(args.start, args.end)
    groups = QUERY_GROUPS
    if args.groups:
        want = {g.strip() for g in args.groups.split(",") if g.strip()}
        groups = [g for g in QUERY_GROUPS if g[0] in want]

    merged = {}
    errors = []
    for i, (gname, cond) in enumerate(groups):
        if i:
            time.sleep(3)  # arXiv API 礼仪间隔
        query = f"{date_expr} AND ({cond})"
        try:
            xml_text = fetch(query, args.max_per_query)
        except RuntimeError as e:
            errors.append(f"[{gname}] {e}")
            continue
        for p in parse_feed(xml_text):
            # submittedDate 过滤后仍需以 published(v1) 精确校验落在区间内
            if not in_range(p["published"], args.start, args.end):
                continue
            key = p["arxiv_id"]
            if key not in merged:
                p["queries"] = [gname]
                merged[key] = p
            else:
                if gname not in merged[key]["queries"]:
                    merged[key]["queries"].append(gname)

    results = []
    for p in merged.values():
        p["score"] = score_paper(p)
        full_sweep = bool(set(p["queries"]) & {"cs.PL-full", "cs.AR-full"})
        if p["score"] < args.min_score and not full_sweep:
            continue
        results.append(p)
    results.sort(key=lambda x: (-x["score"], x["published"]), reverse=False)

    out_lines = [json.dumps(p, ensure_ascii=False) for p in results]
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("\n".join(out_lines) + ("\n" if out_lines else ""))
    else:
        for line in out_lines:
            print(line)

    # 人类可读的运行摘要走 stderr，不污染 stdout 的 JSONL
    print(f"[summary] 区间 {args.start}~{args.end}：候选 {len(results)} 条 "
          f"(全量组豁免相关度过滤)。查询组: {', '.join(g[0] for g in groups)}。", file=sys.stderr)
    if errors:
        for e in errors:
            print(f"[warn] {e}", file=sys.stderr)
        print("[warn] 部分查询组失败，可单独重跑: --groups <组名>", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
