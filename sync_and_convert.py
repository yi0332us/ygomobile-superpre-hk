import os
import shutil
import sqlite3
import subprocess
import zipfile
import urllib.request
import opencc

URL_VERSION = "https://cdn02.moecube.com:444/ygopro-super-pre/data/version.txt"
URL_JSON = "https://cdn02.moecube.com:444/ygopro-super-pre/data/test-release.json"
URL_YPK = "https://cdn02.moecube.com:444/ygopro-super-pre/archive/ygopro-super-pre.ypk"

def main():
    cc = opencc.OpenCC("s2hk")

    # 1. 下載最新版本號字串
    req = urllib.request.Request(URL_VERSION, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        upstream_version = resp.read().decode("utf-8").strip()

    local_version = ""
    if os.path.exists("version.txt"):
        with open("version.txt", "r", encoding="utf-8") as f:
            local_version = f.read().strip()

    print(f"本地版本: {local_version} | 上游版本: {upstream_version}")

    if local_version == upstream_version and os.path.exists("ygopro-super-pre.ypk"):
        print("先行卡版本一致，無須更新。")
        return

    print("檢測到先行卡有更新，開始下載與繁化...")

    # 2. 下載並繁化 test-release.json (控制清單介面文字)
    req_json = urllib.request.Request(URL_JSON, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req_json, timeout=25) as resp:
        raw_json_text = resp.read().decode("utf-8")
    
    converted_json_text = cc.convert(raw_json_text)
    with open("test-release.json", "w", encoding="utf-8") as f:
        f.write(converted_json_text)

    # 3. 下載 ygopro-super-pre.ypk
    subprocess.run([
        "curl", "-s", "-L",
        "--http1.1",
        "--retry", "3",
        "--max-time", "300",
        "-o", "upstream.ypk",
        URL_YPK
    ], check=True)

    # 4. 解壓 YPK
    extract_dir = "tmp_ypk"
    if os.path.exists(extract_dir):
        shutil.rmtree(extract_dir)
    os.makedirs(extract_dir, exist_ok=True)

    with zipfile.ZipFile("upstream.ypk", "r") as zip_ref:
        zip_ref.extractall(extract_dir)

    # 5. 繁化裡面的 cards.cdb
    cdb_path = os.path.join(extract_dir, "cards.cdb")
    if os.path.exists(cdb_path):
        conn = sqlite3.connect(cdb_path)
        cur = conn.cursor()
        cur.execute("SELECT id, name, desc, str1, str2, str3, str4, str5, str6, str7, str8, str9, str10, str11, str12, str13, str14, str15, str16 FROM texts")
        rows = cur.fetchall()

        for r in rows:
            cid = r[0]
            name = cc.convert(r[1]) if r[1] else ""
            desc = cc.convert(r[2]) if r[2] else ""
            strs = [cc.convert(s) if s else "" for s in r[3:]]
            cur.execute("""
                UPDATE texts SET
                    name=?, desc=?, str1=?, str2=?, str3=?, str4=?, str5=?, str6=?,
                    str7=?, str8=?, str9=?, str10=?, str11=?, str12=?, str13=?,
                    str14=?, str15=?, str16=?
                WHERE id=?
            """, (name, desc, *strs, cid))

        conn.commit()
        conn.close()
        print("cards.cdb 繁體化完成！")

    # 6. 重新封裝成 ygopro-super-pre.ypk
    if os.path.exists("ygopro-super-pre.ypk"):
        os.remove("ygopro-super-pre.ypk")

    with zipfile.ZipFile("ygopro-super-pre.ypk", "w", compression=zipfile.ZIP_DEFLATED) as zip_out:
        for root, _, files in os.walk(extract_dir):
            for file in files:
                abs_f = os.path.join(root, file)
                rel_f = os.path.relpath(abs_f, extract_dir)
                zip_out.write(abs_f, rel_f)

    # 7. 更新本地版本記錄
    with open("version.txt", "w", encoding="utf-8") as f:
        f.write(upstream_version)

    # 清理暫存檔
    shutil.rmtree(extract_dir)
    if os.path.exists("upstream.ypk"):
        os.remove("upstream.ypk")

    print("全部先行卡檔案打包完畢！")

if __name__ == "__main__":
    main()
