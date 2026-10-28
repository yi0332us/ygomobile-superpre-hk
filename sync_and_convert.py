import collections
import concurrent.futures as cf
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile

import opencc

URL_VERSION = "https://cdn02.moecube.com:444/ygopro-super-pre/data/version.txt"
URL_JSON = "https://cdn02.moecube.com:444/ygopro-super-pre/data/test-release.json"
URL_YPK = "https://cdn02.moecube.com:444/ygopro-super-pre/archive/ygopro-super-pre.ypk"

# 同主 build.yml 保持一致用 s2twp；想用香港字形可改成 s2hk（兩個 pip 套件都認）
CONFIG = os.environ.get("OPENCC_CONFIG", "s2twp")
FORCE = os.environ.get("FORCE", "").lower() in ("1", "true", "yes")
UA = {"User-Agent": "Mozilla/5.0"}


def make_cc():
    # opencc-python-reimplemented 用 's2twp'；官方 opencc 套件用 's2twp.json'
    try:
        return opencc.OpenCC(CONFIG)
    except Exception:
        return opencc.OpenCC(CONFIG + ".json")


def convert_cdb(path, cc):
    """轉換一個 cdb 入面 texts 表嘅 name/desc/str1~str16。回傳處理筆數，冇 texts 表回傳 -1。"""
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "texts" not in tables:
        conn.close()
        return -1
    wanted = ["name", "desc"] + [f"str{i}" for i in range(1, 17)]
    have = [r[1] for r in cur.execute("PRAGMA table_info(texts)")]
    cols = [c for c in wanted if c in have]
    rows = cur.execute(f"SELECT id, {', '.join(cols)} FROM texts").fetchall()
    data = [tuple(cc.convert(v) if isinstance(v, str) and v else v for v in r[1:]) + (r[0],) for r in rows]
    cur.executemany(f"UPDATE texts SET {', '.join(c + '=?' for c in cols)} WHERE id=?", data)
    conn.commit()
    conn.close()
    return len(rows)


def convert_strings_conf(path, cc):
    with open(path, "r", encoding="utf-8-sig") as f:
        text = f.read()
    out = cc.convert(text)
    ids = lambda t: re.findall(r"^!(\w+)\s+(\S+)", t, re.M)
    if ids(text) != ids(out):
        raise SystemExit(f"{path}: 轉換後條目編號同原檔唔一致，停止")
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    return len(ids(text))


def digests(path):
    out = {}
    for name in ("md5", "sha1", "sha256"):
        h = hashlib.new(name)
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        out[name] = h.hexdigest()
    return out


PIC_URLS = [
    os.environ.get("PIC_URL_1", "https://cdn.233.momobako.com/ygopro/pics/{id}.jpg"),
    os.environ.get("PIC_URL_2", "https://images.ygoprodeck.com/images/cards/{id}.jpg"),
]
BASE_CDB_URL = "https://github.com/mycard/ygopro-database/raw/master/locales/zh-CN/cards.cdb"
FILL_PICS = os.environ.get("FILL_PICS", "1").lower() in ("1", "true", "yes")
FILL_MAX = int(os.environ.get("FILL_MAX", "800"))
PIC_UA = {"User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"}


def card_ids_from_cdb(path):
    ids = set()
    try:
        conn = sqlite3.connect(path)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if {"datas", "texts"} <= tables:
            for (i,) in conn.execute("SELECT d.id FROM datas d JOIN texts t ON t.id = d.id "
                                     "WHERE (d.type & 16384) = 0 AND t.name != ''"):
                ids.add(str(i))
        conn.close()
    except Exception as e:
        print(f"::warning::讀 {path} 失敗: {e}")
    return ids


def fetch_pic(cid):
    for idx, fmt in enumerate(PIC_URLS):
        url = fmt.format(id=cid)
        for _ in range(2):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=PIC_UA), timeout=20) as r:
                    data = r.read()
                if len(data) > 2048 and data[:3] == b"\xff\xd8\xff":
                    return cid, data, idx
                break
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    break
            except Exception:
                pass
    return cid, None, None


