# 交接 — ContentMaster

寫於 2026-09-30。貼這份給下一個模型當開場 prompt。

---

## 1. 這是什麼

`/Users/liunils/Desktop/Marketing hack` — 本機優先的行銷內容 pipeline。
從一份來源文件抽主題 → 產草稿 → 人審核 → 發到 Bluesky → 追成效 → 用成效決定下一輪。

**核心主張**:產生內容已經免費了,難的是「知道它有沒有用」。所以整個系統的重點是
**拒絕宣稱它證明不了的事**。

跑在一台 8GB 的 Mac 上,五個本機 Ollama 模型 + Cloudflare Workers AI 生圖。

```
Advisor A（提策略）      llama3.2:3b      DISCUSS_MODEL_A
Advisor B（另提一套）    mistral:latest   DISCUSS_MODEL_B
Judge（裁決）            qwen3:4b         DISCUSS_JUDGE_MODEL
寫草稿/主題/影片腳本      llama3.2:3b      LLM_IMPROVE_MODEL
寫圖片 prompt            gemma3:4b        IMAGE_PROMPT_MODEL
生圖                     FLUX.1-schnell   Cloudflare Workers AI（非本機）
```

`phi4-mini-reasoning` 測過被淘汰 —— 它吐 7844 字元的 `<think>` 獨白,
而 judge 的輸出會變成 `improvement_note` 直接餵進下一輪 prompt。原因記在
`discuss.py:44-60`。**不要再把 judge 換成 phi。**

## 2. 環境與指令

```bash
cd "/Users/liunils/Desktop/Marketing hack"

./.venv/bin/contentmaster run                    # extract → draft → 人審 → 發佈
./.venv/bin/contentmaster run --no-image          # 不生圖（迭代文案時用）
./.venv/bin/contentmaster run --terminal          # 留在終端機,不開瀏覽器
./.venv/bin/contentmaster refresh                 # 只重讀成效數字,隨時可跑
./.venv/bin/contentmaster review --checkpoint 24h # 跑判定（一篇一個 tier 只能跑一次）
./.venv/bin/contentmaster status                  # 現在什麼在等人
./.venv/bin/contentmaster folders                 # 來源文件在哪
./scripts/run_tests.sh                            # 15 個測試套件
./.venv/bin/python scripts/update_golden.py       # 改 golden 檔（要先讀 diff）
```

pandoc 3.11 已裝(`brew install pandoc`)。Cognee 停用(`COGNEE_ENABLED=0`)。
`.env` 有 Bluesky 和 Cloudflare 的憑證。

## 3. 這幾天做完了什麼

**停用 Cognee 當主題抽取器。** 它的 `GRAPH_COMPLETION` 設計上就是讓 LLM 寫敘事,
所以 22,000 字元的白皮書回來變 1,776 字元、11 個章節壓成 6 個、還被截斷在
`but it's essen`。它沒有保留文件結構,問它章節標題會回「organized into various chunks」。
**Cognee 沒有刪掉** —— 等累積大量貼文、成效、log、人機建議之後,
「這類主張以前哪次有效」才是真正的檢索問題。一份文件不是。

**改用 pandoc + 三層抽取策略**(`src/contentmaster/document.py`):

```
第 1 層  文件自己宣告 heading（Word/Docs style、Markdown、HTML、tagged PDF）
         → pandoc 輸出 # / ##,層級明說。零啟發式。
第 2 層  什麼都沒宣告（TextEdit 手動加粗、貼來貼去的文件）
         → 位置 + 粗體 + 空行的啟發式。門檻全是為這份白皮書調的。
第 3 層  被拒絕的行列給人看,附原因和現成摘要 → 人撿回來
```

範例文件 `Demo-white paper/cat.rtfd` 從 7 個主題變 **41 個**,另外 17 行列給人確認。
4 張內嵌 `.webp` 也對應到各自的章節(`images_for()`,不需要圖片套件)。

**評分與判定**(`src/contentmaster/scoring.py`):

```
engagement_score = 3×repost + 3×bookmark + 1×like + 1×reply   （加權和,不是平均）
Gate 1  ≥8 篇可比貼文 且 ≥10 個互動事件  → 否則 hypothesis
Gate 2  ≥1 repost/bookmark 且 score > p75 → 否則 likes_only / below_baseline
五種狀態  proven / proven_tentative / likes_only / below_baseline / hypothesis
對照組    每批測試 20% 必須違背現有證據（control_arm_size()）
```

**修掉的 bug**(有些是我自己造的):

- `analysis.py` 的 `engagement_events` 在三個地方都不存在 → `trend` 永遠卡在
  `insufficient_for_trend`。改成從四個原始 count 用 `scoring.engagement_events()` 算。
