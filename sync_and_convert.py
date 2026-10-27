import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
import zipfile

import opencc

URL_VERSION = "https://cdn02.moecube.com:444/ygopro-super-pre/data/version.txt"
URL_JSON = "https://cdn02.moecube.com:444/ygopro-super-pre/data/test-release.json"
URL_YPK = "https://cdn02.moecube.com:444/ygopro-super-pre/archive/ygopro-super-pre.ypk"

# 同主 build.yml 保持一致用 s2twp；想用香港字形可改成 s2hk（兩個 pip 套件都認）
CONFIG = os.environ.get("OPENCC_CONFIG", "s2hk")
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
