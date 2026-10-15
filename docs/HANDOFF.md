# 交接 — ContentMaster

寫於 2026-10-15(取代 9/30 和 10/05 的版本)。**貼這份給下一個模型當開場 prompt。**

開場先做三件事:
1. 讀完這份。
2. `./scripts/run_tests.sh` —— 應該 **18 個套件全過**。
3. `./.venv/bin/contentmaster status` 和 `./.venv/bin/contentmaster clusters` 看什麼在等人。

然後看 §6「下一步」,**標著「等使用者」的不要自己開始**。

---

## 1. 這是什麼

`/Users/liunils/Desktop/Marketing hack` — 本機優先的行銷內容 pipeline。
從一份來源文件抽主題 → 產草稿 → 人審核 → 發到 Bluesky → 追成效 → 用成效決定下一輪。

**核心主張**:產生內容已經免費了,難的是「知道它有沒有用」。整個系統的重點是
**拒絕宣稱它證明不了的事** —— 資料不夠就說「hypothesis」,不包裝成結論。

產品是 **Fluffy roommate**(一隻貓 —— category `A domestic cat`),來源文件
`Demo-white paper/cat.rtfd`(一份「如何使用一隻貓」的幽默白皮書,41 個主題)。
帳號 Bluesky,**目前 0 個追蹤者**(following 1、posts 15)。

### 機器與模型

8GB RAM 的 Apple M3。Ollama 0.35。Cloudflare Workers AI 生圖。

```
顧問 A（提 hypothesis）   llama3.2:3b      DISCUSS_MODEL_A
顧問 B（另提一個）        mistral:latest   DISCUSS_MODEL_B
裁判（只選 A 或 B）       qwen3:4b         DISCUSS_JUDGE_MODEL
寫草稿/主題/影片腳本       llama3.2:3b      LLM_IMPROVE_MODEL
寫圖片 prompt            gemma3:4b        IMAGE_PROMPT_MODEL
生圖                     FLUX.1-schnell   Cloudflare Workers AI（非本機）
```

已下載、**還沒實測、還沒採用**:`granite4.2:8b`(5.3GB)、`gemma4:e2b`(4.6GB)、`qwen3.5:4b`(3.3GB)。
見 §6 第 1 項。8GB 機器塞得下的上限約 5.5GB;`qwen3.5:9b`、`gemma4:e4b`、`gemma4:12b` 都太大。

**淘汰過、不要再換回來的**:
- `phi4-mini-reasoning` 當裁判 —— 吐 7844 字 `<think>` 獨白(`discuss.py` 有記錄)。
- `qwen3:4b` 當顧問 —— 每次 120–170 秒,1/3 吐 3000+ 字讀不出答案。
- `qwen3:4b` 關思考模式（`think: false`）—— 反而把上萬字思考寫進答案。開著思考才乾淨,但一次約 2.5 分鐘。
- `qwen2.5:3b` 當顧問 —— 把貓當成產品寫「功能與好處」。

## 2. 指令

```bash
cd "/Users/liunils/Desktop/Marketing hack"

./.venv/bin/contentmaster run                     # 額外貼文:extract → draft → 人審 → 核准即發佈
./.venv/bin/contentmaster run --posts 3           # 一次 3 篇額外貼文
./.venv/bin/contentmaster run --cluster           # 下週的 cluster:7 篇,核准「不」發佈,等排程
./.venv/bin/contentmaster run --terminal          # 留在終端機審,不開瀏覽器
./.venv/bin/contentmaster run --no-image          # 不生圖
./.venv/bin/contentmaster review                  # 24h checkpoint 分析
./.venv/bin/contentmaster review --checkpoint 7d  # 7d(一篇一個 tier 只能決定一次)
./.venv/bin/contentmaster refresh                 # 只重讀成效數字,隨時可跑
./.venv/bin/contentmaster status                  # 什麼在等人
./.venv/bin/contentmaster clusters                # 每個 cluster 和裡面的貼文
./scripts/run_tests.sh                            # 18 個測試套件
./.venv/bin/python scripts/update_golden.py       # 改 golden 檔（互動確認,要先讀 diff）
```

