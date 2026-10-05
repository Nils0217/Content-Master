# 交接 — ContentMaster

寫於 2026-09-30,2026-10-05 更新。貼這份給下一個模型當開場 prompt。

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
Judge（只選 A 或 B）    qwen3:4b         DISCUSS_JUDGE_MODEL
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
./scripts/run_tests.sh                            # 16 個測試套件
./.venv/bin/python scripts/update_golden.py       # 改 golden 檔（要先讀 diff,互動確認）
```

查某篇貼文分析的每一步（`dbt run` 之後,在 warehouse/local.duckdb）:
```sql
select step_no, step, role, model, raw from stg_reasoning
where post_id = 'draft-…' and checkpoint = '7d' order by ts, step_no;
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


### 2026-10-01 ～ 10-05

**提交並推上 GitHub。** 原本未提交的改動切成 7 個 commit,加上這輪 4 個,`main` 已 push。

**表格章節按欄位摘要。** `Comparing lifestyle risks` 原本是兩個欄位標題黏在一起。
`document._table_brief()` 讀 pandoc simple table 和 pipe table,輸出
`欄位: 項目, 項目, … / 欄位: 項目, …`,兩欄平分 400 字元上限,放不下的以 `…` 結尾。
第 1、2 層抽取都套用。golden 已更新（只差這一行,使用者核准過）。

**streamlit 用錯 Python。** `launch_streamlit()` 呼叫裸的 `streamlit`,PATH 上找到系統的
Python 3.12,沒有專案套件 → `ModuleNotFoundError: atproto_client`。改成 `sys.executable -m streamlit`。
**8501 和 8502 上各有一個舊的系統 Python streamlit 一直開著**,`launch_streamlit()` 看到 port
有人就沿用,所以修了也看不到 —— 都已關掉。

**沒有證據時測 hypothesis,而且每一步都留下來**（`hypothesis.py`、`reasoning.py`、`discuss.py`）。
原本的狀況:顧問有提 hypothesis,但裁判被要求「給一個最終策略」,把它改寫掉、丟掉測試;
寫草稿那一步根本收不到;顧問只拿到產品名和 Google Trends,所以八條建議全在追
「cat in the hat」,還有一條把 Fluffy roommate 當成貓的品種。

```
hypothesis  = TEST / WHY / WATCH
              WATCH 只能是 likes / reposts / replies / bookmarks（系統只讀得到這四個）
              沒有「什麼結果代表錯」—— 見 §7 使用者原話
裁判        = 只回 PICK: A|B + REASON,不改寫;存的是顧問原文
顧問拿到    = 貼文原文 + 文件章節清單 + 已確認的 category（和寫草稿共用 _subject_description）
每一步      = plays/_reasoning.jsonl,一筆 analysis 一列,含每個模型的 prompt、原始回覆、解析結果
              以 reasoning_id 連到 history;discuss 的 audit 事件也帶 post_id / checkpoint
              dbt: stg_reasoning（一步一列）;stg_success_history 多了追溯欄位
發文環境    = 發佈時記錄 followers / following / posts（Bluesky profile）、星期與當地小時、
              有圖/影片、trend 資料日期 → tracking entry 的 `environment`
寫草稿      = prompt 帶 HYPOTHESIS TO TEST;每篇宣告 HYPOTHESIS: yes/no,
              yes 的那篇記 tests_hypothesis_id;沒有任何一篇宣告會印出提醒
ANALYSIS_SCHEMA = 3 → 之前排隊的分析標成過期,`review` 會重新分析（不消耗 checkpoint）
```

**對照組規則只在有基準線之後才啟用。** 沒有基準線時「證據」只是上一篇和它的 0 分,
違背它什麼都測不到;而且一次只發 1 篇時,同一篇被要求同時當對照組又測 hypothesis = 一次改兩件事。
實際草稿的 EVIDENCE-AGAINST 寫的是 `None`、`Lifestyle` 或空白,規則本來就沒在運作。
現在 `find_best_prior()` 帶 `has_baseline`,沒有就 `controls = 0`,hypothesis 就是那段期間的測試。

