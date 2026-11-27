# 视频规格（渲染与音频链行为契约）

画面 1080p/30fps，底图 4K 超采样（`render_slides.py --base-scale 2.0`）；
libx264 crf 18。改这些默认值走 config `[video]`。

## 画面层（render_video.py 逐帧渲染）

| 元素 | 行为 | 参数 |
|---|---|---|
| 讲解光标 | 段切换时 0.45–1.0s 缓动滑到新区域中心，到位白圈脉冲（0.38s），页尾 0.35s 淡出；区域不变不移动 | 光标 56px 程序生成箭头 |
| 高亮框 | 当前讲解区域外扩 12px 描圆角黄框（#FFD54A，5px）+ 软填充，亮度脉动 0.62±0.30 | 讲解段存在时叠加 |
| 聚焦压暗 | 区域外整帧 ×0.84，30px 软边过渡；放大时不再叠加 | 与高亮同开 |
| 局部放大 | 从 4K 底图裁 16:9 放大框推近（0.55s 缓入）→ 保持 → 缓出；框=图区外扩 16% 取 16:9 居中、不出画布（保证图内边缘的标题标签不被裁）；放大期间光标/高亮淡出 | zooms[].box |
| 封面开场 | 开场卡整段 Ken Burns 缓推 3.5% | ken_burns |
| 页间过渡 | 黑场淡入淡出各 0.5s（旁白在 lead_in 之后，不受影响） | fade |

动画只画在「讲解段时间窗」内：窗 = segment t 外扩 0.15/0.45s。窗口之外
画面保持纯底图，因此 check_video 的「动画生效抽检」能检出光标没画上的页。

## 音频链（assemble）

```
逐页 mp3 --adelay(页起始+lead_in)--> amix(相加) --> aresample 44.1k 立体声
                                                  ├────────────┐
bgm 循环铺满 --> volume(gain_db) --> afade in 0.9s --> sidechaincompress(threshold .02,
                                                    ratio 6, attack 25ms, release 450ms)
                                                    --> volume(duck_db) --> afade out 1.5s
最终 amix --> alimiter(0.95) --> aac 192k
```

设计电平（check_video 按此核）：人声窗口 mean −20±8dB；纯音乐间隙比人声低
≥10dB；片头 BGM 可闻（>−55dB）；max ≤ −0.5dB 无削波。

## 字幕

- 切行：句读优先（。！？；），超长按逗号顿号补刀；补刀后的硬切走 **jieba 词元**
  （中文不拆词、ASCII 连串如 Transformer/3.91× 不拆），上限 18 字/行；
- 显示文本剥行尾标点（广播字幕惯例），行内标点保留；行内原文仍含标点，
  讲解段按句末标点分组不受影响；
- 对齐：edge-tts WordBoundary 词级时间戳回填原文（标点跳过匹配，数字读法
  对不上时整页退回按字数比例）；
- 烧录：**生成 .ass 交给 libass**（PlayRes 1080；实测 force_style 会丢
  BorderStyle，故不走 srt+force_style）。白字 + 0.24×字号 深描边 + 柔影，
  浅色总结条上"白芯黑边"仍清晰（已过可读性探针与视觉评审）；
- 字号/边距换算：`ass_size = config.size × 1080/288`、`margin_v × 1.5`；
  改字体/字号/颜色只动 config `[subtitle]`。

## 背景音乐配方（make_bgm.py，确定性）

C 大调 I–V–vi–IV 进行（收尾 Cmaj9 回环无缝）：软垫和弦（基频+2 个低幅泛音，
双声部 ±0.15% 失谐分左右）+ C2 低音 + 五声音阶稀疏拨音（55% 概率/2 拍）。
bpm 72、每和弦 8 拍、峰值 0.5。**同 seed 同输出**——全库系列视频共用同一首。

## TTS 后端（voice backend）

- 默认 **CosyVoice2 本地**（独立 venv：`~/models/CosyVoice/.venv`，模型
  `~/models/CosyVoice2-0.5B`；无 GPU，CPU 96 线程 RTF≈4.8，30 分钟音频约 2.2 小时
  后台合成）：整批走 `--job` 子进程模式，一次装载整批合成，句级真实时长 + 句内
  按字数分配给分句（字幕行对齐粒度不变）；
- 兜底 **edge-tts**（联网、快，词级时间戳）；找不到 CosyVoice 环境或作业失败自动退回；
- **数字读法归一化**（num_norm.py，两个后端共用）：342→三百四十二、40%→百分之四十、
  2.81×→二点八一倍、2024年→二零二四、3×128→三乘一百二十八、千分位剥逗号、
  引用标记 [N] 不念；映射存 tts.json num_map 供溯源；
- **B0 验收门**：CosyVoice RTF > 8 自动退回 edge-tts 并在报告注明；
- 声音试听：合成后抽 2–3 段（数字句/颜色句）导出 wav 人耳验收。

## 指图一致性（check_video 门禁）

- 分派链：旁白「图 N / 表 N」→ figure.ref 编号精确匹配 → brief/axes 词面重合
  （≥2 实词）→ 左/右/上/下位置词 → 默认第 0 张；
- 光标分句级：句内按逗号顿号细分，目标变化且分句 ≥1.5s 才移动；图内落点解析
  位置词映射九宫格（左上/右下/中间…）；
- 门禁：说图 N 的段抽帧检测黄色高亮框（或放大框）与图 N bbox 的 IoU ≥0.45，
  否则报「说的图≠指的图」——用户三次强调的重点，机械拦截。

## 修复入口速查

| 症状 | 动作 |
|---|---|
| 人声听不清 | `[mix] narration_gain_db` +2~4 → assemble-only |
| BGM 抢解说 | `[bgm] gain_db` 再 −2 或 `duck_db` 再 −2 → assemble-only |
| 某页糊 | 查 slides/ 对应底图是否 3840 宽；crf ≤18；原文图模糊回 pdf2ppt 换图 |
| 光标/高亮没出现 | 该页 segments 为空（旁白没被切出段）——查 build_timeline 输出 |
| 字幕越窗 | build_timeline 的行对齐退化（tts.json 词数异常）→ 重跑该页 TTS |