沒給 `--product-name` 時,會沿用**人上次給這份文件取的名字**(目前 = `Fluffy roommate`)。

查某篇貼文分析的每一步(先在 `warehouse/` 跑 `dbt run`):
```sql
select step_no, step, role, model, raw from stg_reasoning
where post_id = '…' and checkpoint = '7d' order by ts, step_no;
```

## 3. 資料:每張表一列代表什麼

原始紀錄都是 **append-only、最後一列為準**(同一個 key 取最新那列)。

| 檔案 | 一列 = | 內容 |
|---|---|---|
| `plays/_posts.jsonl` | 一篇貼文的一次狀態變更 | `natural_id`、`post_id`、`cluster_id`、`kind`、`experiment`、`status`、`scheduled_for`… **沒有內文** |
| `plays/_clusters.jsonl` | 一個 cluster 的一次狀態變更 | 週一日期、hypothesis、experiment 星期、`learned_from` |
| `plays/_draft_review.jsonl` | 一份草稿的一次狀態變更 | **所有草稿內文**(含退件)、圖片路徑、標籤 |
| `plays/_tracking_review.jsonl` | 一篇已發佈貼文的一次更新 | 實際發出的 `final_text`、`environment`、`checkpoints_done` |
| `plays/_analysis_review.jsonl` | 一份分析(post×checkpoint)的一次狀態變更 | 等人 confirm/disagree 的分析 |
| `plays/_history.jsonl` | 一篇貼文 × 一個 checkpoint 的最終判定 | 人決定後才寫;帶 `post_id`、`tests_hypothesis_id`、`next_hypothesis`、`hypothesis_result`、`reasoning_id` |
| `plays/_reasoning.jsonl` | 一次分析執行的所有模型步驟 | 每個模型的 prompt + 原始回覆 + 解析結果 |
| `plays/_failures.jsonl` | 一次失敗 | ⚠️ 混了「人審退件」和「發佈後 checkpoint 沒過」 |
| `metrics/post_metrics.jsonl` | 一次成效讀取 | refresh 每次加一列 |
| `plays/_products.jsonl` | 一個產品名的一次更新 | `explicit` = 人打的名字 |
| `plays/topics/fluffy-roommate.json` | 一個產品一份檔 | 41 個主題 + category |

DuckDB(`warehouse/`):`stg_posts`(一篇一列)、`stg_clusters`(一個 cluster 一列)、
`stg_reasoning`(一次模型呼叫一列)、`stg_success_history`(post × checkpoint)。
⚠️ `campaign_performance` 混了兩種粒度(每產品×平台一列「目前最好」+ 每次失敗一列),不要拿來加總。

ID 規則:`post_id` 新貼文是完整 UUID(舊的是 `draft-xxxxxxxx`)。`natural_id` 在**核准時**才給,
退件不佔號,依產品×平台各自從 1 數。舊的 10 篇是 cluster 0 的 #1–#10(#7 被手動刪除,保留號碼)。

## 4. 已決定的事(附理由 —— 不要推翻,除非使用者改口)

### 成效怎麼算
- `engagement_score = 3×repost + 3×bookmark + 1×like + 1×reply`(加權和)。quote 有記錄、不算分。
- 只跟同一個 checkpoint 比(24h 只比 24h)。
- 舊門檻(跨 cluster 的長期比較用):≥8 篇可比 且 ≥10 個互動 → 否則 `hypothesis`;
  ≥1 repost/bookmark 且 > p75 → 否則 `likes_only`/`below_baseline`;<20 篇 → `proven_tentative`。

### hypothesis
- 格式 **TEST / WHY / WATCH**。WATCH 只能是 likes / reposts / replies / bookmarks(系統讀得到的四個)。
- **沒有「什麼代表錯」** —— 使用者原話:「沒有錯這個東西,只有當下環境不適合。」
  結果只說「動了」/「在這些條件下沒動」,附發文環境(追蹤者、星期幾幾點、有沒有圖、trend 日期)。
- 裁判只回 `PICK: A|B` + `REASON`,**不改寫**;存的是顧問原文。WATCH 讀不到數字的提案不能參選。
- **每一步都留**(使用者原話:「回朔或 trouble shoot 的時候才照得到東西」):每個模型呼叫的
  prompt、原始回覆存在 `_reasoning.jsonl`,用 `post_id` 找得到。
