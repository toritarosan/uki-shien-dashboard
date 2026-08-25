#!/usr/bin/env python3
"""災害支援情報ダッシュボードの構造検証スクリプト。

宇城市版・八代市版のどちらでも動くように作ってある。
自動巡回がPRをマージしてよいかどうかの判定に使う。

使い方:
    python3 tools/verify_structure.py index.html --asof "2026-08-24 12:08"

    # 時点チェックを省略する場合（構造だけ見る）
    python3 tools/verify_structure.py index.html

終了コード 0 = 全項目通過（マージしてよい） / 1 = 1つ以上失敗（マージしない）

注意: 「git diff --check」は別途シェルで実行すること（このスクリプトには含まれない）。
"""

import argparse
import re
import subprocess
import sys
from html.parser import HTMLParser

VOID = {
    "area", "base", "br", "col", "embed", "hr", "img",
    "input", "link", "meta", "param", "source", "track", "wbr",
}


class TagValidator(HTMLParser):
    """開きタグと閉じタグの対応を検証する。"""

    def __init__(self):
        super().__init__()
        self.stack = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append((tag, self.getpos()))

    def handle_endtag(self, tag):
        if not self.stack:
            self.errors.append(f"対応する開きタグのない </{tag}> が {self.getpos()} にあります")
            return
        expected, pos = self.stack.pop()
        if expected != tag:
            self.errors.append(
                f"{pos} で開いた <{expected}> が閉じられる前に、"
                f"{self.getpos()} で </{tag}> が現れました"
            )

    def finish(self):
        for tag, pos in self.stack:
            self.errors.append(f"{pos} で開いた <{tag}> が閉じられていません")
        return self.errors


def visible_text(html):
    """CSS・スクリプト・コメント・タグを除いた可視文字を返す（空白は除去）。"""
    h = re.sub(r"<style.*?</style>", "", html, flags=re.S)
    h = re.sub(r"<script.*?</script>", "", h, flags=re.S)
    h = re.sub(r"<!--.*?-->", "", h, flags=re.S)
    h = re.sub(r"<[^>]+>", "", h)
    return re.sub(r"\s+", "", h)


