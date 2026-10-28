導入集合
import concurrent.futures as cf
導入 hashlib
導入 json
導入作業系統
導入 re
進口關塔
導入 sqlite3
導入子流程
導入系統
導入 urllib.error
導入 urllib.request
導入 zip 文件

導入 opencc

URL_VERSION = "https://cdn02.moecube.com:444/ygopro-super-pre/data/version.txt"
URL_JSON = "https://cdn02.moecube.com:444/ygopro-super-pre/data/test-release.json"
URL_YPK = "https://cdn02.moecube.com:444/ygopro-super-pre/archive/ygopro-super-pre.ypk"

# 同主 build.yml 保持與 s2twp 一致；想用香港字形可改成 s2hk（兩個 pip 套件都認）
CONFIG = os.environ.get("OPENCC_CONFIG", "s2twp")
FORCE = os.environ.get("FORCE", "").lower() in ("1", "true", "yes")
UA = {"User-Agent": "Mozilla/5.0"}


def make_cc():
    # opencc-python-reimplemented 使用 's2twp'；官方 opencc 套件使用 's2twp.json'
    嘗試：
        返回 opencc.OpenCC(CONFIG)
    例外情況：
        返回 opencc.OpenCC(CONFIG + ".json")


def convert_cdb(path, cc):
    """轉換一個cdb入面文字表感name/desc/str1~str16。回傳處理筆數，冇文字表回傳-1。"""
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    如果「texts」不在表格中：
        conn.close()
        返回 -1
    wanted = ["name", "desc"] + [f"str{i}" for i in range(1, 17)]
    have = [r[1] for r in cur.execute("PRAGMA table_info(texts)")]
    cols = [c for c in wanted if c in have]
    rows = cur.execute(f"SELECT id, {', '.join(cols)} FROM texts").fetchall()
    data = [tuple(cc.convert(v) if isinstance(v, str) and v else v for v in r[1:]) + (r[0],) for r in rows]
    cur.executemany(f"UPDATE texts SET {', '.join(c + '=?' for c in cols)} WHERE id=?", data)
    conn.commit()
    conn.close()
    返回 len(rows)


def convert_strings_conf(path, cc):
    with open(path, "r", encoding="utf-8-sig") as f:
        text = f.read()
    out = cc.convert(text)
    ids = lambda t: re.findall(r"^!(\w+)\s+(\S+)", t, re.M)
    如果 ids(text) != ids(out):
        raise SystemExit(f"{path}: 轉換後條目編號同原檔較不一致，停止")
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    返回 len(ids(text))


def digests(path):
    輸出 = {}
    for name in ("md5", "sha1", "sha256"):
        h = hashlib.new(name)
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        out[name] = h.hexdigest()
    返回


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
    嘗試：
        conn = sqlite3.connect(path)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        如果 {"datas", "texts"} <= tables:
            for (i,) in conn.execute("SELECT d.id FROM datas d JOIN texts t ON t.id = d.id "
                                     "WHERE (d.type & 16384) = 0 AND t.name != ''"):
                ids.add(str(i))
        conn.close()
    除異常 e 外：
        print(f"::警告::讀 {path} 失敗: {e}")
    返回 ID


def fetch_pic(cid):
    for idx, fmt in enumerate(PIC_URLS):
        url = fmt.format(id=cid)
        for _ in range(2):
            嘗試：
                使用 urllib.request.urlopen(urllib.request.Request(url, headers=PIC_UA), timeout=20) 作為 r：
                    data = r.read()
                如果 len(data) > 2048 且 data[:3] == b"\xff\xd8\xff":
                    返回 cid、資料、idx
                休息
            except urllib.error.HTTPError as e:
                如果 e.code == 404：
                    休息
            例外情況：
                經過
    返回 cid、None、None


def report_and_fill_pics(work, base_cdb_path=None):
    """報告先行包內新卡感卡圖狀況；FILL_PICS開啟時，由 CDN 補入 ypk 入面缺感卡圖。"""
    ypk_ids, jpgs = set(), []
    for root, _, files in os.walk(work):
        for fn in files:
            p = os.path.join(root, fn)
            如果 fn.lower().endswith(".cdb"):
                ypk_ids |= card_ids_from_cdb(p)
            elif fn.lower().endswith(".jpg"):
                jpgs.append(os.path.relpath(p, work))
    have = {os.path.basename(n)[:-4] for n in jpgs}
    pic_dir = collections.Counter(os.path.dirname(n) for n in jpgs).Most_common(1)[0][0] if jpgs else "pics"

    base_ids = set()
    如果 base_cdb_path 為 None：
        base_cdb_path = "base_cards.cdb"
        rc = subprocess.run(["curl", "-sSfL", "--retry", "3", "--max-time", "120", "-o", base_cdb_path, BASE_CDB_URL]).returncode
        如果 rc != 0：
            base_cdb_path = None
    如果 base_cdb_path 存在且 os.path.exists(base_cdb_path):
        base_ids = card_ids_from_cdb(base_cdb_path)
    print(f"===先行包卡圖狀況===")
    print(f"先行包 cdb 內卡圖: {len(ypk_ids)} | 主程式卡庫卡數: {len(base_ids)} | ypk 內已卡圖: {len(have)}（目錄 {pic_dir!r}）")
    如果沒有 base_ids：
        print("::警告::攞不到主課程卡庫，無法分辨邊啲系新卡，今次不補圖")
        返回 0
    new_ids = ypk_ids - base_ids
    missing = sorted(new_ids - have, key=int, reverse=True)
    print(f"先行包強調新卡: {len(new_ids)} 張，其中 ypk 冇卡圖: {len(missing)} 張")
    如果 FILL_PICS 不為空或不缺失：
        如果缺失：
            print("缺圖卡號（最新30個）:", ", ".join(missing[:30]))
        返回 0
    如果缺失值數量大於 FILL_MAX：
        print(f"::warning::缺圖超過上限{FILL_MAX}，只補最新{FILL_MAX}張")
        缺失 = missing[:FILL_MAX]
    添加，未找到 = [0] * len(PIC_URLS), []
    target = os.path.join(work, pic_dir)
    os.makedirs(target, exist_ok=True)
    使用 cf.ThreadPoolExecutor(max_workers=6) 作為 ex：
        for cid, data, idx in ex.map(fetch_pic, missing):
            如果數據為 None：
                notfound.append(cid)
            別的：
                added[idx] += 1
                with open(os.path.join(target, cid + ".jpg"), "wb") as f:
                    f.write(data)
    print(f"補圖完成：主來源{added[0]}張，後備來源{added[1]}張，雙肩都冇{len(notfound)}張")
    如果未找到：
        print("仍凜圖感卡號（最新30個）:", ", ".join(notfound[:30]))
    返回總和（加法）