- `pipeline.py` 四個 `log_event`(analysis.recorded / modiqo.judged / success.captured /
  failure.captured)沒有 `post_id` → 判決追不回貼文。補上了。
- `_extract_category()` 沒有跟著 `COGNEE_ENABLED` gate → 每次跑都白等一次 Cognee timeout。
- `genuinely_new` 只比名稱 → 章節名沒變、內文重寫會被靜默丟掉。加了
  `brief_changed()`,10% 門檻(`difflib`),超過就問人。
- reformat 步驟憑空造主題(餵它「沒有主題」的散文,它造出 `Empty Text` 和 `Rules`)→
  加了 `_grounded_only()`,名稱裡一個詞都對不上來源就丟掉。
- 抽取本身:group 吞掉後面五節、斜體只拿掉開頭星號、清單沒接上、摘要 28–869 字元沒上限。

**加了投資人 one-pager**(artifact): https://claude.ai/artifact/3JCxxw1BmoMsx9rhgUzX6n
橫向 16:9。底部 status 用產品自己的判定詞彙寫 `hypothesis`,而不是把 10 篇貼文包裝成 traction。

## 4. 現在的狀態

**全部未提交。** 最後一個 commit 是 `d7dede4`。改動涵蓋 21 個檔案 + 未追蹤的
`tests/`、`scripts/`、`docs/EXTRACTION_TOOLS.md`、`folders.py`、`video_generator.py`、`.claude/`。

**`plays/topics/` 是空的。** 下次 `contentmaster run` 會從零抽 41 個主題並問人確認,
還會問一次 category(存起來之後不再問)。

**等人處理的:**
```
draft-98f2d720 @ 24h  在等 confirm/disagree
8 篇追蹤中,1 篇 24h 到期
重複錯誤: 5× bluesky | post.deleted（最後一次 2026-09-22）
metrics 最後一次 refresh 是 2026-09-22
```

**基準還沒開:**
```
24h  基準 2 筆（需要 8 篇 / 10 個事件）
7d   基準 0 筆 ← 從來沒跑過真實貼文
30d  基準 0 筆
```
7d 有 6 篇到期,但 `mark_checkpoint_done()` 是無條件執行的 —— 跑了就消耗掉,
6 篇會全部回 `hypothesis`。**先把基準養到 8 篇再跑 7d。**

## 5. 下一步(優先序)

1. **提交。** 這麼多改動未提交是最大的風險。建議切分成幾個 commit,不要一包。
2. **`Comparing lifestyle risks` 的摘要讀不通** —— 41 個主題裡唯一一個。
   那一節內容整個在表格裡,現在的摘要是兩個欄位標題黏在一起。
   要修就是把表格轉成 `Indoors: A, B, C / Outdoors: D, E, F`。**已問過使用者,還沒回答。**
3. **`contentmaster refresh`** 補上 9/22 之後的成效。
4. **跑 24h** 把基準往 8 篇推進。
5. 從 `docs/SCHEDULE.md` Phase 11:UI 的生圖開關、完整 error index 帶檢索、
   platform 層級的學習規則、trend 自動化(目前 Google Trends 是手動匯出的 CSV seed,
   最後更新 2026-09-14,**沒有 API**)。

## 6. 測試主題跟期望值

`./scripts/run_tests.sh` — 15 個套件。每個套件是一支獨立 script,
吃一個暫存目錄當 `CONTENTMASTER_DATA_ROOT`,印編號的中文斷言。

