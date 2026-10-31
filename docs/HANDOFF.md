# 交接 — ContentMaster

寫於 2026-10-26 21:30(取代 9/30、10/05、10/15 的版本)。**貼這份給下一個模型當開場 prompt。**

開場先做:
1. 讀完這份,尤其 §0「明天的待辦」和 §8「使用者的要求」。
2. `./scripts/run_tests.sh` —— 應該 **20 個套件全過**。
3. `./.venv/bin/contentmaster status` 和 `./.venv/bin/contentmaster clusters`。
4. **全部用繁體中文回覆**,包括做事中途的進度說明。

---

## 0. 明天(2026-10-27,週二)的待辦

**明天是 cluster 1 的第 2 天。Mac 第一次要在 18:00 自己發文(#12)。**

### 使用者
| 時間 | 做什麼 |
|---|---|
| 18:00 前 | **Mac 開著、螢幕不要蓋上**(蓋上 = 睡著,要等打開才會補發、標成晚發) |
| 任何時候 | 週六(10/31)有 1 篇待審:「Meet our **Furry** Roommate…」名字寫錯 → **Reject**,再跑 `contentmaster run --cluster` 補,然後 Approve(核准後自動排到 18:00) |
| 18:00 之後 | `contentmaster status` —— 確認 #12 從「排程中」變成已發佈;沒有就看 `logs/publish.log` 最後幾行,貼給 Claude |
| 21:00 之後 | `contentmaster review` —— #11 的 24h 檢查(#11 是 10/26 20:57 發的,滿 24 小時才開放) |

### Claude Code
1. **確認第一次自動發文**:使用者貼回 `status` / `logs/publish.log` 時,檢查 #12 有沒有發、晚多少、`environment` 有沒有記。
   launchd 的實際執行從沒在真實時間點驗證過(安裝有驗證,`runs = 0`)。
2. **續做被打斷的工作(使用者已同意,10/25):**
   - **把所有模型指示搬到 `prompts/`**,一個用途一個檔,程式只決定用哪段、填哪些變數。
     **做法:先存快照、搬完逐字比對。** 快照腳本原本在 scratchpad(已不存在),要重寫:
     對固定輸入呼叫每個指示產生器,並用假的 `requests.post` 抓住送出去的完整文字,存成 JSON;搬完再跑一次,必須逐字相同。
     指示所在位置見 §3「指示在哪裡」。
   - **記錄每一次模型呼叫**:新表 `plays/_model_calls.jsonl`,一次呼叫一列:用途、模型、完整指示、
     原始回覆、用到的指示檔(含版本雜湊)、`cluster_id`、`post_id`。草稿記下是哪一次呼叫寫的(`call_id`)。
     建議用 contextvar 的 scope(`with model_calls.scope(cluster_id=1, arm="main"):`)讓呼叫自動帶上 cluster。
   - **所有表都要能用 `cluster_id` join**(使用者要求):`_reasoning.jsonl`、`_history.jsonl`、`_account.jsonl`
     目前沒有 `cluster_id`,要補;`stg_*` 也要補欄位。
3. **cluster 第 4 段:分析(使用者已同意,11/02 之前一定要好)**
   - 24h:只在明顯變化時才改還沒發的貼文(某篇 ≥10 互動 **且** ≥ cluster 中位數 3 倍),否則只記數字、不叫模型。
   - 7d:決定下一個 cluster 的 hypothesis。**11/02(一)cluster 1 結束,當天 `run --cluster` 會開 cluster 2**,
     用「最近一個人按 Confirm 的 hypothesis」。所以 11/02 之前,cluster 1 的 7d 分析流程要能產生下一個 hypothesis。
   - 30d:cluster 結論 —— 5 篇主要 vs 2 篇 experiment 比黏著度;一週追蹤變化;門檻一個 cluster ≥10 互動;
     額外貼文(kind=extra)互動大或那週追蹤者暴增 → 結論標「可能受影響」。
   - **追蹤變化量只算到 cluster 層級,不拆給單篇**(使用者:追蹤者不是穩定的趨勢線)。

---

## 1. 這是什麼

`/Users/liunils/Marketing hack` — 本機優先的行銷內容 pipeline。
來源文件抽主題 → 產草稿 → 人審 → 排程發到 Bluesky → 追成效 → 用成效決定下一輪。

**核心主張**:產生內容免費,難的是「知道它有沒有用」。系統**拒絕宣稱它證明不了的事**。

**使用者說明(2026-10-21):** 現在持續用這個貓的主題,只是為了驗證**整個邏輯和 pipeline 正確、沒有 bug**。
優先順序是「pipeline 對不對」,不是「文案好不好」。

產品 **Fluffy roommate**(就是那隻貓,category `A domestic cat`),來源 `Demo-white paper/cat.rtfd`(41 個主題)。
Bluesky 帳號 **0 個追蹤者**。

### 機器與模型(8GB RAM、Apple M3、Ollama 0.35)

```
顧問 A（提 hypothesis）       llama3.2:3b    DISCUSS_MODEL_A
顧問 B（另提一個）            gemma3:4b      DISCUSS_MODEL_B      ← 10/23 換掉 mistral
裁判（只選 A 或 B）           qwen3:4b       DISCUSS_JUDGE_MODEL
寫草稿/縮短/修改               llama3.2:3b    LLM_IMPROVE_MODEL
寫生圖指令                     gemma3:4b      IMAGE_PROMPT_MODEL
看圖檢查(相符/有字/捏造)      gemma3:4b      IMAGE_REVIEW_MODEL   ← 10/22 新
角色檢查(有沒有做這週的改變)  gemma3:4b      ARM_CHECK_MODEL      ← 10/24 新
生圖                           FLUX.1-schnell Cloudflare Workers AI
```
三個分析模型必須是三個不同家族(llama / gemma / qwen)。

已下載、未採用:`gemma4:e2b`、`qwen3.5:4b`(實測等使用者通知;**跑實測會佔 Ollama 15–20 分鐘,要先問**)。
淘汰、不要換回:`phi4-mini-reasoning`(7844 字思考)、`mistral` 當顧問(9 次都提「問題排解系列」、0/9 被選)、
`qwen3:4b` 當顧問(120–170 秒、1/3 讀不出)、`qwen3:4b` 關思考(反而寫上萬字)、`qwen2.5:3b`(把貓當產品)、
`granite4.2:8b`(8GB 跑不動,9/9 逾時,已刪除)。

### Python 環境
`.venv` 連到 **Homebrew 的 python@3.14**。10/24 晚上它被移除(原因不明),所有指令和排程發佈都靜靜壞掉;
10/25 重裝 3.14.9 並標成「主動安裝」。**不准在沒問使用者的情況下跑 brew uninstall / autoremove / cleanup / upgrade。**

## 2. 指令

```bash
cd "/Users/liunils/Marketing hack" && source .venv/bin/activate

contentmaster run --cluster          # 填目前這週 cluster 的空日子;沒有在跑的 cluster 才開新的
contentmaster run                    # 額外貼文(kind=extra),核准即發佈
contentmaster run --posts 3 / --terminal / --no-image
contentmaster schedule --time 18:00 [--cluster 1]   # 跟審核頁 Schedule 分頁同一個函式
contentmaster publish-due            # 發出時間已到的排程貼文(Mac 在發文時間跑的就是這個)
contentmaster clusters               # 每個 cluster 和裡面的貼文
contentmaster status                 # 什麼在等人;排程中的;過期沒發的(!! OVERDUE)
contentmaster review [--checkpoint 7d]
contentmaster refresh                # 重讀成效 + 誰互動 + 帳號快照
./scripts/run_tests.sh               # 20 個套件
```
審核頁(streamlit,`run`/`review` 會自動開):**Draft review / Analysis review / Schedule** 三個分頁。

## 3. 資料

### 每張表一列代表什麼(append-only,同一個 key 取最新那列)

| 檔案 | 一列 = | 重點欄位 |
|---|---|---|
| `plays/_posts.jsonl` | 一篇貼文的一次狀態變更 | `natural_id`(核准時給)、`post_id`(UUID)、`cluster_id`、`kind` cluster/extra、`experiment`、`status`、`scheduled_for`、`scheduled_at`、`published_at`、`late_minutes`、`seq` |
| `plays/_clusters.jsonl` | 一個 cluster 的一次狀態變更 | 週一、hypothesis、experiment 星期、`post_time`、`status`(drafting/scheduled/cancelled)、`learned_from`、`seq` |
| `plays/_draft_review.jsonl` | 一份草稿的一次狀態變更 | **內文**、圖片路徑、`image_description`/`image_check`(Gemma)、`arm_check`、標籤 |
| `plays/_tracking_review.jsonl` | 已發佈貼文的一次更新 | 實際發出的 `final_text`、`environment` |
| `plays/_analysis_review.jsonl` | 一份分析的一次狀態變更 | 等人 confirm/disagree |
| `plays/_history.jsonl` | 貼文 × checkpoint 的最終判定 | `next_hypothesis`、`hypothesis_result`(黏著度)、`reasoning_id` |
| `plays/_reasoning.jsonl` | 一次分析的所有模型步驟 | 顧問 / 裁判的 prompt + 原始回覆 |
| `plays/_account.jsonl` | 一次帳號快照 | 追蹤者數(每次發文和 refresh 記一筆) |
| `metrics/post_metrics.jsonl` | 一次成效讀取 | 四個數字 + `engagers`(誰按讚 / 轉發 / 回覆) |

DuckDB:`stg_posts`(一篇一列)、`stg_clusters`(一個 cluster 一列)、`stg_reasoning`(一次模型呼叫一列)、
`stg_success_history`。⚠️ `campaign_performance` 混兩種粒度,不要加總。
**join 的主鍵是 `post_id` → `stg_posts.cluster_id`。** 還沒有 `cluster_id` 的表見 §0 Claude 第 2 項。

### 指示在哪裡(全部寫在 Python 裡,還沒搬到 `prompts/`)
| 用途 | 位置 |
|---|---|
| 寫草稿 | `draft_generator.py` `_llm_draft_posts_for_topics`、`_post_field`、`_hypothesis_instruction`、`_subject_description`、`_llm_draft_posts`、`revise_post` |
| 顧問 / 裁判 | `discuss.py` `_proposal_prompt`、`_hypothesis()` 和 `_evidence_strategy()` 裡的 `judge_prompt` |
| 角色檢查 | `arm_check.py` `PROMPT` |
| 看圖 | `image_review.py` `PROMPT` |
| 生圖指令 | `image_generator.py` `_PROMPT_INSTRUCTION` |
| 主題整理 | `topic_index.py` `_REFORMAT_INSTRUCTION` |

框架句子是固定的;hypothesis、內文、主題、圖片描述在執行時填入。

## 4. 已決定的事(附理由 —— 不要推翻,除非使用者改口)

### 成效與 hypothesis
- `engagement_score = 3×repost + 3×bookmark + 1×like + 1×reply`。
- hypothesis = **TEST / WHY**。**看什麼是固定的,模型不選**(10/22):
  - **粉絲黏著度** = 這篇的互動者裡,以前也互動過的比例(bookmark 在 Bluesky 是私密的,不算)。
    沒人互動 = 「沒有黏著度」,不是 0%;讀不到誰互動 = 「未知」。
  - **追蹤變化量** = 一個 cluster 週的淨增加,只比 cluster 之間。
- 沒有「什麼代表錯」。使用者原話:「沒有錯這個東西,只有當下環境不適合。」
- 裁判只回 `PICK: A|B` + `REASON`,不改寫。**只有使用者按 Confirm 的 hypothesis 會被下一個 cluster 用**(10/20)。
- 每一步都留(使用者:「回朔或 trouble shoot 的時候才照得到東西」)。
- **搜尋趨勢關閉**(10/21,`TRENDS_ENABLED=0`):資料停在 9/13,顧問一直拿它出題。
- **50 追蹤者以前**,目標是衝到 50 追蹤者。

### 模型分流(10/21–10/24)
- **圖片交給 Gemma**:看每張圖、寫一句描述、檢查相符 / 有字 / 捏造。文字模型拿描述、被告知只改文字。
  Gemma 只檢查,**不提圖片測試**(一週只測一件事)。
- **角色檢查**:每篇 cluster 草稿寫完問 Gemma「有沒有做這週的改變」。主要方向答否、experiment 答是 → 丟掉重寫。
  有一次誤判(有問句的被判沒有),重試補上了。

### cluster
- **一個日曆週(一到日)、一個 hypothesis、7 篇**。5 篇主要方向、2 篇 experiment(20% 無條件進位)。
- experiment 星期照 cluster 編號輪替:1→一/四、2→二/五、3→三/六、4→四/日…(不照月曆)。
- 跨月算週一所在的月;分析單位是 cluster。
- **`run --cluster` 規則(10/26 修正)**:先補「還在跑、還有空日子」的 cluster(過去的日子不補);
  目前的 cluster 還在跑而且滿了 → **不開下一個**(下一個要等這週的結果);沒有在跑的 → 今天是週一就從今天開,否則下週一。
- 只給模型一個主題時,直接用那個主題(模型寫錯主題名也一樣)。
- 額外貼文 `kind=extra`:不進比較,但掛在當週 cluster 下。
- cluster 可以**取消**:沒發的貼文標 `cancelled`,編號保留不重用,那一週可以重開;取消的不能排程。

### 排程發佈(10/25,使用者同意:「自動發佈按鈕」= 人逐篇核准 + 人按排程)
- 核准 cluster 貼文**不發佈**,只給 `natural_id`;發佈守門在**核准時**就跑(排程發佈時沒人在看)。
- 一個 cluster 一個發文時間(半小時一格 06:00–23:30)。排程後才核准的自動加入。
- **Mac 只在發文時間醒來**(launchd `com.contentmaster.publish`,每個時間一個 `StartCalendarInterval`),
  不是每 5 分鐘檢查(使用者要求)。經過 `scripts/publish_due.sh`:環境壞了會跳 macOS 通知。
- 睡著錯過 → 醒來補發並記 `late_minutes`。網路 / 流量失敗留在排程下次再試;內容被拒標失敗。pid 鎖防重發。
- 隔離資料夾(測試)**永遠不碰** launchd、不開瀏覽器。

### 不做的
Elasticsearch(資料量小、問題是內容沒結構)、限定 hypothesis 只改寫法、寫 7 篇前讓人確認 hypothesis、
換顧問以外的模型(等使用者通知)。

## 5. Log

- **9/30 – 10/05**:pandoc 三層抽取(41 主題)、表格按欄位摘要、streamlit 用 venv 的 Python、hypothesis 全流程重寫、
  對照組只在有基準線後啟用、測完讀結果。
- **10/06 – 10/10**:cluster 資料層、產品名 `cat` 併入 `Fluffy roommate`、`run --cluster`、審核頁過時自動重啟、太長的草稿先縮短。
- **10/20**:只有 Confirm 的 hypothesis 會被用。
- **10/21 – 10/22**:趨勢關閉;Gemma 看圖;黏著度 + 追蹤變化量;分析版本升到 4(舊分析重跑)。
  測試不再開審核頁(兩個測試套件曾經每跑一次就開瀏覽器)。
- **10/23**:顧問 B mistral → gemma3:4b(重播 9 份真實指示比較);刪 granite。
- **10/24**:角色檢查(週四 experiment 有問句被人眼抓到 → 自動化);指示段落標題被當主題名 → 修;重試中的失敗不再顯示 `[error]`。
- **10/25**:「必須 / 不准」寫在 POST 欄位旁(主要方向 0/2 → 2/3);日期顯示 `Cluster 1 · Wed 2026-10-28 · Main …`;
  **排程發佈**;Python 環境消失 → 重裝 + 防範(通知、OVERDUE、規則)。
- **10/26**:`run --cluster` 在 cluster 自己的週一開了 cluster 2、Schedule 預設選最新 → **週一 #11 錯過 18:00**。
  修:先補在跑的 cluster、預設選本週、可取消。**取消 cluster 2(#16–18)**;排程 cluster 1 18:00;
  **#11 於 20:57 補發(晚 177 分鐘,使用者同意)**。只給一個主題時直接用。取消的 cluster 不能排程。

全部已 push 到 `origin/main`。

## 6. 現在的狀態(10/26 21:30)

**Cluster 1**(10/26–11/1,hypothesis `hyp-6a35ba4f`「每篇至少一個問句」,learned_from 0)

| 日 | 角色 | 狀態 |
|---|---|---|
| 一 10/26 | experiment | ✅ #11 已發佈(晚 177 分鐘) |
| 二 10/27 | 主要 | 🕒 #12 18:00 |
| 三 10/28 | 主要 | 🕒 #13 18:00 |
| 四 10/29 | experiment | 🕒 #14 18:00 |
| 五 10/30 | 主要 | 🕒 #15 18:00 |
| 六 10/31 | 主要 | 📝 1 篇待審(名字寫成 Furry,建議退) |
| 日 11/1 | 主要 | 🕒 #19 18:00 |

Cluster 2(11/02 週)**已取消**(#16–18),紀錄保留。cluster 0 = 舊的 #1–#10。
待決定分析 0(使用者 9 份都按了 Confirm)。帳號快照 1 筆(之後每次發文一筆)。launchd 已安裝,`runs = 0`。
全帳號掃描:沒有資料不一致。

## 7. 測試

`./scripts/run_tests.sh` — **20 個套件**,每個用暫存目錄當 `CONTENTMASTER_DATA_ROOT`(不開瀏覽器、不碰 launchd)。

| 套件 | 測什麼 |
|---|---|
| test_golden_extraction | 真實文件抽取逐字比對 golden |
| test_headings / test_topic_extraction / test_parsers / test_rtfd_bundle / test_folders | 抽取與解析 |
| test_scoring_gates / test_evidence_gating | 門檻;沒基準線問 TEST/WHY;趨勢關閉 |
| **test_hypothesis**(24) | 真實回覆解析、裁判只選、每步帶 post_id、黏著度、追蹤變化、Gemma 圖片描述進顧問指示 |
| **test_clusters**(7) | 輪替、週一、跨月、natural_id、cluster 0、seq、產品名 |
| **test_cluster_run**(21+) | `run --cluster` 全流程、角色檢查、核准不發佈、只補空格、**在 cluster 自己的週一不開下一個**、預設選本週、取消、單一主題 |
| **test_scheduler**(7) | 一個時間套整週、核准後自動排、只發到期的、晚發記錄、失敗分類、鎖、launchd 設定且測試沒碰真的 |
| **test_image_review**(5) | Gemma 真實回覆、捏造裝置被抓、讀不出≠通過、誰互動的紀錄規則 |
| 其他 | test_category_*、test_image_validation、test_subject_and_no_image、test_judge_model、test_no_invented_content、test_video |

慣例:**用真實模型的原樣回覆當 fixture**;寫完要**故意改壞程式確認測試會失敗**;**改指示前先跑真實模型看回什麼**。

## 8. 使用者的要求(原話,不要違背)

- **全部用繁體中文回覆**,包括中途進度。程式碼、指令、檔名維持英文;commit 訊息、對外貼文用英文。
- **「你不准在沒跟我說的情況下亂開背景。」** 下載、長時間測試、streamlit 都要先講。
- **「when you're building and testing keep in the backend with streamlit. it keeps popping up is annoying.」**
  測試和檢查不准開瀏覽器;要看畫面就用 `--server.headless true`、8502 以上的 port、隔離資料夾,看完關掉。
- **跑模型實測會佔 Ollama,使用者的 `run` 會逾時** —— 先問。
- **「產品名由人類改,llm不准動。」** 程式裡不准寫死產品名。
- **「無論如何llm不允許自動發佈,除非之後我們做出自動發佈按鈕。」** 排程 = 人逐篇核准 + 人按排程。
  補發過期貼文(`publish-due`)也要使用者說可以。
- **不准在沒問的情況下動 Homebrew**(uninstall / autoremove / cleanup / upgrade)。
- 「我不要你分析或丟想法」(要原始輸出時)、「講重點」、「don't give me technical term」。
- 模型或套件的決定:**先說再改**。push:每次都要使用者說可以。

## 9. 已知陷阱

**同一個觀念只在誕生的地方生效。** 出現十次以上:兩個審核介面不同步、欄位清單寫四份、`run`/`video` 各一份產品名預設、
Schedule 分頁和 CLI 各自選預設 cluster。改規則前先 grep;共用的放兩個介面都會呼叫的那一層。

**測試通過不等於正確 —— 跑真實模型、看真實資料。** 這兩週每一段都是真實實跑才抓到問題
(主題名對不上、標題被當主題、角色站錯邊、日期算錯、預設選錯 cluster)。

**日期邊界要測「當天」。** `week_start_after` 在週一回傳下週一,`run --cluster` 在 cluster 自己的週一就開了下一個。
任何「本週 / 下週」的邏輯,都要測週一、週日、跨月。

**時間戳記只到秒會打平。** 用 `seq` 或寫入順序決定「最新」。

**streamlit 會留著舊模組**(已自動重啟,但只認絕對路徑啟動的)。**`.venv` 依賴 Homebrew Python**(見 §1)。

**小模型的限制**:`llama3.2:3b` 會寫出只有標題的貼文、把指示段落標題當主題、忘記遠處的要求(要寫在欄位旁)、
把產品名寫錯(Furry)。`publish_guard` 擋不住「只有標題」。

**`.env` 有憑證**(Bluesky、Cloudflare、Cognee 密碼)。不要印出來、不要寫進文件。
