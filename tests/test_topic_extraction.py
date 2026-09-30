import os, shutil, sys
from pathlib import Path
R=Path(sys.argv[1]); shutil.rmtree(R, ignore_errors=True); R.mkdir(parents=True)
os.environ["CONTENTMASTER_DATA_ROOT"]=str(R)
from contentmaster import topic_index as ti

NUMBERED = """This text is a comprehensive guide to caring for cats.

The text is organized into several sections, including:

1. **Boredom in Cats**: This section addresses the issue of boredom and how to stimulate natural behaviours.
2. **Spraying in Cats**: This section provides guidance on addressing spraying, a sign of stress.
3. **Attacking People or Other Pets**: This section offers advice on preventing aggressive behaviour."""

got = ti.parse_topics_response(NUMBERED)
assert [t["topic"] for t in got] == ["Boredom in Cats","Spraying in Cats","Attacking People or Other Pets"], got
print("1. 編號清單格式解析得出來(這次丟掉的就是這種)✅")

assert not any(t["topic"].lower().startswith(("the text","this text")) for t in got)
print("2. 回答本身的鋪陳句不會被當成主題 ✅")

exact = "Topic: Risks | Brief: Outdoor access raises risk.\nTopic: Needs | Brief: Cats need interaction."
assert [t["topic"] for t in ti.parse_topics_response(exact)] == ["Risks","Needs"]
print("3. 原本要求的格式仍然優先 ✅")

# unparseable answer must not mark the document processed.
# _local_complete is stubbed out rather than left to a live Ollama: with a
# real model this asserted on whatever the model happened to say that run,
# which is not what the test is about. Returning None is also the honest
# case — it is what a stopped Ollama returns.
ti._local_complete = lambda prompt: None
doc = R/"w.rtf"; doc.write_text(r"{\rtf1\ansi words}")
ti.save_index("P", {"product":"P","source_hash":"OLD","topics":[{"topic":"A","brief":"b","used_count":0}]})
out = ti.sync_topics("P", doc, lambda: "Some prose that contains no topics at all whatsoever.")
assert out["source_hash"] == "OLD", out["source_hash"]
assert len(out["topics"]) == 1
print("4. 解析不出主題時不更新雜湊 — 下次會再試,不會當成已處理 ✅")

# --- 憑空造的主題要丟掉(2026-09-28)---
# 把散文交給模型重新排版,模型寧可回答也不願回空的。餵它「完全沒有主題」
# 的句子,它造出 'Empty Text' 和 'Rules' —— 兩個詞在輸入裡都不存在。
src = "Some prose that contains no topics at all whatsoever."
invented = [{"topic":"Empty Text","brief":"x"}, {"topic":"Rules","brief":"y"}]
assert ti._grounded_only(invented, src) == []
print("5. 名稱在來源裡完全找不到的主題會被丟掉 ✅")

real_src = "Spraying in cats is often a sign of stress. Indoor living has real effects."
real = [{"topic":"Spraying Behaviour","brief":"x"}, {"topic":"Effects Of Indoor Living","brief":"y"}]
assert len(ti._grounded_only(real, real_src)) == 2
print("6. 詞形變化(spraying/spray)算同一個詞,真實主題不會被誤殺 ✅")

# --- brief 改了要被抓到(2026-09-28)---
A = "A cat is a domesticated mammal belonging to the species Felis catus, unlike many devices."
assert ti.brief_changed(A, A) is False
assert ti.brief_changed(A, "A cat is a domesticated mammal belonging to the\n  species Felis catus, unlike many devices.") is False
print("7. 完全相同或只是重新換行,不算改變 ✅")

assert ti.brief_changed(A, A.replace("domesticated","domestecated")) is False
assert ti.brief_changed(A, A.replace("many devices","most appliances")) is False
print("8. 錯字和換一個詞在 10% 門檻以下,不打擾人 ✅")

assert ti.brief_changed(A, "Cats are independent animals that choose when to participate.") is True
assert ti.brief_changed("", A) is True
print("9. 重寫過的摘要超過門檻,要人確認 ✅")

# 名稱沒變、內文重寫的章節,以前會被靜默丟掉(genuinely_new 只比名稱)
R2 = R/"two"; R2.mkdir()
ti.save_index("Q", {"product":"Q","source_hash":"OLD",
                    "topics":[{"topic":"Introduction","brief":A,"used_count":7}]})
idx = ti.load_index("Q")
fresh = {"topic":"Introduction","brief":"Cats are independent animals that choose when to participate."}
existing = idx["topics"][0]
assert existing["topic"].lower() == fresh["topic"].lower()
assert ti.brief_changed(existing["brief"], fresh["brief"]) is True
print("10. 名稱相同但內文重寫 -> 偵測得到（以前完全沒有路徑會發現）✅")
print("\nall passed")