- 測完要收尾:7d 分析遇到測試貼文,讀它 WATCH 的數字 vs 同 checkpoint、**沒測**這個假設的貼文中位數(≥2 篇才比)。

### cluster(2026-10-06/07 和使用者一起設計)
- **一個 cluster = 一個日曆週(週一到週日)、一個 hypothesis、7 篇,一天一篇。**
- 5 篇 `experiment=false` 照 hypothesis 寫;**2 篇 `experiment=true`(20%,無條件進位)故意不做那個改變**。
  使用者原話:「experiment means the 20% posts needs to be different than the rest」。
- experiment 的星期**照 cluster 編號輪替**,不照月曆:1→一/四、2→二/五、3→三/六、4→四/日…
  任何連續 7 個 cluster 每個星期幾剛好 2 次。
- 跨月的 cluster 不拆開,算在**週一所在的月份**。分析單位是 cluster,不是月。
- 為什麼一週:cluster N 的第 1 篇滿 7d 的那天剛好是下週一,當天就能決定 N+1,不用等。
- **額外貼文**(`run --posts N`):`kind=extra`,掛在當週 cluster 下,**不算進比較**;
  互動很多或那週追蹤者暴增 → 該 cluster 結論標「可能受影響」。使用者確認保留這個指令。
- **checkpoint 的分工**:24h 決定細節修改 / 7d 決定下一個 cluster 的大方向 / 30d 綜合比較。
- 24h **只在明顯變化時**才改還沒發的貼文:某篇 ≥10 個互動 **且** ≥ cluster 中位數 3 倍。
  否則只記數字,不叫模型。改了要重新人審。改的東西不能動到正在測的那件事。
- cluster 結論門檻:一個 cluster 合計 ≥10 個互動(追蹤者 <50 時見下)。
- 一個 cluster 用**同一個發文時間**(時段本身是環境條件)。
- 「沒有觀眾」門檻 **50 追蹤者**。使用者原話:「before 50 followers the hypothesis test goal is to get 50 followers」。
  設計見 §6 第 3 項(**還沒確認**)。
- 對照組規則(rolling 20% control quota)在 cluster 內關閉 —— experiment 那 2 篇就是對照。

### 不做的
- **Elasticsearch**:回饋亂是內容沒結構,不是搜尋問題;資料幾百筆;8GB 已跑五個模型。累積幾千篇再評估,先試 Cognee。
- **限定 hypothesis 只能改寫法**:使用者說不要(2026-10-12)。
- **寫 7 篇前先讓人確認 hypothesis**:使用者說不要。
- **換模型**:使用者說現在不要,實測等他通知。

## 5. 這段時間的 log

**9/30 – 10/05**
- pandoc 三層抽取取代 Cognee(文件宣告的 heading → 位置+粗體啟發式 → 人撿回)。41 個主題。
- 表格章節按欄位摘要(`Comparing lifestyle risks`)。golden 已更新。
- streamlit 用 `sys.executable -m streamlit` 啟動(裸的 `streamlit` 找到系統 Python → `ModuleNotFoundError`)。
- hypothesis 全流程重寫:TEST/WHY/WATCH、裁判只選、每一步存 `_reasoning.jsonl`、發文環境、草稿帶 hypothesis。
- 對照組只在有基準線之後啟用。
- 測完收尾:7d 讀 WATCH 數字。

**10/06 – 10/10**
- cluster 第 1 段(資料):`posts.py`、`clusters.py`、`contentmaster clusters`、`stg_posts`/`stg_clusters`。
  同一秒寫兩列時 `ts` 打平 → 加 `seq`。
- 產品名:`cat.rtfd` 被預設成產品 `cat`,10/05 那篇變成另一個歷史的 #1。使用者確認是同一產品,
  已用追加列併進 `Fluffy roommate`;沒給 `--product-name` 時改為沿用人取過的名字。
- cluster 第 2 段(產生):`run --cluster`。真實模型實跑抓到:模型拿掉章節編號和彎引號 → 主題對不上全被丟;
  顧問提「改文件」;裁判選了量不到的假設;錯誤訊息把「全被丟」說成「沒回應」。都修了。
