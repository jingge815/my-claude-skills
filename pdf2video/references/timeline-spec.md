# timeline.json 契约（时间轴与手工微调指南）

`build_timeline.py` 产出 `timeline.json` + `subtitles.srt`；`render_video.py`
只认 timeline.json。它是三路对齐（声音/字幕/画面）的唯一事实源。

## 时间约定（易混，注意）

- `pages[].t0` / `lines[].t`：**全片时间**（秒，相对成片开头）；
- `segments[].t` / `zooms[].t` / `cursor[].t`：**页内时间**（秒，相对该页开头，
  含 lead_in）。页内 0 = 该页段视频第一帧。

## 顶层

```json
{"fps":30, "size":[1920,1080], "total_dur":692.1, "fade":0.5,
 "subtitle_style":{"font","size","primary","outline","margin_v"},
 "srt":"subtitles.srt", "srt_count":241,
 "pages":[...]}
```

## pages[] 每项

| 字段 | 说明 |
|---|---|
| idx | 段视频序号（seg-NN.mp4）；0 = 开场封面卡，1..N = PPT 第 N 页 |
| page_no | 对应 content.json 页码（封面卡为 0） |
| kind | video-cover / cover / text-left / status / figure-left / refs / closing |
| slide | 底图路径（相对工作目录）；封面卡用 cover/cover_2x.png |
| audio / audio_dur | 该页解说音频与时长；无旁白页为 null |
| lead_in / lead_out / seg_dur | 页内留白与总段长 |
| ken_burns | 开场封面卡整段缓慢推近 |
| lines | 字幕行：`{"text", "t":[全局起,全局止], "page"}`（供 srt 与门禁） |
| segments | 讲解段：`{"t":[页内起,止], "text", "region", "fig"}` |
| regions | 该页区域几何（1080p 像素）：title/body/summary/figures[] |
| cursor | 光标关键帧：`{"t","x","y","click","fade_out"}`（页内时间） |
| zooms | 局部放大：`{"t":[起,止], "box":[x,y,w,h]}`（页内时间） |

## region 取值

`figure`（配 fig=图区下标，指 slots.json 的 figures[]） / `body` / `summary` /
`title`。光标移动目标、高亮框、聚焦压暗都按它定位。

## 分派链（自动，可手改）

- 指图优先级：旁白「图 N / 表 N」→ content.json figure.ref 编号精确匹配 →
  brief/axes 词面重合 → 左|上|第一→fig0 等位置词兜底 → 默认第 0 张；
  光标在图内按「左上/右下/中间」等位置词落九宫格点；
- 网址/链接/arXiv → body；总结/回顾/要点/启发 → summary；refs 页恒 body；
- closing 页 → summary（Take-away 框）；
- 其余 → body。
讲图段时长 ≥ `[video] zoom_min_sec` 才排放大；放大框必须 16:9、**完整包含图区
（外扩 24px）**、**不与正文/标题/总结等其他区域相交**（否则框边把文字切半截）、
不出画布；居中放不下就左右锚定，都不行就放弃放大（保留高亮）。相邻同框放大
段自动合并（间隙 <1.2s）。

## 手工微调场景

| 问题 | 改法 |
|---|---|
| 光标停在错的图上 | 改该 segment 的 `fig` 下标（figures[] 顺序：上到下、左到右） |
| 该放大没放大 | 给该 segment 手工加 zooms 条目（box 从 regions.figures 取，留余量） |
| 放大太频繁 | 删 zooms 条目，或调大 config `[video] zoom_min_sec` 后重建时间轴 |
| 某句该指总结条却指正文 | 改 segment 的 `region` 为 "summary" |
| 页间节奏太赶 | 调 config `lead_in/lead_out` 后重跑 build_timeline（不改渲染过的页可不重渲染） |

改完只重渲染受影响的页：`render_video.py --render-only --only <idx>`，
再 `--assemble-only` 合成，最后重跑 check_video.py。
