# 抽取工具清單

記錄於 2026-09-26,停用 Cognee 作為主題抽取器的同時。

Cognee 沒有刪除 —— 它保留給它真正擅長的事:**跨多份文件、貼文、成效、
人工註記累積之後的檢索**。「這類主張以前哪一次有效」是真正的檢索問題,
一份文件不是。停用的只是「從一份文件抽主題」這條路。詳見
`src/contentmaster/pipeline.py` 的 `_extract()`。

---

## 文件與文字

| 套件 | 能抽什麼 |
|---|---|
| **Docling** | PDF、Word、PPT、Excel、HTML、圖片 → Markdown / JSON |
| **Unstructured** | PDF、Word、PPT、RTF、HTML、Email、圖片 → 切成標題、段落、表格等區塊 |
| **PyMuPDF** | PDF 的文字、頁碼、座標、圖片、表格 |
| **pdfplumber** | 文字型 PDF 的表格、欄列、線條與座標 |
| **python-docx** | DOCX 的段落、表格、標題 |
| **python-pptx** | PPTX 每張投影片的文字、表格、講者備註 |
| **striprtf** | RTF 的純文字 |
| **Apache Tika** | 通用抽取器;PDF、Office、RTF、HTML、Email 和許多未知格式 |
| **LibreOffice headless** | 把 RTF、RTFD、DOCX、PPTX 等轉成較好處理的 PDF / DOCX / HTML |

## OCR / 掃描檔

| 套件 | 能抽什麼 |
|---|---|
| **PaddleOCR** | 圖片、掃描 PDF、截圖中的中英文文字、版面與表格 |
| **Tesseract / pytesseract** | 較輕量地抽圖片或掃描檔中的文字 |
| **OCRmyPDF** | 把掃描 PDF 加上可搜尋文字層 |

## 影音

| 套件 | 能抽什麼 |
|---|---|
| **FFmpeg** | 從影片抽音訊、字幕軌、影格;轉換影音格式 |
| **faster-whisper** | 把音訊／影片語音轉成逐字稿與時間戳 |
| **Whisper** | 逐字稿、辨識語言、可翻譯 |
| **WhisperX** | 逐字稿 + 更準時間戳 + 講者區分 |
| **OpenCV** | 從影片抽影格、找場景變化、處理圖片 |
| **transformers + 開源 VLM** | 從圖片或影片影格抽「畫面意思」、UI、圖表、物件描述 |

---

## 這台機器的限制

8 GB 統一記憶體,而且要跟 Ollama(產稿、圖片 prompt、顧問、裁判)共用。
白皮書當初放棄本地擴散模型就是這個原因。

`docling` 本體只有 5 KB,但 `pip install docling` 會拉進 **68 個套件**,
其中包含 `torch`、`torchvision`、`transformers`、`opencv-python`、
`rapidocr`、`scipy`。目前 venv 是 693 MB。

`ffmpeg` 已安裝(影片合成在用)。`striprtf` 已安裝。

## 目前實際在用的

只有 `striprtf`(RTF/RTFD)和選用的 `pypdf`(未安裝)。
`document.read_text()` 一個函式涵蓋 `.rtf` / `.rtfd` / `.pdf` / `.txt` / `.md`,
`document.extract_headings()` 用正則從文字裡找章節標題。

這份白皮書的 7 個章節就是這樣抽出來的,沒有模型參與。