- 審核頁會自動重啟:開在程式修改前的 streamlit 會讀新頁面但用舊模組 → `ImportError: NotSchedulable`。
- 太長的草稿先請模型縮短一次:約 1/3 的草稿超過 300 字(302、747),`--posts 1` 就整輪空手。
- 模型調查:查了 Ollama 模型庫,下載 3 個候選。實測做了一半(只跑了 qwen3:4b 當裁判的基準)就停 ——
  **它佔住 Ollama,讓使用者的 `run` 逾時**。之後實測一律等使用者說可以。

全部已 push 到 `origin/main`(最後 `c52e011`),之後只有這份交接文件。

## 6. 下一步(依序)

1. **新模型實測 —— 等使用者通知,不要自己開始。**
   腳本在 scratchpad 已不存在,要重寫:同一份 cluster-start 顧問 prompt(`discuss._proposal_prompt`
   + 41 個主題),每個模型 3 次;裁判用兩個真實提案問 PICK。用 Ollama 原生 `/api/chat`
   才能控制 `think`。**跑的時候 Ollama 會被佔住 15–20 分鐘。**
   建議組合(待實測):顧問 A `granite4.2:8b`、顧問 B `gemma4:e2b`、裁判 `qwen3.5:4b`(三個不同家族)。

2. **cluster 第 3 段:排程與自動發佈 —— 等使用者確認做法。**
   已同意:審核頁加「時間下拉選單 + Schedule post 按鈕」,整個 cluster 一個時間;用這台 Mac;
   核准後才能排程(這就是交接說的「之後做出自動發佈按鈕」——每篇都人核准過)。
   **待確認**:使用者質疑「每 5 分鐘檢查」。最新提案是 launchd 只在發文時間叫醒一次
   (`StartCalendarInterval`),睡著錯過時醒來補發,環境記實際發出時間。
   注意 Bluesky 沒有內建排程 API。發佈要走 `step5_publish` / `step6_queue_for_review`
   (guard 已在核准時跑過,見 `approve_for_schedule`)。

3. **cluster 第 4 段:分析 —— 使用者說 ok,但追蹤者的設計要先確認。**
   - 24h 明顯變化觸發(≥10 互動 且 ≥3× 中位數)→ 只改還沒發的、要重新核准。
   - 7d → 決定下一個 cluster 的 hypothesis(`run --cluster` 已會讀最新已決定分析的 `next_hypothesis`)。
   - 30d → cluster 結論:5 篇主要 vs 2 篇 experiment,門檻,額外貼文影響標記。
   - **追蹤者 <50 時,WATCH = 新增追蹤者**。最新提案(**待確認**):按天拆 ——
     這篇帶來的 = 下一篇發佈時的追蹤者 − 這篇發佈時的追蹤者(環境紀錄已有);週日那篇用下個 cluster 週一;
     有額外貼文的那天標「受影響」不算;結論門檻「一週至少 +5 追蹤者」。
     要新增 `followers` 當 WATCH 的合法值,顧問 prompt 在 <50 時要求 WATCH=followers。

4. **重跑 8 份舊格式 7d 分析**:`contentmaster review --checkpoint 7d`(還沒消耗 checkpoint)。
   另外 1 篇到期要跑 24h。由使用者執行。

5. 之後(交接舊排程):trend 自動化(停在 2026-09-13,手動 CSV,沒 API);
   `post.deleted` 重複 5 次要從源頭防;UI 生圖開關、可檢索 error index、平台層級學習規則(SCHEDULE Phase 11)。

已知品質問題(還沒處理):`llama3.2:3b` 有時寫出**只有標題**的貼文(34 字、「The Purr-fect Toy: …」),
`publish_guard` 擋不住,靠人審退件;experiment 篇偶爾沒照「不要做這個改變」寫。
錯誤訊息若能分辨「Ollama 正在忙別的請求」會更好查。

## 7. 測試

`./scripts/run_tests.sh` — 18 個套件,每個吃一個暫存目錄當 `CONTENTMASTER_DATA_ROOT`,印編號的中文斷言。