**討論過、決定不做:Elasticsearch。** AI 回饋太亂太長是因為內容沒結構,不是搜尋問題;
資料量幾百筆;8GB 機器已跑五個模型。改成結構化欄位 + 一行摘要 + DuckDB 查詢。
累積到幾千篇、需要「找意思相近的」時再評估,先試已保留的 Cognee。

## 4. 現在的狀態（2026-10-05）

**全部已提交並 push**（除非你看到 `git status` 不是乾淨的）。

**帳號 0 個追蹤者**（following 1、posts 15）。這是目前沒有成效最可能的原因,
任何文案測試在沒人看到的情況下都量不出差別。這只有使用者能處理（追蹤相關帳號、回覆、簡介）。

**`plays/topics/cat.json` 已建立:** 41 個主題,category 已確認為 `A domestic cat`。

**等人處理的:**
```
8 份 7d 分析 pending,但都是 schema 2 → 已過期,必須重跑:
    ./.venv/bin/contentmaster review --checkpoint 7d
  重跑會產生新格式的 hypothesis;checkpoint 還沒消耗（streamlit 路徑要等人決定才標記完成）
draft-c40f6ecd  10/05 發佈,第一篇記下發文環境的貼文;24h 之後要跑 review
重複錯誤: 5× bluesky | post.deleted（最後一次 2026-09-22）
```
**人最後決定的那一份分析,就是下一篇草稿要測的 hypothesis**（`find_best_prior()` 取最成熟 tier 的最新一筆）。

**基準線:**
```
24h  基準不足（需要 8 篇 / 10 個事件）
7d   8 篇有讀數但總共 2 個互動 → 事件數不足,下一篇 7d 仍會是 hypothesis
30d  0 筆
```

## 5. 下一步（優先序）

1. **重跑 7d 分析**（見 §4）,讓人在新格式上做決定。
2. **確認新設計在真實 run 裡有效:** 下一次 `contentmaster run` 的草稿 prompt 應該帶
   `HYPOTHESIS TO TEST`,至少一篇宣告 `HYPOTHESIS: yes`;`plays/_reasoning.jsonl` 應該有紀錄。
   這些只在隔離資料夾用真實模型驗證過,還沒在真實帳號的完整循環裡跑過。
3. **0 追蹤者**:由使用者處理。系統這邊每篇已記下追蹤者數,之後可以對照。
4. **trend 資料自動化** —— 目前停在 2026-09-13,手動匯出的 CSV,**沒有 API**。
   顧問和寫草稿都在用過期的趨勢。
5. **`post.deleted` 重複 5 次**,值得從源頭防止而不只是處理。
6. `docs/SCHEDULE.md` Phase 11:UI 的生圖開關、完整 error index 帶檢索、platform 層級的學習規則。

已知小問題（使用者看過,未處理）:主題 20 的摘要是原文兩個問句;主題 13 的
`Covers:` 列出 `Problem: … .` 時句點後接逗號（`you., Problem:`）。

## 6. 測試主題跟期望值

`./scripts/run_tests.sh` — 16 個套件。每個套件是一支獨立 script,
吃一個暫存目錄當 `CONTENTMASTER_DATA_ROOT`,印編號的中文斷言。