def report_and_fill_pics(work, base_cdb_path=None, cc=None):
    """報告先行包內新卡嘅卡圖狀況；FILL_PICS 開啟時，由 CDN 補入 ypk 入面缺嘅卡圖。"""
    ypk_ids, jpgs = set(), []
    for root, _, files in os.walk(work):
        for fn in files:
            p = os.path.join(root, fn)
            if fn.lower().endswith(".cdb"):
                ypk_ids |= card_ids_from_cdb(p)
            elif fn.lower().endswith(".jpg"):
                jpgs.append(os.path.relpath(p, work))
    have = {os.path.basename(n)[:-4] for n in jpgs}
    pic_dir = collections.Counter(os.path.dirname(n) for n in jpgs).most_common(1)[0][0] if jpgs else "pics"

    base_ids = set()
    if base_cdb_path is None:
        base_cdb_path = "base_cards.cdb"
        rc = subprocess.run(["curl", "-sSfL", "--retry", "3", "--max-time", "120", "-o", base_cdb_path, BASE_CDB_URL]).returncode
        if rc != 0:
            base_cdb_path = None
    if base_cdb_path and os.path.exists(base_cdb_path):
        base_ids = card_ids_from_cdb(base_cdb_path)
    if cc is not None:
        report_overlap(work, base_cdb_path, cc)
    print(f"=== 先行包卡圖狀況 ===")
    print(f"先行包 cdb 內卡數: {len(ypk_ids)} | 主程式卡庫卡數: {len(base_ids)} | ypk 內已有卡圖: {len(have)}（目錄 {pic_dir!r}）")
    if not base_ids:
        print("::warning::攞唔到主程式卡庫，無法分辨邊啲係新卡，今次唔補圖")
        return 0
    new_ids = ypk_ids - base_ids
    missing = sorted(new_ids - have, key=int, reverse=True)
    print(f"先行包獨有新卡: {len(new_ids)} 張，其中 ypk 冇卡圖: {len(missing)} 張")
    if not FILL_PICS or not missing:
        if missing:
            print("缺圖卡號（最新 30 個）:", ", ".join(missing[:30]))
        return 0
    if len(missing) > FILL_MAX:
        print(f"::warning::缺圖超過上限 {FILL_MAX}，只補最新 {FILL_MAX} 張")
        missing = missing[:FILL_MAX]
    added, notfound = [0] * len(PIC_URLS), []
    target = os.path.join(work, pic_dir)
    os.makedirs(target, exist_ok=True)
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        for cid, data, idx in ex.map(fetch_pic, missing):
            if data is None:
                notfound.append(cid)
            else:
                added[idx] += 1
                with open(os.path.join(target, cid + ".jpg"), "wb") as f:
                    f.write(data)
    print(f"補圖完成：主來源 {added[0]} 張，後備來源 {added[1]} 張，兩邊都冇 {len(notfound)} 張")
    if notfound:
        print("仍然冇圖嘅卡號（最新 30 個）:", ", ".join(notfound[:30]))
    return sum(added)



def names_by_id(path):
    out = {}
    try:
        conn = sqlite3.connect(path)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "texts" in tables:
            for i, n in conn.execute("SELECT id, name FROM texts WHERE name != ''"):
                out[str(i)] = n
        conn.close()
    except Exception as e:
        print(f"::warning::讀 {path} 失敗: {e}")
    return out


def report_overlap(work, base_cdb_path, cc):
    """診斷：先行包內嘅「臨時編號卡」（例如 100xxxxxx）同主卡庫正式編號（8 位）係咪同名重複。"""
    base_names = names_by_id(base_cdb_path) if base_cdb_path and os.path.exists(base_cdb_path) else {}
    if not base_names:
        return
    by_name = collections.defaultdict(list)
    for i, n in base_names.items():
        by_name[cc.convert(n)].append(i)   # 主卡庫係簡體，先轉成同先行包一樣嘅繁體再比對
    for root, _, files in os.walk(work):
        for fn in sorted(files):
            if not fn.lower().endswith(".cdb"):
                continue
            names = names_by_id(os.path.join(root, fn))
            in_base = [i for i in names if i in base_names]
            temp_ids = [i for i in names if len(i) >= 9]
            dup = [(i, by_name[names[i]]) for i in temp_ids if by_name.get(names[i])]
            print(f"--- {fn}: 共 {len(names)} 張 | 同編號已喺主卡庫 {len(in_base)} 張 | 9 位以上臨時編號 {len(temp_ids)} 張 | 其中同名卡已有 8 位正式編號 {len(dup)} 張")
            for i, formal in dup[:8]:
                print(f"    {i} 《{names[i]}》 <-> 主卡庫編號 {', '.join(formal[:4])}")
            for i in in_base[:5]:
                print(f"    同編號例子 {i} 《{names[i]}》")



