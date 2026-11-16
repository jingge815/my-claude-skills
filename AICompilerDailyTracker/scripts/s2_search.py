#!/usr/bin/env python3
"""Semantic Scholar 检索脚本（AICompilerDailyTracker 技能专用）。

补充 arXiv 之外的正式发表源：期刊（TPDS/TOCS/TOPLAS/TACO 等）与会议论文按
publicationDate 精确到日检索。走 bulk search 端点（限流宽松），逐日逐查询组请求，
输出 JSON Lines。只用标准库。

注意：
- S2 bulk 端点的 `publicationDateOrJournalPublicationDate` 服务端过滤实测不生效
  （无论单日还是冒号区间格式，都混入全年代结果），日期精确性完全靠本脚本本地硬校验；
- 无 API key 限流严重（实测本机会持续 429），已内置短退避 + 连续失败熔断 + 全局时限
  （--max-seconds，默认 240s），超时带已有结果收束：保证有界运行，不会挂死技能；
- S2 query 不支持布尔运算，仅关键词/引号短语匹配；结果按相关度排序翻页（--max-pages），
  窄词（如 MLIR）可全覆盖，宽词存在截断漏检，故默认词表全为窄词；
- venue 字段可能为空串，正式出处以 externalIds.DOI/DBLP 与 dblp 交叉核验为准。

用法：
  python3 s2_search.py --start 2026-11-12 --end 2026-11-12
  python3 s2_search.py --start 2026-11-10 --end 2026-11-12 --out /tmp/s2.jsonl

退出码：0 成功（可能 0 条）；2 网络/限流失败；3 参数错误。
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BULK = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
FIELDS = "title,authors,venue,publicationDate,publicationTypes,externalIds,abstract,url,citationCount"
UA = "Mozilla/5.0 (AICompilerDailyTracker skill; research use)"

# 查询词：S2 query 无布尔运算、服务端日期过滤实测失效（本地硬校验兜底），
# 所以只用窄词——窄词（如 MLIR）的匹配集小，翻页可全覆盖；宽词只取前几页（相关度序）
QUERY_TERMS = [
    "MLIR",
    "LLVM compiler optimization",
    "TVM tensor compiler",
    "Triton kernel compiler",
    "code generation GPU kernel",
    "polyhedral compilation",
    "LLM inference system",
    "KV cache LLM inference",
    "vLLM PagedAttention",
    "LLM serving scheduler",
    "processing-in-memory LLM",
    "CGRA compiler mapping",
    "DNN accelerator code generation",
]

SCORE_RULES = [
    (r"\bcompiler\b|compilation|MLIR|LLVM|TVM|XLA|torch\.compile|inductor|polyhedral", 6, 2),
    (r"intermediate representation|code generation|autotun|auto-tun|superoptim|e-graph|equality saturation", 5, 2),
    (r"LLM (inference|serving|training)|KV ?[Cc]ache|PagedAttention|vLLM|speculative decoding|tensor parallel", 6, 2),
    (r"processing[- ]in[- ]memory|compute[- ]in[- ]memory|PIM\b|CGRA|reconfigurable array", 6, 2),
    (r"(AI|ML|DNN|neural|LLM) (accelerator|infra|system)|tensor core|dataflow accelerator", 4, 1),
    (r"high-level synthesis|kernel (fusion|generation|schedul|launch)|FPGA accelerator", 4, 1),
    (r"machine learning (for|based) compiler|learned cost model|LLM[s]? (for|based) (code|compil)", 5, 2),
]
NEG_RULES = [
    (r"quantum|biolog|medic|chemistr|protein|climate|survey|review of literature", -6, -2),
]


def parse_args():
    ap = argparse.ArgumentParser(description="Semantic Scholar bulk search by exact date range")
    ap.add_argument("--start", required=True, help="起始日期 YYYY-MM-DD（含）")
    ap.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD（含）")
    ap.add_argument("--out", default=None, help="输出 JSONL 路径，缺省 stdout")
    ap.add_argument("--terms", default=None, help="自定义查询词，分号分隔；缺省用内置组")
    ap.add_argument("--min-score", type=float, default=4.0, help="相关度下限（低于则丢弃）")
    ap.add_argument("--sleep", type=float, default=1.2, help="相邻请求间隔秒数")
    ap.add_argument("--max-seconds", type=float, default=240,
                    help="全局时限：超时后带着已有结果收束（S2 无 key 限流严重时保证有界运行）")
    ap.add_argument("--max-pages", type=int, default=3,
                    help="每个查询词最多翻页数（每页1000，相关度序）：窄词全覆盖，宽词截断")
    return ap.parse_args()


def fetch_term(term: str, date_range: str, token: str = None, deadline: float = None):
    params = {
        "query": term,
        "fields": FIELDS,
        "publicationDateOrJournalPublicationDate": date_range,  # 冒号区间，单日会被忽略
    }
    if token:
        params["token"] = token
    url = f"{BULK}?{urllib.parse.urlencode(params)}"
    last = None
    for i in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8", "ignore"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503):
                wait = 4 * (i + 1)  # 4/8/12/16s：限流常见，快速退避快速放弃
                last = f"HTTP {e.code}"
                if deadline is not None and time.monotonic() + wait > deadline:
                    raise RuntimeError(f"S2 API 持续限流（{last}）且已达时限: term={term!r}")
                time.sleep(wait)
                continue
            raise RuntimeError(f"S2 API HTTP {e.code}: {url}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"S2 API 重试 4 次仍失败（{last}）: term={term!r}")


def score_paper(title: str, abstract: str) -> float:
    t, a = (title or "").lower(), (abstract or "").lower()
    s = 0.0
    for pat, wt, wa in SCORE_RULES:
        if re.search(pat, t, re.I):
            s += wt
        elif re.search(pat, a, re.I):
            s += wa
    for pat, wt, wa in NEG_RULES:
        if re.search(pat, t, re.I):
            s += wt
        elif re.search(pat, a[:300], re.I):
            s += wa
    return s


def main():
    args = parse_args()
    for tag, name in (("start", args.start), ("end", args.end)):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", name):
            sys.exit(f"3|参数错误: --{tag} 需要 YYYY-MM-DD，收到 {name!r}")

    terms = [t.strip() for t in args.terms.split(";") if t.strip()] if args.terms else QUERY_TERMS
    date_range = f"{args.start}:{args.end}"  # S2 bulk 冒号区间；单日为 X:X
    merged, errors = {}, []
    n_req = 0
    consecutive_fail = 0  # 连续失败熔断：限流持续时尽早保住已有结果
    abort = None
    t_start = time.monotonic()
    t_deadline = t_start + args.max_seconds
    for term in terms:
        if abort:
            break
        if time.monotonic() > t_deadline:
            abort = f"已达全局时限 {args.max_seconds:.0f}s，带着已有结果收束"
            break
        if n_req:
            time.sleep(args.sleep)
        n_req += 1
        token = None
        page = 0
        try:
            while page < args.max_pages:  # 翻页（bulk 每页 1000，相关度序）
                data = fetch_term(term, date_range, token, t_deadline)
                for p in data.get("data", []):
                    pid = p.get("paperId")
                    if not pid:
                        continue
                    if pid not in merged:
                        merged[pid] = p
                token = data.get("token")
                page += 1
                if not token or not data.get("data"):
                    break
            consecutive_fail = 0
        except RuntimeError as e:
            errors.append(str(e))
            consecutive_fail += 1
            if consecutive_fail >= 4:
                abort = f"连续 {consecutive_fail} 个请求失败（多为持续限流），提前收束保住已有结果"
    if not merged and errors:
        for e in errors[:5]:
            print(f"[error] {e}", file=sys.stderr)
        sys.exit("2|Semantic Scholar 全部请求失败（多为限流），本次跳过 S2 补充源")

    results = []
    dropped_date = 0
    for p in merged.values():
        # 本地日期硬校验：S2 过滤参数可能被忽略，publicationDate 必须落在区间内
        pd_ = p.get("publicationDate")
        if not pd_ or not (args.start <= pd_ <= args.end):
            dropped_date += 1
            continue
        sc = score_paper(p.get("title", ""), p.get("abstract") or "")
        if sc < args.min_score:
            continue
        ext = p.get("externalIds") or {}
        p["score"] = sc
        p["source"] = "semantic-scholar"
        p["doi_url"] = f"https://doi.org/{ext['DOI']}" if ext.get("DOI") else None
        p["dblp_url"] = f"https://dblp.org/rec/{ext['DBLP']}.html" if ext.get("DBLP") else None
        p["arxiv_url"] = f"https://arxiv.org/abs/{ext['ArXiv']}" if ext.get("ArXiv") else None
        results.append(p)
    results.sort(key=lambda x: (-x["score"], x.get("publicationDate") or ""))

    lines = [json.dumps(p, ensure_ascii=False) for p in results]
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
    else:
        for ln in lines:
            print(ln)

    warn = f"，失败 {len(errors)} 个请求" if errors else ""
    print(f"[summary] S2 区间 {args.start}~{args.end}：{len(terms)} 个查询词，"
          f"原始 {len(merged)} 条，日期硬校验剔除 {dropped_date} 条，"
          f"过滤后 {len(results)} 条{warn}。", file=sys.stderr)
    if abort:
        print(f"[warn] {abort}", file=sys.stderr)
    if errors:
        print("[warn] 部分请求失败，结果可能不完整", file=sys.stderr)


if __name__ == "__main__":
    main()