| 套件 | 測什麼 | 期望值 |
|---|---|---|
| **test_golden_extraction** | 真實文件的輸出逐字比對 + 8 條不變條件 | golden 檔 88 行完全相同（第 21 項是按欄位的表格摘要）;41 個主題;摘要無 markdown 殘留、≤400 字元、無空值、無共用內文;Covers 不跨編號章節;無重複標題;17 行待確認且都有原因;4 張圖都對到存在的章節 |
| test_headings | 三層抽取策略,39 項 | 宣告 heading 的文件直接讀(不套長度/字數上限);沒宣告的走啟發式;`Step 1` 這種沒粗體的抓得到;粗體標語(14 字)擋掉;冒號引導語擋掉;結尾句點又沒冒號的擋掉;`Problem: …` 留著;表格分隔線不當摘要;表格章節按欄位摘要（真實 pandoc 輸出、pipe table、第 1 層）;文件標題區塊跳過;真實文件 41 個 |
| test_topic_extraction | 主題解析與造假防護 | 編號清單格式解析得出;鋪陳句不當主題;解析不出時**不更新 source_hash**;名稱在來源找不到的主題丟掉;詞形變化(spraying/spray)不誤殺;brief 改動 10% 以上才問人 |
| test_scoring_gates | 兩道閘門與五種狀態 | 8 篇/10 事件以下一律 `hypothesis`;沒 repost/bookmark → `likes_only`;score ≤ p75 → `below_baseline`;<20 篇 → `proven_tentative`;加權和單調遞增 |
| test_evidence_gating | 證據不足時 prompt 不能給指示 | 沒有基準時問 TEST/WHY/WATCH,不產生「照這樣做」,也沒有「什麼代表錯」 |
| test_parsers | 模型回傳格式的六種變體 | `-- POST:` 前綴、`1. **粗體**:`、純散文、`axis = tone` 等都要解析得出來 |
| test_no_invented_content | 不編造 | 產出裡不出現文件裡沒有的產品事實 |
| test_category_fallback | Cognee 掛掉時 category 照樣拿得到 | 退回本機 LLM 讀文件;>80 字元或含換行的提議一律拒絕 |
| test_category_and_assets | category 驅動圖片 | 有 category 時圖片 prompt 帶「What it actually is」;人供圖優先於生成圖,least-used-first |
| test_image_validation | 圖片格式 | magic byte 判斷真實格式(Cloudflare 回 JPEG 不是 PNG);壞檔不進發佈 |
| test_subject_and_no_image | 主體與無圖路徑 | `--no-image` 記成 characteristic,讓「圖有沒有用」之後答得出來 |
| test_rtfd_bundle | macOS .rtfd 是資料夾不是檔案 | `resolve_bundle()` 指到裡面的 `TXT.rtf`;不存在的 bundle 回 `""` 不拋錯 |
| test_folders | 資料夾選擇 | whitepapers / external 兩個獨立設定,存在 `plays/_folders.json` |
| test_judge_model | 三模型互評 | judge 必須是第三個不同家族;prompt 不含模型 ID;裁判失敗時退回兩案 |
| **test_hypothesis** | hypothesis 全流程,18 項 | 用真實模型原樣回覆解析;裁判只選不改寫;三步 prompt+回覆都存且帶 post_id;讀不出 vs. 沒回應分得開;兩個審核介面共用畫面;草稿帶 hypothesis 並記 id;沒基準線不要求對照組;環境查不到記成未知不是 0;history 一列追得回去 |
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

**同一份清單寫了好幾份。** 草稿標籤欄位（`LABEL_KEYS`）原本在 streamlit 一份、pipeline 裡又兩份,
各自少了不同欄位。現在統一用 `pipeline.LABEL_KEYS`。新增標籤只改那一個地方。

**舊的 streamlit 會一直開著。** `launch_streamlit()` 看到 port 有人在聽就直接沿用,所以改了程式
之後要先關掉舊的 server（`lsof -nP -iTCP:8501 -sTCP:LISTEN`）,不然看到的還是舊畫面。
驗證 UI 時開隔離的一個:
`CONTENTMASTER_DATA_ROOT=<暫存> ./.venv/bin/python -m streamlit run streamlit_app.py --server.port 8502`
（開背景程序前先跟使用者說）。

**改模型 prompt 之前先跑真實模型看回什麼。** 這輪 hypothesis 的 WATCH 實際回來是
`100 likes`、`Reposts (shares) as…`、`Likes or reposts…` —— 解析器是照這些寫的,
`tests/test_hypothesis.py` 存著原文。