def process_ypk(src_ypk, out_ypk, raw_json_text, cc, work="tmp_ypk"):
    if os.path.exists(work):
        shutil.rmtree(work)
    os.makedirs(work)
    old = digests(src_ypk)

    with zipfile.ZipFile(src_ypk) as z:
        print("=== ypk 內容（前 40 項）===")
        for i in z.infolist()[:40]:
            print(f"{i.file_size:>10,}  {i.filename}")
        print(f"共 {len(z.infolist())} 項")
        z.extractall(work)

    n_cdb = n_conf = 0
    for root, _, files in os.walk(work):
        for fn in files:
            p = os.path.join(root, fn)
            low = fn.lower()
            if low.endswith(".cdb"):
                n = convert_cdb(p, cc)
                print(f"cdb: {os.path.relpath(p, work)} -> {n if n >= 0 else '冇 texts 表，略過'}")
                if n >= 0:
                    n_cdb += 1
            elif low.endswith("strings.conf"):
                n = convert_strings_conf(p, cc)
                print(f"conf: {os.path.relpath(p, work)} -> {n} 條")
                n_conf += 1
    if n_cdb == 0:
        raise SystemExit("ypk 入面搵唔到可轉換嘅 cdb（texts 表），停止，避免發布未繁化嘅檔案")

    report_and_fill_pics(work, os.environ.get("BASE_CDB"), cc)

    if os.path.exists(out_ypk):
        os.remove(out_ypk)
    with zipfile.ZipFile(out_ypk, "w", compression=zipfile.ZIP_DEFLATED) as zo:
        for root, _, files in os.walk(work):
            for fn in sorted(files):
                ap = os.path.join(root, fn)
                zo.write(ap, os.path.relpath(ap, work))
    shutil.rmtree(work)
    new = digests(out_ypk)

    # JSON：繁化文字；如果入面有舊 ypk 嘅校驗值，換成新 ypk 嘅，避免 App 校驗失敗
    out_json = cc.convert(raw_json_text)
    swapped = []
    for name in ("md5", "sha1", "sha256"):
        for variant in (old[name], old[name].upper()):
            if variant in out_json:
                out_json = out_json.replace(variant, new[name] if variant == old[name] else new[name].upper())
                swapped.append(name)
    try:
        json.loads(out_json)
    except Exception as e:
        print(f"::warning::繁化後 JSON 唔合法（{e}），保留原文")
        out_json = raw_json_text
    print(f"cdb 轉換 {n_cdb} 個，strings.conf {n_conf} 個；JSON 內更新校驗值: {swapped or '無（冇搵到舊校驗值）'}")
    print("新 ypk 大小:", os.path.getsize(out_ypk), "bytes")
    return out_json


def main():
    cc = make_cc()

    req = urllib.request.Request(URL_VERSION, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as resp:
        upstream_version = resp.read().decode("utf-8").strip()

    local_version = ""
    if os.path.exists("version.txt"):
        local_version = open("version.txt", encoding="utf-8").read().strip()
    print(f"本地版本: {local_version} | 上游版本: {upstream_version} | FORCE={FORCE}")

    if not FORCE and local_version == upstream_version and os.path.exists("ygopro-super-pre.ypk"):
        print("先行卡版本一致，無須更新。")
        return

    req_json = urllib.request.Request(URL_JSON, headers=UA)
    with urllib.request.urlopen(req_json, timeout=25) as resp:
        raw_json_text = resp.read().decode("utf-8")

    subprocess.run(["curl", "-sSfL", "--http1.1", "--retry", "3", "--max-time", "300",
                    "-o", "upstream.ypk", URL_YPK], check=True)
    if not zipfile.is_zipfile("upstream.ypk"):
        raise SystemExit("下載到嘅 upstream.ypk 唔係有效 zip")

    out_json = process_ypk("upstream.ypk", "ygopro-super-pre.ypk", raw_json_text, cc)
    with open("test-release.json", "w", encoding="utf-8") as f:
        f.write(out_json)

    size = os.path.getsize("ygopro-super-pre.ypk")
    if size > 95 * 1024 * 1024:
        raise SystemExit(f"ypk 有 {size/1048576:.1f}MB，超過 GitHub 單檔 100MB 限制，唔可以直接 push")

    with open("version.txt", "w", encoding="utf-8") as f:
        f.write(upstream_version)
    if os.path.exists("upstream.ypk"):
        os.remove("upstream.ypk")
    print("全部先行卡檔案打包完畢！")


if __name__ == "__main__":
    main()