| 套件 | 測什麼 | 期望值 |
|---|---|---|
| **test_golden_extraction** | 真實文件的輸出逐字比對 + 8 條不變條件 | golden 檔 88 行完全相同;41 個主題;摘要無 markdown 殘留、≤400 字元、無空值、無共用內文;Covers 不跨編號章節;無重複標題;17 行待確認且都有原因;4 張圖都對到存在的章節 |
| test_headings | 三層抽取策略,36 項 | 宣告 heading 的文件直接讀(不套長度/字數上限);沒宣告的走啟發式;`Step 1` 這種沒粗體的抓得到;粗體標語(14 字)擋掉;冒號引導語擋掉;結尾句點又沒冒號的擋掉;`Problem: …` 留著;表格分隔線不當摘要;文件標題區塊跳過;真實文件 41 個 |
| test_topic_extraction | 主題解析與造假防護 | 編號清單格式解析得出;鋪陳句不當主題;解析不出時**不更新 source_hash**;名稱在來源找不到的主題丟掉;詞形變化(spraying/spray)不誤殺;brief 改動 10% 以上才問人 |
| test_scoring_gates | 兩道閘門與五種狀態 | 8 篇/10 事件以下一律 `hypothesis`;沒 repost/bookmark → `likes_only`;score ≤ p75 → `below_baseline`;<20 篇 → `proven_tentative`;加權和單調遞增 |
| test_evidence_gating | 證據不足時 prompt 不能給指示 | 沒有基準時不產生「照這樣做」的句子 |
| test_parsers | 模型回傳格式的六種變體 | `-- POST:` 前綴、`1. **粗體**:`、純散文、`axis = tone` 等都要解析得出來 |
| test_no_invented_content | 不編造 | 產出裡不出現文件裡沒有的產品事實 |
| test_category_fallback | Cognee 掛掉時 category 照樣拿得到 | 退回本機 LLM 讀文件;>80 字元或含換行的提議一律拒絕 |
| test_category_and_assets | category 驅動圖片 | 有 category 時圖片 prompt 帶「What it actually is」;人供圖優先於生成圖,least-used-first |
| test_image_validation | 圖片格式 | magic byte 判斷真實格式(Cloudflare 回 JPEG 不是 PNG);壞檔不進發佈 |
| test_subject_and_no_image | 主體與無圖路徑 | `--no-image` 記成 characteristic,讓「圖有沒有用」之後答得出來 |
| test_rtfd_bundle | macOS .rtfd 是資料夾不是檔案 | `resolve_bundle()` 指到裡面的 `TXT.rtf`;不存在的 bundle 回 `""` 不拋錯 |
| test_folders | 資料夾選擇 | whitepapers / external 兩個獨立設定,存在 `plays/_folders.json` |
| test_judge_model | 三模型互評 | judge 必須是第三個不同家族;輸出不能是兩案的混合 |
| test_video | 影片組裝 | script → 分鏡 → TTS → ffmpeg;`-- ` 前綴的分鏡也要解析得出來 |

**跑法:**
```bash
./scripts/run_tests.sh                                     # 全部
./.venv/bin/python tests/test_golden_extraction.py /tmp/x   # 單一套件
```

## 7. 使用者的行為要求(原話,不要違背)

- **「你不准在沒跟我說的情況下亂開背景。」** 開背景程序前一定要先講。
- **「產品名由人類改,llm不准動。」**
- **「無論如何llm不允許自動發佈,除非之後我們做出自動發佈按鈕。」**
- **「我不要你分析或丟想法」** —— 要原始輸出時就只給原始輸出。
- **「我不需要看到不重要的文字,什麼更嚴重的問題之類的。講重點。」**
- **「don't give me technical term」** —— 用白話解釋。
- 模型或套件的決定:**先說再改**,不要直接動程式碼。
- 回覆用繁體中文,專有名詞保留英文。對外的成品文件(貼文、pitch 材料)用英文。

## 8. 已知陷阱

**「每個新觀念只被套用在它誕生的那個位置」** —— 這個 repo 反覆出現的失敗模式,
出現過 5–6 次。修一個 bug 只修在發現它的地方,第二個呼叫同一個概念的地方保持舊行為。
表現形式:terminal 跟 Streamlit 兩個審核介面不同步、parser 沒一起更新、
`COGNEE_ENABLED` gate 只加在主題路徑沒加在 category 路徑。
**改任何規則之前,先 grep 有幾個地方用到同一個概念。**
`.claude/skills/two-review-surfaces/` 和 `.claude/skills/parse-what-models-return/` 記了案例。

**測試通過不等於正確。** 這一輪學到的:我寫的 fixture 是憑印象重建的,
不是從文件抄的,所以它跟程式碼抱著同一個錯誤信念,測試通過只證明我自己前後一致。
四個 bug 在 `assert len(real) == 41` 通過的情況下全部溜過去 —— 我在數個數,使用者在讀內容。
`test_golden_extraction.py` 就是為此存在:**真實輸出本身當基準**,變了就印 diff 要人核准。
**改完抽取邏輯後,要驗證測試會在已知 bug 上失敗,不只驗證它會通過。**

**啟發式的門檻全是為這一份文件調的。** `MAX_HEADING_WORDS = 12`(因為那句粗體標語 14 字)、
`MAX_HEADING_CHARS = 70`、「結尾句點沒冒號就不是標題」、編號章節當 group 邊界。
程式碼註解已寫明這些是 best effort 不是規格。別人的文件要靠第 1 層(宣告 heading)
或第 3 層(人撿回來),不是靠調這些數字。

**`_history.jsonl` 只有 2 筆是正常的。** 2026-09-18 之前 `capture_failure()` 不寫 history,
而 era 0 的資料在 2026-09-20 被封存到 `archive/era0-2026-09-20/`。對過帳,數字是對的。

**Streamlit 不做主題審核。** 兩種模式的主題審核都走終端機的 `_review_topics_interactive`
(`_extract_topics_and_generate` 共用)。所以主題相關的改動只有一個介面。
但草稿審核和 analysis 審核**兩邊都有**,那些要同步改。