| 套件 | 測什麼 |
|---|---|
| **test_golden_extraction** | 真實文件抽取逐字比對 golden(41 主題、第 21 項按欄位)+ 8 條不變條件 |
| test_headings | 三層抽取,39 項(含表格三種形式) |
| test_topic_extraction | 主題解析與造假防護 |
| test_scoring_gates | 兩道閘門與五種狀態 |
| test_evidence_gating | 沒基準線時問 TEST/WHY/WATCH,沒有「什麼代表錯」 |
| **test_hypothesis** | 24 項:真實模型回覆解析、裁判只選不改寫、每步存且帶 post_id、讀不出 vs 沒回應、兩介面同一畫面、草稿帶 hypothesis、無基準線不要求對照組、環境、history 追溯、測完讀 WATCH、量不到的不能參選 |
| **test_clusters** | 7 項:輪替、週一開始、跨月、natural_id 規則、cluster 0 回填、同秒 seq、產品名沿用 |
| **test_cluster_run** | 12 項:`run --cluster` 全流程、主要/experiment 指示、核准不發佈+guard、只補空格、learned_from、額外貼文、模型拿掉章節編號/彎引號、太長先縮短 |
| test_parsers / test_no_invented_content / test_category_* / test_image_validation / test_subject_and_no_image / test_rtfd_bundle / test_folders / test_judge_model / test_video | 見各檔開頭 |

新測試的慣例:**用真實模型的原樣回覆當 fixture**,寫完要**故意把程式改壞,確認測試會失敗**。

## 8. 使用者的要求(原話,不要違背)

- **全部用繁體中文回覆**,包括做事中途的進度說明。程式碼、指令、檔名維持英文。對外成品(貼文、commit 訊息)用英文。
- **「你不准在沒跟我說的情況下亂開背景。」** 開背景程序(streamlit、下載、長時間測試)前先講。
- **Ollama 被佔用時使用者的 `run` 會逾時** —— 跑模型實測前要使用者同意。
- **「產品名由人類改,llm不准動。」**
- **「無論如何llm不允許自動發佈,除非之後我們做出自動發佈按鈕。」** —— 排程發佈 = 每篇人核准 + 人按排程。
- **「我不要你分析或丟想法」** —— 要原始輸出時只給原始輸出。
- **「我不需要看到不重要的文字…講重點。」「don't give me technical term」** —— 白話。
- 模型或套件的決定:**先說再改**。
- push:每次都要使用者說可以。

## 9. 已知陷阱

**每個新觀念只被套用在它誕生的那個位置。** 這個 repo 反覆出現(已十次以上):terminal 和 Streamlit
兩個審核介面不同步、同一份欄位清單寫了四份、`run` 和 `video` 各自一份產品名預設。
**改任何規則前先 grep 有幾個地方用到同一個概念。** 共用的東西放在兩個介面都會呼叫的那一層
(`approve_for_schedule`、`record_rejected`、`hypothesis.review_view`、`LABEL_KEYS`)。
`.claude/skills/two-review-surfaces/` 和 `.claude/skills/parse-what-models-return/` 有案例。

**測試通過不等於正確 —— 要跑真實模型。** cluster 第 2 段的測試一次全過,真實模型一跑就發現四個問題。
解析器照真實回覆寫(WATCH 實際回來是 `100 likes`、`Reposts (shares) as…`、`Likes or reposts…`)。

**時間戳記只到秒會打平。** `_posts.jsonl` 和產品名紀錄都踩過:同一秒寫兩列,SQL 或「最新」取到舊的。用 `seq` 或寫入順序。

**streamlit 會留著舊模組。** 已自動處理(`_stale_review_server`),但只認得用絕對路徑啟動的那個。
手動驗證 UI 時:`CONTENTMASTER_DATA_ROOT=<暫存> ./.venv/bin/python -m streamlit run "$PWD/streamlit_app.py" --server.port 8502`。

**啟發式門檻是為這一份白皮書調的**(`MAX_HEADING_WORDS = 12` 等)。別人的文件靠第 1 層或人撿回,不是調數字。

**`.env` 有憑證**(Bluesky、Cloudflare、Cognee 密碼)。不要印出來、不要寫進任何文件。