def process_ypk(src_ypk, out_ypk, raw_json_text, cc, work="tmp_ypk"):
    如果 os.path.exists(work):
        shutil.rmtree(work)
    os.makedirs(work)
    old = digests(src_ypk)

    使用 zipfile.ZipFile(src_ypk) 作為 z：
        print("=== ypk內容（前40項）===")
        for i in z.infolist()[:40]:
            print(f"{i.file_size:>10,} {i.filename}")
        print(f"共 {len(z.infolist())} 項")
        z.extractall(work)

    n_cdb = n_conf = 0
    for root, _, files in os.walk(work):
        for fn in files:
            p = os.path.join(root, fn)
            low = fn.lower()
            如果 low.endswith(".cdb"):
                n = convert_cdb(p, cc)
                print(f"cdb: {os.path.relpath(p, work)} -> {n if n >= 0 else '凜文字表，略過'}")
                如果 n >= 0：
                    n_cdb += 1
            elif low.endswith("strings.conf"):
                n = convert_strings_conf(p, cc)
                print(f"conf: {os.path.relpath(p, work)} -> {n} 條")
                n_conf += 1
    如果 n_cdb == 0：
        raise SystemExit("ypk 入面搵不到可轉換感 cdb（文字表），停止，避免發布未繁化感檔案")

    report_and_fill_pics(work, os.environ.get("BASE_CDB"))

    如果 os.path.exists(out_ypk):
        os.remove(out_ypk)
    使用 zipfile.ZipFile(out_ypk, "w", compression=zipfile.ZIP_DEFLATED) 作為 zo：
        for root, _, files in os.walk(work):
            for fn in sorted(files):
                ap = os.path.join(root, fn)
                zo.write(ap, os.path.relpath(ap, 工作))
    shutil.rmtree(work)
    new = digests(out_ypk)

    # JSON：繁化文字；如果入面有舊 ypk 感校驗值，換成新 ypk 感，避免 App 校驗失敗
    out_json = cc.convert(raw_json_text)
    已交換 = []
    for name in ("md5", "sha1", "sha256"):
        for variant in (old[name], old[name].upper()):
            如果 out_json 中存在 variant：
                out_json = out_json.replace(variant, new[name] if variant == old[name] else new[name].upper())
                swapped.append(name)
    嘗試：
        json.loads(out_json)
    除異常 e 外：
        print(f"::warning::繁化後JSON不合法（{e}），保留原文")
        out_json = raw_json_text
    print(f"cdb 轉換 {n_cdb} 個，strings.conf {n_conf} 個；JSON 內更新值: {swapped or '無（冇搵到舊驗證值）'}")
    print("新 ypk 大小：", os.path.getsize(out_ypk), "bytes")
    返回 out_json


def main():
    cc = make_cc()

    req = urllib.request.Request(URL_VERSION, headers=UA)
    使用 urllib.request.urlopen(req, timeout=20) 作為 resp：
        upstream_version = resp.read().decode("utf-8").strip()

    local_version = ""
    如果 os.path.exists("version.txt"):
        local_version = open("version.txt", encoding="utf-8").read().strip()
    print(f"本地版本: {local_version} | 上游版本: {upstream_version} | FORCE={FORCE}")

    如果不是 FORCE 且 local_version == upstream_version 且 os.path.exists("ygopro-super-pre.ypk"):
        print("先行卡版本一致，前鋒更新。")
        返回

    req_json = urllib.request.Request(URL_JSON, headers=UA)
    使用 urllib.request.urlopen(req_json, timeout=25) 作為 resp：
        raw_json_text = resp.read().decode("utf-8")

    subprocess.run(["curl", "-sSfL", "--http1.1", "--retry", "3", "--max-time", "300",
                    "-o", "upstream.ypk", URL_YPK], check=True)
    如果不是 zipfile.is_zipfile("upstream.ypk"):
        raise SystemExit("下載到感上游.ypk 不繫有效 zip")

    out_json = process_ypk("upstream.ypk", "ygopro-super-pre.ypk", raw_json_text, cc)
    with open("test-release.json", "w", encoding="utf-8") as f:
        f.write(out_json)

    size = os.path.getsize("ygopro-super-pre.ypk")
    若尺寸大於 95 * 1024 * 1024：
        raise SystemExit(f"ypk 有 {size/1048576:.1f}MB，超過 GitHub 單檔 100MB 限制，無法直接推播")

    with open("version.txt", "w", encoding="utf-8") 如 f:
        f.write(upstream_version)
    如果 os.path.exists("upstream.ypk"):
        os.remove("upstream.ypk")
    print("全部先行卡文件資源完畢！")


如果 __name__ == "__main__":
    主要的（）