def git_show_head(path):
    """1つ前のコミットの同ファイルを返す（取得できなければ None）。"""
    try:
        r = subprocess.run(
            ["git", "show", f"HEAD:{path}"],
            capture_output=True, text=True, check=True,
        )
        return r.stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", help="検証するHTMLファイル（例: index.html）")
    ap.add_argument("--asof", help='今回の裏取り時刻。例: "2026-08-24 12:08"')
    args = ap.parse_args()

    html = open(args.file, encoding="utf-8").read()
    old = git_show_head(args.file)
    failures = []

    def report(no, name, ok, detail=""):
        mark = "OK" if ok else "NG"
        line = f"{no}. {name:<22}: {mark}"
        if detail:
            line += f"  {detail}"
        print(line)
        if not ok:
            failures.append(no)

    # --- 1. タグ整合性 ---
    v = TagValidator()
    v.feed(html)
    errs = v.finish()
    report(1, "タグ整合性", not errs, "" if not errs else "\n     - " + "\n     - ".join(errs))

    # --- 2. ページ内アンカーが全て id と対応 ---
    ids = set(re.findall(r'id="([\w-]+)"', html))
    anchors = set(re.findall(r'href="#([\w-]+)"', html))
    unresolved = sorted(anchors - ids)
    report(2, "アンカー整合性", not unresolved,
           f"({len(anchors)}件検査)" if not unresolved else f"未解決: {unresolved}")

    # --- 3. 姉妹ページへの固定導線が消えていないこと ---
    #     数を決め打ちせず、1つ前のコミットと比べて減っていないかで判定する
    #     （宇城市版と八代市版でリンク数が違っても動くように）
    def link_count(src):
        return len(re.findall(r"kumamoto-katazuke-manual", src))

    now_links = link_count(html)
    has_banner = 'class="guide-banner"' in html
    has_inline = 'class="guide-inline"' in html
    has_footer = "関連ページ" in html
    if old is not None:
        prev_links = link_count(old)
        not_decreased = now_links >= prev_links
        detail = f"マニュアルへのリンク {prev_links}→{now_links}, banner={has_banner}, inline={has_inline}, footer={has_footer}"
    else:
        prev_links = None
        not_decreased = now_links > 0
        detail = f"マニュアルへのリンク {now_links}件（前版と比較できず）, banner={has_banner}, inline={has_inline}, footer={has_footer}"
    ok3 = not_decreased and has_banner and has_inline and has_footer
    report(3, "姉妹ページ導線の保全", ok3, detail)

    # --- 4. asof-badge とフッターの時刻が今回の時刻に更新されていること ---
    m_badge = re.search(r'class="asof-badge">(.*?)</span>', html, re.S)
    m_foot = re.search(r"最終裏取り:\s*([\d-]+\s+[\d:]+)\s*JST", html)
    badge_raw = m_badge.group(1) if m_badge else ""
    badge_txt = re.sub(r"<[^>]+>", "", badge_raw).strip()
    foot_txt = m_foot.group(1) if m_foot else ""
    if args.asof:
        date_part, time_part = args.asof.split()
        y, mo, d = date_part.split("-")
        jp = f"{y}年{int(mo)}月{int(d)}日"
        ok4 = (jp in badge_txt and time_part in badge_txt
               and foot_txt.replace("  ", " ") == args.asof)
        report(4, "時点の更新", ok4, f'badge="{badge_txt}" footer="{foot_txt}"')
    else:
        report(4, "時点の更新", True, f'（--asof 未指定のためスキップ）badge="{badge_txt}"')

    # --- 5. asof-badge が1行に保たれていること ---
    n = len(badge_txt)
    report(5, "asof-badge 100字以内", n <= 100, f"{n}文字")

    # --- 6. 「今日変わったこと」と「経緯・訂正の記録」が存在すること ---
    has_today = 'class="today"' in html
    has_kiroku = 'id="kiroku"' in html
    report(6, "today/kiroku の存在", has_today and has_kiroku,
           f"today={has_today}, kiroku={has_kiroku}")

    # --- 7. 出典URLが失われていないこと（減った分を報告） ---
    if old is not None:
        o = set(re.findall(r'href="(https?://[^"]+)"', old))
        nset = set(re.findall(r'href="(https?://[^"]+)"', html))
        dropped = sorted(o - nset)
        # 落ちたURLがあっても自動NGにはしない（新しい報への差し替え・受付終了の削除は正当）。
        # ただし必ず人が目で確認できるよう列挙する。
        print(f"7. 出典URLの増減        : 参考  {len(o)}→{len(nset)}件"
              f"（参照されなくなった {len(dropped)}件）")
        for u in dropped:
            print(f"     - {u}")
        if dropped:
            print("     ※ 新しい報への差し替え・受付終了・404 なら問題なし。"
                  "本文に残した事実の出典が落ちていないか目視で確認すること。")
    else:
        print("7. 出典URLの増減        : 参考  前版と比較できず")

    # --- 参考情報: 可視文字数 ---
    vis = visible_text(html)
    m_k = re.search(r'<section id="kiroku">.*?</section>', html, re.S)
    arc = len(visible_text(m_k.group(0))) if m_k else 0
    print()
    print(f"   可視文字数: {len(vis):,}"
          + (f"（うち下部の折りたたみ {arc:,} / 最初に目に入る本文 {len(vis) - arc:,}）" if arc else ""))
    if old is not None:
        vo = len(visible_text(old))
        print(f"   前版比: {vo:,} → {len(vis):,} ({len(vis) / vo * 100:.0f}%)")

    print()
    if failures:
        print(f"=> 失敗: {failures} — マージしないこと。PRを残して通知する。")
        return 1
    print("=> 全項目通過 — マージしてよい。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
